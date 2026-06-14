"""Tests for gear detail pages and catalog comment views (guitars + gear).

Covers ``gear_detail`` (per-kind spec pages with publication gating + the
"used on" reverse list), the gear links rendered on the guitar page, the
``add_catalog_comment`` POST endpoint, and comment pagination.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog import services
from catalog.constants import PickupPosition, PublicationStatus
from catalog.models import (
    Brand,
    Bridge,
    BridgeType,
    CatalogComment,
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
PUB = PublicationStatus.PUBLISHED


def make_user(username, **flags):
    return User.objects.create_user(
        username=username, email=f"{username}@example.com",
        password="pw-12345", email_confirmed=True, **flags,
    )


class GearDetailViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.brand = Brand.objects.create(name="Acme", status=PUB)
        cls.bt = BridgeType.objects.create(name="Tremolo", is_tremolo=True)
        cls.pt = PickupType.objects.create(name="Humbucker", symbol="H", is_humbucking=True)
        cls.nm = NutMaterial.objects.create(name="Bone")
        cls.bridge = Bridge.objects.create(brand=cls.brand, name="Trem-1", bridge_type=cls.bt, status=PUB)
        cls.pickup = Pickup.objects.create(brand=cls.brand, name="HB-1", pickup_type=cls.pt, status=PUB)
        cls.tuner = Tuner.objects.create(brand=cls.brand, name="Lock-1", is_locking=True, status=PUB)
        cls.nut = Nut.objects.create(brand=cls.brand, name="Nut-1", material=cls.nm, status=PUB)
        cls.guitar = GuitarModel.objects.create(
            brand=cls.brand, name="GTR-1", num_strings=6,
            scale_length_min_inches=Decimal("25.5"), scale_length_max_inches=Decimal("25.5"),
            bridge=cls.bridge, tuners=cls.tuner, nut=cls.nut, status=PUB,
        )
        GuitarPickup.objects.create(guitar=cls.guitar, pickup=cls.pickup, position=PickupPosition.BRIDGE)

    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def _gear_url(self, kind, obj):
        return reverse("catalog:gear_detail", args=[kind, obj.pk])

    def test_each_gear_kind_detail_renders(self):
        for kind, obj in (
            ("bridge", self.bridge), ("pickup", self.pickup),
            ("tuner", self.tuner), ("nut", self.nut),
        ):
            with self.subTest(kind=kind):
                resp = self.client.get(self._gear_url(kind, obj))
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, obj.name)

    def test_unknown_kind_404(self):
        self.assertEqual(self.client.get("/gear/widget/1/").status_code, 404)

    def test_unpublished_gear_404(self):
        draft = Bridge.objects.create(brand=self.brand, name="Draft", bridge_type=self.bt,
                                      status=PublicationStatus.UNDER_REVISION)
        self.assertEqual(self.client.get(self._gear_url("bridge", draft)).status_code, 404)

    def test_gear_with_unpublished_brand_404(self):
        draft_brand = Brand.objects.create(name="Pending", status=PublicationStatus.UNDER_REVISION)
        bridge = Bridge.objects.create(brand=draft_brand, name="B2", bridge_type=self.bt, status=PUB)
        self.assertEqual(self.client.get(self._gear_url("bridge", bridge)).status_code, 404)

    def test_gear_detail_lists_using_guitars(self):
        resp = self.client.get(self._gear_url("bridge", self.bridge))
        self.assertContains(resp, "GTR-1")
        self.assertContains(resp, reverse("catalog:detail", args=[self.guitar.pk]))

    def test_pickup_detail_lists_using_guitars(self):
        resp = self.client.get(self._gear_url("pickup", self.pickup))
        self.assertContains(resp, "GTR-1")

    def test_guitar_detail_links_to_each_gear(self):
        resp = self.client.get(reverse("catalog:detail", args=[self.guitar.pk]))
        self.assertContains(resp, self._gear_url("bridge", self.bridge))
        self.assertContains(resp, self._gear_url("pickup", self.pickup))
        self.assertContains(resp, self._gear_url("tuner", self.tuner))
        self.assertContains(resp, self._gear_url("nut", self.nut))


class CatalogCommentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.brand = Brand.objects.create(name="Acme", status=PUB)
        cls.guitar = GuitarModel.objects.create(
            brand=cls.brand, name="GTR-1", num_strings=6,
            scale_length_min_inches=Decimal("25.5"), scale_length_max_inches=Decimal("25.5"),
            status=PUB,
        )
        cls.url = reverse("catalog:detail", args=[cls.guitar.pk])
        cls.comment_url = reverse("catalog:add_comment", args=["guitar", cls.guitar.pk])

    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()
        self.user = make_user("alice")

    def test_post_comment_creates_and_redirects(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.comment_url, {"body": "Great guitar"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], self.url)
        self.assertEqual(CatalogComment.objects.filter(parent__isnull=True).count(), 1)
        self.assertContains(self.client.get(self.url), "Great guitar")

    def test_post_reply_creates_nested(self):
        top = services.add_catalog_comment(target=self.guitar, author=self.user, body="top")
        self.client.force_login(make_user("bob"))
        resp = self.client.post(self.comment_url, {"body": "a reply", "parent": top.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(top.replies.count(), 1)

    def test_anonymous_cannot_comment(self):
        resp = self.client.post(self.comment_url, {"body": "anon"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        self.assertEqual(CatalogComment.objects.count(), 0)

    def test_comments_paginate_at_ten(self):
        for i in range(11):
            services.add_catalog_comment(target=self.guitar, author=self.user, body=f"c{i}")
        resp = self.client.get(self.url)
        page = resp.context["comments_page"]
        self.assertEqual(len(page.object_list), 10)
        self.assertEqual(page.paginator.num_pages, 2)
        self.assertEqual(resp.context["comments_total"], 11)
        # page 2 holds the remaining one
        page2 = self.client.get(self.url, {"page": 2}).context["comments_page"]
        self.assertEqual(len(page2.object_list), 1)

    def test_author_deletes_comment_via_endpoint(self):
        c = services.add_catalog_comment(target=self.guitar, author=self.user, body="secret plans")
        self.client.force_login(self.user)
        resp = self.client.post(reverse("catalog:delete_comment", args=[c.pk]))
        self.assertEqual(resp.status_code, 302)
        from catalog.models import CatalogComment as CC
        self.assertTrue(CC.objects.get(pk=c.pk).is_deleted)
        # Page now shows the placeholder, not the original body.
        page = self.client.get(self.url)
        self.assertContains(page, "This message was deleted.")
        self.assertNotContains(page, "secret plans")

    def test_non_author_cannot_delete_via_endpoint(self):
        c = services.add_catalog_comment(target=self.guitar, author=self.user, body="mine")
        self.client.force_login(make_user("intruder"))
        self.client.post(reverse("catalog:delete_comment", args=[c.pk]))
        from catalog.models import CatalogComment as CC
        self.assertFalse(CC.objects.get(pk=c.pk).is_deleted)
