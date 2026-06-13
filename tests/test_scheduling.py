"""Tests for scheduled evaluation of proposals and submissions.

Contract (built in parallel):
  forum.services.sweep_due_proposals()        -> {"evaluated","accepted","rejected"}
  catalog.services.sweep_pending_submissions() -> {"evaluated","published"}
  management command `evaluate_pending` runs both and prints a summary.
"""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from catalog.constants import PublicationStatus, VoteValue
from catalog.models import Brand, GuitarModel
from catalog.services import cast_review_vote, sweep_pending_submissions
from core.models import SiteConfiguration
from forum.constants import ProposalStatus, ProposalVoteValue
from forum.models import (
    ProposalVote,
    SubtopicProposal,
    Subtopic,
    Topic,
    TopicProposal,
)
from forum.services import sweep_due_proposals

User = get_user_model()


def make_user(name):
    return User.objects.create_user(name, f"{name}@x.com", "pw12345!")


def past():
    return timezone.now() - timedelta(days=1)


def future():
    return timezone.now() + timedelta(days=1)


def vote_topic(proposal, voters, value):
    for v in voters:
        ProposalVote.objects.create(voter=v, topic_proposal=proposal, value=value)


# ---------------------------------------------------------------------------
# Proposal sweep
# ---------------------------------------------------------------------------
class SweepDueProposalsTests(TestCase):
    def setUp(self):
        self.proposer = make_user("prop")
        self.voters = [make_user(f"v{i}") for i in range(3)]

    def test_due_passing_proposal_accepted_and_materialised(self):
        p = TopicProposal.objects.create(
            proposer=self.proposer, proposed_name="Pedals", closes_at=past()
        )
        vote_topic(p, self.voters, ProposalVoteValue.UP)  # 100% yes >= 0.75
        result = sweep_due_proposals()
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.ACCEPTED)
        self.assertTrue(Topic.objects.filter(name="Pedals").exists())
        self.assertEqual(result["accepted"], 1)

    def test_due_failing_proposal_rejected(self):
        p = TopicProposal.objects.create(
            proposer=self.proposer, proposed_name="Spam", closes_at=past()
        )
        vote_topic(p, self.voters[:1], ProposalVoteValue.UP)
        vote_topic(p, self.voters[1:], ProposalVoteValue.DOWN)  # 33% yes < 0.75
        result = sweep_due_proposals()
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.REJECTED)
        self.assertFalse(Topic.objects.filter(name="Spam").exists())
        self.assertEqual(result["rejected"], 1)

    def test_open_but_not_due_is_untouched(self):
        p = TopicProposal.objects.create(
            proposer=self.proposer, proposed_name="Later", closes_at=future()
        )
        vote_topic(p, self.voters, ProposalVoteValue.UP)
        result = sweep_due_proposals()
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.OPEN)
        self.assertEqual(result["evaluated"], 0)

    def test_subtopic_proposal_swept_and_materialised(self):
        topic = Topic.objects.create(name="Gear")
        p = SubtopicProposal.objects.create(
            proposer=self.proposer, parent_topic=topic,
            proposed_name="Cables", closes_at=past(),
        )
        for v in self.voters:
            ProposalVote.objects.create(voter=v, subtopic_proposal=p, value=ProposalVoteValue.UP)
        sweep_due_proposals()
        p.refresh_from_db()
        self.assertEqual(p.status, ProposalStatus.ACCEPTED)
        self.assertTrue(Subtopic.objects.filter(topic=topic, name="Cables").exists())

    def test_counts_across_a_pass_and_a_fail(self):
        ok = TopicProposal.objects.create(
            proposer=self.proposer, proposed_name="Yes", closes_at=past()
        )
        no = TopicProposal.objects.create(
            proposer=self.proposer, proposed_name="No", closes_at=past()
        )
        vote_topic(ok, self.voters, ProposalVoteValue.UP)
        vote_topic(no, self.voters, ProposalVoteValue.DOWN)
        result = sweep_due_proposals()
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rejected"], 1)


# ---------------------------------------------------------------------------
# Submission sweep
# ---------------------------------------------------------------------------
class SweepPendingSubmissionsTests(TestCase):
    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()
        self.submitter = make_user("submitter")
        self.brand = Brand.objects.create(name="Acme", status=PublicationStatus.PUBLISHED)
        self.collabs = []
        for i in range(3):
            c = make_user(f"collab{i}")
            c.accepted_submissions_count = 3
            c.save(update_fields=["accepted_submissions_count"])
            self.collabs.append(c)

    def _guitar(self, name):
        return GuitarModel.objects.create(
            brand=self.brand, name=name, num_strings=6,
            scale_length_min_inches="25.5", scale_length_max_inches="25.5",
            submitted_by=self.submitter,
        )

    def test_qualifying_submission_published(self):
        g = self._guitar("Qualifies")
        for c in self.collabs:
            cast_review_vote(c, g, VoteValue.UP)
        result = sweep_pending_submissions()
        g.refresh_from_db()
        self.assertEqual(g.status, PublicationStatus.PUBLISHED)
        self.assertEqual(result["published"], 1)

    def test_non_qualifying_stays_under_revision(self):
        g = self._guitar("NoVotes")
        sweep_pending_submissions()
        g.refresh_from_db()
        self.assertEqual(g.status, PublicationStatus.UNDER_REVISION)

    def test_counts(self):
        good = self._guitar("Good")
        self._guitar("Meh")  # no votes -> not published
        for c in self.collabs:
            cast_review_vote(c, good, VoteValue.UP)
        result = sweep_pending_submissions()
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(result["published"], 1)


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------
class EvaluatePendingCommandTests(TestCase):
    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def test_command_resolves_proposal_and_publishes_submission(self):
        proposer = make_user("prop")
        voters = [make_user(f"v{i}") for i in range(3)]
        prop = TopicProposal.objects.create(
            proposer=proposer, proposed_name="Effects", closes_at=past()
        )
        vote_topic(prop, voters, ProposalVoteValue.UP)

        submitter = make_user("sub")
        brand = Brand.objects.create(name="Acme", status=PublicationStatus.PUBLISHED)
        guitar = GuitarModel.objects.create(
            brand=brand, name="CmdPub", num_strings=6,
            scale_length_min_inches="25.5", scale_length_max_inches="25.5",
            submitted_by=submitter,
        )
        for i in range(3):
            c = make_user(f"c{i}")
            c.accepted_submissions_count = 3
            c.save(update_fields=["accepted_submissions_count"])
            cast_review_vote(c, guitar, VoteValue.UP)

        out = StringIO()
        call_command("evaluate_pending", stdout=out)

        prop.refresh_from_db()
        guitar.refresh_from_db()
        self.assertEqual(prop.status, ProposalStatus.ACCEPTED)
        self.assertEqual(guitar.status, PublicationStatus.PUBLISHED)
        self.assertIn("evaluated", out.getvalue().lower())
