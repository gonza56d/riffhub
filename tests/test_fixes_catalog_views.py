"""Regression tests for confirmed bugs in ``catalog/views.py`` and
``catalog/views_submit.py``.

Covers:

* #1/#2/#12 — ``filter_guitars`` must not 500 the public browse page ``/`` on
  non-numeric FK/scale facet values (``?neck=abc``, ``?shape=xyz``,
  ``?country=foo``, ``?scale=foo``); valid values must still filter.
* #3 — a guitar submission with a non-integer pickup slot id must not 500 and
  must never leave an orphaned ``UNDER_REVISION`` ``GuitarModel`` behind (the
  create + component attach run inside a single ``transaction.atomic``).
* #28 — the public guitar detail page must surface only PUBLISHED attached
  components, and the submit form must offer only published pickups.

Fixtures are built by hand (no ``seed_catalog``) so each test exercises exactly
the rows it cares about. ``status`` defaults to ``UNDER_REVISION`` on
``CatalogEntry``, so every published fixture sets it explicitly.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.constants import PickupPosition, PublicationStatus
from catalog.models import (
    Brand,
    Bridge,
    BridgeType,
    Country,
    GuitarModel,
    GuitarPickup,
    NeckConstruction,
    Nut,
    NutMaterial,
    Pickup,
    PickupType,
    Tuner,
)
from catalog.views import filter_guitars
from core.models import SiteConfiguration

User = get_user_model()


# --- shared helpers --------------------------------------------------------

def _configure_thresholds():
    """Set the promotion thresholds so any level derivation never crashes."""
    config = SiteConfiguration.get_solo()
    config.collaborator_promotion_threshold = 3
    config.founder_threshold = 30
    config.save()
    return config


def _make_user(username, *, email_confirmed=True, **extra):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pw-12345",
        email_confirmed=email_confirmed,
        **extra,
    )


def _published_brand(name="Acme"):
    return Brand.objects.create(name=name, status=PublicationStatus.PUBLISHED)


def make_guitar(brand, name, **overrides):
    """Create a PUBLISHED GuitarModel with sane defaults for the required
    fields, overriding any facet/spec the test cares about."""
    defaults = dict(
        status=PublicationStatus.PUBLISHED,
        num_strings=6,
        scale_length_min_inches=Decimal("25.5"),
        scale_length_max_inches=Decimal("25.5"),
        num_frets=22,
    )
    defaults.update(overrides)
    return GuitarModel.objects.create(brand=brand, name=name, **defaults)


# --- #1/#2/#12: garbage facet values must not 500 the browse page ----------

class FilterGuitarsGarbageFacetTests(TestCase):
    """``filter_guitars`` / the ``/`` browse view must always return 200, even
    for non-numeric FK or scale facet values."""

    @classmethod
    def setUpTestData(cls):
        cls.brand = _published_brand()
        cls.usa = Country.objects.create(name="USA")
        cls.neck_bolt = NeckConstruction.objects.create(name="Bolt-on")
        cls.strat = make_guitar(
            cls.brand,
            "Strat",
            scale_length_min_inches=Decimal("25.5"),
            scale_length_max_inches=Decimal("25.5"),
            neck_construction=cls.neck_bolt,
            country_of_origin=cls.usa,
        )
        cls.lp = make_guitar(
            cls.brand,
            "Les Paul",
            scale_length_min_inches=Decimal("24.75"),
            scale_length_max_inches=Decimal("24.75"),
        )

    def _qs(self, query=""):
        from django.http import QueryDict

        return filter_guitars(QueryDict(query))

    # filter_guitars itself must not raise on junk values ------------------
    def test_filter_guitars_non_numeric_neck_no_crash(self):
        # Old behaviour: ValueError feeding "abc" into neck_construction_id.
        qs = self._qs("neck=abc")
        self.assertEqual(qs.count(), 2)

    def test_filter_guitars_non_numeric_shape_no_crash(self):
        qs = self._qs("shape=xyz")
        self.assertEqual(qs.count(), 2)

    def test_filter_guitars_non_numeric_country_no_crash(self):
        qs = self._qs("country=foo")
        self.assertEqual(qs.count(), 2)

    def test_filter_guitars_non_decimal_scale_no_crash(self):
        # Old behaviour: decimal.InvalidOperation feeding "foo" into the
        # scale_length_*_inches DecimalFields.
        qs = self._qs("scale=foo")
        self.assertEqual(qs.count(), 2)

    def test_filter_guitars_non_finite_scale_no_crash(self):
        # "NaN"/"Infinity" parse as *valid* Decimals but blow up the DB
        # comparison; they must be dropped like any other junk value.
        for junk in ("NaN", "Infinity", "-Infinity", "sNaN"):
            with self.subTest(scale=junk):
                self.assertEqual(self._qs(f"scale={junk}").count(), 2)

    def test_browse_page_non_finite_scale_returns_200(self):
        url = reverse("catalog:browse")
        for junk in ("NaN", "Infinity"):
            with self.subTest(scale=junk):
                self.assertEqual(self.client.get(url, {"scale": junk}).status_code, 200)

    # the public browse page (the actual bug report) -----------------------
    def test_browse_page_neck_garbage_returns_200(self):
        url = reverse("catalog:browse")
        self.assertEqual(self.client.get(url, {"neck": "abc"}).status_code, 200)

    def test_browse_page_shape_garbage_returns_200(self):
        url = reverse("catalog:browse")
        self.assertEqual(self.client.get(url, {"shape": "xyz"}).status_code, 200)

    def test_browse_page_country_garbage_returns_200(self):
        url = reverse("catalog:browse")
        self.assertEqual(self.client.get(url, {"country": "foo"}).status_code, 200)

    def test_browse_page_scale_garbage_returns_200(self):
        url = reverse("catalog:browse")
        self.assertEqual(self.client.get(url, {"scale": "foo"}).status_code, 200)

    # valid values must still filter exactly as before ---------------------
    def test_valid_neck_id_still_filters(self):
        qs = self._qs(f"neck={self.neck_bolt.pk}")
        self.assertEqual([g.name for g in qs], ["Strat"])

    def test_valid_country_id_still_filters(self):
        qs = self._qs(f"country={self.usa.pk}")
        self.assertEqual([g.name for g in qs], ["Strat"])

    def test_valid_scale_value_still_filters(self):
        qs = self._qs("scale=24.75")
        self.assertEqual([g.name for g in qs], ["Les Paul"])

    def test_valid_neck_id_via_browse_page(self):
        url = reverse("catalog:browse")
        response = self.client.get(url, {"neck": str(self.neck_bolt.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [g.name for g in response.context["page_obj"]], ["Strat"]
        )


# --- #3: a bad pickup slot id must not 500 / orphan a guitar ---------------

class GuitarSubmissionBadPickupSlotTests(TestCase):
    """A non-integer pickup slot id must be ignored, never 500, and never leave
    a committed orphan guitar behind."""

    def setUp(self):
        _configure_thresholds()
        self.brand = Brand.objects.create(
            name="Ibanez", status=PublicationStatus.PUBLISHED
        )
        self.user = _make_user("guitar_submitter", email_confirmed=True)
        self.client.force_login(self.user)
        self.url = reverse("catalog:submit_entry", args=["guitar"])

    def _valid_payload(self, **overrides):
        data = {
            "brand": self.brand.pk,
            "name": "RG550",
            "num_strings": "6",
            "scale_length_min_inches": "25.5",
            "scale_length_max_inches": "25.5",
        }
        data.update(overrides)
        return data

    def test_non_numeric_pickup_slot_does_not_500(self):
        # Old behaviour: ValueError from Pickup.objects.filter(pk="notanumber").
        response = self.client.post(
            self.url, self._valid_payload(name="NaN Slot", pickup_bridge="notanumber")
        )
        self.assertIn(response.status_code, (200, 302))

    def test_non_numeric_pickup_slot_is_ignored_no_rows(self):
        response = self.client.post(
            self.url, self._valid_payload(name="NaN Slot", pickup_bridge="notanumber")
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="NaN Slot")
        self.assertEqual(GuitarPickup.objects.filter(guitar=guitar).count(), 0)

    def test_float_string_pickup_slot_does_not_500(self):
        # "3.0" is not a valid int -> must be skipped, not crash.
        response = self.client.post(
            self.url, self._valid_payload(name="Float Slot", pickup_bridge="3.0")
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertEqual(
            GuitarPickup.objects.filter(guitar__name="Float Slot").count(), 0
        )

    def test_no_orphan_guitar_when_attach_fails_midway(self):
        # The create + attach run inside one transaction.atomic(), so a failure
        # during component attachment rolls the whole submission back — no
        # orphaned UNDER_REVISION guitar is committed. Old behaviour: obj.save()
        # ran before attach, so a crash mid-attach left a committed orphan.
        with mock.patch(
            "catalog.views_submit._attach_pickups",
            side_effect=ValueError("boom"),
        ):
            with self.assertRaises(ValueError):
                self.client.post(self.url, self._valid_payload(name="Orphan Risk"))
        self.assertFalse(GuitarModel.objects.filter(name="Orphan Risk").exists())


# --- #28: only PUBLISHED components surface / are offered -------------------

class GuitarDetailPublishedComponentsTests(TestCase):
    """The public guitar detail page must hide unpublished attached components."""

    def setUp(self):
        self.brand = _published_brand("Fender")
        self.ptype = PickupType.objects.create(
            name="Single-coil", symbol="S", is_humbucking=False
        )
        self.btype = BridgeType.objects.create(name="Hardtail")
        self.nut_material = NutMaterial.objects.create(name="Bone")

    def _guitar_with(self, **component_overrides):
        guitar = make_guitar(self.brand, "Test Guitar")
        for attr, value in component_overrides.items():
            setattr(guitar, attr, value)
        guitar.save()
        return guitar

    def test_unpublished_bridge_not_rendered(self):
        bridge = Bridge.objects.create(
            brand=self.brand,
            name="Secret Bridge",
            bridge_type=self.btype,
            status=PublicationStatus.UNDER_REVISION,
        )
        guitar = self._guitar_with(bridge=bridge)
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["guitar"].bridge)
        self.assertNotContains(response, "Secret Bridge")

    def test_published_bridge_is_rendered(self):
        bridge = Bridge.objects.create(
            brand=self.brand,
            name="Public Bridge",
            bridge_type=self.btype,
            status=PublicationStatus.PUBLISHED,
        )
        guitar = self._guitar_with(bridge=bridge)
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["guitar"].bridge, bridge)
        self.assertContains(response, "Public Bridge")

    def test_rejected_tuner_not_rendered(self):
        tuner = Tuner.objects.create(
            brand=self.brand,
            name="Rejected Tuner",
            status=PublicationStatus.REJECTED,
        )
        guitar = self._guitar_with(tuners=tuner)
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["guitar"].tuners)
        self.assertNotContains(response, "Rejected Tuner")

    def test_unpublished_nut_not_rendered(self):
        nut = Nut.objects.create(
            brand=self.brand,
            name="Hidden Nut",
            material=self.nut_material,
            status=PublicationStatus.UNDER_REVISION,
        )
        guitar = self._guitar_with(nut=nut)
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["guitar"].nut)
        self.assertNotContains(response, "Hidden Nut")

    def test_unpublished_pickup_not_rendered(self):
        pickup = Pickup.objects.create(
            brand=self.brand,
            name="Hidden Pickup",
            pickup_type=self.ptype,
            status=PublicationStatus.UNDER_REVISION,
        )
        guitar = make_guitar(self.brand, "PU Guitar")
        GuitarPickup.objects.create(
            guitar=guitar, pickup=pickup, position=PickupPosition.BRIDGE
        )
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pickups"], [])
        self.assertNotContains(response, "Hidden Pickup")

    def test_published_pickup_is_rendered(self):
        pickup = Pickup.objects.create(
            brand=self.brand,
            name="Visible Pickup",
            pickup_type=self.ptype,
            status=PublicationStatus.PUBLISHED,
        )
        guitar = make_guitar(self.brand, "PU Guitar 2")
        GuitarPickup.objects.create(
            guitar=guitar, pickup=pickup, position=PickupPosition.BRIDGE
        )
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["pickups"]), 1)
        self.assertContains(response, "Visible Pickup")

    def test_component_with_unpublished_brand_not_rendered(self):
        # A published bridge whose brand is still pending must not leak either.
        pending_brand = Brand.objects.create(
            name="Pending Brand", status=PublicationStatus.UNDER_REVISION
        )
        bridge = Bridge.objects.create(
            brand=pending_brand,
            name="Bridge On Pending Brand",
            bridge_type=self.btype,
            status=PublicationStatus.PUBLISHED,
        )
        guitar = self._guitar_with(bridge=bridge)
        url = reverse("catalog:detail", args=[guitar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["guitar"].bridge)
        self.assertNotContains(response, "Bridge On Pending Brand")


# --- #28 part A: the submit form offers only PUBLISHED pickups -------------

class SubmitFormPublishedPickupChoicesTests(TestCase):
    """The guitar submit form's pickup slots must list only published pickups,
    and an unpublished pickup id must not get attached."""

    def setUp(self):
        _configure_thresholds()
        self.brand = _published_brand("Ibanez")
        self.ptype = PickupType.objects.create(
            name="Humbucker", symbol="H", is_humbucking=True
        )
        self.user = _make_user("submitter", email_confirmed=True)
        self.client.force_login(self.user)
        self.url = reverse("catalog:submit_entry", args=["guitar"])

    def test_form_pickup_queryset_excludes_unpublished(self):
        published = Pickup.objects.create(
            brand=self.brand,
            name="Published PU",
            pickup_type=self.ptype,
            status=PublicationStatus.PUBLISHED,
        )
        unpublished = Pickup.objects.create(
            brand=self.brand,
            name="Unpublished PU",
            pickup_type=self.ptype,
            status=PublicationStatus.UNDER_REVISION,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        offered = list(response.context["pickups"])
        self.assertIn(published, offered)
        self.assertNotIn(unpublished, offered)

    def test_unpublished_pickup_id_not_attached(self):
        unpublished = Pickup.objects.create(
            brand=self.brand,
            name="Unpublished PU",
            pickup_type=self.ptype,
            status=PublicationStatus.UNDER_REVISION,
        )
        response = self.client.post(
            self.url,
            {
                "brand": self.brand.pk,
                "name": "Sneaky Guitar",
                "num_strings": "6",
                "scale_length_min_inches": "25.5",
                "scale_length_max_inches": "25.5",
                "pickup_bridge": unpublished.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="Sneaky Guitar")
        self.assertEqual(GuitarPickup.objects.filter(guitar=guitar).count(), 0)
