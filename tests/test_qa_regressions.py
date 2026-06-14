"""Regression tests for bugs found in the parallel QA audit sweep.

Each class pins a specific fix so the bug can't silently come back. Grouped by
the service/area touched; see the commit message for the full audit write-up
(including the findings that were intentionally *not* fixed, with rationale).

Conventions mirror the existing suite: ``django.test.TestCase`` + service-layer
calls, with ``SiteConfiguration`` thresholds set in ``setUp`` so role/level
derivation never raises ``ImproperlyConfigured``.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from catalog.constants import ElectronicsType, PickupPosition
from catalog.models import (
    Brand,
    Bridge,
    BridgeType,
    GuitarModel,
    GuitarPickup,
    Pickup,
    PickupType,
    Tuner,
)
from catalog.views import _page_number
from core.models import SiteConfiguration
from forum.constants import VoteValue
from forum.models import Comment, Post, Subtopic, Topic
from forum.services import cast_vote, create_comment, create_post, toggle_reaction
from messaging.models import Conversation
from messaging.services import get_conversation
from moderation import services as mod
from moderation.constants import ContentActionType
from moderation.models import Ban, ContentAction

User = get_user_model()


def make_user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pw-12345",
        email_confirmed=True,
        **flags,
    )


class QABase(TestCase):
    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()


# ---------------------------------------------------------------------------
# Forum engagement gate: banned users can't vote/react via the service (F1).
# (Soft-removed content stays moderator-engageable by design — the view layer
# 404s non-moderators — so there is intentionally no service-level removed gate.)
# ---------------------------------------------------------------------------
class ForumEngagementGateTests(QABase):
    def setUp(self):
        super().setUp()
        self.moderator = make_user("mod", is_community_moderator=True)
        self.author = make_user("author")
        self.actor = make_user("actor")
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.post = create_post(
            subtopic=self.subtopic, author=self.author, title="Rig", body="hi"
        )

    def _fresh(self, u):
        return User.objects.get(pk=u.pk)

    def test_banned_user_cannot_vote(self):
        mod.ban(self.moderator, self.actor, reason="illegal")
        with self.assertRaises(PermissionDenied):
            cast_vote(self._fresh(self.actor), self.post, VoteValue.UP)

    def test_banned_user_cannot_react(self):
        mod.ban(self.moderator, self.actor, reason="illegal")
        with self.assertRaises(PermissionDenied):
            toggle_reaction(self._fresh(self.actor), self.post, "🔥")

    def test_active_ban_row_alone_blocks_voting(self):
        # A Ban row without is_active=False still reads as banned via is_banned.
        Ban.objects.create(target=self.actor, issued_by=self.moderator, reason="x")
        self.assertTrue(self.actor.is_active)
        with self.assertRaises(PermissionDenied):
            cast_vote(self._fresh(self.actor), self.post, VoteValue.UP)

    def test_unsanctioned_user_can_still_vote_on_live_post(self):
        # The gates must not block ordinary voting on live content.
        vote = cast_vote(self.actor, self.post, VoteValue.UP)
        self.assertIsNotNone(vote)


# ---------------------------------------------------------------------------
# Catalog: derived facets recompute when a referenced component is edited (F2)
# ---------------------------------------------------------------------------
class DerivedFacetRecomputeOnComponentEditTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Acme")

    def _guitar(self, **kw):
        g = GuitarModel.objects.create(
            brand=self.brand,
            name=kw.pop("name", "Model"),
            num_strings=6,
            scale_length_min_inches=Decimal("25.5"),
            scale_length_max_inches=Decimal("25.5"),
            **kw,
        )
        g.refresh_from_db()
        return g

    def test_editing_bridge_has_piezo_recomputes_guitar(self):
        bt = BridgeType.objects.create(name="Hardtail", is_tremolo=False)
        bridge = Bridge.objects.create(
            brand=self.brand, name="HT", bridge_type=bt, has_piezo=False
        )
        guitar = self._guitar(bridge=bridge)
        self.assertFalse(guitar.has_piezo)

        bridge.has_piezo = True
        bridge.save()

        guitar.refresh_from_db()
        self.assertTrue(guitar.has_piezo)

    def test_editing_bridge_type_is_tremolo_recomputes_guitar(self):
        bt = BridgeType.objects.create(name="Vibrato", is_tremolo=False)
        bridge = Bridge.objects.create(brand=self.brand, name="V", bridge_type=bt)
        guitar = self._guitar(bridge=bridge)
        self.assertFalse(guitar.has_tremolo)

        bt.is_tremolo = True
        bt.save()

        guitar.refresh_from_db()
        self.assertTrue(guitar.has_tremolo)

    def test_editing_tuner_is_locking_recomputes_guitar(self):
        tuner = Tuner.objects.create(brand=self.brand, name="T", is_locking=False)
        guitar = self._guitar(tuners=tuner)
        self.assertFalse(guitar.has_locking_tuners)

        tuner.is_locking = True
        tuner.save()

        guitar.refresh_from_db()
        self.assertTrue(guitar.has_locking_tuners)

    def test_editing_pickup_is_active_recomputes_electronics(self):
        pt = PickupType.objects.create(name="Hum", symbol="H", is_humbucking=True)
        pickup = Pickup.objects.create(
            brand=self.brand, name="P", pickup_type=pt, is_active=False
        )
        guitar = self._guitar()
        GuitarPickup.objects.create(
            guitar=guitar, pickup=pickup, position=PickupPosition.BRIDGE
        )
        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.PASSIVE)

        pickup.is_active = True
        pickup.save()

        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)

    def test_editing_pickup_type_humbucking_recomputes_hum_cancellation(self):
        pt = PickupType.objects.create(name="Single", symbol="S", is_humbucking=False)
        pickup = Pickup.objects.create(
            brand=self.brand, name="SC", pickup_type=pt, is_active=False
        )
        guitar = self._guitar()
        GuitarPickup.objects.create(
            guitar=guitar, pickup=pickup, position=PickupPosition.NECK
        )
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_hum_cancellation)

        pt.is_humbucking = True
        pt.save()

        guitar.refresh_from_db()
        self.assertTrue(guitar.has_hum_cancellation)


# ---------------------------------------------------------------------------
# Accounts: add_reputation is atomic (F3)
# ---------------------------------------------------------------------------
class AddReputationAtomicTests(TestCase):
    def test_concurrent_in_memory_increments_both_survive(self):
        u = make_user("rep")
        a = User.objects.get(pk=u.pk)
        b = User.objects.get(pk=u.pk)  # both loaded at score 0
        a.add_reputation(1)
        b.add_reputation(1)
        u.refresh_from_db()
        # Read-modify-write would lose one; the atomic F() update keeps both.
        self.assertEqual(u.reputation_score, 2)

    def test_negative_delta_applies(self):
        u = make_user("rep2")
        u.add_reputation(5)
        u.add_reputation(-2)
        u.refresh_from_db()
        self.assertEqual(u.reputation_score, 3)


# ---------------------------------------------------------------------------
# Moderation: remove/restore audit guards (F5) + move validation (F6) +
# lift_ban only reactivates a real ban (F10)
# ---------------------------------------------------------------------------
class ModerationGuardTests(QABase):
    def setUp(self):
        super().setUp()
        self.moderator = make_user("mod", is_community_moderator=True)
        self.other_mod = make_user("mod2", is_community_moderator=True)
        self.regular = make_user("regular")
        self.topic = Topic.objects.create(name="Gear")
        self.subtopic = Subtopic.objects.create(topic=self.topic, name="Guitars")
        self.other_subtopic = Subtopic.objects.create(topic=self.topic, name="Basses")
        self.market_topic = Topic.objects.create(
            name="Gear Market", is_market=True, requires_disclaimer=True
        )
        self.market_subtopic = Subtopic.objects.create(
            topic=self.market_topic, name="For sale"
        )

    def test_double_remove_preserves_original_metadata(self):
        post = Post.objects.create(
            subtopic=self.subtopic, author=self.regular, title="x", body="y"
        )
        mod.remove_content(self.moderator, post, reason="off-topic")
        post.refresh_from_db()
        first_by, first_at = post.removed_by_id, post.removed_at

        mod.remove_content(self.other_mod, post, reason="also spam")
        post.refresh_from_db()
        self.assertEqual(post.removed_by_id, first_by)
        self.assertEqual(post.removed_at, first_at)
        self.assertEqual(
            ContentAction.objects.filter(action=ContentActionType.REMOVE).count(), 1
        )

    def test_restore_not_removed_is_noop(self):
        post = Post.objects.create(
            subtopic=self.subtopic, author=self.regular, title="x", body="y"
        )
        self.assertFalse(post.is_removed)
        mod.restore_content(self.moderator, post)
        self.assertEqual(
            ContentAction.objects.filter(action=ContentActionType.RESTORE).count(), 0
        )

    def test_normal_remove_then_restore_still_works(self):
        post = Post.objects.create(
            subtopic=self.subtopic, author=self.regular, title="x", body="y"
        )
        mod.remove_content(self.moderator, post, reason="off-topic")
        mod.restore_content(self.moderator, post)
        post.refresh_from_db()
        self.assertFalse(post.is_removed)
        self.assertEqual(ContentAction.objects.count(), 2)

    def test_move_priceless_post_into_market_rejected(self):
        post = create_post(
            subtopic=self.subtopic, author=self.regular, title="chat", body="hi"
        )
        with self.assertRaises(ValidationError):
            mod.move_content(self.moderator, post, self.market_subtopic)

    def test_move_priced_listing_out_of_market_rejected(self):
        listing = create_post(
            subtopic=self.market_subtopic, author=self.regular,
            title="FS", body="mint", price=Decimal("999.00"), currency="USD",
        )
        with self.assertRaises(ValidationError):
            mod.move_content(self.moderator, listing, self.subtopic)

    def test_move_within_non_market_still_works(self):
        post = create_post(
            subtopic=self.subtopic, author=self.regular, title="bass", body="oops"
        )
        mod.move_content(self.moderator, post, self.other_subtopic)
        post.refresh_from_db()
        self.assertEqual(post.subtopic_id, self.other_subtopic.pk)

    def test_lift_ban_reactivates_a_real_ban(self):
        mod.ban(self.moderator, self.regular, reason="illegal")
        mod.lift_ban(self.moderator, self.regular)
        self.assertTrue(User.objects.get(pk=self.regular.pk).is_active)

    def test_lift_ban_does_not_reactivate_account_without_a_ban(self):
        # Account disabled for some other reason (no Ban row); lifting must NOT
        # silently re-enable it.
        self.regular.is_active = False
        self.regular.save(update_fields=["is_active"])
        mod.lift_ban(self.moderator, self.regular)
        self.assertFalse(User.objects.get(pk=self.regular.pk).is_active)


# ---------------------------------------------------------------------------
# Messaging: no degenerate self-conversation (F8)
# ---------------------------------------------------------------------------
class ConversationSelfPairTests(TestCase):
    def test_get_conversation_with_self_raises_and_persists_nothing(self):
        alice = make_user("alice")
        with self.assertRaises(PermissionDenied):
            get_conversation(alice, alice)
        self.assertFalse(
            Conversation.objects.filter(user_low=alice, user_high=alice).exists()
        )

    def test_get_conversation_between_two_users_is_canonical(self):
        alice = make_user("alice")
        bob = make_user("bob")
        c1 = get_conversation(alice, bob)
        c2 = get_conversation(bob, alice)
        self.assertEqual(c1.pk, c2.pk)


# ---------------------------------------------------------------------------
# Catalog browse: a sub-1 ?page falls back to the first page (F9)
# ---------------------------------------------------------------------------
class PageNumberNormalisationTests(TestCase):
    def test_zero_and_negative_become_first_page(self):
        self.assertEqual(_page_number("0"), 1)
        self.assertEqual(_page_number("-1"), 1)
        self.assertEqual(_page_number("-999"), 1)

    def test_positive_passes_through(self):
        self.assertEqual(_page_number("3"), 3)

    def test_non_numeric_passes_through_for_get_page_fallback(self):
        # Left as-is so Paginator.get_page does its PageNotAnInteger -> page 1.
        self.assertEqual(_page_number("abc"), "abc")
        self.assertEqual(_page_number("1.5"), "1.5")
        self.assertEqual(_page_number(None), None)
