"""Tests for user levels & standing.

Covers:
- ``accounts.models.Level`` ordering & the ``User.level`` precedence rules
  (creator > moderator > sticky founder > derived collaborator > regular).
- ``User.is_at_least`` semantics.
- The "no silent promotion" safety net: with ``SiteConfiguration`` thresholds
  UNSET, ``user.level`` must not raise and may return at most ``REGULAR``.
- ``accounts.services.recompute_standing``: recomputes
  ``accepted_submissions_count`` from PUBLISHED catalog submissions, awards the
  sticky ``is_founder`` badge when newly qualifying, never unsets it, and
  respects ``founder_level_achievable``.

These are unit tests against the models/services directly (no HTTP).
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from accounts.models import Level
from accounts.services import count_accepted_submissions, recompute_standing
from catalog.constants import PublicationStatus
from catalog.models import Brand, GuitarModel
from core.models import SiteConfiguration

User = get_user_model()


def make_user(username, **kwargs):
    """Create a confirmed regular user with overridable flags/counters."""
    defaults = {
        "email": f"{username}@example.com",
        "password": "irrelevant-pw",
        "email_confirmed": True,
    }
    defaults.update(kwargs)
    return User.objects.create_user(username=username, **defaults)


def published_brand(name, submitter):
    """A PUBLISHED Brand submitted by ``submitter``."""
    return Brand.objects.create(
        name=name,
        submitted_by=submitter,
        status=PublicationStatus.PUBLISHED,
    )


# ---------------------------------------------------------------------------
# Level enum ordering
# ---------------------------------------------------------------------------
class LevelOrderingTests(TestCase):
    """The Level IntegerChoices must be strictly ordered as PRODUCT.md states:
    Anonymous < Regular < Collaborator < Founder < Moderator < Creator."""

    def test_integer_values_are_strictly_increasing(self):
        order = [
            Level.ANONYMOUS,
            Level.REGULAR,
            Level.COLLABORATOR,
            Level.FOUNDER,
            Level.MODERATOR,
            Level.CREATOR,
        ]
        values = [int(lvl) for lvl in order]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))

    def test_exact_documented_values(self):
        self.assertEqual(int(Level.ANONYMOUS), 0)
        self.assertEqual(int(Level.REGULAR), 10)
        self.assertEqual(int(Level.COLLABORATOR), 20)
        self.assertEqual(int(Level.FOUNDER), 30)
        self.assertEqual(int(Level.MODERATOR), 40)
        self.assertEqual(int(Level.CREATOR), 50)

    def test_levels_are_directly_comparable(self):
        self.assertLess(Level.REGULAR, Level.COLLABORATOR)
        self.assertLess(Level.COLLABORATOR, Level.FOUNDER)
        self.assertLess(Level.FOUNDER, Level.MODERATOR)
        self.assertLess(Level.MODERATOR, Level.CREATOR)
        self.assertGreater(Level.CREATOR, Level.ANONYMOUS)


# ---------------------------------------------------------------------------
# User.level precedence
# ---------------------------------------------------------------------------
class UserLevelPrecedenceTests(TestCase):
    """Granted roles win first, then sticky Founder, then derived Collaborator,
    otherwise Regular."""

    def setUp(self):
        # A configured, easily-met collaborator threshold so the derived
        # promotion path is *available* — precedence tests then prove the
        # granted/sticky flags still win over it.
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

    def test_logged_in_regular_user_is_regular(self):
        user = make_user("reg")
        self.assertEqual(user.level, Level.REGULAR)

    def test_accepted_count_at_threshold_is_collaborator(self):
        user = make_user("collab", accepted_submissions_count=3)
        self.assertEqual(user.level, Level.COLLABORATOR)

    def test_accepted_count_above_threshold_is_collaborator(self):
        user = make_user("collab2", accepted_submissions_count=99)
        self.assertEqual(user.level, Level.COLLABORATOR)

    def test_accepted_count_below_threshold_is_regular(self):
        user = make_user("almost", accepted_submissions_count=2)
        self.assertEqual(user.level, Level.REGULAR)

    def test_sticky_founder_outranks_derived_collaborator(self):
        # Even with enough accepted submissions to be a Collaborator, the
        # sticky founder badge must surface the higher Founder level.
        user = make_user("founder", is_founder=True, accepted_submissions_count=5)
        self.assertEqual(user.level, Level.FOUNDER)

    def test_sticky_founder_with_zero_accepted_still_founder(self):
        # Founder is sticky: it does not depend on the current accepted count.
        user = make_user("oldtimer", is_founder=True, accepted_submissions_count=0)
        self.assertEqual(user.level, Level.FOUNDER)

    def test_moderator_outranks_founder(self):
        user = make_user(
            "mod",
            is_community_moderator=True,
            is_founder=True,
            accepted_submissions_count=99,
        )
        self.assertEqual(user.level, Level.MODERATOR)

    def test_moderator_without_founder_still_moderator(self):
        user = make_user("mod2", is_community_moderator=True)
        self.assertEqual(user.level, Level.MODERATOR)

    def test_creator_outranks_moderator(self):
        user = make_user(
            "creator",
            is_riffhub_creator=True,
            is_community_moderator=True,
            is_founder=True,
            accepted_submissions_count=99,
        )
        self.assertEqual(user.level, Level.CREATOR)

    def test_creator_flag_alone_is_creator(self):
        user = make_user("creator2", is_riffhub_creator=True)
        self.assertEqual(user.level, Level.CREATOR)

    def test_full_precedence_ladder(self):
        # Build five users, each one rung higher, and assert the exact ordering
        # of the precedence chain creator > moderator > founder > collab > reg.
        regular = make_user("p_reg", accepted_submissions_count=0)
        collab = make_user("p_collab", accepted_submissions_count=3)
        founder = make_user("p_founder", is_founder=True)
        moderator = make_user("p_mod", is_community_moderator=True)
        creator = make_user("p_creator", is_riffhub_creator=True)

        self.assertEqual(regular.level, Level.REGULAR)
        self.assertEqual(collab.level, Level.COLLABORATOR)
        self.assertEqual(founder.level, Level.FOUNDER)
        self.assertEqual(moderator.level, Level.MODERATOR)
        self.assertEqual(creator.level, Level.CREATOR)

        ladder = [regular, collab, founder, moderator, creator]
        levels = [u.level for u in ladder]
        self.assertEqual(levels, sorted(levels))


# ---------------------------------------------------------------------------
# is_at_least
# ---------------------------------------------------------------------------
class IsAtLeastTests(TestCase):
    def setUp(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 3
        config.founder_threshold = 30
        config.save()

    def test_regular_meets_regular_and_below(self):
        user = make_user("reg")
        self.assertTrue(user.is_at_least(Level.REGULAR))
        self.assertTrue(user.is_at_least(Level.ANONYMOUS))

    def test_regular_does_not_meet_higher_levels(self):
        user = make_user("reg2")
        self.assertFalse(user.is_at_least(Level.COLLABORATOR))
        self.assertFalse(user.is_at_least(Level.FOUNDER))
        self.assertFalse(user.is_at_least(Level.MODERATOR))
        self.assertFalse(user.is_at_least(Level.CREATOR))

    def test_collaborator_meets_collaborator_and_below(self):
        user = make_user("collab", accepted_submissions_count=3)
        self.assertTrue(user.is_at_least(Level.REGULAR))
        self.assertTrue(user.is_at_least(Level.COLLABORATOR))
        self.assertFalse(user.is_at_least(Level.FOUNDER))

    def test_creator_meets_every_level(self):
        user = make_user("creator", is_riffhub_creator=True)
        for level in (
            Level.ANONYMOUS,
            Level.REGULAR,
            Level.COLLABORATOR,
            Level.FOUNDER,
            Level.MODERATOR,
            Level.CREATOR,
        ):
            self.assertTrue(
                user.is_at_least(level), f"creator should meet {level!r}"
            )

    def test_is_at_least_is_inclusive_at_own_level(self):
        # is_at_least uses >=, so a user always meets their own exact level.
        moderator = make_user("mod", is_community_moderator=True)
        self.assertTrue(moderator.is_at_least(Level.MODERATOR))


# ---------------------------------------------------------------------------
# CRITICAL: no silent promotion when thresholds are unset
# ---------------------------------------------------------------------------
class UnsetThresholdSafetyNetTests(TestCase):
    """With SiteConfiguration thresholds UNSET, reading ``user.level`` must not
    raise and may return at most REGULAR — no silent promotion.

    NOTE: SiteConfiguration.get_solo() is NOT called in setUp; the singleton's
    thresholds are therefore None (unset), and the guarded accessor raises
    ImproperlyConfigured, which user.level swallows into REGULAR.
    """

    def test_collaborator_threshold_unset_raises_on_accessor(self):
        # Sanity check that the accessor really does raise while unset — this
        # is the condition user.level must defend against.
        config = SiteConfiguration.get_solo()
        self.assertIsNone(config.collaborator_promotion_threshold)
        with self.assertRaises(ImproperlyConfigured):
            _ = config.collaborator_threshold

    def test_level_does_not_raise_with_unset_threshold(self):
        user = make_user("reg", accepted_submissions_count=0)
        try:
            level = user.level
        except ImproperlyConfigured:  # pragma: no cover - asserts the bug
            self.fail("user.level raised ImproperlyConfigured with unset threshold")
        self.assertEqual(level, Level.REGULAR)

    def test_high_accepted_count_not_silently_promoted(self):
        # A user with a large accepted count but NO configured threshold must
        # stay Regular — never silently promoted to Collaborator.
        user = make_user("hoarder", accepted_submissions_count=10_000)
        self.assertEqual(user.level, Level.REGULAR)
        self.assertFalse(user.is_at_least(Level.COLLABORATOR))

    def test_is_at_least_safe_with_unset_threshold(self):
        user = make_user("reg2", accepted_submissions_count=10_000)
        # Must not raise, and must report REGULAR-and-below only.
        self.assertTrue(user.is_at_least(Level.REGULAR))
        self.assertFalse(user.is_at_least(Level.COLLABORATOR))

    def test_granted_roles_still_resolve_without_threshold(self):
        # Granted roles short-circuit before the threshold read, so they must
        # work even when the collaborator threshold is unset.
        creator = make_user("c", is_riffhub_creator=True)
        moderator = make_user("m", is_community_moderator=True)
        founder = make_user("f", is_founder=True)
        self.assertEqual(creator.level, Level.CREATOR)
        self.assertEqual(moderator.level, Level.MODERATOR)
        self.assertEqual(founder.level, Level.FOUNDER)


# ---------------------------------------------------------------------------
# count_accepted_submissions
# ---------------------------------------------------------------------------
class CountAcceptedSubmissionsTests(TestCase):
    """Only PUBLISHED submissions count, and they aggregate across catalog
    entry types via the %(class)s_submissions reverse relations."""

    def test_zero_for_fresh_user(self):
        user = make_user("fresh")
        self.assertEqual(count_accepted_submissions(user), 0)

    def test_counts_only_published_brands(self):
        user = make_user("u")
        published_brand("Published Brand A", user)
        published_brand("Published Brand B", user)
        Brand.objects.create(
            name="Under Revision Brand",
            submitted_by=user,
            status=PublicationStatus.UNDER_REVISION,
        )
        Brand.objects.create(
            name="Rejected Brand",
            submitted_by=user,
            status=PublicationStatus.REJECTED,
        )
        self.assertEqual(count_accepted_submissions(user), 2)

    def test_does_not_count_other_users_submissions(self):
        owner = make_user("owner")
        other = make_user("other")
        published_brand("Owned Brand", owner)
        self.assertEqual(count_accepted_submissions(owner), 1)
        self.assertEqual(count_accepted_submissions(other), 0)

    def test_aggregates_across_entry_types(self):
        # A published Brand plus a published GuitarModel => 2 across two
        # different reverse relations (brand_submissions + guitarmodel_submissions).
        user = make_user("multi")
        brand = published_brand("Brand For Guitar", user)
        GuitarModel.objects.create(
            brand=brand,
            name="Some Model",
            num_strings=6,
            scale_length_min_inches="25.500",
            scale_length_max_inches="25.500",
            submitted_by=user,
            status=PublicationStatus.PUBLISHED,
        )
        self.assertEqual(count_accepted_submissions(user), 2)


# ---------------------------------------------------------------------------
# recompute_standing — accepted count
# ---------------------------------------------------------------------------
class RecomputeStandingCountTests(TestCase):
    def setUp(self):
        # Founder threshold present but high; achievable. So the count logic is
        # exercised without incidentally awarding the badge.
        config = SiteConfiguration.get_solo()
        config.founder_threshold = 30
        config.founder_level_achievable = True
        config.save()

    def test_recompute_sets_count_from_published(self):
        user = make_user("u", accepted_submissions_count=0)
        published_brand("Recount A", user)
        published_brand("Recount B", user)
        recompute_standing(user)
        self.assertEqual(user.accepted_submissions_count, 2)
        user.refresh_from_db()
        self.assertEqual(user.accepted_submissions_count, 2)

    def test_recompute_ignores_non_published(self):
        user = make_user("u2", accepted_submissions_count=0)
        published_brand("Counted", user)
        Brand.objects.create(
            name="Not Counted (revision)",
            submitted_by=user,
            status=PublicationStatus.UNDER_REVISION,
        )
        recompute_standing(user)
        user.refresh_from_db()
        self.assertEqual(user.accepted_submissions_count, 1)

    def test_recompute_corrects_stale_count_downward(self):
        # The stored count is the source of truth's responsibility: recompute
        # overwrites a stale (too-high) value with the real published count.
        user = make_user("stale", accepted_submissions_count=50)
        published_brand("Only One", user)
        recompute_standing(user)
        user.refresh_from_db()
        self.assertEqual(user.accepted_submissions_count, 1)

    def test_recompute_to_zero_when_none_published(self):
        user = make_user("dropped", accepted_submissions_count=7)
        Brand.objects.create(
            name="Rejected Only",
            submitted_by=user,
            status=PublicationStatus.REJECTED,
        )
        recompute_standing(user)
        user.refresh_from_db()
        self.assertEqual(user.accepted_submissions_count, 0)


# ---------------------------------------------------------------------------
# recompute_standing — sticky Founder badge
# ---------------------------------------------------------------------------
class RecomputeStandingFounderTests(TestCase):
    def _config(self, *, threshold, achievable=True):
        config = SiteConfiguration.get_solo()
        config.founder_threshold = threshold
        config.founder_level_achievable = achievable
        config.save()
        return config

    def test_awards_founder_when_newly_qualifying(self):
        self._config(threshold=2, achievable=True)
        user = make_user("rising", accepted_submissions_count=0)
        self.assertFalse(user.is_founder)
        published_brand("Qual A", user)
        published_brand("Qual B", user)
        recompute_standing(user)
        user.refresh_from_db()
        self.assertTrue(user.is_founder)
        self.assertEqual(user.level, Level.FOUNDER)

    def test_awards_founder_at_exact_threshold(self):
        # Boundary: accepted == threshold qualifies (>=).
        self._config(threshold=3, achievable=True)
        user = make_user("exact", accepted_submissions_count=0)
        for i in range(3):
            published_brand(f"Exact {i}", user)
        recompute_standing(user)
        user.refresh_from_db()
        self.assertTrue(user.is_founder)

    def test_does_not_award_below_threshold(self):
        self._config(threshold=3, achievable=True)
        user = make_user("notyet", accepted_submissions_count=0)
        published_brand("Below A", user)
        published_brand("Below B", user)
        recompute_standing(user)
        user.refresh_from_db()
        self.assertFalse(user.is_founder)
        self.assertEqual(user.accepted_submissions_count, 2)

    def test_does_not_award_when_level_not_achievable(self):
        # Even at/over the threshold, a closed Founder level must not grant the
        # badge to someone who had not yet earned it.
        self._config(threshold=2, achievable=False)
        user = make_user("toolate", accepted_submissions_count=0)
        published_brand("Late A", user)
        published_brand("Late B", user)
        published_brand("Late C", user)
        recompute_standing(user)
        user.refresh_from_db()
        self.assertFalse(user.is_founder)
        # ...but the accepted count is still recomputed.
        self.assertEqual(user.accepted_submissions_count, 3)

    def test_never_unsets_existing_founder_when_dropping_below(self):
        # Sticky badge: an existing founder who now has zero published
        # submissions keeps the badge.
        self._config(threshold=3, achievable=True)
        user = make_user("kept", is_founder=True, accepted_submissions_count=10)
        # No published submissions at all now.
        recompute_standing(user)
        user.refresh_from_db()
        self.assertTrue(user.is_founder)
        self.assertEqual(user.accepted_submissions_count, 0)
        self.assertEqual(user.level, Level.FOUNDER)

    def test_never_unsets_existing_founder_when_level_closed(self):
        # Closing the Founder level must not strip an already-earned badge.
        self._config(threshold=3, achievable=False)
        user = make_user("elder", is_founder=True, accepted_submissions_count=0)
        recompute_standing(user)
        user.refresh_from_db()
        self.assertTrue(user.is_founder)

    def test_founder_threshold_unset_does_not_raise_or_award(self):
        # founder_threshold is None here -> recompute_standing must swallow the
        # ImproperlyConfigured and simply not award the badge, while still
        # updating the accepted count.
        config = SiteConfiguration.get_solo()
        self.assertIsNone(config.founder_threshold)
        user = make_user("noconf", accepted_submissions_count=0)
        published_brand("NC A", user)
        published_brand("NC B", user)
        try:
            recompute_standing(user)
        except ImproperlyConfigured:  # pragma: no cover - asserts the bug
            self.fail("recompute_standing raised with unset founder_threshold")
        user.refresh_from_db()
        self.assertFalse(user.is_founder)
        self.assertEqual(user.accepted_submissions_count, 2)

    def test_recompute_persists_founder_flag_via_update_fields(self):
        # Guard against the badge being computed in-memory but not saved: a
        # fresh fetch from the DB must reflect the awarded flag.
        self._config(threshold=1, achievable=True)
        user = make_user("persist", accepted_submissions_count=0)
        published_brand("Persist A", user)
        recompute_standing(user)
        reloaded = User.objects.get(pk=user.pk)
        self.assertTrue(reloaded.is_founder)
        self.assertEqual(reloaded.accepted_submissions_count, 1)
