"""Tests for ``core.models.SiteConfiguration``.

Covers the PRODUCT.md rules for the runtime-config singleton:
- ``get_solo()`` returns a pk=1 singleton; ``save()`` always forces pk=1.
- ``collaborator_threshold`` / ``founder_promotion_threshold`` raise
  ``ImproperlyConfigured`` while their backing field is ``None`` and return
  the value once set (no silent default — "a default value should not exist
  and raise an error if this is not configured").
- Acceptance/cooldown defaults (3 / 3 / 3) and topic-proposal defaults
  (enabled True, 7-day window, 0.75 pass ratio) are present.
"""

from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from core.models import SiteConfiguration


class SiteConfigurationSingletonTests(TestCase):
    """The model is a strict pk=1 singleton."""

    def test_get_solo_returns_pk_1(self):
        config = SiteConfiguration.get_solo()
        self.assertEqual(config.pk, 1)

    def test_get_solo_creates_a_row_when_none_exists(self):
        # Test DB starts empty (migrated) — get_solo must create the row.
        self.assertEqual(SiteConfiguration.objects.count(), 0)
        SiteConfiguration.get_solo()
        self.assertEqual(SiteConfiguration.objects.count(), 1)
        self.assertTrue(SiteConfiguration.objects.filter(pk=1).exists())

    def test_get_solo_is_idempotent(self):
        first = SiteConfiguration.get_solo()
        second = SiteConfiguration.get_solo()
        self.assertEqual(first.pk, second.pk)
        # Repeated calls must not multiply rows.
        self.assertEqual(SiteConfiguration.objects.count(), 1)

    def test_get_solo_reflects_persisted_changes(self):
        config = SiteConfiguration.get_solo()
        config.collaborator_promotion_threshold = 5
        config.save()

        reloaded = SiteConfiguration.get_solo()
        self.assertEqual(reloaded.collaborator_promotion_threshold, 5)

    def test_save_forces_pk_to_1_on_create(self):
        # Even when we never set a pk, save() pins it to 1.
        config = SiteConfiguration()
        config.save()
        self.assertEqual(config.pk, 1)

    def test_save_overrides_an_explicitly_assigned_pk(self):
        config = SiteConfiguration(pk=999)
        config.save()
        self.assertEqual(config.pk, 1)
        self.assertFalse(SiteConfiguration.objects.filter(pk=999).exists())
        self.assertTrue(SiteConfiguration.objects.filter(pk=1).exists())

    def test_only_one_row_ever_exists(self):
        # Two independent instances both collapse onto pk=1 (the second is an
        # UPDATE, not a second INSERT).
        first = SiteConfiguration(collaborator_promotion_threshold=3)
        first.save()
        second = SiteConfiguration(collaborator_promotion_threshold=7)
        second.save()

        self.assertEqual(SiteConfiguration.objects.count(), 1)
        self.assertEqual(SiteConfiguration.get_solo().collaborator_promotion_threshold, 7)

    def test_str_is_stable(self):
        self.assertEqual(str(SiteConfiguration.get_solo()), "Site configuration")


class SiteConfigurationThresholdAccessorTests(TestCase):
    """Guarded promotion-threshold accessors: no silent defaults."""

    def setUp(self):
        self.config = SiteConfiguration.get_solo()

    # --- collaborator_threshold ------------------------------------------
    def test_collaborator_threshold_raises_when_unset(self):
        self.assertIsNone(self.config.collaborator_promotion_threshold)
        with self.assertRaises(ImproperlyConfigured):
            self.config.collaborator_threshold

    def test_collaborator_threshold_returns_value_when_set(self):
        self.config.collaborator_promotion_threshold = 3
        self.assertEqual(self.config.collaborator_threshold, 3)

    def test_collaborator_threshold_returns_zero_when_set_to_zero(self):
        # Zero is a configured value (not None) and must NOT raise — only the
        # unconfigured (None) state is an error.
        self.config.collaborator_promotion_threshold = 0
        self.assertEqual(self.config.collaborator_threshold, 0)

    def test_collaborator_threshold_error_message_names_the_field(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.config.collaborator_threshold
        self.assertIn("collaborator_promotion_threshold", str(ctx.exception))

    def test_collaborator_threshold_reads_persisted_value(self):
        self.config.collaborator_promotion_threshold = 12
        self.config.save()
        self.assertEqual(SiteConfiguration.get_solo().collaborator_threshold, 12)

    # --- founder_promotion_threshold -------------------------------------
    def test_founder_promotion_threshold_raises_when_unset(self):
        self.assertIsNone(self.config.founder_threshold)
        with self.assertRaises(ImproperlyConfigured):
            self.config.founder_promotion_threshold

    def test_founder_promotion_threshold_returns_value_when_set(self):
        self.config.founder_threshold = 30
        self.assertEqual(self.config.founder_promotion_threshold, 30)

    def test_founder_promotion_threshold_returns_zero_when_set_to_zero(self):
        self.config.founder_threshold = 0
        self.assertEqual(self.config.founder_promotion_threshold, 0)

    def test_founder_promotion_threshold_error_message_names_the_field(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self.config.founder_promotion_threshold
        self.assertIn("founder_threshold", str(ctx.exception))

    def test_founder_promotion_threshold_reads_persisted_value(self):
        self.config.founder_threshold = 30
        self.config.save()
        self.assertEqual(SiteConfiguration.get_solo().founder_promotion_threshold, 30)

    def test_thresholds_are_independent(self):
        # Setting one must not satisfy the other's guard.
        self.config.collaborator_promotion_threshold = 3
        self.assertEqual(self.config.collaborator_threshold, 3)
        with self.assertRaises(ImproperlyConfigured):
            self.config.founder_promotion_threshold


class SiteConfigurationDefaultsTests(TestCase):
    """A freshly created singleton carries the documented defaults."""

    def setUp(self):
        self.config = SiteConfiguration.get_solo()

    # --- promotion thresholds intentionally have NO default --------------
    def test_promotion_thresholds_default_to_none(self):
        self.assertIsNone(self.config.collaborator_promotion_threshold)
        self.assertIsNone(self.config.founder_threshold)

    # --- founder toggle ---------------------------------------------------
    def test_founder_level_achievable_defaults_true(self):
        self.assertTrue(self.config.founder_level_achievable)

    # --- acceptance / cooldown knobs (3 / 3 / 3) -------------------------
    def test_gear_acceptance_min_net_votes_defaults_to_3(self):
        self.assertEqual(self.config.gear_acceptance_min_net_votes, 3)

    def test_gear_acceptance_min_voters_defaults_to_3(self):
        self.assertEqual(self.config.gear_acceptance_min_voters, 3)

    def test_max_rejected_before_cooldown_defaults_to_3(self):
        self.assertEqual(self.config.max_rejected_before_cooldown, 3)

    # --- topic / subtopic proposal feature -------------------------------
    def test_topic_proposals_enabled_defaults_true(self):
        self.assertTrue(self.config.topic_proposals_enabled)

    def test_topic_proposal_voting_days_defaults_to_7(self):
        self.assertEqual(self.config.topic_proposal_voting_days, 7)

    def test_topic_proposal_pass_ratio_defaults_to_three_quarters(self):
        # Stored as a Decimal (max_digits=4, decimal_places=3 -> 0.750).
        self.assertEqual(self.config.topic_proposal_pass_ratio, Decimal("0.750"))

    def test_defaults_survive_a_round_trip_to_the_db(self):
        # Defaults are real column defaults, not just Python-side attributes.
        self.config.save()
        reloaded = SiteConfiguration.get_solo()
        self.assertEqual(reloaded.gear_acceptance_min_net_votes, 3)
        self.assertEqual(reloaded.gear_acceptance_min_voters, 3)
        self.assertEqual(reloaded.max_rejected_before_cooldown, 3)
        self.assertTrue(reloaded.topic_proposals_enabled)
        self.assertEqual(reloaded.topic_proposal_voting_days, 7)
        self.assertEqual(reloaded.topic_proposal_pass_ratio, Decimal("0.750"))
        self.assertTrue(reloaded.founder_level_achievable)
