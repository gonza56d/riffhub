"""Tests for the Gear Market and community topic/subtopic proposals.

Area under test (PRODUCT.md "Selling stuff" + "Notes on topics and subtopics"):

* ``forum.models.Post.clean`` — a price is REQUIRED in a market subtopic and
  FORBIDDEN everywhere else (the market/price coupling).
* The Gear Market disclaimer flow: ``/forum/s/<pk>/post/`` is blocked until the
  user has accepted the disclaimer; ``accept_market_disclaimer`` /
  ``has_accepted_market_disclaimer`` record/report acceptance; once accepted the
  posted price is captured on the listing.
* Community proposals:
    - ``open_topic_proposal`` / ``open_subtopic_proposal`` require Collaborator+
      and respect ``topic_proposals_enabled``;
    - ``cast_proposal_vote`` — any authenticated user, one vote per voter,
      re-voting updates the value;
    - ``evaluate_proposal`` — after the window, accept on >= pass_ratio and
      materialise the real Topic/Subtopic; zero votes never passes.

Service rules are exercised directly; the disclaimer/price gate is also driven
through the HTTP view with ``django.test.Client``.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Level
from core.models import SiteConfiguration
from forum import services
from forum.constants import (
    DEFAULT_CURRENCY,
    ProposalStatus,
    ProposalVoteValue,
)
from forum.models import (
    MarketDisclaimerAcceptance,
    Post,
    ProposalVote,
    Subtopic,
    SubtopicProposal,
    Topic,
    TopicProposal,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_user(username, **kwargs):
    """Create a confirmed user; overridable role flags / counters."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "irrelevant-pw",
        "email_confirmed": True,
    }
    defaults.update(kwargs)
    return User.objects.create_user(username=username, **defaults)


def configure_proposals(
    *,
    enabled=True,
    voting_days=7,
    pass_ratio="0.750",
    collaborator_threshold=3,
    founder_threshold=30,
):
    """Set the proposal-related SiteConfiguration knobs and return the singleton."""
    config = SiteConfiguration.get_solo()
    config.topic_proposals_enabled = enabled
    config.topic_proposal_voting_days = voting_days
    config.topic_proposal_pass_ratio = Decimal(pass_ratio)
    config.collaborator_promotion_threshold = collaborator_threshold
    config.founder_threshold = founder_threshold
    config.save()
    return config


# ===========================================================================
# Post.clean — market/price coupling
# ===========================================================================
class PostCleanPriceCouplingTests(TestCase):
    """PRODUCT.md "Selling stuff": price is unlocked ONLY in the Gear Market.

    A price is required for a market listing and forbidden in every ordinary
    (non-market) subtopic. Enforced in ``Post.clean``.
    """

    def setUp(self):
        self.author = make_user("seller")

        self.market_topic = Topic.objects.create(
            name="Gear Market",
            is_market=True,
            requires_disclaimer=True,
            is_predefined=True,
        )
        self.market_sub = Subtopic.objects.create(
            topic=self.market_topic, name="Guitars"
        )

        self.regular_topic = Topic.objects.create(name="Gear")
        self.regular_sub = Subtopic.objects.create(
            topic=self.regular_topic, name="Guitars"
        )

    # --- market subtopic: price required ----------------------------------
    def test_market_post_without_price_is_invalid(self):
        post = Post(
            subtopic=self.market_sub,
            author=self.author,
            title="Selling my Strat",
            body="Mint condition.",
            price=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            post.full_clean()
        self.assertIn("price", ctx.exception.error_dict)

    def test_market_post_with_price_is_valid(self):
        post = Post(
            subtopic=self.market_sub,
            author=self.author,
            title="Selling my Strat",
            body="Mint condition.",
            price=Decimal("1200.00"),
            currency="USD",
        )
        # Should not raise.
        post.full_clean()

    def test_market_post_with_zero_price_is_allowed(self):
        # Zero is a real price (giveaway / placeholder); only None is rejected.
        post = Post(
            subtopic=self.market_sub,
            author=self.author,
            title="Free pick",
            body="Take it.",
            price=Decimal("0.00"),
        )
        post.full_clean()

    # --- non-market subtopic: price forbidden -----------------------------
    def test_non_market_post_with_price_is_invalid(self):
        post = Post(
            subtopic=self.regular_sub,
            author=self.author,
            title="Jackson vs Ibanez",
            body="Discuss.",
            price=Decimal("100.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            post.full_clean()
        self.assertIn("price", ctx.exception.error_dict)

    def test_non_market_post_without_price_is_valid(self):
        post = Post(
            subtopic=self.regular_sub,
            author=self.author,
            title="Jackson vs Ibanez",
            body="Discuss.",
        )
        post.full_clean()

    # --- via the service (full_clean runs inside create_post) -------------
    def test_create_post_in_market_requires_price(self):
        with self.assertRaises(ValidationError):
            services.create_post(
                subtopic=self.market_sub,
                author=self.author,
                title="No price listing",
                body="oops",
            )

    def test_create_post_in_market_captures_price(self):
        post = services.create_post(
            subtopic=self.market_sub,
            author=self.author,
            title="Selling a bass",
            body="Great shape.",
            price=Decimal("799.99"),
            currency="EUR",
        )
        post.refresh_from_db()
        self.assertEqual(post.price, Decimal("799.99"))
        self.assertEqual(post.currency, "EUR")

    def test_create_post_non_market_with_price_rejected(self):
        with self.assertRaises(ValidationError):
            services.create_post(
                subtopic=self.regular_sub,
                author=self.author,
                title="Has a price but shouldn't",
                body="nope",
                price=Decimal("10.00"),
            )


# ===========================================================================
# Disclaimer service helpers
# ===========================================================================
class MarketDisclaimerServiceTests(TestCase):
    """accept_market_disclaimer / has_accepted_market_disclaimer behaviour."""

    def setUp(self):
        self.user = make_user("buyer")

    def test_has_not_accepted_by_default(self):
        self.assertFalse(services.has_accepted_market_disclaimer(self.user))

    def test_accept_records_acceptance(self):
        acceptance = services.accept_market_disclaimer(self.user)
        self.assertIsInstance(acceptance, MarketDisclaimerAcceptance)
        self.assertTrue(services.has_accepted_market_disclaimer(self.user))
        self.assertEqual(
            MarketDisclaimerAcceptance.objects.filter(user=self.user).count(), 1
        )

    def test_accept_is_idempotent(self):
        first = services.accept_market_disclaimer(self.user)
        second = services.accept_market_disclaimer(self.user)
        # get_or_create -> same row, never a duplicate (OneToOne anyway).
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            MarketDisclaimerAcceptance.objects.filter(user=self.user).count(), 1
        )

    def test_acceptance_is_per_user(self):
        other = make_user("otherbuyer")
        services.accept_market_disclaimer(self.user)
        self.assertTrue(services.has_accepted_market_disclaimer(self.user))
        self.assertFalse(services.has_accepted_market_disclaimer(other))

    def test_anonymous_has_not_accepted(self):
        class _Anon:
            is_authenticated = False

        self.assertFalse(services.has_accepted_market_disclaimer(_Anon()))


# ===========================================================================
# HTTP view: /forum/s/<pk>/post/ disclaimer gate + price capture
# ===========================================================================
class MarketPostViewDisclaimerGateTests(TestCase):
    """The market post endpoint is blocked until the disclaimer is accepted,
    then captures the price (PRODUCT.md).
    """

    def setUp(self):
        self.user = make_user("listing-user")
        self.market_topic = Topic.objects.create(
            name="Gear Market",
            is_market=True,
            requires_disclaimer=True,
            is_predefined=True,
        )
        self.market_sub = Subtopic.objects.create(
            topic=self.market_topic, name="Guitars"
        )
        self.post_url = reverse("forum:post_create", args=[self.market_sub.pk])
        self.disclaimer_url = reverse(
            "forum:accept_disclaimer", args=[self.market_sub.pk]
        )
        self.subtopic_url = reverse("forum:subtopic", args=[self.market_sub.pk])

    def test_post_blocked_before_disclaimer_accepted(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            self.post_url,
            {"title": "Selling", "body": "stuff", "price": "500", "currency": "USD"},
        )
        # Redirects back to the subtopic with an error; no post created.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], self.subtopic_url)
        self.assertFalse(Post.objects.exists())
        self.assertFalse(services.has_accepted_market_disclaimer(self.user))

    def test_accept_disclaimer_endpoint_records_acceptance(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.disclaimer_url)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(services.has_accepted_market_disclaimer(self.user))

    def test_post_allowed_and_price_captured_after_acceptance(self):
        self.client.force_login(self.user)
        services.accept_market_disclaimer(self.user)
        resp = self.client.post(
            self.post_url,
            {
                "title": "Selling my LP",
                "body": "Cherry burst.",
                "price": "1499.50",
                "currency": "USD",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Post.objects.count(), 1)
        post = Post.objects.get()
        self.assertEqual(post.title, "Selling my LP")
        self.assertEqual(post.price, Decimal("1499.50"))
        self.assertEqual(post.currency, "USD")
        # Redirects to the freshly-created post.
        self.assertEqual(resp.headers["Location"], reverse("forum:post", args=[post.pk]))

    def test_market_post_without_price_is_rejected_by_view(self):
        # Disclaimer accepted, but no price -> Post.clean rejects via the view's
        # ValidationError branch; redirect back, no post created.
        self.client.force_login(self.user)
        services.accept_market_disclaimer(self.user)
        resp = self.client.post(
            self.post_url,
            {"title": "No price", "body": "oops", "currency": "USD"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], self.subtopic_url)
        self.assertFalse(Post.objects.exists())

    def test_market_post_defaults_currency_when_omitted(self):
        self.client.force_login(self.user)
        services.accept_market_disclaimer(self.user)
        resp = self.client.post(
            self.post_url,
            {"title": "Defaulted currency", "body": "body", "price": "10"},
        )
        self.assertEqual(resp.status_code, 302)
        post = Post.objects.get()
        self.assertEqual(post.currency, DEFAULT_CURRENCY)

    def test_anonymous_post_redirects_to_login(self):
        resp = self.client.post(
            self.post_url,
            {"title": "x", "body": "y", "price": "1"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.headers["Location"])
        self.assertFalse(Post.objects.exists())


class NonMarketPostViewTests(TestCase):
    """A non-market subtopic post needs no disclaimer and carries no price."""

    def setUp(self):
        self.user = make_user("poster")
        self.topic = Topic.objects.create(name="Gear")
        self.sub = Subtopic.objects.create(topic=self.topic, name="Guitars")

    def test_non_market_post_created_without_disclaimer_or_price(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("forum:post_create", args=[self.sub.pk]),
            {"title": "Dinky vs RG", "body": "Discuss away."},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Post.objects.count(), 1)
        post = Post.objects.get()
        self.assertIsNone(post.price)
        self.assertEqual(resp.headers["Location"], reverse("forum:post", args=[post.pk]))


# ===========================================================================
# Proposals: who may propose (Collaborator+ and feature toggle)
# ===========================================================================
class OpenProposalPermissionTests(TestCase):
    """PRODUCT.md: ONLY Database Collaborators or higher may PROPOSE new
    topics/subtopics, and the feature can be disabled by a Creator.
    """

    def setUp(self):
        # Collaborator threshold = 3, proposals enabled.
        configure_proposals(enabled=True, collaborator_threshold=3)
        self.parent_topic = Topic.objects.create(name="Gear")

    def test_regular_user_cannot_open_topic_proposal(self):
        regular = make_user("reg", accepted_submissions_count=0)
        self.assertEqual(regular.level, Level.REGULAR)
        with self.assertRaises(PermissionDenied):
            services.open_topic_proposal(regular, name="Pedals")
        self.assertEqual(TopicProposal.objects.count(), 0)

    def test_regular_user_cannot_open_subtopic_proposal(self):
        regular = make_user("reg2")
        with self.assertRaises(PermissionDenied):
            services.open_subtopic_proposal(
                regular, parent_topic=self.parent_topic, name="Acoustic"
            )
        self.assertEqual(SubtopicProposal.objects.count(), 0)

    def test_collaborator_can_open_topic_proposal(self):
        collab = make_user("collab", accepted_submissions_count=3)
        self.assertEqual(collab.level, Level.COLLABORATOR)
        proposal = services.open_topic_proposal(
            collab, name="Pedals", description="Stompboxes"
        )
        self.assertEqual(proposal.proposer, collab)
        self.assertEqual(proposal.proposed_name, "Pedals")
        self.assertEqual(proposal.proposed_description, "Stompboxes")
        self.assertEqual(proposal.status, ProposalStatus.OPEN)

    def test_collaborator_can_open_subtopic_proposal(self):
        collab = make_user("collab2", accepted_submissions_count=3)
        proposal = services.open_subtopic_proposal(
            collab, parent_topic=self.parent_topic, name="Acoustic"
        )
        self.assertEqual(proposal.parent_topic, self.parent_topic)
        self.assertEqual(proposal.proposed_name, "Acoustic")
        self.assertEqual(proposal.status, ProposalStatus.OPEN)

    def test_founder_can_open_proposal(self):
        founder = make_user("founder", is_founder=True)
        proposal = services.open_topic_proposal(founder, name="Recording")
        self.assertEqual(proposal.status, ProposalStatus.OPEN)

    def test_moderator_can_open_proposal(self):
        mod = make_user("mod", is_community_moderator=True)
        proposal = services.open_topic_proposal(mod, name="Lessons")
        self.assertEqual(proposal.status, ProposalStatus.OPEN)

    def test_anonymous_cannot_open_proposal(self):
        class _Anon:
            is_authenticated = False

        with self.assertRaises(PermissionDenied):
            services.open_topic_proposal(_Anon(), name="Nope")

    def test_proposal_window_set_from_config(self):
        configure_proposals(enabled=True, voting_days=7, collaborator_threshold=3)
        collab = make_user("windowed", accepted_submissions_count=3)
        before = timezone.now()
        proposal = services.open_topic_proposal(collab, name="Windowed")
        after = timezone.now()
        # closes_at ~= now + 7 days.
        self.assertGreaterEqual(proposal.closes_at, before + timedelta(days=7))
        self.assertLessEqual(proposal.closes_at, after + timedelta(days=7))


class ProposalsDisabledTests(TestCase):
    """When a Creator disables proposals, even Collaborators cannot open one."""

    def setUp(self):
        configure_proposals(enabled=False, collaborator_threshold=3)
        self.parent_topic = Topic.objects.create(name="Gear")

    def test_collaborator_cannot_open_topic_proposal_when_disabled(self):
        collab = make_user("collab", accepted_submissions_count=3)
        with self.assertRaises(PermissionDenied):
            services.open_topic_proposal(collab, name="Blocked")
        self.assertEqual(TopicProposal.objects.count(), 0)

    def test_collaborator_cannot_open_subtopic_proposal_when_disabled(self):
        collab = make_user("collab2", accepted_submissions_count=3)
        with self.assertRaises(PermissionDenied):
            services.open_subtopic_proposal(
                collab, parent_topic=self.parent_topic, name="Blocked"
            )
        self.assertEqual(SubtopicProposal.objects.count(), 0)

    def test_creator_cannot_bypass_disabled_toggle(self):
        # The toggle gates EVERYONE, even a Creator (it is the on/off switch,
        # not a privilege check).
        creator = make_user("creator", is_riffhub_creator=True)
        with self.assertRaises(PermissionDenied):
            services.open_topic_proposal(creator, name="StillBlocked")


# ===========================================================================
# Proposals: casting votes
# ===========================================================================
class CastProposalVoteTests(TestCase):
    """PRODUCT.md: ANY non-anonymous user may vote on a proposal; one vote per
    voter, re-voting updates the value (no duplicate rows).
    """

    def setUp(self):
        configure_proposals(enabled=True, collaborator_threshold=3)
        self.proposer = make_user("proposer", accepted_submissions_count=3)
        self.proposal = services.open_topic_proposal(self.proposer, name="Pedals")
        self.sub_proposal = services.open_subtopic_proposal(
            self.proposer,
            parent_topic=Topic.objects.create(name="Gear"),
            name="Acoustic",
        )

    def test_regular_user_can_vote(self):
        # A plain regular user (cannot propose) CAN vote.
        voter = make_user("voter", accepted_submissions_count=0)
        self.assertEqual(voter.level, Level.REGULAR)
        vote = services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)
        self.assertEqual(vote.value, ProposalVoteValue.UP)
        self.assertEqual(vote.voter, voter)
        self.assertEqual(vote.topic_proposal, self.proposal)

    def test_anonymous_cannot_vote(self):
        class _Anon:
            is_authenticated = False

        with self.assertRaises(PermissionDenied):
            services.cast_proposal_vote(_Anon(), self.proposal, ProposalVoteValue.UP)

    def test_invalid_vote_value_rejected(self):
        voter = make_user("badvalue")
        with self.assertRaises(ValidationError):
            services.cast_proposal_vote(voter, self.proposal, 5)

    def test_one_vote_per_voter(self):
        voter = make_user("once")
        services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)
        services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)
        self.assertEqual(
            ProposalVote.objects.filter(
                voter=voter, topic_proposal=self.proposal
            ).count(),
            1,
        )

    def test_revote_updates_value_in_place(self):
        voter = make_user("changer")
        first = services.cast_proposal_vote(
            voter, self.proposal, ProposalVoteValue.UP
        )
        second = services.cast_proposal_vote(
            voter, self.proposal, ProposalVoteValue.DOWN
        )
        # Same row, value flipped (re-vote updates, never duplicates).
        self.assertEqual(first.pk, second.pk)
        second.refresh_from_db()
        self.assertEqual(second.value, ProposalVoteValue.DOWN)
        self.assertEqual(
            ProposalVote.objects.filter(
                voter=voter, topic_proposal=self.proposal
            ).count(),
            1,
        )

    def test_revote_same_value_keeps_value(self):
        # Re-casting the SAME value is a no-op on the value (proposal votes do
        # NOT toggle off, unlike content votes).
        voter = make_user("samevote")
        services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)
        services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)
        vote = ProposalVote.objects.get(voter=voter, topic_proposal=self.proposal)
        self.assertEqual(vote.value, ProposalVoteValue.UP)

    def test_different_voters_each_get_a_vote(self):
        a = make_user("a")
        b = make_user("b")
        services.cast_proposal_vote(a, self.proposal, ProposalVoteValue.UP)
        services.cast_proposal_vote(b, self.proposal, ProposalVoteValue.DOWN)
        self.assertEqual(
            ProposalVote.objects.filter(topic_proposal=self.proposal).count(), 2
        )

    def test_vote_on_subtopic_proposal(self):
        voter = make_user("subvoter")
        vote = services.cast_proposal_vote(
            voter, self.sub_proposal, ProposalVoteValue.UP
        )
        self.assertEqual(vote.subtopic_proposal, self.sub_proposal)
        self.assertIsNone(vote.topic_proposal)

    def test_cannot_vote_on_closed_proposal(self):
        # Resolve (reject) the proposal, then voting must be refused.
        self.proposal.status = ProposalStatus.REJECTED
        self.proposal.save(update_fields=["status"])
        voter = make_user("latecomer")
        with self.assertRaises(PermissionDenied):
            services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)

    def test_proposal_tally_counts_separately(self):
        for i in range(3):
            services.cast_proposal_vote(
                make_user(f"up{i}"), self.proposal, ProposalVoteValue.UP
            )
        services.cast_proposal_vote(
            make_user("down0"), self.proposal, ProposalVoteValue.DOWN
        )
        tally = services.proposal_tally(self.proposal)
        self.assertEqual(tally, {"up": 3, "down": 1})

    def test_votes_isolated_between_topic_and_subtopic_proposals(self):
        # A vote on the topic proposal must not bleed into the subtopic
        # proposal's tally (the two nullable-FK targets are independent).
        voter = make_user("isolated")
        services.cast_proposal_vote(voter, self.proposal, ProposalVoteValue.UP)
        self.assertEqual(services.proposal_tally(self.proposal), {"up": 1, "down": 0})
        self.assertEqual(
            services.proposal_tally(self.sub_proposal), {"up": 0, "down": 0}
        )


# ===========================================================================
# Proposals: evaluation (window + pass-ratio + materialisation)
# ===========================================================================
class EvaluateProposalTests(TestCase):
    """PRODUCT.md: after the one-week (configurable) window a proposal passes
    iff its positive ratio is >= the configured pass ratio (default 75%); a
    proposal with no votes can never pass; accepting materialises the real
    Topic / Subtopic.
    """

    def setUp(self):
        configure_proposals(
            enabled=True,
            voting_days=7,
            pass_ratio="0.750",
            collaborator_threshold=3,
        )
        self.proposer = make_user("proposer", accepted_submissions_count=3)
        self.parent_topic = Topic.objects.create(name="Gear")

    # --- helpers ----------------------------------------------------------
    def _open_topic_proposal(self, name="Pedals"):
        return services.open_topic_proposal(self.proposer, name=name)

    def _open_subtopic_proposal(self, name="Acoustic"):
        return services.open_subtopic_proposal(
            self.proposer, parent_topic=self.parent_topic, name=name
        )

    def _close_window(self, proposal):
        """Force the voting window shut so evaluate_proposal will resolve it."""
        proposal.closes_at = timezone.now() - timedelta(seconds=1)
        proposal.save(update_fields=["closes_at"])

    def _vote(self, proposal, ups, downs):
        for i in range(ups):
            services.cast_proposal_vote(
                make_user(f"{proposal.pk}_up{i}"), proposal, ProposalVoteValue.UP
            )
        for i in range(downs):
            services.cast_proposal_vote(
                make_user(f"{proposal.pk}_down{i}"), proposal, ProposalVoteValue.DOWN
            )

    # --- window still open: no decision -----------------------------------
    def test_open_window_does_not_resolve(self):
        proposal = self._open_topic_proposal()
        self._vote(proposal, ups=10, downs=0)
        result = services.evaluate_proposal(proposal)
        # Window not elapsed -> still OPEN, nothing materialised.
        self.assertEqual(result.status, ProposalStatus.OPEN)
        self.assertFalse(Topic.objects.filter(name="Pedals").exists())

    # --- zero votes never passes ------------------------------------------
    def test_zero_votes_does_not_pass(self):
        proposal = self._open_topic_proposal()
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertFalse(Topic.objects.filter(name="Pedals").exists())

    # --- accept at / above pass ratio -------------------------------------
    def test_accepts_at_exactly_pass_ratio(self):
        # 3 up / 1 down = 0.75 ratio -> meets the >= 0.75 bar.
        proposal = self._open_topic_proposal(name="ExactlyPasses")
        self._vote(proposal, ups=3, downs=1)
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.ACCEPTED)
        self.assertTrue(Topic.objects.filter(name="ExactlyPasses").exists())

    def test_accepts_unanimous(self):
        proposal = self._open_topic_proposal(name="Unanimous")
        self._vote(proposal, ups=5, downs=0)
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.ACCEPTED)
        self.assertTrue(Topic.objects.filter(name="Unanimous").exists())

    def test_rejects_just_below_pass_ratio(self):
        # 2 up / 1 down = 0.666... < 0.75 -> rejected.
        proposal = self._open_topic_proposal(name="JustBelow")
        self._vote(proposal, ups=2, downs=1)
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertFalse(Topic.objects.filter(name="JustBelow").exists())

    def test_rejects_majority_negative(self):
        proposal = self._open_topic_proposal(name="Unpopular")
        self._vote(proposal, ups=1, downs=9)
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.REJECTED)
        self.assertFalse(Topic.objects.filter(name="Unpopular").exists())

    # --- materialisation specifics ----------------------------------------
    def test_accepted_topic_materialises_with_description(self):
        proposal = services.open_topic_proposal(
            self.proposer, name="Recording", description="Studio talk"
        )
        self._vote(proposal, ups=4, downs=0)
        self._close_window(proposal)
        services.evaluate_proposal(proposal)
        topic = Topic.objects.get(name="Recording")
        self.assertEqual(topic.description, "Studio talk")
        # Materialised topics are NOT predefined and NOT market by default.
        self.assertFalse(topic.is_predefined)
        self.assertFalse(topic.is_market)

    def test_accepted_subtopic_materialises_under_parent(self):
        proposal = self._open_subtopic_proposal(name="Acoustic")
        self._vote(proposal, ups=4, downs=0)
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.ACCEPTED)
        self.assertTrue(
            Subtopic.objects.filter(
                topic=self.parent_topic, name="Acoustic"
            ).exists()
        )

    def test_rejected_subtopic_not_materialised(self):
        proposal = self._open_subtopic_proposal(name="Rejected Sub")
        self._vote(proposal, ups=0, downs=3)
        self._close_window(proposal)
        services.evaluate_proposal(proposal)
        self.assertFalse(
            Subtopic.objects.filter(
                topic=self.parent_topic, name="Rejected Sub"
            ).exists()
        )

    # --- idempotency / no-op guards ---------------------------------------
    def test_evaluate_is_noop_on_already_accepted(self):
        proposal = self._open_topic_proposal(name="AlreadyDone")
        self._vote(proposal, ups=4, downs=0)
        self._close_window(proposal)
        services.evaluate_proposal(proposal)
        self.assertEqual(proposal.status, ProposalStatus.ACCEPTED)
        topic_count = Topic.objects.filter(name="AlreadyDone").count()
        # Re-running must not flip status or create a duplicate topic.
        again = services.evaluate_proposal(proposal)
        self.assertEqual(again.status, ProposalStatus.ACCEPTED)
        self.assertEqual(Topic.objects.filter(name="AlreadyDone").count(), topic_count)

    def test_evaluate_noop_while_open_keeps_votes_open(self):
        proposal = self._open_topic_proposal(name="StillOpen")
        self._vote(proposal, ups=1, downs=0)
        services.evaluate_proposal(proposal)
        # Still OPEN -> a voter can still cast a vote afterwards.
        voter = make_user("after_eval")
        vote = services.cast_proposal_vote(voter, proposal, ProposalVoteValue.UP)
        self.assertEqual(vote.value, ProposalVoteValue.UP)

    def test_pass_ratio_respects_custom_config(self):
        # Lower the bar to 50%: a 1-up/1-down (0.5) proposal then passes.
        configure_proposals(
            enabled=True,
            voting_days=7,
            pass_ratio="0.500",
            collaborator_threshold=3,
        )
        proposal = self._open_topic_proposal(name="HalfPass")
        self._vote(proposal, ups=1, downs=1)
        self._close_window(proposal)
        result = services.evaluate_proposal(proposal)
        self.assertEqual(result.status, ProposalStatus.ACCEPTED)
        self.assertTrue(Topic.objects.filter(name="HalfPass").exists())

    def test_status_persisted_to_db(self):
        # Guard against an in-memory-only status change.
        proposal = self._open_topic_proposal(name="Persisted")
        self._vote(proposal, ups=4, downs=0)
        self._close_window(proposal)
        services.evaluate_proposal(proposal)
        reloaded = TopicProposal.objects.get(pk=proposal.pk)
        self.assertEqual(reloaded.status, ProposalStatus.ACCEPTED)
