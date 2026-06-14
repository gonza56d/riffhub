"""Regression tests for confirmed bugs in ``forum.views`` (file group "forum-views").

Each test class targets one fix:

* #11/#16/#19 — engagement endpoints (comment/vote/react) must NOT let a normal
  user write to moderator-removed content (a removed Post, a removed Comment, or
  a Comment whose parent Post is removed). They now 404 for non-mods and create
  no row; moderators are unaffected (and can still load a removed post's detail).
* #18 — ``vote`` must accept ONLY the exact values "up"/"down"; anything else
  (e.g. "UP", "garbage") returns HTTP 400 and stores no Vote (it used to coerce
  every non-"up" value into a DOWNVOTE).
* #27 — the subtopic listing's ``num_comments`` annotation must count only
  *visible* comments, so it never advertises more than the post page shows.
* #24 — ``subtopic_create`` with an over-length name must redirect with a flash
  error instead of raising ``DataError`` (HTTP 500).

HTTP-level tests via ``django.test.Client`` mirroring tests/test_forum_read.py
and tests/test_forum_voting.py.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from core.models import SiteConfiguration
from forum.constants import VoteValue
from forum.models import Comment, Post, Reaction, Subtopic, Topic, Vote

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_forum_read.py)
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


def votes_for(target):
    ct = ContentType.objects.get_for_model(target, for_concrete_model=False)
    return Vote.objects.filter(content_type=ct, object_id=target.pk)


def reactions_for(target):
    ct = ContentType.objects.get_for_model(target, for_concrete_model=False)
    return Reaction.objects.filter(content_type=ct, object_id=target.pk)


# ---------------------------------------------------------------------------
# #11/#16/#19 — no engagement on moderator-removed content (non-mods)
# ---------------------------------------------------------------------------
class EngagementOnRemovedContentTests(TestCase):
    """A normal user may not comment/vote/react on soft-removed content."""

    def setUp(self):
        # Thresholds configured so level derivation never raises (PRODUCT.md).
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

        self.author = make_user("author")
        self.actor = make_user("actor")
        self.mod = make_user("mod", is_community_moderator=True)

        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")

        # A removed post (and a separate live post that holds a removed comment).
        self.removed_post = make_post(
            self.subtopic, self.author, title="Removed thread", body="gone"
        )
        self.removed_post.mark_removed(by=self.mod, reason="off-topic")

        self.live_post = make_post(
            self.subtopic, self.author, title="Live thread", body="here"
        )
        self.removed_comment = make_comment(
            self.live_post, self.author, body="removed comment body"
        )
        self.removed_comment.mark_removed(by=self.mod, reason="spam")

    # -- comment_create -------------------------------------------------------
    def test_comment_on_removed_post_404_and_no_comment_created(self):
        self.client.force_login(self.actor)
        before = self.removed_post.comments.count()
        resp = self.client.post(
            reverse("forum:comment_create", args=[self.removed_post.pk]),
            {"body": "sneaking in"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.removed_post.comments.count(), before)
        self.assertFalse(
            Comment.objects.filter(
                post=self.removed_post, body="sneaking in"
            ).exists()
        )

    # -- vote -----------------------------------------------------------------
    def test_vote_on_removed_post_404_and_no_vote_created(self):
        self.client.force_login(self.actor)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.removed_post.pk, "up"])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(votes_for(self.removed_post).exists())

    def test_vote_on_removed_comment_404_and_no_vote_created(self):
        self.client.force_login(self.actor)
        resp = self.client.post(
            reverse("forum:vote", args=["comment", self.removed_comment.pk, "up"])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(votes_for(self.removed_comment).exists())

    def test_vote_on_comment_whose_post_is_removed_404(self):
        """A live comment under a removed post is also unreachable for non-mods."""
        comment = make_comment(self.removed_post, self.author, body="child")
        self.client.force_login(self.actor)
        resp = self.client.post(
            reverse("forum:vote", args=["comment", comment.pk, "up"])
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(votes_for(comment).exists())

    # -- react ----------------------------------------------------------------
    def test_react_on_removed_post_404_and_no_reaction_created(self):
        self.client.force_login(self.actor)
        resp = self.client.post(
            reverse("forum:react", args=["post", self.removed_post.pk]),
            {"emoji": "🔥"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(reactions_for(self.removed_post).exists())

    def test_react_on_removed_comment_404_and_no_reaction_created(self):
        self.client.force_login(self.actor)
        resp = self.client.post(
            reverse("forum:react", args=["comment", self.removed_comment.pk]),
            {"emoji": "🔥"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(reactions_for(self.removed_comment).exists())

    # -- moderators are unaffected -------------------------------------------
    def test_moderator_can_still_load_removed_post_detail(self):
        """Don't break existing behavior: a mod still reaches a removed post."""
        self.client.force_login(self.mod)
        resp = self.client.get(reverse("forum:post", args=[self.removed_post.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Removed thread")

    def test_moderator_can_vote_on_removed_post(self):
        self.client.force_login(self.mod)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.removed_post.pk, "up"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(votes_for(self.removed_post).count(), 1)

    def test_engagement_on_live_content_still_works_for_normal_user(self):
        """Sanity: the gate only blocks removed content, not live content."""
        self.client.force_login(self.actor)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.live_post.pk, "up"])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(votes_for(self.live_post).count(), 1)


# ---------------------------------------------------------------------------
# #18 — vote accepts only exact "up"/"down"; anything else is 400
# ---------------------------------------------------------------------------
class VoteValueStrictParsingTests(TestCase):
    """``/vote/<value>/`` must reject non-"up"/"down" values with HTTP 400."""

    def setUp(self):
        self.author = make_user("vauthor")
        self.voter = make_user("vvoter")
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")
        self.post = make_post(self.subtopic, self.author, title="Votable")

    def test_uppercase_UP_is_400_and_stores_no_vote(self):
        """Regression: "UP" used to be coerced into a DOWNVOTE."""
        self.client.force_login(self.voter)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.post.pk, "UP"])
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(votes_for(self.post).exists())

    def test_garbage_value_is_400_and_stores_no_vote(self):
        self.client.force_login(self.voter)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.post.pk, "garbage"])
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(votes_for(self.post).exists())

    def test_numeric_one_is_400_and_stores_no_vote(self):
        self.client.force_login(self.voter)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.post.pk, "1"])
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(votes_for(self.post).exists())

    def test_lowercase_up_still_records_an_upvote(self):
        self.client.force_login(self.voter)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.post.pk, "up"])
        )
        self.assertEqual(resp.status_code, 200)
        vote = votes_for(self.post).get()
        self.assertEqual(vote.value, VoteValue.UP)

    def test_lowercase_down_still_records_a_downvote(self):
        self.client.force_login(self.voter)
        resp = self.client.post(
            reverse("forum:vote", args=["post", self.post.pk, "down"])
        )
        self.assertEqual(resp.status_code, 200)
        vote = votes_for(self.post).get()
        self.assertEqual(vote.value, VoteValue.DOWN)


# ---------------------------------------------------------------------------
# #27 — subtopic listing num_comments counts only visible comments
# ---------------------------------------------------------------------------
class SubtopicCommentCountExcludesRemovedTests(TestCase):
    """The subtopic listing's ``num_comments`` must exclude removed comments."""

    def setUp(self):
        self.author = make_user("scauthor")
        self.mod = make_user("scmod", is_community_moderator=True)
        self.topic = make_topic("Gear")
        self.subtopic = make_subtopic(self.topic, "Guitars")
        self.post = make_post(self.subtopic, self.author, title="Thread")

    def test_num_comments_excludes_removed_comment(self):
        make_comment(self.post, self.author, body="visible one")
        removed = make_comment(self.post, self.author, body="removed one")
        removed.mark_removed(by=self.mod, reason="spam")
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        annotated = resp.context["posts"][0]
        # Only the one non-removed comment is counted (was 2 before the fix).
        self.assertEqual(annotated.num_comments, 1)

    def test_num_comments_zero_when_only_removed(self):
        removed = make_comment(self.post, self.author, body="only removed")
        removed.mark_removed(by=self.mod, reason="spam")
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        annotated = resp.context["posts"][0]
        self.assertEqual(annotated.num_comments, 0)

    def test_num_comments_counts_all_when_none_removed(self):
        make_comment(self.post, self.author, body="one")
        make_comment(self.post, self.author, body="two")
        resp = self.client.get(reverse("forum:subtopic", args=[self.subtopic.pk]))
        annotated = resp.context["posts"][0]
        self.assertEqual(annotated.num_comments, 2)


# ---------------------------------------------------------------------------
# #24 — subtopic_create with an over-length name redirects (no 500)
# ---------------------------------------------------------------------------
class SubtopicCreateOverlengthNameTests(TestCase):
    """An over-length subtopic name is rejected gracefully, never a 500."""

    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

        self.creator = make_user("creator", is_riffhub_creator=True)
        self.topic = make_topic("Gear")

    def test_overlength_name_redirects_without_500(self):
        self.client.force_login(self.creator)
        max_len = Subtopic._meta.get_field("name").max_length
        too_long = "x" * (max_len + 1)
        before = Subtopic.objects.filter(topic=self.topic).count()
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": too_long},
        )
        self.assertNotEqual(resp.status_code, 500)
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        # Nothing was created.
        self.assertEqual(
            Subtopic.objects.filter(topic=self.topic).count(), before
        )
        self.assertFalse(
            Subtopic.objects.filter(topic=self.topic, name=too_long).exists()
        )

    def test_overlength_name_flashes_error_message(self):
        self.client.force_login(self.creator)
        max_len = Subtopic._meta.get_field("name").max_length
        too_long = "x" * (max_len + 1)
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": too_long},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        msgs = [str(m) for m in resp.context["messages"]]
        self.assertTrue(
            any("at most" in m and str(max_len) in m for m in msgs),
            msgs,
        )

    def test_name_at_max_length_is_created(self):
        """A name exactly at the limit is still accepted (boundary)."""
        self.client.force_login(self.creator)
        max_len = Subtopic._meta.get_field("name").max_length
        at_limit = "y" * max_len
        resp = self.client.post(
            reverse("forum:subtopic_create", args=[self.topic.pk]),
            {"name": at_limit},
        )
        self.assertRedirects(resp, reverse("forum:manage_topics"))
        self.assertTrue(
            Subtopic.objects.filter(topic=self.topic, name=at_limit).exists()
        )
