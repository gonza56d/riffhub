"""Tests for ``catalog.services`` — the collab-db review workflow.

Covers the four public service functions PRODUCT.md describes:

* ``cast_review_vote``    — Collaborator+ only, no self-vote, +1/−1 toggle/switch,
                            and the ``ReviewVote`` tally helpers it feeds.
* ``evaluate_submission`` — auto-publishes only when BOTH the net-vote floor and
                            the distinct-voter floor are cleared; credits the
                            submitter (+1 accepted, +10 reputation, recompute).
* ``reject_submission``   — marks REJECTED and ticks the troll-guard counter.
* ``can_submit_to_collab``— needs a confirmed e-mail and blocks once too many
                            rejects accumulate.

All targets are ``Brand`` rows: ``Brand`` is the simplest ``CatalogEntry``
subclass (only a unique ``name`` plus the inherited status/submitted_by/review
fields), so it exercises the generic-relation machinery without dragging in the
vocabulary FKs the gear/guitar models need.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from accounts.models import Level
from catalog.constants import (
    REP_ACCEPTED_SUBMISSION,
    PublicationStatus,
    VoteValue,
)
from catalog.models import Brand, ReviewVote
from catalog.services import (
    can_submit_to_collab,
    cast_review_vote,
    evaluate_submission,
    reject_submission,
)
from core.models import SiteConfiguration

User = get_user_model()


class CollabServiceTestBase(TestCase):
    """Shared fixtures: configured thresholds + a small bench of users.

    The collaborator/founder thresholds intentionally have *no* default
    (``ImproperlyConfigured`` until set), so every test that derives a level
    must configure them. We set them once here and pick numbers that keep the
    bench at Collaborator without tripping the Founder badge.
    """

    # Acceptance bar used across the voting tests unless a test overrides it.
    MIN_NET = 3
    MIN_VOTERS = 3
    MAX_REJECTED = 3

    def setUp(self):
        self.config = SiteConfiguration.get_solo()
        # 1 accepted submission promotes to Collaborator; Founder is far away so
        # the badge never fires by accident in the voting tests.
        self.config.collaborator_promotion_threshold = 1
        self.config.founder_threshold = 30
        self.config.founder_level_achievable = True
        self.config.gear_acceptance_min_net_votes = self.MIN_NET
        self.config.gear_acceptance_min_voters = self.MIN_VOTERS
        self.config.max_rejected_before_cooldown = self.MAX_REJECTED
        self.config.save()

        # Submitter of the entry under review (a confirmed Regular user).
        self.submitter = self._make_user(
            "submitter", accepted=0, email_confirmed=True
        )
        # A pool of Collaborators who are allowed to vote.
        self.collab1 = self._make_collaborator("collab1")
        self.collab2 = self._make_collaborator("collab2")
        self.collab3 = self._make_collaborator("collab3")
        self.collab4 = self._make_collaborator("collab4")

        self.brand = self._make_brand("Submitted Brand", submitted_by=self.submitter)

    # --- fixture helpers ---------------------------------------------------
    def _make_user(self, username, *, accepted=0, email_confirmed=True, **flags):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="pw-12345",
        )
        user.email_confirmed = email_confirmed
        user.accepted_submissions_count = accepted
        for flag, val in flags.items():
            setattr(user, flag, val)
        user.save()
        return user

    def _make_collaborator(self, username):
        """A user who clears the (threshold=1) Collaborator promotion."""
        user = self._make_user(username, accepted=1)
        assert user.level == Level.COLLABORATOR, user.level
        return user

    def _make_brand(self, name, *, submitted_by=None, status=None):
        return Brand.objects.create(
            name=name,
            submitted_by=submitted_by,
            status=status or PublicationStatus.UNDER_REVISION,
        )


# ---------------------------------------------------------------------------
# cast_review_vote — permissions
# ---------------------------------------------------------------------------
class CastReviewVotePermissionTests(CollabServiceTestBase):
    def test_anonymous_regular_user_cannot_vote(self):
        regular = self._make_user("regular", accepted=0)
        self.assertEqual(regular.level, Level.REGULAR)
        with self.assertRaises(PermissionError):
            cast_review_vote(regular, self.brand, VoteValue.UP)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_collaborator_may_vote(self):
        vote = cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        self.assertIsNotNone(vote)
        self.assertEqual(vote.value, VoteValue.UP)
        self.assertEqual(vote.voter, self.collab1)

    def test_founder_may_vote(self):
        founder = self._make_user("founder", is_founder=True)
        self.assertEqual(founder.level, Level.FOUNDER)
        vote = cast_review_vote(founder, self.brand, VoteValue.DOWN)
        self.assertIsNotNone(vote)
        self.assertEqual(vote.value, VoteValue.DOWN)

    def test_moderator_and_creator_may_vote(self):
        moderator = self._make_user("mod", is_community_moderator=True)
        creator = self._make_user("creator", is_riffhub_creator=True)
        self.assertEqual(moderator.level, Level.MODERATOR)
        self.assertEqual(creator.level, Level.CREATOR)
        self.assertIsNotNone(cast_review_vote(moderator, self.brand, VoteValue.UP))
        self.assertIsNotNone(cast_review_vote(creator, self.brand, VoteValue.UP))

    def test_submitter_cannot_vote_on_own_submission(self):
        # The submitter is even *promoted* to Collaborator first, to prove the
        # self-vote guard is independent of the level check.
        self.submitter.accepted_submissions_count = 1
        self.submitter.save(update_fields=["accepted_submissions_count"])
        self.assertEqual(self.submitter.level, Level.COLLABORATOR)
        with self.assertRaises(PermissionError):
            cast_review_vote(self.submitter, self.brand, VoteValue.UP)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_self_vote_guard_uses_submitted_by_id(self):
        # A brand with no submitter must not be mistaken for "owned" by a voter
        # whose pk happens to be falsy-adjacent; collaborators can vote freely.
        orphan = self._make_brand("Orphan Brand", submitted_by=None)
        vote = cast_review_vote(self.collab1, orphan, VoteValue.UP)
        self.assertIsNotNone(vote)


# ---------------------------------------------------------------------------
# cast_review_vote — value, toggle and switch semantics
# ---------------------------------------------------------------------------
class CastReviewVoteToggleTests(CollabServiceTestBase):
    def test_first_upvote_is_created_with_plus_one(self):
        vote = cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        self.assertEqual(vote.value, 1)
        self.assertEqual(ReviewVote.objects.count(), 1)

    def test_first_downvote_is_created_with_minus_one(self):
        vote = cast_review_vote(self.collab1, self.brand, VoteValue.DOWN)
        self.assertEqual(vote.value, -1)
        self.assertEqual(ReviewVote.objects.count(), 1)

    def test_recasting_same_value_toggles_vote_off(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        result = cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        self.assertIsNone(result)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_recasting_same_downvote_toggles_off(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.DOWN)
        result = cast_review_vote(self.collab1, self.brand, VoteValue.DOWN)
        self.assertIsNone(result)
        self.assertEqual(ReviewVote.objects.count(), 0)

    def test_opposite_value_switches_vote_in_place(self):
        first = cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        switched = cast_review_vote(self.collab1, self.brand, VoteValue.DOWN)
        self.assertIsNotNone(switched)
        # Same row, value flipped — not a new vote.
        self.assertEqual(switched.pk, first.pk)
        self.assertEqual(switched.value, -1)
        self.assertEqual(ReviewVote.objects.count(), 1)

    def test_switch_then_switch_back(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab1, self.brand, VoteValue.DOWN)
        back = cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        self.assertEqual(back.value, 1)
        self.assertEqual(ReviewVote.objects.count(), 1)

    def test_toggle_off_then_recreate(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)  # off
        again = cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        self.assertIsNotNone(again)
        self.assertEqual(again.value, 1)
        self.assertEqual(ReviewVote.objects.count(), 1)

    def test_raw_integer_values_are_accepted(self):
        # The service coerces via VoteValue(value); plain ints must work.
        up = cast_review_vote(self.collab1, self.brand, 1)
        self.assertEqual(up.value, 1)
        down = cast_review_vote(self.collab2, self.brand, -1)
        self.assertEqual(down.value, -1)

    def test_invalid_vote_value_raises(self):
        with self.assertRaises(ValueError):
            cast_review_vote(self.collab1, self.brand, 0)
        with self.assertRaises(ValueError):
            cast_review_vote(self.collab1, self.brand, 5)

    def test_votes_are_per_target(self):
        other = self._make_brand("Other Brand", submitted_by=self.submitter)
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab1, other, VoteValue.DOWN)
        # Two distinct rows, one per target.
        self.assertEqual(ReviewVote.objects.count(), 2)
        self.assertEqual(ReviewVote.net_votes(self.brand), 1)
        self.assertEqual(ReviewVote.net_votes(other), -1)


# ---------------------------------------------------------------------------
# ReviewVote tally helpers (net_votes / voter_count)
# ---------------------------------------------------------------------------
class ReviewVoteTallyTests(CollabServiceTestBase):
    def test_net_votes_zero_with_no_votes(self):
        self.assertEqual(ReviewVote.net_votes(self.brand), 0)

    def test_voter_count_zero_with_no_votes(self):
        self.assertEqual(ReviewVote.voter_count(self.brand), 0)

    def test_net_votes_sums_up_and_down(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        cast_review_vote(self.collab3, self.brand, VoteValue.DOWN)
        # +1 +1 -1 = 1
        self.assertEqual(ReviewVote.net_votes(self.brand), 1)

    def test_net_votes_can_be_negative(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.DOWN)
        cast_review_vote(self.collab2, self.brand, VoteValue.DOWN)
        self.assertEqual(ReviewVote.net_votes(self.brand), -2)

    def test_voter_count_counts_distinct_voters(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        cast_review_vote(self.collab3, self.brand, VoteValue.DOWN)
        self.assertEqual(ReviewVote.voter_count(self.brand), 3)

    def test_toggled_off_vote_drops_from_tallies(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)  # toggle off
        self.assertEqual(ReviewVote.net_votes(self.brand), 1)
        self.assertEqual(ReviewVote.voter_count(self.brand), 1)

    def test_switched_vote_updates_net_but_not_count(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        self.assertEqual(ReviewVote.net_votes(self.brand), 2)
        self.assertEqual(ReviewVote.voter_count(self.brand), 2)
        # collab2 switches up -> down: net 2 -> 0, still 2 distinct voters.
        cast_review_vote(self.collab2, self.brand, VoteValue.DOWN)
        self.assertEqual(ReviewVote.net_votes(self.brand), 0)
        self.assertEqual(ReviewVote.voter_count(self.brand), 2)


# ---------------------------------------------------------------------------
# evaluate_submission
# ---------------------------------------------------------------------------
class EvaluateSubmissionTests(CollabServiceTestBase):
    def _three_upvotes(self, target):
        cast_review_vote(self.collab1, target, VoteValue.UP)
        cast_review_vote(self.collab2, target, VoteValue.UP)
        cast_review_vote(self.collab3, target, VoteValue.UP)

    def test_publishes_when_both_floors_met(self):
        self._three_upvotes(self.brand)  # net 3, voters 3 -> exactly the bar
        self.assertTrue(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.PUBLISHED)
        self.assertIsNotNone(self.brand.published_at)
        self.assertIsNotNone(self.brand.reviewed_at)

    def test_false_when_net_below_bar(self):
        # 3 distinct voters (voter floor met) but +1+1-1 = net 1 < 3.
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        cast_review_vote(self.collab3, self.brand, VoteValue.DOWN)
        self.assertFalse(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.UNDER_REVISION)
        self.assertIsNone(self.brand.published_at)

    def test_false_when_voters_below_bar(self):
        # Net is high (a single voter cannot reach net 3 here since one voter =
        # at most +1), so use the dedicated low-voter config instead.
        self.config.gear_acceptance_min_net_votes = 2
        self.config.gear_acceptance_min_voters = 3
        self.config.save()
        # 2 voters, net +2: net floor met, voter floor (3) NOT met.
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        self.assertEqual(ReviewVote.net_votes(self.brand), 2)
        self.assertEqual(ReviewVote.voter_count(self.brand), 2)
        self.assertFalse(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.UNDER_REVISION)

    def test_false_with_no_votes(self):
        self.assertFalse(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.UNDER_REVISION)

    def test_single_enthusiastic_voter_cannot_wave_through(self):
        # Lower the net bar to 1 but keep voter floor at 3: one upvote clears
        # net but must still be blocked by the distinct-voter requirement.
        self.config.gear_acceptance_min_net_votes = 1
        self.config.gear_acceptance_min_voters = 3
        self.config.save()
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        self.assertFalse(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.UNDER_REVISION)

    def test_accepting_credits_submitter_reputation(self):
        rep_before = self.submitter.reputation_score
        self._three_upvotes(self.brand)
        self.assertTrue(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        self.assertEqual(
            self.submitter.reputation_score, rep_before + REP_ACCEPTED_SUBMISSION
        )

    def test_accepting_increments_accepted_count(self):
        # submitter starts at 0 accepted, with no published brands of its own.
        self.assertEqual(self.submitter.accepted_submissions_count, 0)
        self._three_upvotes(self.brand)
        self.assertTrue(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        # recompute_standing re-derives from the catalog (now 1 published
        # brand) and is consistent with the manual +1 increment.
        self.assertEqual(self.submitter.accepted_submissions_count, 1)

    def test_accepting_recomputes_standing_and_can_promote(self):
        # threshold is 1: a fresh user promotes to Collaborator once this single
        # submission is accepted, proving recompute_standing ran.
        rookie = self._make_user("rookie", accepted=0)
        self.assertEqual(rookie.level, Level.REGULAR)
        rookie_brand = self._make_brand("Rookie Brand", submitted_by=rookie)
        self._three_upvotes(rookie_brand)
        self.assertTrue(evaluate_submission(rookie_brand))
        rookie.refresh_from_db()
        self.assertEqual(rookie.accepted_submissions_count, 1)
        self.assertEqual(rookie.level, Level.COLLABORATOR)

    def test_accepting_awards_sticky_founder_badge_at_threshold(self):
        # Founder threshold of 1 + this being the user's 1st published entry
        # should flip the sticky is_founder flag inside recompute_standing.
        self.config.founder_threshold = 1
        self.config.save()
        contributor = self._make_user("almost_founder", accepted=0)
        self.assertFalse(contributor.is_founder)
        their_brand = self._make_brand("Founder Brand", submitted_by=contributor)
        self._three_upvotes(their_brand)
        self.assertTrue(evaluate_submission(their_brand))
        contributor.refresh_from_db()
        self.assertTrue(contributor.is_founder)
        self.assertEqual(contributor.level, Level.FOUNDER)

    def test_below_bar_does_not_touch_submitter(self):
        rep_before = self.submitter.reputation_score
        count_before = self.submitter.accepted_submissions_count
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)  # net 1, < 3
        self.assertFalse(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        self.assertEqual(self.submitter.reputation_score, rep_before)
        self.assertEqual(self.submitter.accepted_submissions_count, count_before)

    def test_accepting_entry_with_no_submitter_does_not_crash(self):
        orphan = self._make_brand("Ownerless Brand", submitted_by=None)
        self._three_upvotes(orphan)
        # submitted_by is None: must publish without trying to credit anyone.
        self.assertTrue(evaluate_submission(orphan))
        orphan.refresh_from_db()
        self.assertEqual(orphan.status, PublicationStatus.PUBLISHED)

    def test_higher_net_than_minimum_still_publishes(self):
        cast_review_vote(self.collab1, self.brand, VoteValue.UP)
        cast_review_vote(self.collab2, self.brand, VoteValue.UP)
        cast_review_vote(self.collab3, self.brand, VoteValue.UP)
        cast_review_vote(self.collab4, self.brand, VoteValue.UP)  # net 4, voters 4
        self.assertTrue(evaluate_submission(self.brand))
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.PUBLISHED)

    def test_evaluate_is_idempotent_when_re_run(self):
        # Once an entry is published, re-running evaluate must be a true no-op:
        # it does not re-publish or re-award reputation. This guards against
        # reputation farming via repeated/toggled votes on an already-published
        # entry (only an UNDER_REVISION entry is ever evaluated).
        self._three_upvotes(self.brand)
        self.assertTrue(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        rep_after_first = self.submitter.reputation_score
        # Second evaluation on the already-published entry changes nothing.
        self.assertFalse(evaluate_submission(self.brand))
        self.submitter.refresh_from_db()
        self.assertEqual(self.submitter.accepted_submissions_count, 1)
        self.assertEqual(self.submitter.reputation_score, rep_after_first)


# ---------------------------------------------------------------------------
# reject_submission
# ---------------------------------------------------------------------------
class RejectSubmissionTests(CollabServiceTestBase):
    def test_marks_entry_rejected(self):
        reject_submission(self.brand)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.REJECTED)

    def test_sets_reviewed_at(self):
        self.assertIsNone(self.brand.reviewed_at)
        reject_submission(self.brand)
        self.brand.refresh_from_db()
        self.assertIsNotNone(self.brand.reviewed_at)

    def test_increments_submitter_reject_counter(self):
        self.assertEqual(self.submitter.rejected_submissions_count, 0)
        reject_submission(self.brand)
        self.submitter.refresh_from_db()
        self.assertEqual(self.submitter.rejected_submissions_count, 1)

    def test_repeated_rejections_accumulate(self):
        b1 = self._make_brand("Reject 1", submitted_by=self.submitter)
        b2 = self._make_brand("Reject 2", submitted_by=self.submitter)
        reject_submission(b1)
        reject_submission(b2)
        self.submitter.refresh_from_db()
        self.assertEqual(self.submitter.rejected_submissions_count, 2)

    def test_does_not_award_reputation(self):
        rep_before = self.submitter.reputation_score
        reject_submission(self.brand)
        self.submitter.refresh_from_db()
        self.assertEqual(self.submitter.reputation_score, rep_before)

    def test_does_not_set_published_at(self):
        reject_submission(self.brand)
        self.brand.refresh_from_db()
        self.assertIsNone(self.brand.published_at)

    def test_reject_with_no_submitter_does_not_crash(self):
        orphan = self._make_brand("Ownerless", submitted_by=None)
        reject_submission(orphan)
        orphan.refresh_from_db()
        self.assertEqual(orphan.status, PublicationStatus.REJECTED)

    def test_reject_accepts_optional_by_argument(self):
        # The signature is reject_submission(entry, *, by=None); passing a
        # moderator must be accepted even though it is currently unused.
        moderator = self._make_user("mod", is_community_moderator=True)
        reject_submission(self.brand, by=moderator)
        self.brand.refresh_from_db()
        self.assertEqual(self.brand.status, PublicationStatus.REJECTED)

    def test_reject_recomputes_standing(self):
        # A rejection recomputes accepted count from the catalog. Give the
        # submitter a stale (too-high) count and a separate published brand;
        # after rejecting an unrelated entry the count is corrected to reality.
        self._make_brand(
            "Already Published",
            submitted_by=self.submitter,
            status=PublicationStatus.PUBLISHED,
        )
        self.submitter.accepted_submissions_count = 99  # stale
        self.submitter.save(update_fields=["accepted_submissions_count"])
        reject_submission(self.brand)
        self.submitter.refresh_from_db()
        # Truth = exactly the one published brand.
        self.assertEqual(self.submitter.accepted_submissions_count, 1)


# ---------------------------------------------------------------------------
# can_submit_to_collab
# ---------------------------------------------------------------------------
class CanSubmitToCollabTests(CollabServiceTestBase):
    def test_unconfirmed_email_blocks(self):
        user = self._make_user("unconfirmed", email_confirmed=False)
        self.assertFalse(can_submit_to_collab(user))

    def test_confirmed_email_with_no_rejections_allows(self):
        user = self._make_user("clean", email_confirmed=True)
        self.assertEqual(user.rejected_submissions_count, 0)
        self.assertTrue(can_submit_to_collab(user))

    def test_blocked_after_exceeding_max_rejected(self):
        # max_rejected_before_cooldown == 3; the guard blocks only when the
        # count is strictly greater, so 4 blocks.
        user = self._make_user("troll", email_confirmed=True)
        user.rejected_submissions_count = self.MAX_REJECTED + 1
        user.save(update_fields=["rejected_submissions_count"])
        self.assertFalse(can_submit_to_collab(user))

    def test_allowed_exactly_at_max_rejected_boundary(self):
        # Strictly-greater comparison: a count EQUAL to the max is still OK.
        user = self._make_user("borderline", email_confirmed=True)
        user.rejected_submissions_count = self.MAX_REJECTED
        user.save(update_fields=["rejected_submissions_count"])
        self.assertTrue(can_submit_to_collab(user))

    def test_unconfirmed_email_blocks_even_with_zero_rejections(self):
        user = self._make_user("new", email_confirmed=False)
        self.assertEqual(user.rejected_submissions_count, 0)
        self.assertFalse(can_submit_to_collab(user))

    def test_unconfirmed_email_takes_precedence_over_reject_count(self):
        # Even a clean reject count cannot save an unconfirmed e-mail.
        user = self._make_user("pending", email_confirmed=False)
        user.rejected_submissions_count = 0
        user.save(update_fields=["rejected_submissions_count"])
        self.assertFalse(can_submit_to_collab(user))

    def test_cooldown_threshold_is_read_from_config(self):
        # Raising the cooldown ceiling re-admits a previously-blocked user.
        user = self._make_user("rehab", email_confirmed=True)
        user.rejected_submissions_count = 4
        user.save(update_fields=["rejected_submissions_count"])
        self.assertFalse(can_submit_to_collab(user))
        self.config.max_rejected_before_cooldown = 10
        self.config.save()
        self.assertTrue(can_submit_to_collab(user))
