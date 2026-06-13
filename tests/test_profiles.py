"""Tests for the public user profile page (``/u/<username>/``).

Covers PRODUCT.md's "user profile pages" requirements as wired in
``accounts.views.profile`` + ``templates/accounts/profile.html``:

* standing: reputation, accepted contributions, post/comment counts;
* level + role badges (Creator / Moderator / Founder / Collaborator / Regular);
* the public "permanently silenced" flag (``Silence.is_public_flag``) and the
  banned note (``User.is_active is False``);
* recent *published* guitar contributions and recent (non-removed) posts;
* unknown username -> 404;
* forum author bylines link to ``/u/<username>/``.

These are HTTP/template tests: we drive the real view through
``django.test.Client`` and assert on rendered markup + context, so the URL
wiring, querysets, ordering, filtering and template branches are all exercised.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Level
from catalog.constants import PublicationStatus
from catalog.models import Brand, GuitarModel
from core.models import SiteConfiguration
from forum.models import Comment, Post, Subtopic, Topic
from moderation.models import Silence

User = get_user_model()


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def make_user(username, **kwargs):
    """Create a confirmed regular user; override flags/counts via kwargs."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "pw-not-used-by-force-login",
        "email_confirmed": True,
    }
    # create_user only takes username/email/password + arbitrary model fields;
    # split flag/field kwargs out and apply them after creation when needed.
    field_overrides = {
        k: kwargs.pop(k)
        for k in list(kwargs)
        if k
        in {
            "is_active",
            "is_founder",
            "is_community_moderator",
            "is_riffhub_creator",
            "reputation_score",
            "accepted_submissions_count",
        }
    }
    defaults.update(kwargs)
    user = User.objects.create_user(username=username, **defaults)
    if field_overrides:
        for key, value in field_overrides.items():
            setattr(user, key, value)
        user.save()
    return user


def make_published_guitar(submitter, name, *, published_at=None):
    """A PUBLISHED guitar attributed to ``submitter`` (shows on the profile)."""
    brand = Brand.objects.create(
        name=f"Brand-{name}", status=PublicationStatus.PUBLISHED
    )
    guitar = GuitarModel.objects.create(
        brand=brand,
        name=name,
        num_strings=6,
        scale_length_min_inches=Decimal("25.500"),
        scale_length_max_inches=Decimal("25.500"),
        submitted_by=submitter,
        status=PublicationStatus.PUBLISHED,
        published_at=published_at or timezone.now(),
    )
    return guitar


def make_unpublished_guitar(submitter, name, status):
    brand = Brand.objects.create(name=f"Brand-{name}", status=status)
    return GuitarModel.objects.create(
        brand=brand,
        name=name,
        num_strings=7,
        scale_length_min_inches=Decimal("25.500"),
        scale_length_max_inches=Decimal("26.500"),
        submitted_by=submitter,
        status=status,
    )


# --------------------------------------------------------------------------- #
# URL wiring / unknown-user handling
# --------------------------------------------------------------------------- #
class ProfileUrlTests(TestCase):
    def test_profile_url_reverses_to_u_username(self):
        self.assertEqual(reverse("profile", args=["jimmy"]), "/u/jimmy/")

    def test_known_user_returns_200(self):
        make_user("alice")
        resp = self.client.get(reverse("profile", args=["alice"]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "accounts/profile.html")

    def test_unknown_username_returns_404(self):
        resp = self.client.get(reverse("profile", args=["nobody"]))
        self.assertEqual(resp.status_code, 404)

    def test_username_lookup_is_case_sensitive_404(self):
        # Django usernames are stored as-given; a different case is a different
        # (non-existent) handle and must 404 rather than silently match.
        make_user("CaseUser")
        resp = self.client.get(reverse("profile", args=["caseuser"]))
        self.assertEqual(resp.status_code, 404)

    def test_profile_visible_to_anonymous_visitor(self):
        make_user("publicguy", reputation_score=5)
        resp = self.client.get(reverse("profile", args=["publicguy"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "publicguy")


# --------------------------------------------------------------------------- #
# Standing: reputation, contributions, post/comment counts
# --------------------------------------------------------------------------- #
class ProfileStandingTests(TestCase):
    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")

    def _post(self, author, title="P", removed=False):
        return Post.objects.create(
            subtopic=self.subtopic,
            author=author,
            title=title,
            body="body",
            is_removed=removed,
        )

    def _comment(self, author, post, removed=False):
        return Comment.objects.create(
            post=post, author=author, body="c", is_removed=removed
        )

    def test_reputation_score_is_rendered(self):
        make_user("repper", reputation_score=137)
        resp = self.client.get(reverse("profile", args=["repper"]))
        self.assertContains(resp, "137")
        self.assertContains(resp, "reputation")

    def test_accepted_contributions_count_is_rendered(self):
        make_user("contrib", accepted_submissions_count=4)
        resp = self.client.get(reverse("profile", args=["contrib"]))
        self.assertEqual(resp.context["profile_user"].accepted_submissions_count, 4)
        self.assertContains(resp, "contributions")

    def test_post_count_counts_only_non_removed_posts(self):
        u = make_user("poster")
        self._post(u, "live-1")
        self._post(u, "live-2")
        self._post(u, "gone", removed=True)
        resp = self.client.get(reverse("profile", args=["poster"]))
        self.assertEqual(resp.context["post_count"], 2)

    def test_comment_count_counts_only_non_removed_comments(self):
        u = make_user("commenter")
        post = self._post(u, "host")
        self._comment(u, post)
        self._comment(u, post)
        self._comment(u, post)
        self._comment(u, post, removed=True)
        resp = self.client.get(reverse("profile", args=["commenter"]))
        self.assertEqual(resp.context["comment_count"], 3)

    def test_counts_are_scoped_to_the_profile_user(self):
        owner = make_user("owner")
        other = make_user("other")
        post_owner = self._post(owner, "owner-post")
        self._post(other, "other-post")
        self._comment(owner, post_owner)
        self._comment(other, post_owner)
        resp = self.client.get(reverse("profile", args=["owner"]))
        self.assertEqual(resp.context["post_count"], 1)
        self.assertEqual(resp.context["comment_count"], 1)

    def test_counts_are_zero_for_a_brand_new_user(self):
        make_user("fresh")
        resp = self.client.get(reverse("profile", args=["fresh"]))
        self.assertEqual(resp.context["post_count"], 0)
        self.assertEqual(resp.context["comment_count"], 0)
        self.assertContains(resp, "No posts yet.")


# --------------------------------------------------------------------------- #
# Level + role badges
# --------------------------------------------------------------------------- #
class ProfileBadgeTests(TestCase):
    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

    def _get(self, username):
        return self.client.get(reverse("profile", args=[username]))

    def test_regular_user_shows_regular_level_no_role_chips(self):
        make_user("reg")
        resp = self._get("reg")
        self.assertEqual(resp.context["level"], Level.REGULAR)
        self.assertContains(resp, Level.REGULAR.label)  # "Regular"
        self.assertNotContains(resp, "Riffhub Creator")
        self.assertNotContains(resp, "Community Moderator")

    def test_creator_shows_creator_level_and_role_chip(self):
        make_user("boss", is_riffhub_creator=True)
        resp = self._get("boss")
        self.assertEqual(resp.context["level"], Level.CREATOR)
        self.assertContains(resp, "Riffhub Creator")

    def test_moderator_shows_moderator_level_and_role_chip(self):
        make_user("mod", is_community_moderator=True)
        resp = self._get("mod")
        self.assertEqual(resp.context["level"], Level.MODERATOR)
        self.assertContains(resp, "Community Moderator")

    def test_founder_badge_shown_for_sticky_founder(self):
        make_user("elder", is_founder=True)
        resp = self._get("elder")
        self.assertEqual(resp.context["level"], Level.FOUNDER)
        self.assertContains(resp, "Community Founder")

    def test_collaborator_level_from_accepted_submissions_threshold(self):
        # threshold is 3 (setUp); a user at/over it derives Collaborator.
        make_user("collab", accepted_submissions_count=3)
        resp = self._get("collab")
        self.assertEqual(resp.context["level"], Level.COLLABORATOR)
        self.assertContains(resp, Level.COLLABORATOR.label)  # "Database Collaborator"

    def test_below_collaborator_threshold_stays_regular(self):
        make_user("almost", accepted_submissions_count=2)
        resp = self._get("almost")
        self.assertEqual(resp.context["level"], Level.REGULAR)

    def test_creator_flag_outranks_founder_badge_in_level(self):
        make_user("multi", is_riffhub_creator=True, is_founder=True)
        resp = self._get("multi")
        # level chip is the highest (Creator) ...
        self.assertEqual(resp.context["level"], Level.CREATOR)
        self.assertContains(resp, "Riffhub Creator")
        # ... but the sticky founder chip is also shown (flag-driven, separate).
        self.assertContains(resp, "Community Founder")

    def test_unset_collaborator_threshold_does_not_crash_and_stays_regular(self):
        # PRODUCT.md: unset thresholds must never crash nor silently promote.
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = None
        config.save()
        make_user("manysubs", accepted_submissions_count=99)
        resp = self._get("manysubs")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["level"], Level.REGULAR)


# --------------------------------------------------------------------------- #
# Silenced / banned flags
# --------------------------------------------------------------------------- #
class ProfileSilenceBanTests(TestCase):
    SILENCED_MARKER = "permanently silenced"
    BANNED_MARKER = "This account is banned."

    def _silence(self, user, *, sequence, permanent, ends_at=None):
        return Silence.objects.create(
            target=user,
            reason="threats",
            sequence=sequence,
            starts_at=timezone.now(),
            ends_at=ends_at,
            is_permanent=permanent,
            is_public_flag=permanent,
        )

    def test_permanent_public_silence_shows_flag(self):
        u = make_user("muted")
        self._silence(u, sequence=3, permanent=True)
        resp = self.client.get(reverse("profile", args=["muted"]))
        self.assertContains(resp, self.SILENCED_MARKER)
        self.assertIsNotNone(resp.context["public_silence"])

    def test_temporary_silence_does_not_set_public_flag(self):
        # 1st/2nd silences are time-bounded and NOT publicly flagged, so the
        # public profile shows no silence banner (is_public_flag=False).
        u = make_user("tempmute")
        self._silence(
            u,
            sequence=1,
            permanent=False,
            ends_at=timezone.now() + timezone.timedelta(weeks=1),
        )
        resp = self.client.get(reverse("profile", args=["tempmute"]))
        self.assertIsNone(resp.context["public_silence"])
        self.assertNotContains(resp, self.SILENCED_MARKER)

    def test_no_silence_no_flag(self):
        make_user("clean")
        resp = self.client.get(reverse("profile", args=["clean"]))
        self.assertIsNone(resp.context["public_silence"])
        self.assertNotContains(resp, self.SILENCED_MARKER)

    def test_banned_user_shows_banned_note(self):
        make_user("badactor", is_active=False)
        resp = self.client.get(reverse("profile", args=["badactor"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.BANNED_MARKER)

    def test_active_user_has_no_banned_note(self):
        make_user("goodactor")
        resp = self.client.get(reverse("profile", args=["goodactor"]))
        self.assertNotContains(resp, self.BANNED_MARKER)

    def test_public_silence_picks_a_flagged_row_when_several_silences_exist(self):
        # A user can have a history of silences; the banner is driven by ANY
        # row carrying is_public_flag=True (the permanent one).
        u = make_user("repeat")
        self._silence(
            u,
            sequence=1,
            permanent=False,
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )
        self._silence(
            u,
            sequence=2,
            permanent=False,
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )
        self._silence(u, sequence=3, permanent=True)
        resp = self.client.get(reverse("profile", args=["repeat"]))
        flagged = resp.context["public_silence"]
        self.assertIsNotNone(flagged)
        self.assertTrue(flagged.is_public_flag)
        self.assertContains(resp, self.SILENCED_MARKER)

    def test_public_silence_flag_is_scoped_to_profile_user(self):
        owner = make_user("owner_s")
        bystander = make_user("bystander")
        self._silence(bystander, sequence=3, permanent=True)
        resp = self.client.get(reverse("profile", args=["owner_s"]))
        self.assertIsNone(resp.context["public_silence"])
        self.assertNotContains(resp, self.SILENCED_MARKER)


# --------------------------------------------------------------------------- #
# Recent published guitar contributions
# --------------------------------------------------------------------------- #
class ProfileContributionsTests(TestCase):
    def test_lists_published_guitar_contributions(self):
        u = make_user("luthier")
        make_published_guitar(u, "Telecaster")
        resp = self.client.get(reverse("profile", args=["luthier"]))
        self.assertContains(resp, "Telecaster")
        self.assertEqual(len(resp.context["guitars"]), 1)

    def test_under_revision_guitar_is_excluded(self):
        u = make_user("pending")
        make_unpublished_guitar(u, "DraftAxe", PublicationStatus.UNDER_REVISION)
        resp = self.client.get(reverse("profile", args=["pending"]))
        self.assertNotContains(resp, "DraftAxe")
        self.assertEqual(len(resp.context["guitars"]), 0)
        self.assertContains(resp, "No published guitar contributions yet.")

    def test_rejected_guitar_is_excluded(self):
        u = make_user("rejected")
        make_unpublished_guitar(u, "BadSpecAxe", PublicationStatus.REJECTED)
        resp = self.client.get(reverse("profile", args=["rejected"]))
        self.assertNotContains(resp, "BadSpecAxe")
        self.assertEqual(len(resp.context["guitars"]), 0)

    def test_only_the_profile_users_guitars_are_listed(self):
        owner = make_user("owner_g")
        other = make_user("other_g")
        make_published_guitar(owner, "OwnerGuitar")
        make_published_guitar(other, "OtherGuitar")
        resp = self.client.get(reverse("profile", args=["owner_g"]))
        self.assertContains(resp, "OwnerGuitar")
        self.assertNotContains(resp, "OtherGuitar")
        self.assertEqual(len(resp.context["guitars"]), 1)

    def test_contributions_link_to_catalog_detail(self):
        u = make_user("linker")
        guitar = make_published_guitar(u, "LinkedAxe")
        resp = self.client.get(reverse("profile", args=["linker"]))
        self.assertContains(resp, reverse("catalog:detail", args=[guitar.pk]))

    def test_contributions_ordered_newest_published_first(self):
        u = make_user("ordered_g")
        old = make_published_guitar(
            u, "OldAxe", published_at=timezone.now() - timezone.timedelta(days=10)
        )
        new = make_published_guitar(
            u, "NewAxe", published_at=timezone.now()
        )
        resp = self.client.get(reverse("profile", args=["ordered_g"]))
        listed = list(resp.context["guitars"])
        self.assertEqual([g.pk for g in listed], [new.pk, old.pk])

    def test_contributions_capped_at_ten(self):
        u = make_user("prolific_g")
        for i in range(12):
            make_published_guitar(
                u,
                f"Axe{i}",
                published_at=timezone.now() - timezone.timedelta(minutes=i),
            )
        resp = self.client.get(reverse("profile", args=["prolific_g"]))
        self.assertEqual(len(resp.context["guitars"]), 10)


# --------------------------------------------------------------------------- #
# Recent posts list
# --------------------------------------------------------------------------- #
class ProfilePostsTests(TestCase):
    def setUp(self):
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")

    def _post(self, author, title, *, removed=False, created_at=None):
        post = Post.objects.create(
            subtopic=self.subtopic,
            author=author,
            title=title,
            body="b",
            is_removed=removed,
        )
        if created_at is not None:
            # created_at is auto_now_add; override after the fact for ordering.
            Post.objects.filter(pk=post.pk).update(created_at=created_at)
            post.refresh_from_db()
        return post

    def test_lists_recent_posts_with_topic_subtopic_byline(self):
        u = make_user("threadstarter")
        self._post(u, "My great thread")
        resp = self.client.get(reverse("profile", args=["threadstarter"]))
        self.assertContains(resp, "My great thread")
        self.assertContains(resp, "Gear")
        self.assertContains(resp, "Guitars")
        self.assertEqual(len(resp.context["posts"]), 1)

    def test_removed_post_excluded_from_list(self):
        u = make_user("hadremoval")
        self._post(u, "VisiblePost")
        self._post(u, "HiddenPost", removed=True)
        resp = self.client.get(reverse("profile", args=["hadremoval"]))
        self.assertContains(resp, "VisiblePost")
        self.assertNotContains(resp, "HiddenPost")
        self.assertEqual(len(resp.context["posts"]), 1)

    def test_posts_link_to_post_detail(self):
        u = make_user("postlinker")
        post = self._post(u, "Clickable")
        resp = self.client.get(reverse("profile", args=["postlinker"]))
        self.assertContains(resp, reverse("forum:post", args=[post.pk]))

    def test_posts_ordered_newest_first(self):
        u = make_user("ordered_p")
        older = self._post(
            u, "Older", created_at=timezone.now() - timezone.timedelta(days=2)
        )
        newer = self._post(
            u, "Newer", created_at=timezone.now()
        )
        resp = self.client.get(reverse("profile", args=["ordered_p"]))
        listed = list(resp.context["posts"])
        self.assertEqual([p.pk for p in listed], [newer.pk, older.pk])

    def test_posts_capped_at_ten(self):
        u = make_user("prolific_p")
        for i in range(12):
            self._post(
                u, f"Post{i}", created_at=timezone.now() - timezone.timedelta(minutes=i)
            )
        resp = self.client.get(reverse("profile", args=["prolific_p"]))
        self.assertEqual(len(resp.context["posts"]), 10)

    def test_only_profile_users_posts_listed(self):
        owner = make_user("owner_p")
        other = make_user("other_p")
        self._post(owner, "OwnerThread")
        self._post(other, "OtherThread")
        resp = self.client.get(reverse("profile", args=["owner_p"]))
        self.assertContains(resp, "OwnerThread")
        self.assertNotContains(resp, "OtherThread")


# --------------------------------------------------------------------------- #
# "(you)" self-marker
# --------------------------------------------------------------------------- #
class ProfileSelfMarkerTests(TestCase):
    def test_viewing_own_profile_sets_is_self(self):
        u = make_user("me")
        self.client.force_login(u)
        resp = self.client.get(reverse("profile", args=["me"]))
        self.assertTrue(resp.context["is_self"])
        self.assertContains(resp, "(you)")

    def test_viewing_other_profile_is_not_self(self):
        viewer = make_user("viewer")
        make_user("subject")
        self.client.force_login(viewer)
        resp = self.client.get(reverse("profile", args=["subject"]))
        self.assertFalse(resp.context["is_self"])
        self.assertNotContains(resp, "(you)")

    def test_anonymous_visitor_is_not_self(self):
        make_user("subject2")
        resp = self.client.get(reverse("profile", args=["subject2"]))
        self.assertFalse(resp.context["is_self"])


# --------------------------------------------------------------------------- #
# Forum author bylines link to /u/<username>/
# --------------------------------------------------------------------------- #
class ForumBylineLinkTests(TestCase):
    """The byline on a post detail (and its comments) must link to the
    author's public profile at ``/u/<username>/``."""

    def setUp(self):
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.author = make_user("riffmaster")
        self.commenter = make_user("commentguy")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.author,
            title="Byline thread",
            body="body",
        )
        Comment.objects.create(
            post=self.post, author=self.commenter, body="nice riff"
        )

    def test_post_detail_links_post_author_byline_to_profile(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 200)
        author_url = reverse("profile", args=["riffmaster"])
        self.assertEqual(author_url, "/u/riffmaster/")
        self.assertContains(resp, f'href="{author_url}"')

    def test_post_detail_links_comment_author_byline_to_profile(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        commenter_url = reverse("profile", args=["commentguy"])
        self.assertContains(resp, f'href="{commenter_url}"')

    def test_subtopic_list_links_post_author_byline_to_profile(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.status_code, 200)
        author_url = reverse("profile", args=["riffmaster"])
        self.assertContains(resp, f'href="{author_url}"')
