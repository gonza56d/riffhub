"""Tests for forum emoji reactions.

Covers ``forum.services.toggle_reaction`` / ``forum.services.reaction_tally``
and the HTMX endpoint ``/forum/<kind>/<pk>/react/`` (``forum:react``).

PRODUCT.md reaction rules under test:
  * you cannot react to your own post/comment -> PermissionDenied
  * one of each emoji type per user per target (unique constraint)
  * clicking the same emoji again removes it (toggle off)
  * a user may stack many *different* emojis on one target
  * reaction_tally counts each emoji separately
  * any reaction counts as activity (bumps subtopic + parent topic)

Endpoint rules:
  * anonymous POST -> 403
  * self-react / empty emoji -> 400
  * a successful (de)react re-renders the ``forum/_reactions.html`` fragment
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from core.models import SiteConfiguration
from forum.models import Comment, Post, Reaction, Subtopic, Topic
from forum.services import reaction_tally, toggle_reaction

User = get_user_model()


class ReactionTestBase(TestCase):
    """Shared fixtures: a topic/subtopic, a post + comment, and two users.

    Reactions go through ``forum.services`` which resolves the owning subtopic
    (for activity) via Post.subtopic / Comment.post.subtopic, so we build the
    full Topic -> Subtopic -> Post -> Comment chain.
    """

    def setUp(self):
        # Configure thresholds so any incidental ``user.level`` read during
        # view rendering can't raise ImproperlyConfigured (no silent default).
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

        self.author = User.objects.create_user(
            username="riffmaster",
            email="riffmaster@example.com",
            password="pw",
            email_confirmed=True,
        )
        self.reactor = User.objects.create_user(
            username="picker",
            email="picker@example.com",
            password="pw",
            email_confirmed=True,
        )
        self.other = User.objects.create_user(
            username="bystander",
            email="bystander@example.com",
            password="pw",
            email_confirmed=True,
        )

        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.author,
            title="Jackson Dinky vs Ibanez RG",
            body="Discuss.",
        )
        self.comment = Comment.objects.create(
            post=self.post, author=self.author, body="I prefer Dinkies."
        )


class ToggleReactionServiceTests(ReactionTestBase):
    """Unit tests for ``forum.services.toggle_reaction`` business rules."""

    def test_add_reaction_creates_row_and_returns_it(self):
        reaction = toggle_reaction(self.reactor, self.post, "👍")

        self.assertIsNotNone(reaction)
        self.assertIsInstance(reaction, Reaction)
        self.assertEqual(reaction.user, self.reactor)
        self.assertEqual(reaction.emoji, "👍")
        ct = ContentType.objects.get_for_model(Post)
        self.assertEqual(reaction.content_type, ct)
        self.assertEqual(reaction.object_id, self.post.pk)
        self.assertEqual(Reaction.objects.count(), 1)

    def test_self_reaction_on_post_raises_permission_denied(self):
        # PRODUCT.md: you cannot react to your own content.
        with self.assertRaises(PermissionDenied):
            toggle_reaction(self.author, self.post, "🔥")
        self.assertEqual(Reaction.objects.count(), 0)

    def test_self_reaction_on_comment_raises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            toggle_reaction(self.author, self.comment, "🔥")
        self.assertEqual(Reaction.objects.count(), 0)

    def test_same_emoji_again_removes_it_and_returns_none(self):
        # PRODUCT.md: clicking the same emoji again removes the reaction.
        toggle_reaction(self.reactor, self.post, "❤️")
        self.assertEqual(Reaction.objects.count(), 1)

        result = toggle_reaction(self.reactor, self.post, "❤️")
        self.assertIsNone(result)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_toggle_off_then_on_again_recreates_the_reaction(self):
        toggle_reaction(self.reactor, self.post, "🤘")
        toggle_reaction(self.reactor, self.post, "🤘")  # off
        again = toggle_reaction(self.reactor, self.post, "🤘")  # on

        self.assertIsNotNone(again)
        self.assertEqual(
            Reaction.objects.filter(
                user=self.reactor, emoji="🤘", object_id=self.post.pk
            ).count(),
            1,
        )

    def test_different_emojis_from_same_user_stack(self):
        # PRODUCT.md: a user may react with as many *different* emojis as they
        # like (only one of each type).
        toggle_reaction(self.reactor, self.post, "👍")
        toggle_reaction(self.reactor, self.post, "❤️")
        toggle_reaction(self.reactor, self.post, "🔥")

        emojis = set(
            Reaction.objects.filter(user=self.reactor).values_list("emoji", flat=True)
        )
        self.assertEqual(emojis, {"👍", "❤️", "🔥"})
        self.assertEqual(Reaction.objects.count(), 3)

    def test_one_of_each_emoji_per_user_per_target(self):
        # Re-adding the same emoji toggles it off rather than creating a second
        # row, so there is never more than one (user, target, emoji) row.
        toggle_reaction(self.reactor, self.post, "👍")
        # Two more toggles: off, then on -> still exactly one row.
        toggle_reaction(self.reactor, self.post, "👍")
        toggle_reaction(self.reactor, self.post, "👍")

        self.assertEqual(
            Reaction.objects.filter(
                user=self.reactor, emoji="👍", object_id=self.post.pk
            ).count(),
            1,
        )

    def test_same_emoji_from_different_users_coexist(self):
        toggle_reaction(self.reactor, self.post, "🔥")
        toggle_reaction(self.other, self.post, "🔥")

        self.assertEqual(
            Reaction.objects.filter(emoji="🔥", object_id=self.post.pk).count(), 2
        )

    def test_reactions_on_post_and_comment_are_independent(self):
        # The post and its comment are distinct generic targets.
        toggle_reaction(self.reactor, self.post, "👍")
        toggle_reaction(self.reactor, self.comment, "👍")

        post_ct = ContentType.objects.get_for_model(Post)
        comment_ct = ContentType.objects.get_for_model(Comment)
        self.assertEqual(
            Reaction.objects.filter(
                content_type=post_ct, object_id=self.post.pk
            ).count(),
            1,
        )
        self.assertEqual(
            Reaction.objects.filter(
                content_type=comment_ct, object_id=self.comment.pk
            ).count(),
            1,
        )
        # Toggling the comment one off leaves the post one intact.
        toggle_reaction(self.reactor, self.comment, "👍")
        self.assertEqual(
            Reaction.objects.filter(
                content_type=post_ct, object_id=self.post.pk
            ).count(),
            1,
        )
        self.assertEqual(
            Reaction.objects.filter(
                content_type=comment_ct, object_id=self.comment.pk
            ).count(),
            0,
        )

    def test_empty_emoji_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            toggle_reaction(self.reactor, self.post, "")
        self.assertEqual(Reaction.objects.count(), 0)

    def test_whitespace_only_emoji_raises_validation_error(self):
        # toggle_reaction strips the emoji; whitespace-only becomes empty.
        with self.assertRaises(ValidationError):
            toggle_reaction(self.reactor, self.post, "   ")
        self.assertEqual(Reaction.objects.count(), 0)

    def test_none_emoji_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            toggle_reaction(self.reactor, self.post, None)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_emoji_is_stripped_before_storing(self):
        reaction = toggle_reaction(self.reactor, self.post, "  🔥 ")
        self.assertEqual(reaction.emoji, "🔥")

    def test_reaction_on_comment_supported(self):
        reaction = toggle_reaction(self.reactor, self.comment, "😂")
        self.assertIsNotNone(reaction)
        self.assertEqual(reaction.object_id, self.comment.pk)
        self.assertEqual(
            reaction.content_type, ContentType.objects.get_for_model(Comment)
        )


class ReactionActivityTests(ReactionTestBase):
    """Reacting bumps activity on the subtopic and its parent topic (PRODUCT.md:
    ANY action counts as +1 activity)."""

    def _refresh_counts(self):
        self.subtopic.refresh_from_db()
        self.topic.refresh_from_db()
        return self.subtopic.activity_count, self.topic.activity_count

    def test_adding_reaction_increments_subtopic_and_topic_activity(self):
        before_sub, before_top = self._refresh_counts()
        toggle_reaction(self.reactor, self.post, "👍")
        after_sub, after_top = self._refresh_counts()

        self.assertEqual(after_sub, before_sub + 1)
        self.assertEqual(after_top, before_top + 1)

    def test_removing_reaction_also_counts_as_activity(self):
        toggle_reaction(self.reactor, self.post, "👍")
        sub_after_add, top_after_add = self._refresh_counts()

        toggle_reaction(self.reactor, self.post, "👍")  # toggle off
        sub_after_remove, top_after_remove = self._refresh_counts()

        self.assertEqual(sub_after_remove, sub_after_add + 1)
        self.assertEqual(top_after_remove, top_after_add + 1)

    def test_reacting_to_comment_bumps_owning_subtopic_and_topic(self):
        before_sub, before_top = self._refresh_counts()
        toggle_reaction(self.reactor, self.comment, "🔥")
        after_sub, after_top = self._refresh_counts()

        self.assertEqual(after_sub, before_sub + 1)
        self.assertEqual(after_top, before_top + 1)

    def test_blocked_self_reaction_does_not_bump_activity(self):
        before_sub, before_top = self._refresh_counts()
        with self.assertRaises(PermissionDenied):
            toggle_reaction(self.author, self.post, "👍")
        after_sub, after_top = self._refresh_counts()

        self.assertEqual(after_sub, before_sub)
        self.assertEqual(after_top, before_top)


class ReactionTallyTests(ReactionTestBase):
    """Unit tests for ``forum.services.reaction_tally``."""

    def test_empty_target_tally_is_empty_dict(self):
        self.assertEqual(reaction_tally(self.post), {})

    def test_tally_counts_each_emoji_separately(self):
        toggle_reaction(self.reactor, self.post, "👍")
        toggle_reaction(self.other, self.post, "👍")
        toggle_reaction(self.reactor, self.post, "🔥")

        tally = reaction_tally(self.post)
        self.assertEqual(tally, {"👍": 2, "🔥": 1})

    def test_tally_reflects_toggle_off(self):
        toggle_reaction(self.reactor, self.post, "❤️")
        toggle_reaction(self.other, self.post, "❤️")
        self.assertEqual(reaction_tally(self.post), {"❤️": 2})

        toggle_reaction(self.reactor, self.post, "❤️")  # remove one
        self.assertEqual(reaction_tally(self.post), {"❤️": 1})

    def test_tally_is_scoped_to_target(self):
        # A reaction on the comment must not leak into the post's tally.
        toggle_reaction(self.reactor, self.post, "👍")
        toggle_reaction(self.reactor, self.comment, "🔥")

        self.assertEqual(reaction_tally(self.post), {"👍": 1})
        self.assertEqual(reaction_tally(self.comment), {"🔥": 1})

    def test_tally_for_comment(self):
        toggle_reaction(self.reactor, self.comment, "😮")
        toggle_reaction(self.other, self.comment, "😮")
        toggle_reaction(self.other, self.comment, "👍")
        self.assertEqual(reaction_tally(self.comment), {"😮": 2, "👍": 1})


class ReactEndpointTests(ReactionTestBase):
    """HTTP tests for the ``forum:react`` HTMX endpoint."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.post_react_url = reverse("forum:react", args=["post", self.post.pk])
        self.comment_react_url = reverse(
            "forum:react", args=["comment", self.comment.pk]
        )

    # --- auth / method ----------------------------------------------------
    def test_anonymous_react_is_forbidden(self):
        resp = self.client.post(self.post_react_url, {"emoji": "👍"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_get_not_allowed(self):
        # The endpoint is @require_POST.
        self.client.force_login(self.reactor)
        resp = self.client.get(self.post_react_url, {"emoji": "👍"})
        self.assertEqual(resp.status_code, 405)

    def test_unknown_target_kind_returns_404(self):
        self.client.force_login(self.reactor)
        url = reverse("forum:react", args=["widget", self.post.pk])
        resp = self.client.post(url, {"emoji": "👍"})
        self.assertEqual(resp.status_code, 404)

    def test_missing_post_returns_404(self):
        self.client.force_login(self.reactor)
        url = reverse("forum:react", args=["post", 999_999])
        resp = self.client.post(url, {"emoji": "👍"})
        self.assertEqual(resp.status_code, 404)

    # --- happy path: emoji POST param creates the reaction ----------------
    def test_post_with_emoji_param_creates_reaction(self):
        self.client.force_login(self.reactor)
        resp = self.client.post(self.post_react_url, {"emoji": "👍"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            Reaction.objects.filter(
                user=self.reactor, emoji="👍", object_id=self.post.pk
            ).count(),
            1,
        )

    def test_react_response_is_reactions_fragment(self):
        self.client.force_login(self.reactor)
        resp = self.client.post(self.post_react_url, {"emoji": "🔥"})

        self.assertEqual(resp.status_code, 200)
        # forum/_reactions.html renders a container span keyed by target.
        self.assertTemplateUsed(resp, "forum/_reactions.html")
        body = resp.content.decode()
        self.assertIn(f'id="react-post-{self.post.pk}"', body)
        # The just-added emoji and its count appear in the fragment.
        self.assertIn("🔥", body)

    def test_react_fragment_marks_users_own_emoji_as_mine(self):
        self.client.force_login(self.reactor)
        resp = self.client.post(self.post_react_url, {"emoji": "🔥"})
        # _react_ctx feeds ``mine`` -> template adds the "mine" class.
        self.assertIn("mine", resp.content.decode())

    def test_post_again_same_emoji_toggles_off(self):
        self.client.force_login(self.reactor)
        self.client.post(self.post_react_url, {"emoji": "👍"})
        resp = self.client.post(self.post_react_url, {"emoji": "👍"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            Reaction.objects.filter(
                user=self.reactor, emoji="👍", object_id=self.post.pk
            ).count(),
            0,
        )

    def test_post_different_emojis_stack_via_endpoint(self):
        self.client.force_login(self.reactor)
        self.client.post(self.post_react_url, {"emoji": "👍"})
        self.client.post(self.post_react_url, {"emoji": "🔥"})

        self.assertEqual(
            set(
                Reaction.objects.filter(
                    user=self.reactor, object_id=self.post.pk
                ).values_list("emoji", flat=True)
            ),
            {"👍", "🔥"},
        )

    def test_react_on_comment_via_endpoint(self):
        self.client.force_login(self.reactor)
        resp = self.client.post(self.comment_react_url, {"emoji": "😂"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn(f'id="react-comment-{self.comment.pk}"', resp.content.decode())
        self.assertEqual(
            Reaction.objects.filter(
                user=self.reactor,
                emoji="😂",
                object_id=self.comment.pk,
                content_type=ContentType.objects.get_for_model(Comment),
            ).count(),
            1,
        )

    # --- negative cases mapped to 400 -------------------------------------
    def test_self_react_via_endpoint_returns_400(self):
        # The author reacting to their own post: PermissionDenied -> 400.
        self.client.force_login(self.author)
        resp = self.client.post(self.post_react_url, {"emoji": "👍"})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_missing_emoji_param_returns_400(self):
        self.client.force_login(self.reactor)
        resp = self.client.post(self.post_react_url, {})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_blank_emoji_param_returns_400(self):
        self.client.force_login(self.reactor)
        resp = self.client.post(self.post_react_url, {"emoji": "   "})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Reaction.objects.count(), 0)

    # --- endpoint side effects --------------------------------------------
    def test_endpoint_react_bumps_activity(self):
        self.client.force_login(self.reactor)
        before = Topic.objects.get(pk=self.topic.pk).activity_count
        self.client.post(self.post_react_url, {"emoji": "👍"})
        after = Topic.objects.get(pk=self.topic.pk).activity_count
        self.assertEqual(after, before + 1)

    def test_fragment_shows_other_users_reaction_count(self):
        # Pre-seed a reaction from someone else, then the reactor adds another
        # emoji; the rendered tally should include both emojis with counts.
        toggle_reaction(self.other, self.post, "🔥")
        self.client.force_login(self.reactor)
        resp = self.client.post(self.post_react_url, {"emoji": "👍"})

        body = resp.content.decode()
        self.assertIn("🔥", body)
        self.assertIn("👍", body)
