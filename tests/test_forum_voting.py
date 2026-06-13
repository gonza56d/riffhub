"""Tests for forum voting.

Covers ``forum.services.cast_vote`` / ``vote_tally`` and the HTMX vote
endpoint ``/forum/<post|comment>/<pk>/vote/<up|down>/`` against the PRODUCT.md
rules:

* you cannot vote your own post/comment (PermissionDenied / HTTP 403),
* the first vote sets the value, re-casting the same value toggles it off,
  the opposite value switches (up/down are mutually exclusive),
* ``vote_tally`` counts up- and down-votes separately,
* every vote action bumps activity on the subtopic and its parent topic,
* the content author's reputation is applied on vote and reversed on toggle,
* the endpoint rejects anonymous users (403) and self-votes (403) and returns
  the ``_vote.html`` widget fragment on success.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from forum import services
from forum.constants import (
    REP_RECEIVED_DOWNVOTE,
    REP_RECEIVED_UPVOTE,
    VoteValue,
)
from forum.models import Comment, Post, Subtopic, Topic, Vote

User = get_user_model()


def _make_user(username, **extra):
    """Create an active, email-confirmed user able to participate."""
    defaults = {"email": f"{username}@example.com", "password": "pw-12345"}
    defaults.update(extra)
    user = User.objects.create_user(username=username, **defaults)
    if not user.email_confirmed:
        user.email_confirmed = True
        user.save(update_fields=["email_confirmed"])
    return user


class VotingTestBase(TestCase):
    """Shared fixtures: a topic/subtopic and a post + comment with an author."""

    def setUp(self):
        self.author = _make_user("author")
        self.voter = _make_user("voter")
        self.other = _make_user("other")

        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.author,
            title="Jackson Dinky vs Ibanez RG",
            body="Which neck do you prefer?",
        )
        self.comment = Comment.objects.create(
            post=self.post, author=self.author, body="I prefer Dinkies."
        )

    # -- helpers ----------------------------------------------------------
    def _reload_author(self):
        self.author.refresh_from_db()
        return self.author

    def _votes_for(self, target):
        ct = ContentType.objects.get_for_model(target, for_concrete_model=False)
        return Vote.objects.filter(content_type=ct, object_id=target.pk)


# ---------------------------------------------------------------------------
# cast_vote — core rules
# ---------------------------------------------------------------------------
class CastVoteSelfVoteTests(VotingTestBase):
    def test_self_vote_on_post_raises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            services.cast_vote(self.author, self.post, VoteValue.UP)
        self.assertFalse(self._votes_for(self.post).exists())

    def test_self_vote_on_comment_raises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            services.cast_vote(self.author, self.comment, VoteValue.DOWN)
        self.assertFalse(self._votes_for(self.comment).exists())

    def test_self_vote_does_not_change_author_reputation(self):
        start = self._reload_author().reputation_score
        with self.assertRaises(PermissionDenied):
            services.cast_vote(self.author, self.post, VoteValue.UP)
        self.assertEqual(self._reload_author().reputation_score, start)


class CastVoteValueValidationTests(VotingTestBase):
    def test_invalid_zero_value_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            services.cast_vote(self.voter, self.post, 0)
        self.assertFalse(self._votes_for(self.post).exists())

    def test_invalid_large_value_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            services.cast_vote(self.voter, self.post, 5)

    def test_string_up_value_is_coerced_to_int(self):
        # cast_vote does int(value); "1" is a valid up-vote.
        vote = services.cast_vote(self.voter, self.post, "1")
        self.assertIsNotNone(vote)
        self.assertEqual(vote.value, VoteValue.UP)


class CastVoteFirstVoteTests(VotingTestBase):
    def test_first_upvote_creates_vote_with_up_value(self):
        vote = services.cast_vote(self.voter, self.post, VoteValue.UP)
        self.assertIsNotNone(vote)
        self.assertEqual(vote.value, VoteValue.UP)
        self.assertEqual(vote.voter, self.voter)
        self.assertEqual(self._votes_for(self.post).count(), 1)

    def test_first_downvote_creates_vote_with_down_value(self):
        vote = services.cast_vote(self.voter, self.comment, VoteValue.DOWN)
        self.assertIsNotNone(vote)
        self.assertEqual(vote.value, VoteValue.DOWN)
        self.assertEqual(self._votes_for(self.comment).count(), 1)

    def test_vote_target_resolves_to_the_voted_object(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        row = self._votes_for(self.post).get()
        self.assertEqual(row.object_id, self.post.pk)
        self.assertEqual(row.target, self.post)


class CastVoteToggleTests(VotingTestBase):
    def test_same_value_toggles_vote_off(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        result = services.cast_vote(self.voter, self.post, VoteValue.UP)
        self.assertIsNone(result)
        self.assertFalse(self._votes_for(self.post).exists())

    def test_downvote_then_downvote_toggles_off(self):
        services.cast_vote(self.voter, self.comment, VoteValue.DOWN)
        result = services.cast_vote(self.voter, self.comment, VoteValue.DOWN)
        self.assertIsNone(result)
        self.assertFalse(self._votes_for(self.comment).exists())

    def test_toggle_off_then_on_again_recreates_vote(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.voter, self.post, VoteValue.UP)  # off
        again = services.cast_vote(self.voter, self.post, VoteValue.UP)  # on
        self.assertIsNotNone(again)
        self.assertEqual(self._votes_for(self.post).count(), 1)
        self.assertEqual(again.value, VoteValue.UP)


class CastVoteSwitchTests(VotingTestBase):
    def test_opposite_value_switches_up_to_down(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        switched = services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        self.assertIsNotNone(switched)
        self.assertEqual(switched.value, VoteValue.DOWN)
        # Mutually exclusive: still exactly one row, now a down-vote.
        rows = self._votes_for(self.post)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().value, VoteValue.DOWN)

    def test_opposite_value_switches_down_to_up(self):
        services.cast_vote(self.voter, self.comment, VoteValue.DOWN)
        switched = services.cast_vote(self.voter, self.comment, VoteValue.UP)
        self.assertEqual(switched.value, VoteValue.UP)
        self.assertEqual(self._votes_for(self.comment).count(), 1)
        self.assertEqual(self._votes_for(self.comment).get().value, VoteValue.UP)

    def test_switch_keeps_same_vote_row_pk(self):
        first = services.cast_vote(self.voter, self.post, VoteValue.UP)
        switched = services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        # Switching replaces the value on the existing row (not delete+create).
        self.assertEqual(first.pk, switched.pk)


class CastVoteMultipleVotersTests(VotingTestBase):
    def test_different_voters_each_get_their_own_vote(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.other, self.post, VoteValue.UP)
        self.assertEqual(self._votes_for(self.post).count(), 2)

    def test_one_voter_toggling_off_leaves_other_voters_intact(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.other, self.post, VoteValue.DOWN)
        services.cast_vote(self.voter, self.post, VoteValue.UP)  # toggle off
        rows = self._votes_for(self.post)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().voter, self.other)


# ---------------------------------------------------------------------------
# vote_tally — separate up/down counts
# ---------------------------------------------------------------------------
class VoteTallyTests(VotingTestBase):
    def test_empty_tally_is_zero_zero(self):
        self.assertEqual(services.vote_tally(self.post), {"up": 0, "down": 0})

    def test_tally_counts_up_and_down_separately(self):
        up_voter_2 = _make_user("up2")
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(up_voter_2, self.post, VoteValue.UP)
        services.cast_vote(self.other, self.post, VoteValue.DOWN)
        self.assertEqual(services.vote_tally(self.post), {"up": 2, "down": 1})

    def test_tally_updates_after_switch(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        self.assertEqual(services.vote_tally(self.post), {"up": 1, "down": 0})
        services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        self.assertEqual(services.vote_tally(self.post), {"up": 0, "down": 1})

    def test_tally_updates_after_toggle_off(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        self.assertEqual(services.vote_tally(self.post), {"up": 0, "down": 0})

    def test_post_and_comment_tallies_are_independent(self):
        # A vote on the post must not leak into the comment's tally (generic
        # relations are keyed on content_type + object_id).
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.voter, self.comment, VoteValue.DOWN)
        self.assertEqual(services.vote_tally(self.post), {"up": 1, "down": 0})
        self.assertEqual(services.vote_tally(self.comment), {"up": 0, "down": 1})

    def test_post_and_comment_with_same_pk_do_not_collide(self):
        # Force a comment whose pk happens to match the post's pk, to prove
        # the tally is scoped by content_type and not object_id alone.
        clash = Comment.objects.create(
            post=self.post, author=self.author, body="clash"
        )
        # Free the post's pk in the comment table, then move the clash comment
        # onto it so a Post and a Comment deliberately share an object_id.
        Comment.objects.filter(pk=self.post.pk).exclude(pk=clash.pk).delete()
        Comment.objects.filter(pk=clash.pk).update(id=self.post.pk)
        clash = Comment.objects.get(pk=self.post.pk)
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.other, clash, VoteValue.DOWN)
        self.assertEqual(services.vote_tally(self.post), {"up": 1, "down": 0})
        self.assertEqual(services.vote_tally(clash), {"up": 0, "down": 1})


# ---------------------------------------------------------------------------
# Activity bumping
# ---------------------------------------------------------------------------
class VotingActivityTests(VotingTestBase):
    def _activity(self):
        self.subtopic.refresh_from_db()
        self.topic.refresh_from_db()
        return self.subtopic.activity_count, self.topic.activity_count

    def test_first_vote_bumps_subtopic_and_topic(self):
        before_sub, before_topic = self._activity()
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        after_sub, after_topic = self._activity()
        self.assertEqual(after_sub, before_sub + 1)
        self.assertEqual(after_topic, before_topic + 1)

    def test_voting_on_comment_bumps_parent_post_subtopic_and_topic(self):
        before_sub, before_topic = self._activity()
        services.cast_vote(self.voter, self.comment, VoteValue.UP)
        after_sub, after_topic = self._activity()
        self.assertEqual(after_sub, before_sub + 1)
        self.assertEqual(after_topic, before_topic + 1)

    def test_switching_vote_counts_as_another_activity(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        sub_after_first, topic_after_first = self._activity()
        services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        sub_after_switch, topic_after_switch = self._activity()
        self.assertEqual(sub_after_switch, sub_after_first + 1)
        self.assertEqual(topic_after_switch, topic_after_first + 1)

    def test_toggling_off_counts_as_another_activity(self):
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        sub_after_first, topic_after_first = self._activity()
        services.cast_vote(self.voter, self.post, VoteValue.UP)  # toggle off
        sub_after_toggle, topic_after_toggle = self._activity()
        self.assertEqual(sub_after_toggle, sub_after_first + 1)
        self.assertEqual(topic_after_toggle, topic_after_first + 1)

    def test_failed_self_vote_does_not_bump_activity(self):
        before_sub, before_topic = self._activity()
        with self.assertRaises(PermissionDenied):
            services.cast_vote(self.author, self.post, VoteValue.UP)
        after_sub, after_topic = self._activity()
        self.assertEqual(after_sub, before_sub)
        self.assertEqual(after_topic, before_topic)


# ---------------------------------------------------------------------------
# Author reputation
# ---------------------------------------------------------------------------
class VotingReputationTests(VotingTestBase):
    def test_upvote_increases_author_reputation(self):
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        self.assertEqual(
            self._reload_author().reputation_score, start + REP_RECEIVED_UPVOTE
        )

    def test_downvote_decreases_author_reputation(self):
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        self.assertEqual(
            self._reload_author().reputation_score, start + REP_RECEIVED_DOWNVOTE
        )

    def test_toggling_upvote_off_reverses_reputation(self):
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.voter, self.post, VoteValue.UP)  # toggle off
        self.assertEqual(self._reload_author().reputation_score, start)

    def test_toggling_downvote_off_reverses_reputation(self):
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.comment, VoteValue.DOWN)
        services.cast_vote(self.voter, self.comment, VoteValue.DOWN)  # toggle off
        self.assertEqual(self._reload_author().reputation_score, start)

    def test_switch_up_to_down_nets_to_down_delta(self):
        # +1 (up) then switch to -1 (down): net effect from baseline is the
        # single received-downvote delta (the up reputation is fully reversed).
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        self.assertEqual(
            self._reload_author().reputation_score, start + REP_RECEIVED_DOWNVOTE
        )

    def test_switch_down_to_up_nets_to_up_delta(self):
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.DOWN)
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        self.assertEqual(
            self._reload_author().reputation_score, start + REP_RECEIVED_UPVOTE
        )

    def test_two_upvotes_from_different_voters_stack_reputation(self):
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.other, self.post, VoteValue.UP)
        self.assertEqual(
            self._reload_author().reputation_score, start + 2 * REP_RECEIVED_UPVOTE
        )

    def test_reputation_returns_to_baseline_after_off_off_cycle(self):
        # up (author), off, up (other), off -> net zero on the author.
        start = self._reload_author().reputation_score
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.voter, self.post, VoteValue.UP)
        services.cast_vote(self.other, self.post, VoteValue.UP)
        services.cast_vote(self.other, self.post, VoteValue.UP)
        self.assertEqual(self._reload_author().reputation_score, start)


# ---------------------------------------------------------------------------
# HTTP endpoint /forum/<target>/<pk>/vote/<value>/
# ---------------------------------------------------------------------------
class VoteEndpointTests(VotingTestBase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def _url(self, target_type, pk, value):
        return reverse("forum:vote", args=[target_type, pk, value])

    # -- auth / permission ------------------------------------------------
    def test_anonymous_vote_is_forbidden(self):
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self._votes_for(self.post).exists())

    def test_self_vote_via_endpoint_is_forbidden(self):
        self.client.force_login(self.author)
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self._votes_for(self.post).exists())

    def test_self_vote_on_comment_via_endpoint_is_forbidden(self):
        self.client.force_login(self.author)
        resp = self.client.post(self._url("comment", self.comment.pk, "down"))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self._votes_for(self.comment).exists())

    # -- GET not allowed --------------------------------------------------
    def test_get_request_is_not_allowed(self):
        self.client.force_login(self.voter)
        resp = self.client.get(self._url("post", self.post.pk, "up"))
        self.assertEqual(resp.status_code, 405)

    # -- unknown target / missing object ----------------------------------
    def test_unknown_target_type_returns_404(self):
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("widget", self.post.pk, "up"))
        self.assertEqual(resp.status_code, 404)

    def test_missing_object_returns_404(self):
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("post", 999999, "up"))
        self.assertEqual(resp.status_code, 404)

    # -- success path -----------------------------------------------------
    def test_upvote_returns_vote_fragment_and_records_vote(self):
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "forum/_vote.html")
        rows = self._votes_for(self.post)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().value, VoteValue.UP)

    def test_fragment_contains_votebox_id_for_target(self):
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        self.assertContains(resp, f'id="vote-post-{self.post.pk}"')

    def test_fragment_shows_updated_up_tally(self):
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        # The widget renders the up-count next to the up arrow.
        self.assertContains(resp, "▲ 1")

    def test_downvote_value_records_down_vote(self):
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("comment", self.comment.pk, "down"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._votes_for(self.comment).get().value, VoteValue.DOWN)

    def test_non_up_value_is_treated_as_downvote(self):
        # The view maps anything that isn't exactly "up" to a down-vote.
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("post", self.post.pk, "down"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._votes_for(self.post).get().value, VoteValue.DOWN)

    def test_endpoint_toggle_removes_vote_on_second_identical_request(self):
        self.client.force_login(self.voter)
        self.client.post(self._url("post", self.post.pk, "up"))
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._votes_for(self.post).exists())
        # After toggle-off the up tally is back to zero in the fragment.
        self.assertContains(resp, "▲ 0")

    def test_endpoint_switch_up_to_down(self):
        self.client.force_login(self.voter)
        self.client.post(self._url("post", self.post.pk, "up"))
        self.client.post(self._url("post", self.post.pk, "down"))
        rows = self._votes_for(self.post)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().value, VoteValue.DOWN)

    def test_endpoint_bumps_activity(self):
        self.client.force_login(self.voter)
        before = Topic.objects.get(pk=self.topic.pk).activity_count
        self.client.post(self._url("post", self.post.pk, "up"))
        after = Topic.objects.get(pk=self.topic.pk).activity_count
        self.assertEqual(after, before + 1)

    def test_endpoint_applies_author_reputation(self):
        self.client.force_login(self.voter)
        start = self._reload_author().reputation_score
        self.client.post(self._url("post", self.post.pk, "up"))
        self.assertEqual(
            self._reload_author().reputation_score, start + REP_RECEIVED_UPVOTE
        )

    def test_fragment_for_voter_renders_active_buttons(self):
        # A user who can vote sees the interactive buttons (hx-post present),
        # confirming the "can_vote" branch of the widget renders for them.
        self.client.force_login(self.voter)
        resp = self.client.post(self._url("post", self.post.pk, "up"))
        self.assertContains(resp, "hx-post")
