"""Tests for the collab-db SUBMISSION UI (``catalog/views_submit.py``, ``/submit/``).

Covers PRODUCT.md's submission rules as wired through the views:

* ``/submit/`` (``catalog:submit_index``) — anonymous users are sent to login,
  e-mail-confirmed users see the "kind" cards, unconfirmed users hit the gate.
* ``/submit/<kind>/`` (``catalog:submit_entry``) — GET renders a form for each of
  the six kinds (guitar, brand, bridge, pickup, tuner, nut); a confirmed POST
  creates the entry ``UNDER_REVISION`` with ``submitted_by`` = the current user.
* Guitar submit enforces required fields and turns up to three positioned pickup
  slots into ``GuitarPickup`` rows.
* Unconfirmed / anonymous POSTs are blocked and create nothing.

All HTTP traffic goes through ``django.test.Client`` (no CSRF under the test
runner; ``force_login`` needs no password). Reference vocabularies / gear are
built minimally per-test (or via ``seed_catalog`` where the full catalog helps).
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
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
    Nut,
    NutMaterial,
    Pickup,
    PickupType,
    Tuner,
)
from core.models import SiteConfiguration

User = get_user_model()


def _make_user(username, *, email_confirmed=True, **extra):
    """Create a user; e-mail derived from the username, confirmed by default."""
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pw-12345",
        email_confirmed=email_confirmed,
        **extra,
    )


def _configure_thresholds():
    """Set the promotion thresholds so any level derivation never crashes.

    The submission gate itself does not read these, but configuring the
    singleton keeps the whole stack (level property, context processors)
    consistent and avoids ``ImproperlyConfigured`` surprises.
    """
    config = SiteConfiguration.get_solo()
    config.collaborator_promotion_threshold = 3
    config.founder_threshold = 30
    config.save()
    return config


class SubmitIndexAccessTests(TestCase):
    """``/submit/`` landing page: anon -> login, confirmed -> cards, unconfirmed -> gate."""

    def setUp(self):
        _configure_thresholds()
        self.url = reverse("catalog:submit_index")

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        # @login_required sends to settings.LOGIN_URL with ?next=<path>.
        self.assertIn(reverse("login"), response["Location"])
        self.assertIn(self.url, response["Location"])

    def test_anonymous_redirect_preserves_next(self):
        response = self.client.get(self.url)
        self.assertIn(f"next={self.url}", response["Location"])

    def test_confirmed_user_sees_kind_cards(self):
        user = _make_user("confirmed_browser", email_confirmed=True)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_submit"])
        # All six kinds are offered as cards.
        slugs = {k["slug"] for k in response.context["kinds"]}
        self.assertEqual(
            slugs, {"guitar", "brand", "bridge", "pickup", "tuner", "nut"}
        )
        self.assertContains(response, "kind-card")
        # Each kind links to its submit_entry URL.
        self.assertContains(
            response, reverse("catalog:submit_entry", args=["guitar"])
        )

    def test_unconfirmed_user_sees_gate_not_cards(self):
        user = _make_user("unconfirmed_browser", email_confirmed=False)
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_submit"])
        # Gate panel is shown; no kind cards are rendered.
        self.assertContains(response, "submit-gate")
        self.assertNotContains(response, "kind-card")
        self.assertContains(response, "Confirm your e-mail first")

    def test_user_in_reject_cooldown_sees_gate(self):
        # Confirmed e-mail, but too many rejected submissions -> troll-guard
        # blocks them, so the index must show the gate rather than the cards.
        config = SiteConfiguration.get_solo()
        user = _make_user(
            "cooldown_user",
            email_confirmed=True,
            rejected_submissions_count=config.max_rejected_before_cooldown + 1,
        )
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_submit"])
        self.assertNotContains(response, "kind-card")


class SubmitEntryAccessTests(TestCase):
    """Access control on ``/submit/<kind>/`` (login + confirmation gate + 404)."""

    def setUp(self):
        _configure_thresholds()

    def test_unknown_kind_returns_404(self):
        user = _make_user("confirmed_404", email_confirmed=True)
        self.client.force_login(user)
        response = self.client.get(
            reverse("catalog:submit_entry", args=["amplifier"])
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_get_redirected_to_login(self):
        url = reverse("catalog:submit_entry", args=["brand"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_unconfirmed_get_redirected_to_index_with_error(self):
        user = _make_user("unconfirmed_get", email_confirmed=False)
        self.client.force_login(user)
        url = reverse("catalog:submit_entry", args=["brand"])
        response = self.client.get(url, follow=True)
        # Bounced to the submit index with a flash error.
        self.assertRedirects(response, reverse("catalog:submit_index"))
        msgs = [m.message for m in response.context["messages"]]
        self.assertTrue(
            any("Confirm your e-mail" in m for m in msgs),
            msgs,
        )

    def test_confirmed_get_renders_form_for_every_kind(self):
        user = _make_user("confirmed_forms", email_confirmed=True)
        self.client.force_login(user)
        for kind in ("guitar", "brand", "bridge", "pickup", "tuner", "nut"):
            with self.subTest(kind=kind):
                response = self.client.get(
                    reverse("catalog:submit_entry", args=[kind])
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["kind"], kind)
                self.assertIsNotNone(response.context["form"])
                self.assertContains(response, "Submit for review")

    def test_guitar_get_exposes_pickup_slots(self):
        user = _make_user("guitar_form", email_confirmed=True)
        self.client.force_login(user)
        response = self.client.get(
            reverse("catalog:submit_entry", args=["guitar"])
        )
        self.assertTrue(response.context["is_guitar"])
        self.assertIsNotNone(response.context["pickup_slots"])
        # The three named slots are present in the rendered form.
        self.assertContains(response, 'name="pickup_bridge"')
        self.assertContains(response, 'name="pickup_middle"')
        self.assertContains(response, 'name="pickup_neck"')

    def test_non_guitar_get_has_no_pickup_slots(self):
        user = _make_user("brand_form", email_confirmed=True)
        self.client.force_login(user)
        response = self.client.get(
            reverse("catalog:submit_entry", args=["brand"])
        )
        self.assertFalse(response.context["is_guitar"])
        self.assertIsNone(response.context["pickup_slots"])
        self.assertIsNone(response.context["pickups"])


class BrandSubmitTests(TestCase):
    """Submitting a Brand (the simplest entity — no FK dependencies required)."""

    def setUp(self):
        _configure_thresholds()
        self.url = reverse("catalog:submit_entry", args=["brand"])

    def test_confirmed_post_creates_brand_under_revision(self):
        user = _make_user("brand_submitter", email_confirmed=True)
        self.client.force_login(user)
        response = self.client.post(
            self.url,
            {
                "name": "Charvel",
                "website": "https://charvel.example",
                "description": "Superstrats.",
            },
        )
        self.assertEqual(response.status_code, 200)
        # Renders the done page rather than redirecting.
        self.assertTemplateUsed(response, "catalog/submit/done.html")
        brand = Brand.objects.get(name="Charvel")
        self.assertEqual(brand.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(brand.submitted_by, user)
        self.assertContains(response, "pending review")

    def test_post_missing_required_name_creates_nothing(self):
        user = _make_user("brand_bad", email_confirmed=True)
        self.client.force_login(user)
        response = self.client.post(self.url, {"name": "", "description": "x"})
        # Form re-rendered (not the done page) and nothing persisted.
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/form.html")
        self.assertFalse(Brand.objects.exists())
        self.assertTrue(response.context["form"].errors)

    def test_unconfirmed_post_blocked_creates_nothing(self):
        user = _make_user("brand_unconfirmed", email_confirmed=False)
        self.client.force_login(user)
        response = self.client.post(self.url, {"name": "ShouldNotExist"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("catalog:submit_index"))
        self.assertFalse(Brand.objects.filter(name="ShouldNotExist").exists())

    def test_anonymous_post_blocked_creates_nothing(self):
        response = self.client.post(self.url, {"name": "AnonBrand"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(Brand.objects.filter(name="AnonBrand").exists())

    def test_cooldown_user_post_blocked_creates_nothing(self):
        config = SiteConfiguration.get_solo()
        user = _make_user(
            "brand_cooldown",
            email_confirmed=True,
            rejected_submissions_count=config.max_rejected_before_cooldown + 1,
        )
        self.client.force_login(user)
        response = self.client.post(self.url, {"name": "CooldownBrand"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("catalog:submit_index"))
        self.assertFalse(Brand.objects.filter(name="CooldownBrand").exists())


class GearSubmitTests(TestCase):
    """Submitting the four gear types: bridge, pickup, tuner, nut."""

    def setUp(self):
        _configure_thresholds()
        # Minimal shared reference data for the gear FKs.
        self.brand = Brand.objects.create(
            name="Generic Co", status=PublicationStatus.PUBLISHED
        )
        self.bridge_type = BridgeType.objects.create(
            name="Hardtail", is_tremolo=False
        )
        self.tremolo_type = BridgeType.objects.create(
            name="Floyd Rose", is_tremolo=True, is_locking=True
        )
        self.pickup_type = PickupType.objects.create(
            name="Humbucker", symbol="H", is_humbucking=True
        )
        self.nut_material = NutMaterial.objects.create(name="Bone")
        self.user = _make_user("gear_submitter", email_confirmed=True)
        self.client.force_login(self.user)

    def test_submit_bridge_creates_under_revision(self):
        url = reverse("catalog:submit_entry", args=["bridge"])
        response = self.client.post(
            url,
            {
                "brand": self.brand.pk,
                "name": "Floyd 1000",
                "description": "Double-locking trem.",
                "bridge_type": self.tremolo_type.pk,
                "has_piezo": "",
                "is_locking": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/done.html")
        bridge = Bridge.objects.get(name="Floyd 1000")
        self.assertEqual(bridge.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(bridge.submitted_by, self.user)
        self.assertEqual(bridge.bridge_type, self.tremolo_type)
        self.assertTrue(bridge.is_locking)

    def test_submit_pickup_creates_under_revision(self):
        url = reverse("catalog:submit_entry", args=["pickup"])
        response = self.client.post(
            url,
            {
                "brand": self.brand.pk,
                "name": "Hot Rails",
                "description": "",
                "pickup_type": self.pickup_type.pk,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        pickup = Pickup.objects.get(name="Hot Rails")
        self.assertEqual(pickup.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(pickup.submitted_by, self.user)
        self.assertTrue(pickup.is_active)

    def test_submit_tuner_creates_under_revision(self):
        url = reverse("catalog:submit_entry", args=["tuner"])
        response = self.client.post(
            url,
            {
                "brand": self.brand.pk,
                "name": "Trim-Lok",
                "description": "",
                "is_locking": "on",
                "ratio": "18:1",
                "tuner_type": "locking",
            },
        )
        self.assertEqual(response.status_code, 200)
        tuner = Tuner.objects.get(name="Trim-Lok")
        self.assertEqual(tuner.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(tuner.submitted_by, self.user)
        self.assertTrue(tuner.is_locking)
        self.assertEqual(tuner.ratio, "18:1")

    def test_submit_nut_creates_under_revision(self):
        url = reverse("catalog:submit_entry", args=["nut"])
        response = self.client.post(
            url,
            {
                "brand": self.brand.pk,
                "name": "Bone Nut",
                "description": "",
                "material": self.nut_material.pk,
                "is_locking": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        nut = Nut.objects.get(name="Bone Nut")
        self.assertEqual(nut.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(nut.submitted_by, self.user)
        self.assertEqual(nut.material, self.nut_material)
        self.assertFalse(nut.is_locking)

    def test_bridge_missing_required_brand_creates_nothing(self):
        url = reverse("catalog:submit_entry", args=["bridge"])
        response = self.client.post(
            url,
            {
                "name": "No Brand Bridge",
                "bridge_type": self.bridge_type.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/form.html")
        self.assertFalse(Bridge.objects.exists())
        self.assertIn("brand", response.context["form"].errors)

    def test_pickup_missing_required_type_creates_nothing(self):
        url = reverse("catalog:submit_entry", args=["pickup"])
        response = self.client.post(
            url,
            {"brand": self.brand.pk, "name": "Typeless"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Pickup.objects.exists())
        self.assertIn("pickup_type", response.context["form"].errors)


class GuitarSubmitTests(TestCase):
    """Guitar submission: required fields, status/submitted_by, pickup slots."""

    def setUp(self):
        _configure_thresholds()
        self.brand = Brand.objects.create(
            name="Ibanez", status=PublicationStatus.PUBLISHED
        )
        self.country = Country.objects.create(name="Japan")
        self.user = _make_user("guitar_submitter", email_confirmed=True)
        self.client.force_login(self.user)
        self.url = reverse("catalog:submit_entry", args=["guitar"])

    def _valid_payload(self, **overrides):
        """A minimal valid guitar POST: only the five required fields.

        Required = fields without null/blank on GuitarModel: brand, name,
        num_strings, scale_length_min_inches, scale_length_max_inches.
        """
        data = {
            "brand": self.brand.pk,
            "name": "RG550",
            "num_strings": "6",
            "scale_length_min_inches": "25.5",
            "scale_length_max_inches": "25.5",
        }
        data.update(overrides)
        return data

    def test_minimal_required_fields_create_guitar(self):
        response = self.client.post(self.url, self._valid_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/done.html")
        guitar = GuitarModel.objects.get(name="RG550")
        self.assertEqual(guitar.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(guitar.submitted_by, self.user)
        self.assertEqual(guitar.num_strings, 6)
        self.assertEqual(guitar.scale_length_min_inches, Decimal("25.5"))

    def test_missing_brand_is_rejected(self):
        payload = self._valid_payload()
        del payload["brand"]
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/form.html")
        self.assertFalse(GuitarModel.objects.exists())
        self.assertIn("brand", response.context["form"].errors)

    def test_missing_name_is_rejected(self):
        response = self.client.post(self.url, self._valid_payload(name=""))
        self.assertFalse(GuitarModel.objects.exists())
        self.assertIn("name", response.context["form"].errors)

    def test_missing_num_strings_is_rejected(self):
        payload = self._valid_payload()
        del payload["num_strings"]
        response = self.client.post(self.url, payload)
        self.assertFalse(GuitarModel.objects.exists())
        self.assertIn("num_strings", response.context["form"].errors)

    def test_missing_scale_min_is_rejected(self):
        payload = self._valid_payload()
        del payload["scale_length_min_inches"]
        response = self.client.post(self.url, payload)
        self.assertFalse(GuitarModel.objects.exists())
        self.assertIn("scale_length_min_inches", response.context["form"].errors)

    def test_missing_scale_max_is_rejected(self):
        payload = self._valid_payload()
        del payload["scale_length_max_inches"]
        response = self.client.post(self.url, payload)
        self.assertFalse(GuitarModel.objects.exists())
        self.assertIn("scale_length_max_inches", response.context["form"].errors)

    def test_empty_post_lists_all_required_errors(self):
        response = self.client.post(self.url, {})
        self.assertFalse(GuitarModel.objects.exists())
        errors = response.context["form"].errors
        for field in (
            "brand",
            "name",
            "num_strings",
            "scale_length_min_inches",
            "scale_length_max_inches",
        ):
            self.assertIn(field, errors)

    def test_optional_fields_left_blank_still_valid(self):
        # year_introduced / fret_material / etc. are nullable -> not required.
        response = self.client.post(
            self.url,
            self._valid_payload(
                name="Minimal Guitar",
                year_introduced="",
                fret_material="",
                neck_profile="",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(GuitarModel.objects.filter(name="Minimal Guitar").exists())

    def _make_pickup(self, name, symbol="H", humbucking=True, active=False):
        ptype, _ = PickupType.objects.get_or_create(
            name=f"Type-{symbol}-{humbucking}",
            defaults={"symbol": symbol, "is_humbucking": humbucking},
        )
        return Pickup.objects.create(
            brand=self.brand,
            name=name,
            pickup_type=ptype,
            is_active=active,
            status=PublicationStatus.PUBLISHED,
        )

    def test_three_positioned_pickups_become_guitar_pickup_rows(self):
        pu_bridge = self._make_pickup("Bridge HB")
        pu_middle = self._make_pickup("Middle SC", symbol="S", humbucking=False)
        pu_neck = self._make_pickup("Neck HB")
        response = self.client.post(
            self.url,
            self._valid_payload(
                name="HSH Guitar",
                pickup_bridge=pu_bridge.pk,
                pickup_middle=pu_middle.pk,
                pickup_neck=pu_neck.pk,
            ),
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="HSH Guitar")
        links = GuitarPickup.objects.filter(guitar=guitar)
        self.assertEqual(links.count(), 3)
        by_pos = {gp.position: gp.pickup_id for gp in links}
        self.assertEqual(by_pos[PickupPosition.BRIDGE], pu_bridge.pk)
        self.assertEqual(by_pos[PickupPosition.MIDDLE], pu_middle.pk)
        self.assertEqual(by_pos[PickupPosition.NECK], pu_neck.pk)

    def test_pickup_slots_drive_derived_facets(self):
        # Two humbuckers -> "HH" combination and hum-cancellation True, computed
        # via the post_save signal once the GuitarPickup rows are attached.
        pu_bridge = self._make_pickup("HB Bridge")
        pu_neck = self._make_pickup("HB Neck")
        self.client.post(
            self.url,
            self._valid_payload(
                name="HH Guitar",
                pickup_bridge=pu_bridge.pk,
                pickup_neck=pu_neck.pk,
            ),
        )
        guitar = GuitarModel.objects.get(name="HH Guitar")
        guitar.refresh_from_db()
        self.assertEqual(guitar.pickup_combination, "HH")
        self.assertTrue(guitar.has_hum_cancellation)

    def test_partial_pickup_slots_create_only_filled_rows(self):
        pu_bridge = self._make_pickup("Only Bridge")
        response = self.client.post(
            self.url,
            self._valid_payload(
                name="Single PU Guitar",
                pickup_bridge=pu_bridge.pk,
                pickup_middle="",
                pickup_neck="",
            ),
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="Single PU Guitar")
        links = GuitarPickup.objects.filter(guitar=guitar)
        self.assertEqual(links.count(), 1)
        self.assertEqual(links.first().position, PickupPosition.BRIDGE)

    def test_no_pickup_slots_create_no_rows(self):
        response = self.client.post(
            self.url, self._valid_payload(name="No PU Guitar")
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="No PU Guitar")
        self.assertEqual(GuitarPickup.objects.filter(guitar=guitar).count(), 0)
        self.assertEqual(guitar.pickup_combination, "")

    def test_bogus_pickup_id_is_silently_ignored(self):
        # _attach_pickups silently skips slots pointing at a missing pickup id;
        # the guitar itself is still created.
        response = self.client.post(
            self.url,
            self._valid_payload(
                name="Bogus PU Guitar",
                pickup_bridge="999999",
            ),
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="Bogus PU Guitar")
        self.assertEqual(GuitarPickup.objects.filter(guitar=guitar).count(), 0)

    def test_invalid_guitar_post_creates_no_pickup_rows(self):
        # If the form itself is invalid, no guitar and therefore no pickups.
        pu_bridge = self._make_pickup("Orphan PU")
        before = GuitarPickup.objects.count()
        response = self.client.post(
            self.url,
            {
                "name": "",  # missing required name -> invalid
                "num_strings": "6",
                "pickup_bridge": pu_bridge.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/form.html")
        self.assertFalse(GuitarModel.objects.exists())
        self.assertEqual(GuitarPickup.objects.count(), before)

    def test_multiscale_facet_derived_from_scale_range(self):
        # scale min != max -> is_multiscale True (derived on save via signals).
        response = self.client.post(
            self.url,
            self._valid_payload(
                name="Multiscale Guitar",
                scale_length_min_inches="25.5",
                scale_length_max_inches="27.0",
            ),
        )
        self.assertEqual(response.status_code, 200)
        guitar = GuitarModel.objects.get(name="Multiscale Guitar")
        guitar.refresh_from_db()
        self.assertTrue(guitar.is_multiscale)

    def test_unconfirmed_guitar_post_blocked(self):
        unconfirmed = _make_user("guitar_unconfirmed", email_confirmed=False)
        self.client.force_login(unconfirmed)
        response = self.client.post(
            self.url, self._valid_payload(name="Blocked Guitar")
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("catalog:submit_index"))
        self.assertFalse(GuitarModel.objects.filter(name="Blocked Guitar").exists())

    def test_anonymous_guitar_post_blocked(self):
        self.client.logout()
        response = self.client.post(
            self.url, self._valid_payload(name="AnonGuitar")
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertFalse(GuitarModel.objects.filter(name="AnonGuitar").exists())


class SubmitWithSeededCatalogTests(TestCase):
    """Smoke test against the full seeded catalog (real vocab + gear).

    Confirms the guitar submit path works end-to-end with the same reference
    data the app ships, including choosing real seeded pickups for the slots.
    """

    def setUp(self):
        _configure_thresholds()
        call_command("seed_catalog")
        self.user = _make_user("seed_submitter", email_confirmed=True)
        self.client.force_login(self.user)
        self.url = reverse("catalog:submit_entry", args=["guitar"])

    def test_guitar_form_lists_seeded_pickups(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # All published+under-revision pickups are offered (view uses .all()).
        self.assertEqual(
            list(response.context["pickups"]), list(Pickup.objects.all())
        )
        self.assertGreater(Pickup.objects.count(), 0)

    def test_submit_guitar_with_seeded_brand_and_pickups(self):
        brand = Brand.objects.get(name="Ibanez")
        pickups = list(Pickup.objects.all()[:2])
        response = self.client.post(
            self.url,
            {
                "brand": brand.pk,
                "name": "Custom Submission RG",
                "num_strings": "7",
                "scale_length_min_inches": "25.5",
                "scale_length_max_inches": "25.5",
                "pickup_bridge": pickups[0].pk,
                "pickup_neck": pickups[1].pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "catalog/submit/done.html")
        guitar = GuitarModel.objects.get(name="Custom Submission RG")
        self.assertEqual(guitar.status, PublicationStatus.UNDER_REVISION)
        self.assertEqual(guitar.submitted_by, self.user)
        self.assertEqual(
            GuitarPickup.objects.filter(guitar=guitar).count(), 2
        )
        # A brand-new under-revision guitar must NOT appear in published browse.
        self.assertNotIn(guitar, GuitarModel.objects.published())
