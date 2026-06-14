"""Regression tests for confirmed forum-core bugs.

Each test below would FAIL on the pre-fix behaviour and PASSES with the fix:

* #6/#7/#25/#29 — over-long emoji used to reach ``Reaction.objects.create``
  with no length guard, raising ``DataError`` (HTTP 500) because the column was
  ``max_length=8``. The column is now ``max_length=32`` and ``toggle_reaction``
  rejects an empty or >32-char emoji with ``ValidationError`` (mapped to 400),
  while legitimate multi-codepoint (ZWJ) emoji are stored.
* #8 — ``_materialise_proposal`` deduped on the NAME, but the DB enforces the
  auto-generated SLUG. A proposal whose name slugifies to an existing topic's
  slug used to INSERT a duplicate slug -> ``IntegrityError`` swallowed by the
  sweep, leaving the proposal stuck OPEN forever. It now reuses the existing
  row and resolves to a terminal ACCEPTED status.
* #20/#33 — ``open_topic_proposal`` / ``open_subtopic_proposal`` accepted a
  blank or over-length ``proposed_name``; they now raise ``ValidationError``.
* #17 — ``Post.clean`` accepted a negative Gear Market price; it now rejects it.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone
from django.utils.text import slugify

from datetime import timedelta

from core.models import SiteConfiguration
from forum import services
from forum.constants import ProposalStatus, ProposalVoteValue
from forum.models import (
    Post,
    Reaction,
    Subtopic,
    Topic,
    TopicProposal,
)

User = get_user_model()


def make_user(username, **kwargs):
    """Create a confirmed user; overridable role flags / counters."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "irrelevant-pw",
        "email_confirmed": True,
    }
    defaults.update(kwargs)
    return User.objects.create_user(username=username, **defaults)


def configure(*, collaborator_threshold=3, founder_threshold=30):
    """Set the thresholds Collaborator/Founder derivation depends on."""
    config = SiteConfiguration.get_solo()
    config.collaborator_promotion_threshold = collaborator_threshold
    config.founder_threshold = founder_threshold
    config.topic_proposals_enabled = True
    config.topic_proposal_voting_days = 7
    config.topic_proposal_pass_ratio = Decimal("0.750")
    config.save()
    return config


# ===========================================================================
# #6/#7/#25/#29 — over-long emoji no longer 500s; legitimate emoji stored
# ===========================================================================
class ReactionEmojiLengthTests(TestCase):
    def setUp(self):
        configure()
        self.author = make_user("author")
        self.reactor = make_user("reactor")
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.post = Post.objects.create(
            subtopic=self.subtopic,
            author=self.author,
            title="Jackson vs Ibanez",
            body="Discuss.",
        )

    def test_emoji_longer_than_max_length_raises_validation_error(self):
        # Old behaviour: create(...) with no guard -> DataError (HTTP 500).
        # New behaviour: a clean ValidationError (the view maps it to 400).
        too_long = "x" * 33
        with self.assertRaises(ValidationError):
            services.toggle_reaction(self.reactor, self.post, too_long)
        self.assertEqual(Reaction.objects.count(), 0)

    def test_empty_emoji_still_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            services.toggle_reaction(self.reactor, self.post, "   ")
        self.assertEqual(Reaction.objects.count(), 0)

    def test_legitimate_zwj_emoji_is_stored(self):
        # 7-codepoint ZWJ "family" sequence: would have exceeded the old
        # max_length=8 column and raised DataError; now fits in max_length=32.
        family = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"
        self.assertEqual(len(family), 7)

        reaction = services.toggle_reaction(self.reactor, self.post, family)

        self.assertIsNotNone(reaction)
        self.assertEqual(reaction.emoji, family)
        self.assertEqual(Reaction.objects.count(), 1)

    def test_multi_codepoint_emoji_within_limit_is_stored(self):
        # An 8-codepoint "kiss" ZWJ sequence (> old 8-char column, <= new 32).
        kiss = (
            "\U0001F469‍❤️‍\U0001F48B‍\U0001F468"
        )
        self.assertEqual(len(kiss), 8)

        reaction = services.toggle_reaction(self.reactor, self.post, kiss)

        self.assertIsNotNone(reaction)
        self.assertEqual(reaction.emoji, kiss)
        self.assertEqual(Reaction.objects.count(), 1)


# ===========================================================================
# #8 — proposal whose name slugifies to an existing topic's slug reuses it
# ===========================================================================
class MaterialiseProposalSlugCollisionTests(TestCase):
    def setUp(self):
        configure()
        self.proposer = make_user("proposer", accepted_submissions_count=3)
        self.parent_topic = Topic.objects.create(name="Gear")

    def _close_window(self, proposal):
        proposal.closes_at = timezone.now() - timedelta(seconds=1)
        proposal.save(update_fields=["closes_at"])

    def _pass_votes(self, proposal):
        # 3 up / 0 down clears the 0.75 pass ratio.
        for i in range(3):
            services.cast_proposal_vote(
                make_user(f"voter_{proposal.pk}_{i}"),
                proposal,
                ProposalVoteValue.UP,
            )

    def test_topic_proposal_colliding_slug_reuses_existing_topic(self):
        # An existing topic whose slug the proposed name will collide with.
        existing = Topic.objects.create(name="Effects Pedals")
        colliding_name = "Effects   Pedals"  # different name, SAME slug
        self.assertEqual(slugify(colliding_name), existing.slug)

        proposal = services.open_topic_proposal(
            self.proposer, name=colliding_name
        )
        self._pass_votes(proposal)
        self._close_window(proposal)

        # Old behaviour: get_or_create(name=...) misses -> INSERT duplicate slug
        # -> IntegrityError. New behaviour: reuse the existing row, resolve.
        try:
            result = services.evaluate_proposal(proposal)
        except IntegrityError:  # pragma: no cover - the bug we are fixing
            self.fail("evaluate_proposal raised IntegrityError on slug collision")

        self.assertEqual(result.status, ProposalStatus.ACCEPTED)
        # No duplicate topic was created — the existing row was reused.
        self.assertEqual(Topic.objects.filter(slug=existing.slug).count(), 1)

    def test_sweep_resolves_colliding_proposal_to_terminal_status(self):
        existing = Topic.objects.create(name="Recording Gear")
        colliding_name = "Recording  Gear"
        self.assertEqual(slugify(colliding_name), existing.slug)

        proposal = services.open_topic_proposal(
            self.proposer, name=colliding_name
        )
        self._pass_votes(proposal)
        self._close_window(proposal)

        # The sweep must not get stuck re-processing the proposal forever.
        result = services.sweep_due_proposals()
        self.assertEqual(result["evaluated"], 1)
        self.assertEqual(result["accepted"], 1)

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ProposalStatus.ACCEPTED)
        self.assertEqual(Topic.objects.filter(slug=existing.slug).count(), 1)


# ===========================================================================
# #20/#33 — proposed name validation (blank / over-length)
# ===========================================================================
class ProposalNameValidationTests(TestCase):
    def setUp(self):
        configure()
        self.proposer = make_user("proposer", accepted_submissions_count=3)
        self.parent_topic = Topic.objects.create(name="Gear")
        self.name_max = TopicProposal._meta.get_field("proposed_name").max_length

    def test_open_topic_proposal_empty_name_raises(self):
        with self.assertRaises(ValidationError):
            services.open_topic_proposal(self.proposer, name="")
        self.assertEqual(TopicProposal.objects.count(), 0)

    def test_open_topic_proposal_whitespace_name_raises(self):
        with self.assertRaises(ValidationError):
            services.open_topic_proposal(self.proposer, name="   ")
        self.assertEqual(TopicProposal.objects.count(), 0)

    def test_open_topic_proposal_overlong_name_raises(self):
        with self.assertRaises(ValidationError):
            services.open_topic_proposal(
                self.proposer, name="x" * (self.name_max + 1)
            )
        self.assertEqual(TopicProposal.objects.count(), 0)

    def test_open_topic_proposal_strips_and_stores_name(self):
        proposal = services.open_topic_proposal(
            self.proposer, name="  Pedals  "
        )
        self.assertEqual(proposal.proposed_name, "Pedals")
        self.assertEqual(proposal.status, ProposalStatus.OPEN)

    def test_open_subtopic_proposal_empty_name_raises(self):
        with self.assertRaises(ValidationError):
            services.open_subtopic_proposal(
                self.proposer, parent_topic=self.parent_topic, name="   "
            )

    def test_open_subtopic_proposal_overlong_name_raises(self):
        with self.assertRaises(ValidationError):
            services.open_subtopic_proposal(
                self.proposer,
                parent_topic=self.parent_topic,
                name="x" * (self.name_max + 1),
            )


# ===========================================================================
# #17 — negative Gear Market price is rejected
# ===========================================================================
class MarketPostNegativePriceTests(TestCase):
    def setUp(self):
        configure()
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

    def test_negative_price_is_invalid(self):
        post = Post(
            subtopic=self.market_sub,
            author=self.author,
            title="Selling my Strat",
            body="Mint condition.",
            price=Decimal("-5.00"),
        )
        with self.assertRaises(ValidationError) as ctx:
            post.full_clean()
        self.assertIn("price", ctx.exception.error_dict)

    def test_zero_price_is_allowed(self):
        post = Post(
            subtopic=self.market_sub,
            author=self.author,
            title="Free pick",
            body="Take it.",
            price=Decimal("0.00"),
        )
        # Should not raise — zero is a valid (giveaway) price.
        post.full_clean()

    def test_positive_price_is_allowed(self):
        post = Post(
            subtopic=self.market_sub,
            author=self.author,
            title="Selling my Strat",
            body="Mint condition.",
            price=Decimal("1200.00"),
            currency="USD",
        )
        post.full_clean()

    def test_create_post_with_negative_price_raises(self):
        # create_post runs full_clean, so the rule is enforced via the service.
        with self.assertRaises(ValidationError):
            services.create_post(
                subtopic=self.market_sub,
                author=self.author,
                title="Selling my Strat",
                body="Mint condition.",
                price=Decimal("-1.00"),
            )
