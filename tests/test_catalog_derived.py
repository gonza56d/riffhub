"""Tests for GuitarModel's *derived facets* — the "truth guarantee" columns
that PRODUCT.md says must be calculated from attached components, never typed.

Covered:
- pickup_combination (read bridge -> middle -> neck, e.g. HSS)
- electronics_type (all-active ACTIVE / all-passive PASSIVE / mixed MIXED / none UNKNOWN)
- has_hum_cancellation (>=1 humbucking pickup type)
- has_tremolo / has_piezo (derived from the bridge)
- has_locking_tuners (derived from the tuner)
- neck_thickness_class (neck_depth_1st_fret_mm thresholds)
- is_multiscale (scale min != max)
- recompute triggered by GuitarModel.save and GuitarPickup post_save/post_delete signals

All facets are recomputed via catalog.signals; tests therefore exercise the
real signal path (plain .save() / .create() / .delete()) and confirm the
denormalised columns match what compute_derived() intends.
"""

from decimal import Decimal

from django.test import TestCase

from catalog.constants import (
    NECK_THICK_MIN_MM,
    NECK_THIN_MAX_MM,
    ElectronicsType,
    NeckThickness,
    PickupPosition,
)
from catalog.models import (
    Brand,
    Bridge,
    BridgeType,
    GuitarModel,
    GuitarPickup,
    NutMaterial,
    Pickup,
    PickupType,
    Tuner,
)


class DerivedFacetTestBase(TestCase):
    """Shared minimal fixtures: a brand plus the pickup-type vocabulary used to
    build combination strings."""

    def setUp(self):
        self.brand = Brand.objects.create(name="Fender")

        # Pickup types: H is humbucking, S/P are single-coil-ish (not humbucking).
        self.humbucker = PickupType.objects.create(
            name="Humbucker", symbol="H", is_humbucking=True
        )
        self.single = PickupType.objects.create(
            name="Single-coil", symbol="S", is_humbucking=False
        )
        self.p90 = PickupType.objects.create(
            name="P-90", symbol="P", is_humbucking=False
        )

    # -- helpers ------------------------------------------------------------
    def make_guitar(self, **overrides):
        """A guitar with the two required scale fields equal (non-multiscale)
        unless overridden."""
        defaults = dict(
            brand=self.brand,
            name="Test Guitar",
            num_strings=6,
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("25.500"),
        )
        defaults.update(overrides)
        return GuitarModel.objects.create(**defaults)

    def make_pickup(self, pickup_type, *, is_active=False, name="PU"):
        return Pickup.objects.create(
            brand=self.brand, name=name, pickup_type=pickup_type, is_active=is_active
        )

    def add_pickup(self, guitar, pickup, position):
        return GuitarPickup.objects.create(
            guitar=guitar, pickup=pickup, position=position
        )


# ---------------------------------------------------------------------------
# pickup_combination
# ---------------------------------------------------------------------------
class PickupCombinationTests(DerivedFacetTestBase):
    def test_hss_read_bridge_to_neck(self):
        """bridge=H, middle=S, neck=S => 'HSS' (read bridge -> neck)."""
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="HB"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, name="S1"),
                        PickupPosition.MIDDLE)
        self.add_pickup(guitar, self.make_pickup(self.single, name="S2"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HSS")

    def test_combination_ordering_independent_of_insertion_order(self):
        """Even if links are added neck-first, the string is bridge->neck."""
        guitar = self.make_guitar()
        # Insert in a deliberately scrambled order.
        self.add_pickup(guitar, self.make_pickup(self.single, name="N"),
                        PickupPosition.NECK)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, name="M"),
                        PickupPosition.MIDDLE)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HSS")

    def test_hsh_combination(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, name="M"),
                        PickupPosition.MIDDLE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HSH")

    def test_hh_two_humbuckers(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HH")

    def test_single_neck_pickup_only(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.p90, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "P")

    def test_no_pickups_empty_combination(self):
        guitar = self.make_guitar()
        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "")

    def test_two_pickup_bridge_neck_combination(self):
        """A two-pickup (bridge + neck, no middle) layout reads bridge->neck."""
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, name="N"),
                        PickupPosition.NECK)
        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HS")


# ---------------------------------------------------------------------------
# electronics_type
# ---------------------------------------------------------------------------
class ElectronicsTypeTests(DerivedFacetTestBase):
    def test_all_active_is_active(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)

    def test_all_passive_is_passive(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.single, is_active=False, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, is_active=False, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.PASSIVE)

    def test_mixed_active_and_passive_is_mixed(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, is_active=False, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.MIXED)

    def test_no_pickups_is_unknown(self):
        guitar = self.make_guitar()
        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.UNKNOWN)

    def test_single_active_pickup_is_active(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="N"),
                        PickupPosition.NECK)
        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)

    def test_single_passive_pickup_is_passive(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.single, is_active=False, name="N"),
                        PickupPosition.NECK)
        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.PASSIVE)


# ---------------------------------------------------------------------------
# has_hum_cancellation
# ---------------------------------------------------------------------------
class HumCancellationTests(DerivedFacetTestBase):
    def test_true_with_one_humbucking_pickup(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.single, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertTrue(guitar.has_hum_cancellation)

    def test_false_with_only_single_coils(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.single, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.p90, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertFalse(guitar.has_hum_cancellation)

    def test_false_with_no_pickups(self):
        guitar = self.make_guitar()
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_hum_cancellation)

    def test_true_when_all_pickups_humbucking(self):
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertTrue(guitar.has_hum_cancellation)


# ---------------------------------------------------------------------------
# has_tremolo / has_piezo (from the bridge)
# ---------------------------------------------------------------------------
class BridgeDerivedTests(DerivedFacetTestBase):
    def setUp(self):
        super().setUp()
        self.trem_type = BridgeType.objects.create(
            name="Vintage tremolo", is_tremolo=True, is_locking=False
        )
        self.hardtail_type = BridgeType.objects.create(
            name="Hardtail", is_tremolo=False, is_locking=False
        )

    def test_has_tremolo_true_for_tremolo_bridge(self):
        bridge = Bridge.objects.create(
            brand=self.brand, name="Synchronized Tremolo",
            bridge_type=self.trem_type, has_piezo=False,
        )
        guitar = self.make_guitar(bridge=bridge)
        guitar.refresh_from_db()
        self.assertTrue(guitar.has_tremolo)
        self.assertFalse(guitar.has_piezo)

    def test_has_tremolo_false_for_hardtail(self):
        bridge = Bridge.objects.create(
            brand=self.brand, name="Hardtail Bridge",
            bridge_type=self.hardtail_type, has_piezo=False,
        )
        guitar = self.make_guitar(bridge=bridge)
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_tremolo)

    def test_has_piezo_true_when_bridge_has_piezo(self):
        bridge = Bridge.objects.create(
            brand=self.brand, name="Piezo Hardtail",
            bridge_type=self.hardtail_type, has_piezo=True,
        )
        guitar = self.make_guitar(bridge=bridge)
        guitar.refresh_from_db()
        self.assertTrue(guitar.has_piezo)
        self.assertFalse(guitar.has_tremolo)

    def test_no_bridge_means_no_tremolo_no_piezo(self):
        guitar = self.make_guitar()  # bridge defaults to None
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_tremolo)
        self.assertFalse(guitar.has_piezo)

    def test_tremolo_and_piezo_can_both_be_true(self):
        bridge = Bridge.objects.create(
            brand=self.brand, name="Trem + piezo",
            bridge_type=self.trem_type, has_piezo=True,
        )
        guitar = self.make_guitar(bridge=bridge)
        guitar.refresh_from_db()
        self.assertTrue(guitar.has_tremolo)
        self.assertTrue(guitar.has_piezo)

    def test_changing_bridge_recomputes_on_save(self):
        """Re-pointing the guitar's bridge and saving must recompute facets."""
        trem_bridge = Bridge.objects.create(
            brand=self.brand, name="Trem", bridge_type=self.trem_type,
        )
        hardtail_bridge = Bridge.objects.create(
            brand=self.brand, name="HT", bridge_type=self.hardtail_type,
        )
        guitar = self.make_guitar(bridge=trem_bridge)
        guitar.refresh_from_db()
        self.assertTrue(guitar.has_tremolo)

        guitar.bridge = hardtail_bridge
        guitar.save()
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_tremolo)


# ---------------------------------------------------------------------------
# has_locking_tuners (from the tuner)
# ---------------------------------------------------------------------------
class LockingTunersTests(DerivedFacetTestBase):
    def test_true_for_locking_tuner(self):
        tuner = Tuner.objects.create(brand=self.brand, name="Locking Tuner",
                                     is_locking=True)
        guitar = self.make_guitar(tuners=tuner)
        guitar.refresh_from_db()
        self.assertTrue(guitar.has_locking_tuners)

    def test_false_for_non_locking_tuner(self):
        tuner = Tuner.objects.create(brand=self.brand, name="Vintage Tuner",
                                     is_locking=False)
        guitar = self.make_guitar(tuners=tuner)
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_locking_tuners)

    def test_false_with_no_tuner(self):
        guitar = self.make_guitar()  # tuners default None
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_locking_tuners)

    def test_swapping_to_locking_tuner_recomputes(self):
        plain = Tuner.objects.create(brand=self.brand, name="Plain", is_locking=False)
        locking = Tuner.objects.create(brand=self.brand, name="Lock", is_locking=True)
        guitar = self.make_guitar(tuners=plain)
        guitar.refresh_from_db()
        self.assertFalse(guitar.has_locking_tuners)

        guitar.tuners = locking
        guitar.save()
        guitar.refresh_from_db()
        self.assertTrue(guitar.has_locking_tuners)


# ---------------------------------------------------------------------------
# neck_thickness_class (from neck_depth_1st_fret_mm thresholds)
# ---------------------------------------------------------------------------
class NeckThicknessTests(DerivedFacetTestBase):
    def test_thin_at_or_below_thin_max(self):
        # NECK_THIN_MAX_MM == 19.5 ; <= is THIN.
        guitar = self.make_guitar(neck_depth_1st_fret_mm=Decimal("19.0"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THIN)

    def test_thin_exactly_at_thin_max_boundary(self):
        guitar = self.make_guitar(
            neck_depth_1st_fret_mm=Decimal(str(NECK_THIN_MAX_MM))
        )
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THIN)

    def test_medium_between_thresholds(self):
        # Strictly between 19.5 and 21.5 -> MEDIUM.
        guitar = self.make_guitar(neck_depth_1st_fret_mm=Decimal("20.5"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.MEDIUM)

    def test_thick_at_or_above_thick_min(self):
        guitar = self.make_guitar(neck_depth_1st_fret_mm=Decimal("22.0"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THICK)

    def test_thick_exactly_at_thick_min_boundary(self):
        guitar = self.make_guitar(
            neck_depth_1st_fret_mm=Decimal(str(NECK_THICK_MIN_MM))
        )
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THICK)

    def test_unknown_when_depth_missing(self):
        guitar = self.make_guitar(neck_depth_1st_fret_mm=None)
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.UNKNOWN)

    def test_just_above_thin_max_is_medium(self):
        """Just above the THIN cutoff (not <=) must be MEDIUM, not THIN."""
        guitar = self.make_guitar(neck_depth_1st_fret_mm=Decimal("19.6"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.MEDIUM)

    def test_just_below_thick_min_is_medium(self):
        """Just below the THICK cutoff (not >=) must be MEDIUM, not THICK."""
        guitar = self.make_guitar(neck_depth_1st_fret_mm=Decimal("21.4"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.MEDIUM)

    def test_changing_depth_recomputes_class(self):
        guitar = self.make_guitar(neck_depth_1st_fret_mm=Decimal("18.0"))
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THIN)

        guitar.neck_depth_1st_fret_mm = Decimal("23.0")
        guitar.save()
        guitar.refresh_from_db()
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THICK)


# ---------------------------------------------------------------------------
# is_multiscale (scale min != max)
# ---------------------------------------------------------------------------
class MultiscaleTests(DerivedFacetTestBase):
    def test_not_multiscale_when_min_equals_max(self):
        guitar = self.make_guitar(
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("25.500"),
        )
        guitar.refresh_from_db()
        self.assertFalse(guitar.is_multiscale)

    def test_multiscale_when_min_differs_from_max(self):
        guitar = self.make_guitar(
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("27.000"),
        )
        guitar.refresh_from_db()
        self.assertTrue(guitar.is_multiscale)

    def test_changing_scale_to_fanned_sets_multiscale(self):
        guitar = self.make_guitar(
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("25.500"),
        )
        guitar.refresh_from_db()
        self.assertFalse(guitar.is_multiscale)

        guitar.scale_length_max_inches = Decimal("26.500")
        guitar.save()
        guitar.refresh_from_db()
        self.assertTrue(guitar.is_multiscale)

    def test_tiny_scale_difference_is_multiscale(self):
        """Any difference at the stored precision counts as multiscale."""
        guitar = self.make_guitar(
            scale_length_min_inches=Decimal("24.750"),
            scale_length_max_inches=Decimal("24.751"),
        )
        guitar.refresh_from_db()
        self.assertTrue(guitar.is_multiscale)


# ---------------------------------------------------------------------------
# Signal-driven recompute on adding/removing a GuitarPickup
# ---------------------------------------------------------------------------
class PickupSignalRecomputeTests(DerivedFacetTestBase):
    def test_adding_pickup_recomputes_facets(self):
        """post_save on GuitarPickup updates combination/electronics/hum."""
        guitar = self.make_guitar()
        guitar.refresh_from_db()
        # baseline: nothing yet.
        self.assertEqual(guitar.pickup_combination, "")
        self.assertEqual(guitar.electronics_type, ElectronicsType.UNKNOWN)
        self.assertFalse(guitar.has_hum_cancellation)

        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "H")
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)
        self.assertTrue(guitar.has_hum_cancellation)

    def test_removing_pickup_recomputes_facets(self):
        """post_delete on GuitarPickup recomputes; removing the only humbucker
        flips hum-cancellation back off and updates the combination string."""
        guitar = self.make_guitar()
        link_h = self.add_pickup(
            guitar, self.make_pickup(self.humbucker, is_active=False, name="B"),
            PickupPosition.BRIDGE,
        )
        self.add_pickup(
            guitar, self.make_pickup(self.single, is_active=False, name="N"),
            PickupPosition.NECK,
        )
        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HS")
        self.assertTrue(guitar.has_hum_cancellation)

        link_h.delete()

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "S")
        self.assertFalse(guitar.has_hum_cancellation)

    def test_removing_all_pickups_resets_to_unknown(self):
        guitar = self.make_guitar()
        link = self.add_pickup(
            guitar, self.make_pickup(self.humbucker, is_active=True, name="N"),
            PickupPosition.NECK,
        )
        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)

        link.delete()
        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "")
        self.assertEqual(guitar.electronics_type, ElectronicsType.UNKNOWN)
        self.assertFalse(guitar.has_hum_cancellation)

    def test_changing_pickup_link_active_flag_via_resave(self):
        """Saving a GuitarPickup again (post_save) re-derives electronics_type
        from the current pickup state."""
        guitar = self.make_guitar()
        pickup = self.make_pickup(self.humbucker, is_active=False, name="N")
        link = self.add_pickup(guitar, pickup, PickupPosition.NECK)
        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.PASSIVE)

        # Flip the underlying pickup to active, then re-save the link to fire
        # the GuitarPickup post_save signal (which recomputes the guitar).
        pickup.is_active = True
        pickup.save()
        link.save()

        guitar.refresh_from_db()
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)

    def test_deleting_guitar_with_pickups_does_not_error(self):
        """Cascade delete of GuitarPickup must not crash in the post_delete
        signal (the signal guards against the parent already being gone)."""
        guitar = self.make_guitar()
        self.add_pickup(guitar, self.make_pickup(self.humbucker, name="N"),
                        PickupPosition.NECK)
        guitar_pk = guitar.pk

        guitar.delete()  # cascades GuitarPickup; signal must short-circuit.

        self.assertFalse(GuitarModel.objects.filter(pk=guitar_pk).exists())
        self.assertFalse(GuitarPickup.objects.filter(guitar_id=guitar_pk).exists())


# ---------------------------------------------------------------------------
# compute_derived() unit behaviour (no signal layer)
# ---------------------------------------------------------------------------
class ComputeDerivedUnitTests(DerivedFacetTestBase):
    def test_compute_derived_returns_all_facet_keys(self):
        guitar = self.make_guitar()
        values = guitar.compute_derived()
        for key in (
            "is_multiscale",
            "neck_thickness_class",
            "has_tremolo",
            "has_piezo",
            "has_locking_tuners",
            "pickup_combination",
            "has_hum_cancellation",
            "electronics_type",
        ):
            self.assertIn(key, values)

    def test_recompute_derived_noop_for_unsaved_instance(self):
        """recompute_derived() returns early (no DB write) when pk is None."""
        guitar = GuitarModel(
            brand=self.brand,
            name="Unsaved",
            num_strings=6,
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("25.500"),
        )
        # Should simply do nothing rather than raise.
        guitar.recompute_derived()
        self.assertIsNone(guitar.pk)

    def test_compute_derived_unsaved_has_empty_pickup_facets(self):
        """Without a pk, pickup-derived facets default to empty/unknown."""
        guitar = GuitarModel(
            brand=self.brand,
            name="Unsaved",
            num_strings=6,
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("26.500"),
            neck_depth_1st_fret_mm=Decimal("20.0"),
        )
        values = guitar.compute_derived()
        self.assertEqual(values["pickup_combination"], "")
        self.assertEqual(values["electronics_type"], ElectronicsType.UNKNOWN)
        self.assertFalse(values["has_hum_cancellation"])
        # Non-pickup facets still compute from own fields.
        self.assertTrue(values["is_multiscale"])
        self.assertEqual(values["neck_thickness_class"], NeckThickness.MEDIUM)


# ---------------------------------------------------------------------------
# An integrated guitar exercising several facets at once.
# ---------------------------------------------------------------------------
class IntegratedFacetTests(DerivedFacetTestBase):
    def test_les_paul_like_guitar(self):
        """HH, passive, hum-cancelling, hardtail (tune-o-matic), thick neck,
        single scale -> a Les-Paul-ish facet profile."""
        ht_type = BridgeType.objects.create(
            name="Tune-o-matic", is_tremolo=False, is_locking=False
        )
        bridge = Bridge.objects.create(
            brand=self.brand, name="ABR-1", bridge_type=ht_type, has_piezo=False
        )
        tuner = Tuner.objects.create(brand=self.brand, name="Vintage", is_locking=False)
        guitar = self.make_guitar(
            name="Les Paulish",
            scale_length_min_inches=Decimal("24.750"),
            scale_length_max_inches=Decimal("24.750"),
            neck_depth_1st_fret_mm=Decimal("22.0"),
            bridge=bridge,
            tuners=tuner,
        )
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=False, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=False, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HH")
        self.assertEqual(guitar.electronics_type, ElectronicsType.PASSIVE)
        self.assertTrue(guitar.has_hum_cancellation)
        self.assertFalse(guitar.has_tremolo)
        self.assertFalse(guitar.has_piezo)
        self.assertFalse(guitar.has_locking_tuners)
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THICK)
        self.assertFalse(guitar.is_multiscale)

    def test_floyd_active_multiscale_locking_guitar(self):
        """A modern shred machine: locking-trem bridge, locking tuners, active
        HSH, multiscale, thin neck."""
        floyd_type = BridgeType.objects.create(
            name="Floyd Rose", is_tremolo=True, is_locking=True
        )
        bridge = Bridge.objects.create(
            brand=self.brand, name="Original Floyd", bridge_type=floyd_type,
            has_piezo=True, is_locking=True,
        )
        tuner = Tuner.objects.create(brand=self.brand, name="Locking", is_locking=True)
        guitar = self.make_guitar(
            name="Shredder",
            scale_length_min_inches=Decimal("25.500"),
            scale_length_max_inches=Decimal("27.000"),
            neck_depth_1st_fret_mm=Decimal("19.0"),
            bridge=bridge,
            tuners=tuner,
        )
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="B"),
                        PickupPosition.BRIDGE)
        self.add_pickup(guitar, self.make_pickup(self.single, is_active=True, name="M"),
                        PickupPosition.MIDDLE)
        self.add_pickup(guitar, self.make_pickup(self.humbucker, is_active=True, name="N"),
                        PickupPosition.NECK)

        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HSH")
        self.assertEqual(guitar.electronics_type, ElectronicsType.ACTIVE)
        self.assertTrue(guitar.has_hum_cancellation)
        self.assertTrue(guitar.has_tremolo)
        self.assertTrue(guitar.has_piezo)
        self.assertTrue(guitar.has_locking_tuners)
        self.assertEqual(guitar.neck_thickness_class, NeckThickness.THIN)
        self.assertTrue(guitar.is_multiscale)
