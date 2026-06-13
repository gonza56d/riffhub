"""Cross-cutting security / permission tests for riffhub.

These tests do NOT re-prove each domain's internal business rules; they assert
the *gate* in front of every write endpoint and the public-readability of the
anonymous-facing GET pages:

  * Anonymous POSTs to every write endpoint are rejected (redirect-to-login for
    ``@login_required`` / explicit ``redirect("login")`` views; 403 for the
    HTMX/service-guarded endpoints and the moderator/creator ``PermissionDenied``
    views).
  * A logged-in *regular* (non-collaborator) user cannot cast a review vote nor
    open a topic/subtopic proposal (PRODUCT.md: those need Database Collaborator).
  * A regular user cannot reach the moderation dashboard nor the Creator
    topic-management UI (403).
  * A banned user (``is_active=False``) cannot authenticate via the login form
    (Django's AuthenticationForm rejects inactive accounts).
  * Sanity: the public GETs (``/``, ``/forum/``, ``/u/<name>/`` and a published
    guitar detail) are 200 for an anonymous visitor.

Conventions per the test brief: ``django.test.TestCase`` (+ rollback),
``django.test.Client`` with ``force_login`` (no CSRF, no password), seed data via
``call_command`` where useful, and ``SiteConfiguration`` thresholds set in setUp
so the derived-level machinery is exercised the way PRODUCT.md intends.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Level
from catalog.models import Brand, GuitarModel
from core.models import SiteConfiguration
from forum.models import Comment, Post, Subtopic, Topic
from forum.services import open_subtopic_proposal, open_topic_proposal

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------
def _config(collaborator_threshold=3, founder_threshold=30):
    """Set the promotion thresholds PRODUCT.md requires (no silent default).

    Without these the ``User.level`` machinery deliberately falls back to
    Regular; we set explicit values so the derived-level checks behave the way
    the spec intends rather than relying on the safety net.
    """
    config = SiteConfiguration.get_solo()
    config.collaborator_promotion_threshold = collaborator_threshold
    config.founder_threshold = founder_threshold
    config.save()
    return config


def _make_user(username, *, email_confirmed=True, **flags):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="riff-pass-123",
    )
    user.email_confirmed = email_confirmed
    for field, value in flags.items():
        setattr(user, field, value)
    user.save()
    return user


def _published_guitar(name="Test Strat"):
    brand = Brand.objects.create(name=f"Brand {name}", status="published")
    guitar = GuitarModel.objects.create(
        brand=brand,
        name=name,
        num_strings=6,
        scale_length_min_inches="25.500",
        scale_length_max_inches="25.500",
        status="published",
    )
    return guitar


def _under_revision_guitar(submitted_by, name="Proposed RG"):
    brand = Brand.objects.create(name=f"Brand {name}", status="published")
    return GuitarModel.objects.create(
        brand=brand,
        name=name,
        num_strings=7,
        scale_length_min_inches="25.500",
        scale_length_max_inches="25.500",
        submitted_by=submitted_by,
        # status defaults to UNDER_REVISION
    )


# ---------------------------------------------------------------------------
# Anonymous POSTs are rejected on every write endpoint
# ---------------------------------------------------------------------------
class AnonymousWriteEndpointsRejectedTests(TestCase):
    """Every state-changing endpoint must refuse an anonymous POST.

    "Rejected" means one of: a 302 redirect to login (the ``@login_required`` /
    ``redirect("login")`` views) or a 403 (the HTMX/service-guarded endpoints
    and the moderator/creator ``PermissionDenied`` views). Crucially, none of
    them may return a 2xx success for an anonymous user.
    """

    @classmethod
    def setUpTestData(cls):
        _config()
        # A minimal forum tree + one post/comment to point engagement URLs at.
        cls.topic = Topic.objects.create(name="Gear")
        cls.subtopic = Subtopic.objects.create(topic=cls.topic, name="Guitars")
        cls.author = _make_user("anon_target_author")
        cls.post = Post.objects.create(
            subtopic=cls.subtopic, author=cls.author, title="A post", body="Body"
        )
        cls.comment = Comment.objects.create(
            post=cls.post, author=cls.author, body="A comment"
        )
        cls.guitar = _under_revision_guitar(cls.author)
        cls.victim = _make_user("anon_victim")

    def _assert_rejected(self, response, label):
        """A reject is a redirect to login (302/-> /accounts/login) or a 403."""
        self.assertIn(
            response.status_code,
            (302, 403),
            msg=f"{label}: expected reject (302/403), got {response.status_code}",
        )
        if response.status_code == 302:
            # When it's a redirect, it must head to the login page, never deeper
            # into a protected area.
            self.assertIn(
                "login",
                response["Location"],
                msg=f"{label}: 302 should redirect to login, got {response['Location']}",
            )

    # --- forum write endpoints --------------------------------------------
    def test_forum_post_create_rejects_anonymous(self):
        url = reverse("forum:post_create", args=[self.subtopic.pk])
        resp = self.client.post(url, {"title": "x", "body": "y"})
        self._assert_rejected(resp, "forum:post_create")
        # And no post was actually created by the anonymous request.
        self.assertEqual(Post.objects.filter(title="x").count(), 0)

    def test_forum_comment_create_rejects_anonymous(self):
        url = reverse("forum:comment_create", args=[self.post.pk])
        resp = self.client.post(url, {"body": "sneaky"})
        self._assert_rejected(resp, "forum:comment_create")
        self.assertEqual(Comment.objects.filter(body="sneaky").count(), 0)

    def test_forum_vote_post_rejects_anonymous(self):
        url = reverse("forum:vote", args=["post", self.post.pk, "up"])
        resp = self.client.post(url)
        # The view short-circuits to 403 for anonymous before doing anything.
        self.assertEqual(resp.status_code, 403)

    def test_forum_vote_comment_rejects_anonymous(self):
        url = reverse("forum:vote", args=["comment", self.comment.pk, "down"])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)

    def test_forum_react_rejects_anonymous(self):
        url = reverse("forum:react", args=["post", self.post.pk])
        resp = self.client.post(url, {"emoji": "🔥"})
        self.assertEqual(resp.status_code, 403)

    def test_forum_accept_disclaimer_anonymous_creates_nothing(self):
        # accept_disclaimer is a POST view that simply no-ops for anonymous
        # (it only records acceptance when authenticated) and redirects back.
        from forum.models import MarketDisclaimerAcceptance

        url = reverse("forum:accept_disclaimer", args=[self.subtopic.pk])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(MarketDisclaimerAcceptance.objects.count(), 0)

    # --- catalog submit endpoints -----------------------------------------
    def test_catalog_submit_index_rejects_anonymous(self):
        resp = self.client.get(reverse("catalog:submit_index"))
        self._assert_rejected(resp, "catalog:submit_index")

    def test_catalog_submit_entry_rejects_anonymous(self):
        url = reverse("catalog:submit_entry", args=["guitar"])
        resp = self.client.post(url, {"name": "Hack RG"})
        self._assert_rejected(resp, "catalog:submit_entry")
        self.assertEqual(GuitarModel.objects.filter(name="Hack RG").count(), 0)

    # --- catalog review endpoints -----------------------------------------
    def test_catalog_review_queue_rejects_anonymous(self):
        resp = self.client.get(reverse("catalog:review_queue"))
        self._assert_rejected(resp, "catalog:review_queue")

    def test_catalog_review_detail_rejects_anonymous(self):
        url = reverse("catalog:review_detail", args=["guitar", self.guitar.pk])
        resp = self.client.get(url)
        self._assert_rejected(resp, "catalog:review_detail")

    def test_catalog_review_vote_rejects_anonymous(self):
        url = reverse("catalog:review_vote", args=["guitar", self.guitar.pk, "up"])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)

    def test_catalog_review_correct_rejects_anonymous(self):
        url = reverse("catalog:review_correct", args=["guitar", self.guitar.pk])
        resp = self.client.post(url, {"body": "fix this"})
        self.assertEqual(resp.status_code, 403)

    # --- moderation endpoints (all of them) -------------------------------
    def test_moderation_dashboard_rejects_anonymous(self):
        resp = self.client.get(reverse("moderation:dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_moderation_warn_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:warn_user", args=[self.victim.pk]), {"reason": "x"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_moderation_silence_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:silence_user", args=[self.victim.pk]), {"reason": "x"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_moderation_ban_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:ban_user", args=[self.victim.pk]), {"reason": "x"}
        )
        self.assertEqual(resp.status_code, 403)
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.is_active, "anon ban must not deactivate anyone")

    def test_moderation_lift_ban_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:lift_ban_user", args=[self.victim.pk])
        )
        self.assertEqual(resp.status_code, 403)

    def test_moderation_move_post_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:move_post", args=[self.post.pk]),
            {"subtopic": self.subtopic.pk},
        )
        self.assertEqual(resp.status_code, 403)

    def test_moderation_remove_content_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:remove_content", args=["post", self.post.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_removed)

    def test_moderation_restore_content_rejects_anonymous(self):
        resp = self.client.post(
            reverse("moderation:restore_content", args=["post", self.post.pk])
        )
        self.assertEqual(resp.status_code, 403)

    # --- creator-managed topic/subtopic endpoints -------------------------
    def test_manage_topics_rejects_anonymous(self):
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)

    def test_topic_create_rejects_anonymous(self):
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "Sneaky Topic"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Topic.objects.filter(name="Sneaky Topic").exists())

    def test_topic_edit_rejects_anonymous(self):
        resp = self.client.post(
            reverse("forum:topic_edit", args=[self.topic.pk]), {"name": "Renamed"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_topic_delete_rejects_anonymous(self):
        resp = self.client.post(reverse("forum:topic_delete", args=[self.topic.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_subtopic_create_rejects_anonymous(self):
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "Sneaky Sub"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Subtopic.objects.filter(name="Sneaky Sub").exists())

    def test_subtopic_edit_rejects_anonymous(self):
        resp = self.client.post(
            reverse("forum:subtopic_edit", args=[self.subtopic.pk]), {"name": "X"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_subtopic_delete_rejects_anonymous(self):
        resp = self.client.post(
            reverse("forum:subtopic_delete", args=[self.subtopic.pk])
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Subtopic.objects.filter(pk=self.subtopic.pk).exists())


# ---------------------------------------------------------------------------
# A regular (non-collaborator) user is denied collaborator-only actions
# ---------------------------------------------------------------------------
class RegularUserCollaboratorGateTests(TestCase):
    """A logged-in Regular user can vote on posts/comments and submit gear, but
    PRODUCT.md withholds two things until they reach Database Collaborator:
    voting on collab-db submissions and *proposing* a topic/subtopic.
    """

    def setUp(self):
        # Threshold deliberately high so the user stays Regular even after a
        # couple of accepted submissions; here they have none anyway.
        _config(collaborator_threshold=3)
        self.regular = _make_user("regular_joe")
        self.submitter = _make_user("submitter_jane")
        self.guitar = _under_revision_guitar(self.submitter)
        self.client.force_login(self.regular)

    def test_regular_user_is_only_regular_level(self):
        # Guards the premise of the rest of this class.
        self.regular.refresh_from_db()
        self.assertEqual(self.regular.level, Level.REGULAR)
        self.assertFalse(self.regular.is_at_least(Level.COLLABORATOR))

    def test_regular_user_cannot_cast_review_vote_via_view(self):
        url = reverse("catalog:review_vote", args=["guitar", self.guitar.pk, "up"])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)
        # No vote should have been recorded.
        from catalog.models import ReviewVote

        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_regular_user_cannot_file_correction_via_view(self):
        url = reverse("catalog:review_correct", args=["guitar", self.guitar.pk])
        resp = self.client.post(url, {"body": "I think this is wrong"})
        self.assertEqual(resp.status_code, 403)
        from catalog.models import Correction

        self.assertEqual(Correction.objects.count(), 0)

    def test_regular_user_cannot_cast_review_vote_via_service(self):
        # The service is the single source of truth for the rule.
        from catalog.constants import VoteValue
        from catalog.services import cast_review_vote

        with self.assertRaises(PermissionError):
            cast_review_vote(self.regular, self.guitar, VoteValue.UP)

    def test_regular_user_cannot_open_topic_proposal(self):
        with self.assertRaises(PermissionDenied):
            open_topic_proposal(self.regular, name="Synths")

    def test_regular_user_cannot_open_subtopic_proposal(self):
        parent = Topic.objects.create(name="Gear")
        with self.assertRaises(PermissionDenied):
            open_subtopic_proposal(self.regular, parent_topic=parent, name="Pedals")

    def test_regular_user_review_queue_get_is_allowed_but_marked_readonly(self):
        # Transparency: a logged-in non-collaborator may *read* the queue, but
        # the context flags them as unable to act.
        resp = self.client.get(reverse("catalog:review_queue"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_review"])


class CollaboratorCanReviewVoteTests(TestCase):
    """Positive control: once a user actually reaches Collaborator (accepted
    submissions >= threshold) the same review-vote endpoint is allowed. This
    makes the negative tests above meaningful rather than always-403.
    """

    def setUp(self):
        _config(collaborator_threshold=3)
        self.collaborator = _make_user(
            "collab_carl", accepted_submissions_count=3
        )
        self.submitter = _make_user("submitter_sam")
        self.guitar = _under_revision_guitar(self.submitter)
        self.client.force_login(self.collaborator)

    def test_collaborator_level_is_at_least_collaborator(self):
        self.assertTrue(self.collaborator.is_at_least(Level.COLLABORATOR))
        self.assertEqual(self.collaborator.level, Level.COLLABORATOR)

    def test_collaborator_can_cast_review_vote(self):
        url = reverse(
            "catalog:review_vote", args=["guitar", self.guitar.pk, "up"]
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        from catalog.models import ReviewVote

        self.assertEqual(ReviewVote.net_votes(self.guitar), 1)


# ---------------------------------------------------------------------------
# A regular user cannot reach moderation or creator-management areas
# ---------------------------------------------------------------------------
class RegularUserCannotReachPrivilegedAreasTests(TestCase):
    """A plain authenticated user must be 403'd from the moderator dashboard and
    every Creator-only topic-management endpoint (not merely the anonymous case).
    """

    def setUp(self):
        _config()
        self.regular = _make_user("regular_rita")
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.victim = _make_user("priv_victim")
        self.client.force_login(self.regular)

    def test_regular_cannot_open_moderation_dashboard(self):
        resp = self.client.get(reverse("moderation:dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_regular_cannot_ban_a_user(self):
        resp = self.client.post(
            reverse("moderation:ban_user", args=[self.victim.pk]), {"reason": "x"}
        )
        self.assertEqual(resp.status_code, 403)
        self.victim.refresh_from_db()
        self.assertTrue(self.victim.is_active)

    def test_regular_cannot_warn_a_user(self):
        resp = self.client.post(
            reverse("moderation:warn_user", args=[self.victim.pk]), {"reason": "x"}
        )
        self.assertEqual(resp.status_code, 403)
        from moderation.models import Warning

        self.assertEqual(Warning.objects.count(), 0)

    def test_regular_cannot_remove_content(self):
        post = Post.objects.create(
            subtopic=self.subtopic, author=self.victim, title="T", body="B"
        )
        resp = self.client.post(
            reverse("moderation:remove_content", args=["post", post.pk])
        )
        self.assertEqual(resp.status_code, 403)
        post.refresh_from_db()
        self.assertFalse(post.is_removed)

    def test_regular_cannot_open_manage_topics(self):
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)

    def test_regular_cannot_create_topic(self):
        resp = self.client.post(
            reverse("forum:topic_create"), {"name": "Regular Made This"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Topic.objects.filter(name="Regular Made This").exists())

    def test_regular_cannot_delete_topic(self):
        resp = self.client.post(reverse("forum:topic_delete", args=[self.topic.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_regular_cannot_create_subtopic(self):
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": "Regular Sub"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Subtopic.objects.filter(name="Regular Sub").exists())


class FounderAndModeratorBoundaryTests(TestCase):
    """The moderation dashboard and the Creator manage-UI sit at *different*
    levels (MODERATOR vs CREATOR). A Founder (level 30) is below both, so it is
    a sharp boundary case worth pinning: a Founder can reach neither.

    A Moderator can reach the dashboard but NOT the Creator-only manage-UI.
    """

    def setUp(self):
        _config(collaborator_threshold=3, founder_threshold=30)
        self.founder = _make_user("founder_fred", is_founder=True)
        self.moderator = _make_user("mod_mary", is_community_moderator=True)
        self.topic = Topic.objects.create(name="Gear")

    def test_founder_is_below_moderator(self):
        self.assertEqual(self.founder.level, Level.FOUNDER)
        self.assertFalse(self.founder.is_at_least(Level.MODERATOR))
        self.assertFalse(self.founder.is_at_least(Level.CREATOR))

    def test_founder_cannot_open_moderation_dashboard(self):
        self.client.force_login(self.founder)
        resp = self.client.get(reverse("moderation:dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_founder_cannot_open_manage_topics(self):
        self.client.force_login(self.founder)
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)

    def test_moderator_can_open_dashboard(self):
        self.client.force_login(self.moderator)
        resp = self.client.get(reverse("moderation:dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_moderator_cannot_open_manage_topics(self):
        # manage_topics requires CREATOR; a MODERATOR must still be denied.
        self.client.force_login(self.moderator)
        resp = self.client.get(reverse("forum:manage_topics"))
        self.assertEqual(resp.status_code, 403)

    def test_creator_can_open_both(self):
        creator = _make_user("creator_cleo", is_riffhub_creator=True)
        self.client.force_login(creator)
        self.assertEqual(
            self.client.get(reverse("moderation:dashboard")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("forum:manage_topics")).status_code, 200
        )


# ---------------------------------------------------------------------------
# A banned (inactive) user cannot authenticate via the login form
# ---------------------------------------------------------------------------
class BannedUserCannotLoginTests(TestCase):
    """Banning deactivates the account (``is_active=False``). Django's
    ``AuthenticationForm`` rejects inactive accounts, so the login form must not
    establish a session for a banned user — even with correct credentials.
    """

    def setUp(self):
        _config()
        self.password = "right-password-123"
        self.user = User.objects.create_user(
            username="banned_bart",
            email="banned_bart@example.com",
            password=self.password,
        )

    def test_active_user_can_login_baseline(self):
        # Sanity baseline so the negative case below is meaningful.
        ok = self.client.login(username="banned_bart", password=self.password)
        self.assertTrue(ok)

    def test_banned_user_login_form_post_does_not_authenticate(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        resp = self.client.post(
            reverse("login"),
            {"username": "banned_bart", "password": self.password},
        )
        # Form re-renders with errors (200) rather than redirecting on success;
        # and the user is NOT logged in.
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["user"].is_authenticated)
        self.assertTrue(resp.context["form"].errors)

    def test_banned_user_client_login_helper_fails(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        ok = self.client.login(username="banned_bart", password=self.password)
        self.assertFalse(ok)

    def test_banned_user_via_ban_service_cannot_login(self):
        # Exercise the real ban path (sets is_active=False) then confirm login
        # is blocked end-to-end.
        from moderation import services

        moderator = _make_user("ban_issuer_mod", is_community_moderator=True)
        services.ban(moderator, self.user, "off-topic spam")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        ok = self.client.login(username="banned_bart", password=self.password)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Public GETs are 200 for anonymous visitors
# ---------------------------------------------------------------------------
class PublicGetPagesAreReadableTests(TestCase):
    """PRODUCT.md: anonymous users can *see* the content. The catalog browse
    landing, the forum index, a public profile and a published guitar's detail
    page must all render 200 without authentication.
    """

    @classmethod
    def setUpTestData(cls):
        _config()
        # Full reference data so the browse + forum index render realistically.
        call_command("seed_catalog")
        call_command("seed_forum")
        cls.user = _make_user("public_pete")
        cls.guitar = GuitarModel.objects.published().first()

    def test_catalog_browse_root_is_public(self):
        resp = self.client.get(reverse("catalog:browse"))
        self.assertEqual(resp.status_code, 200)

    def test_forum_index_is_public(self):
        resp = self.client.get(reverse("forum:index"))
        self.assertEqual(resp.status_code, 200)

    def test_public_profile_is_public(self):
        resp = self.client.get(reverse("profile", args=[self.user.username]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["profile_user"], self.user)

    def test_published_guitar_detail_is_public(self):
        self.assertIsNotNone(
            self.guitar, "seed_catalog should publish at least one guitar"
        )
        resp = self.client.get(reverse("catalog:detail", args=[self.guitar.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_subtopic_read_page_is_public(self):
        subtopic = Subtopic.objects.first()
        self.assertIsNotNone(subtopic, "seed_forum should create subtopics")
        resp = self.client.get(reverse("forum:subtopic", args=[subtopic.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_under_revision_guitar_detail_is_404_for_public(self):
        # Counterpart to the published case: unpublished entries stay hidden in
        # the public catalog (only the collab/review section surfaces them).
        hidden = _under_revision_guitar(self.user, name="Hidden Proto")
        resp = self.client.get(reverse("catalog:detail", args=[hidden.pk]))
        self.assertEqual(resp.status_code, 404)
