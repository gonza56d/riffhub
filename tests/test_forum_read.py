"""Tests for the forum *read* views (index, subtopic listing, post detail).

Scope (per the assignment):
  * ``/forum/``            — topics + their subtopics ordered by -activity_count
  * ``/forum/s/<pk>/``     — lists non-removed posts of a subtopic
  * ``/forum/post/<pk>/``  — renders a post and its comments
  * removed posts          — 404 for anon/regular, visible to moderators
  * removed comments       — hidden for non-mods, shown (badged) to mods
  * ``seed_forum``         — creates the predefined topics/subtopics

These are HTTP-level tests using ``django.test.Client``; the read views simply
resolve targets, enforce visibility (removed-content hiding), and choose the
template. Engagement/mutation endpoints are covered by sibling test modules.
"""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from forum import services
from forum.constants import (
    GEAR_MARKET_SUBTOPICS,
    GEAR_MARKET_TOPIC_NAME,
    PREDEFINED_TOPICS,
    VoteValue,
)
from forum.models import Comment, Post, Subtopic, Topic

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_user(username, **flags):
    """Create a confirmed user; pass role flags (is_community_moderator, …)."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "pw-12345",
        "email_confirmed": True,
    }
    defaults.update(flags)
    return User.objects.create_user(username=username, **defaults)


def make_topic(name="Gear", **kwargs):
    return Topic.objects.create(name=name, **kwargs)


def make_subtopic(topic, name="Guitars", **kwargs):
    return Subtopic.objects.create(topic=topic, name=name, **kwargs)


def make_post(subtopic, author, title="A post", body="body text", **kwargs):
    return Post.objects.create(
        subtopic=subtopic, author=author, title=title, body=body, **kwargs
    )


def make_comment(post, author, body="a comment", **kwargs):
    return Comment.objects.create(post=post, author=author, body=body, **kwargs)


# ---------------------------------------------------------------------------
# /forum/ — the index page
# ---------------------------------------------------------------------------
class ForumIndexTests(TestCase):
    """The forum landing page lists topics (and their subtopics)."""

    def setUp(self):
        self.author = make_user("indexauthor")

    def test_index_returns_200_and_uses_index_template(self):
        resp = self.client.get(reverse("forum:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/index.html")

    def test_index_lists_topic_names(self):
        make_topic("Gear")
        make_topic("Events")
        resp = self.client.get(reverse("forum:index"))
        self.assertContains(resp, "Gear")
        self.assertContains(resp, "Events")

    def test_index_lists_subtopic_names_and_links(self):
        topic = make_topic("Gear")
        sub = make_subtopic(topic, "Guitars")
        resp = self.client.get(reverse("forum:index"))
        self.assertContains(resp, "Guitars")
        # The subtopic name renders as a link to its detail page.
        self.assertContains(resp, reverse("forum:subtopic", args=[sub.pk]))

    def test_index_topics_ordered_by_activity_count_desc(self):
        """PRODUCT.md: topics are sorted by activity (busiest first)."""
        quiet = make_topic("Quiet", activity_count=1)
        busy = make_topic("Busy", activity_count=99)
        mid = make_topic("Mid", activity_count=50)
        resp = self.client.get(reverse("forum:index"))
        topics = list(resp.context["topics"])
        self.assertEqual(topics, [busy, mid, quiet])

    def test_index_topic_tie_break_is_name(self):
        """Equal activity -> deterministic alphabetical tie-break (Meta ordering)."""
        b = make_topic("Bravo", activity_count=5)
        a = make_topic("Alpha", activity_count=5)
        resp = self.client.get(reverse("forum:index"))
        topics = list(resp.context["topics"])
        self.assertEqual(topics, [a, b])

    def test_index_subtopics_ordered_by_activity_count_desc_in_html(self):
        """Subtopics under a topic render busiest-first (Subtopic Meta ordering)."""
        topic = make_topic("Gear")
        make_subtopic(topic, "Quiet", activity_count=1)
        make_subtopic(topic, "Busy", activity_count=42)
        resp = self.client.get(reverse("forum:index"))
        html = resp.content.decode()
        self.assertLess(html.index("Busy"), html.index("Quiet"))

    def test_index_shows_activity_count_value(self):
        topic = make_topic("Gear")
        make_subtopic(topic, "Guitars", activity_count=7)
        resp = self.client.get(reverse("forum:index"))
        self.assertContains(resp, "7 interactions")

    def test_index_empty_when_no_topics(self):
        resp = self.client.get(reverse("forum:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["topics"]), [])

    def test_index_market_topic_tagged(self):
        make_topic(GEAR_MARKET_TOPIC_NAME, is_market=True)
        resp = self.client.get(reverse("forum:index"))
        # The template tags a market topic with a "Market" badge.
        self.assertContains(resp, "Market")

    def test_index_topic_with_no_subtopics_shows_placeholder(self):
        make_topic("Lonely")
        resp = self.client.get(reverse("forum:index"))
        self.assertContains(resp, "No subtopics yet.")

    def test_index_accessible_anonymously(self):
        make_topic("Gear")
        # No login at all — anonymous users can read the forum (PRODUCT.md).
        resp = self.client.get(reverse("forum:index"))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# /forum/s/<pk>/ — subtopic detail (post listing)
# ---------------------------------------------------------------------------
class SubtopicDetailTests(TestCase):
    """A subtopic page lists its (non-removed) posts."""

    def setUp(self):
        self.author = make_user("subauthor")
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")

    def test_subtopic_detail_200_and_template(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/subtopic.html")

    def test_subtopic_detail_shows_topic_and_subtopic_names(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, "Gear")
        self.assertContains(resp, "Guitars")

    def test_subtopic_detail_context_has_subtopic_and_topic(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.context["subtopic"], self.subtopic)
        self.assertEqual(resp.context["topic"], self.topic)

    def test_subtopic_detail_lists_posts(self):
        make_post(self.subtopic, self.author, title="Strat vs Les Paul")
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, "Strat vs Les Paul")

    def test_subtopic_detail_post_links_to_detail(self):
        post = make_post(self.subtopic, self.author)
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, reverse("forum:post", args=[post.pk]))

    def test_subtopic_detail_hides_removed_posts(self):
        visible = make_post(self.subtopic, self.author, title="Visible thread")
        removed = make_post(self.subtopic, self.author, title="Removed thread")
        removed.mark_removed(by=self.author, reason="off-topic")
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, "Visible thread")
        self.assertNotContains(resp, "Removed thread")
        posts = list(resp.context["posts"])
        self.assertIn(visible, posts)
        self.assertNotIn(removed, posts)

    def test_subtopic_detail_hides_removed_posts_even_for_moderator(self):
        """The *listing* always hides removed posts; mods still reach them by URL.

        ``subtopic_detail`` unconditionally filters ``is_removed=False`` (unlike
        ``post_detail``, which lets moderators view a removed post directly)."""
        mod = make_user("listmod", is_community_moderator=True)
        make_post(self.subtopic, self.author, title="Shown thread")
        removed = make_post(self.subtopic, self.author, title="Gone thread")
        removed.mark_removed(by=mod, reason="spam")
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, "Shown thread")
        self.assertNotContains(resp, "Gone thread")

    def test_subtopic_detail_empty_listing_message(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, "No posts yet")

    def test_subtopic_detail_comment_count_annotation(self):
        post = make_post(self.subtopic, self.author)
        make_comment(post, self.author)
        make_comment(post, self.author, body="second")
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        annotated = resp.context["posts"][0]
        self.assertEqual(annotated.num_comments, 2)

    def test_subtopic_detail_unknown_pk_404(self):
        resp = self.client.get(reverse("forum:subtopic", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_subtopic_detail_non_market_flag_false(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertFalse(resp.context["is_market"])

    def test_subtopic_detail_market_flag_true(self):
        market_topic = make_topic(GEAR_MARKET_TOPIC_NAME, is_market=True)
        market_sub = make_subtopic(market_topic, "Guitars")
        resp = self.client.get(reverse("forum:subtopic", args=[market_sub.pk]))
        self.assertTrue(resp.context["is_market"])

    def test_subtopic_detail_disclaimer_not_ok_for_anonymous(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        # Anonymous: has_accepted_market_disclaimer short-circuits to False.
        self.assertFalse(resp.context["disclaimer_ok"])

    def test_subtopic_detail_anonymous_sees_signin_hint(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertContains(resp, "Sign in")


# ---------------------------------------------------------------------------
# /forum/s/<pk>/ — post ordering (recency first, then activity, then -pk)
# ---------------------------------------------------------------------------
class SubtopicPostOrderingTests(TestCase):
    """Posts on a subtopic page sort by ``-updated_at``, then activity desc, ``-pk``.

    "Activity" of a post = number of (comments + votes + reactions) it has
    received, computed as a query annotation in ``subtopic_detail`` (no DB
    field, no migration). These tests assert the order via ``posts`` context pks.

    ``updated_at`` is ``auto_now`` so it can't be passed to ``create``; we pin
    distinct, timezone-aware values with a bulk ``.update`` (which doesn't touch
    ``auto_now``) after the rows exist.
    """

    def setUp(self):
        self.author = make_user("orderauthor")
        # Distinct actors so nobody self-votes/reacts (the services forbid it).
        self.actor1 = make_user("orderactor1")
        self.actor2 = make_user("orderactor2")
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")

    def _pin_updated(self, post, when):
        """Pin ``post.updated_at`` (an ``auto_now`` field) to an aware datetime."""
        Post.objects.filter(pk=post.pk).update(updated_at=when)

    def _posts(self):
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        self.assertEqual(resp.status_code, 200)
        return list(resp.context["posts"])

    def test_more_recently_updated_post_lists_first(self):
        older = make_post(self.subtopic, self.author, title="Older")
        newer = make_post(self.subtopic, self.author, title="Newer")
        # Pin updated_at so recency is unambiguous regardless of creation order.
        self._pin_updated(older, datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
        self._pin_updated(newer, datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
        self.assertEqual(
            [p.pk for p in self._posts()], [newer.pk, older.pk]
        )

    def test_recency_wins_over_activity(self):
        """A busier-but-older post still sorts below a fresher quiet one."""
        busy_old = make_post(self.subtopic, self.author, title="Busy old")
        quiet_new = make_post(self.subtopic, self.author, title="Quiet new")
        # Give the older post real activity; the newer one none.
        make_comment(busy_old, self.actor1)
        services.cast_vote(self.actor1, busy_old, VoteValue.UP)
        services.toggle_reaction(self.actor2, busy_old, "🔥")
        self._pin_updated(busy_old, datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
        self._pin_updated(quiet_new, datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
        self.assertEqual(
            [p.pk for p in self._posts()], [quiet_new.pk, busy_old.pk]
        )

    def test_equal_updated_at_orders_by_activity_desc(self):
        """Same updated_at -> the post with more comments/votes/reactions first."""
        same = datetime(2022, 6, 1, tzinfo=dt_timezone.utc)
        quiet = make_post(self.subtopic, self.author, title="Quiet")
        busy = make_post(self.subtopic, self.author, title="Busy")
        # busy gets 1 comment + 1 vote + 1 reaction = activity 3; quiet stays 0.
        make_comment(busy, self.actor1, body="nice")
        services.cast_vote(self.actor1, busy, VoteValue.UP)
        services.toggle_reaction(self.actor2, busy, "🤘")
        self._pin_updated(quiet, same)
        self._pin_updated(busy, same)
        ordered = self._posts()
        self.assertEqual([p.pk for p in ordered], [busy.pk, quiet.pk])
        # The annotation also exposes the component counts.
        by_pk = {p.pk: p for p in ordered}
        self.assertEqual(by_pk[busy.pk].activity, 3)
        self.assertEqual(by_pk[busy.pk].num_comments, 1)
        self.assertEqual(by_pk[busy.pk].num_votes, 1)
        self.assertEqual(by_pk[busy.pk].num_reactions, 1)
        self.assertEqual(by_pk[quiet.pk].activity, 0)

    def test_votes_alone_count_as_activity(self):
        """Activity counts votes even with no comments — no JOIN multiplication."""
        same = datetime(2022, 6, 1, tzinfo=dt_timezone.utc)
        quiet = make_post(self.subtopic, self.author, title="Quiet")
        voted = make_post(self.subtopic, self.author, title="Voted")
        services.cast_vote(self.actor1, voted, VoteValue.UP)
        services.cast_vote(self.actor2, voted, VoteValue.DOWN)
        self._pin_updated(quiet, same)
        self._pin_updated(voted, same)
        ordered = self._posts()
        self.assertEqual([p.pk for p in ordered], [voted.pk, quiet.pk])
        by_pk = {p.pk: p for p in ordered}
        self.assertEqual(by_pk[voted.pk].num_votes, 2)
        self.assertEqual(by_pk[voted.pk].activity, 2)

    def test_full_tie_breaks_by_pk_desc(self):
        """Equal updated_at and equal (zero) activity -> newest pk first."""
        same = datetime(2022, 6, 1, tzinfo=dt_timezone.utc)
        first = make_post(self.subtopic, self.author, title="First")
        second = make_post(self.subtopic, self.author, title="Second")
        self._pin_updated(first, same)
        self._pin_updated(second, same)
        # Both have zero activity and the same updated_at: -pk decides.
        self.assertEqual(
            [p.pk for p in self._posts()], [second.pk, first.pk]
        )


# ---------------------------------------------------------------------------
# /forum/post/<pk>/ — post detail (renders post + comments)
# ---------------------------------------------------------------------------
class PostDetailTests(TestCase):
    """The post page renders the post body and its comments."""

    def setUp(self):
        self.author = make_user("postauthor")
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")
        self.post = make_post(
            self.subtopic, self.author, title="My thread", body="Hello forum"
        )

    def test_post_detail_200_and_template(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/post_detail.html")

    def test_post_detail_renders_title_and_body(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertContains(resp, "My thread")
        self.assertContains(resp, "Hello forum")

    def test_post_detail_context_carries_post_subtopic_topic(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.context["post"], self.post)
        self.assertEqual(resp.context["subtopic"], self.subtopic)
        self.assertEqual(resp.context["topic"], self.topic)

    def test_post_detail_renders_comments(self):
        make_comment(self.post, self.author, body="first reply")
        make_comment(self.post, self.author, body="second reply")
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertContains(resp, "first reply")
        self.assertContains(resp, "second reply")
        self.assertEqual(len(resp.context["comment_rows"]), 2)

    def test_post_detail_comment_count_header(self):
        make_comment(self.post, self.author)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # The template renders "<n> comment(s)" from comment_rows length.
        self.assertContains(resp, "1 comment")

    def test_post_detail_no_comments_shows_zero(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.context["comment_rows"], [])
        self.assertContains(resp, "0 comment")

    def test_post_detail_unknown_pk_404(self):
        resp = self.client.get(reverse("forum:post", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_post_detail_video_link_rendered(self):
        self.post.video_url = "https://youtube.com/watch?v=abc"
        self.post.save(update_fields=["video_url"])
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertContains(resp, "https://youtube.com/watch?v=abc")

    def test_post_detail_vote_and_react_context_present(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertIn("post_vote", resp.context)
        self.assertIn("post_react", resp.context)
        self.assertEqual(resp.context["post_vote"]["tally"], {"up": 0, "down": 0})

    def test_post_detail_anonymous_cannot_vote_in_context(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # Anonymous users can read but not vote (PRODUCT.md): can_vote False.
        self.assertFalse(resp.context["post_vote"]["can_vote"])

    def test_post_detail_author_cannot_vote_own_post(self):
        self.client.force_login(self.author)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # No self-voting (PRODUCT.md): the author's own post is not votable.
        self.assertFalse(resp.context["post_vote"]["can_vote"])

    def test_post_detail_other_user_can_vote(self):
        other = make_user("voter")
        self.client.force_login(other)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertTrue(resp.context["post_vote"]["can_vote"])

    def test_post_detail_anonymous_sees_signin_to_comment(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertContains(resp, "Sign in")

    def test_post_detail_market_price_shown(self):
        market_topic = make_topic(GEAR_MARKET_TOPIC_NAME, is_market=True)
        market_sub = make_subtopic(market_topic, "Guitars")
        listing = make_post(
            market_sub, self.author, title="Selling a Strat", price="1200.00", currency="USD"
        )
        resp = self.client.get(reverse("forum:post", args=[listing.pk]))
        self.assertTrue(resp.context["is_market"])
        self.assertContains(resp, "1200.00")
        self.assertContains(resp, "USD")


# ---------------------------------------------------------------------------
# Removed POSTS — visibility by role
# ---------------------------------------------------------------------------
class RemovedPostVisibilityTests(TestCase):
    """A soft-removed post is a 404 for anon/regular users, visible to mods."""

    def setUp(self):
        self.author = make_user("rpauthor")
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")
        self.post = make_post(
            self.subtopic, self.author, title="Removed post title", body="gone"
        )
        self.post.mark_removed(by=self.author, reason="off-topic football stuff")

    def test_removed_post_404_for_anonymous(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_404_for_regular_user(self):
        regular = make_user("regular")
        self.client.force_login(regular)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_404_for_collaborator(self):
        """Collaborators are below MODERATOR, so they also get a 404."""
        from core.models import SiteConfiguration

        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()
        collab = make_user("collab", accepted_submissions_count=5)
        self.client.force_login(collab)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_404_for_founder(self):
        """Founder (level 30) is still below MODERATOR (level 40)."""
        founder = make_user("founder", is_founder=True)
        self.client.force_login(founder)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_removed_post_visible_to_moderator(self):
        mod = make_user("mod", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Removed post title")

    def test_removed_post_visible_to_creator(self):
        creator = make_user("creator", is_riffhub_creator=True)
        self.client.force_login(creator)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Removed post title")

    def test_removed_post_shows_removal_reason_to_moderator(self):
        mod = make_user("mod2", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # The mod panel surfaces the removal reason and a Restore control.
        self.assertContains(resp, "off-topic football stuff")
        self.assertContains(resp, "Restore post")

    def test_non_removed_post_visible_to_everyone(self):
        live = make_post(self.subtopic, self.author, title="Live post")
        resp = self.client.get(reverse("forum:post", args=[live.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Live post")


# ---------------------------------------------------------------------------
# Removed COMMENTS — hidden for non-mods, shown (badged) to mods
# ---------------------------------------------------------------------------
class RemovedCommentVisibilityTests(TestCase):
    """Removed comments are dropped from the list for non-mods, badged for mods."""

    def setUp(self):
        self.author = make_user("rcauthor")
        self.commenter = make_user("commenter")
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")
        self.post = make_post(self.subtopic, self.author, title="Discussion")
        self.visible_comment = make_comment(
            self.post, self.commenter, body="totally fine comment"
        )
        self.removed_comment = make_comment(
            self.post, self.commenter, body="removed comment body"
        )
        self.removed_comment.mark_removed(by=self.author, reason="unrelated")

    def test_removed_comment_hidden_from_anonymous(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertContains(resp, "totally fine comment")
        self.assertNotContains(resp, "removed comment body")
        self.assertEqual(len(resp.context["comment_rows"]), 1)

    def test_removed_comment_hidden_from_regular_user(self):
        regular = make_user("plainuser")
        self.client.force_login(regular)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertNotContains(resp, "removed comment body")
        self.assertEqual(len(resp.context["comment_rows"]), 1)

    def test_removed_comment_count_header_excludes_removed_for_nonmod(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # Only the one visible comment is counted for non-moderators.
        self.assertContains(resp, "1 comment")

    def test_removed_comment_shown_to_moderator(self):
        mod = make_user("cmod", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # Mod sees both comments...
        self.assertContains(resp, "totally fine comment")
        self.assertContains(resp, "removed comment body")
        self.assertEqual(len(resp.context["comment_rows"]), 2)

    def test_removed_comment_badged_for_moderator(self):
        mod = make_user("cmod2", is_community_moderator=True)
        self.client.force_login(mod)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # ...and the removed one carries the "[removed by moderator]" badge.
        self.assertContains(resp, "[removed by moderator]")

    def test_removed_comment_shown_to_creator(self):
        creator = make_user("ccreator", is_riffhub_creator=True)
        self.client.force_login(creator)
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        self.assertEqual(len(resp.context["comment_rows"]), 2)

    def test_no_removed_badge_for_anonymous(self):
        resp = self.client.get(reverse("forum:post", args=[self.post.pk]))
        # The removed comment isn't in the list at all, so no badge appears.
        self.assertNotContains(resp, "[removed by moderator]")

    def test_all_comments_visible_when_none_removed(self):
        post2 = make_post(self.subtopic, self.author, title="Clean thread")
        make_comment(post2, self.commenter, body="one")
        make_comment(post2, self.commenter, body="two")
        resp = self.client.get(reverse("forum:post", args=[post2.pk]))
        self.assertEqual(len(resp.context["comment_rows"]), 2)


# ---------------------------------------------------------------------------
# seed_forum — creates the predefined topics/subtopics
# ---------------------------------------------------------------------------
class SeedForumTests(TestCase):
    """``manage.py seed_forum`` materialises the PRODUCT.md predefined forum."""

    def test_seed_forum_creates_predefined_topics(self):
        call_command("seed_forum")
        names = set(Topic.objects.values_list("name", flat=True))
        expected = {name for name, _ in PREDEFINED_TOPICS} | {GEAR_MARKET_TOPIC_NAME}
        self.assertEqual(names, expected)

    def test_seed_forum_creates_four_topics(self):
        call_command("seed_forum")
        # Gear, State Of Art, Events, Gear Market.
        self.assertEqual(Topic.objects.count(), 4)

    def test_seed_forum_marks_predefined_topics(self):
        call_command("seed_forum")
        self.assertEqual(Topic.objects.filter(is_predefined=True).count(), 4)

    def test_seed_forum_gear_market_flags(self):
        call_command("seed_forum")
        market = Topic.objects.get(name=GEAR_MARKET_TOPIC_NAME)
        self.assertTrue(market.is_market)
        self.assertTrue(market.requires_disclaimer)
        self.assertTrue(market.is_predefined)

    def test_seed_forum_non_market_topics_are_not_market(self):
        call_command("seed_forum")
        for name, _ in PREDEFINED_TOPICS:
            topic = Topic.objects.get(name=name)
            self.assertFalse(topic.is_market, f"{name} should not be a market topic")

    def test_seed_forum_creates_expected_subtopics_per_topic(self):
        call_command("seed_forum")
        for topic_name, subtopic_names in PREDEFINED_TOPICS:
            topic = Topic.objects.get(name=topic_name)
            got = set(topic.subtopics.values_list("name", flat=True))
            self.assertEqual(got, set(subtopic_names))

    def test_seed_forum_gear_market_subtopics(self):
        call_command("seed_forum")
        market = Topic.objects.get(name=GEAR_MARKET_TOPIC_NAME)
        got = set(market.subtopics.values_list("name", flat=True))
        self.assertEqual(got, set(GEAR_MARKET_SUBTOPICS))

    def test_seed_forum_total_subtopic_count(self):
        call_command("seed_forum")
        # Gear 5 + State Of Art 4 + Events 4 + Gear Market 5 = 18.
        expected = sum(len(subs) for _, subs in PREDEFINED_TOPICS) + len(
            GEAR_MARKET_SUBTOPICS
        )
        self.assertEqual(Subtopic.objects.count(), expected)
        self.assertEqual(expected, 18)

    def test_seed_forum_is_idempotent(self):
        call_command("seed_forum")
        topics_after_first = Topic.objects.count()
        subtopics_after_first = Subtopic.objects.count()
        call_command("seed_forum")
        self.assertEqual(Topic.objects.count(), topics_after_first)
        self.assertEqual(Subtopic.objects.count(), subtopics_after_first)

    def test_seed_forum_topics_browsable_via_index(self):
        """Seeded topics actually render on the public index page."""
        call_command("seed_forum")
        resp = self.client.get(reverse("forum:index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Gear")
        self.assertContains(resp, "State Of Art")
        self.assertContains(resp, "Events")
        self.assertContains(resp, GEAR_MARKET_TOPIC_NAME)

    def test_seed_forum_subtopic_pages_load(self):
        """Every seeded subtopic resolves to a working detail page."""
        call_command("seed_forum")
        for sub in Subtopic.objects.all():
            resp = self.client.get(reverse("forum:subtopic", args=[sub.pk]))
            self.assertEqual(
                resp.status_code, 200, f"subtopic {sub} did not return 200"
            )
