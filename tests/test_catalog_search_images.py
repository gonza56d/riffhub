"""Tests for the "Search Images" button on the catalog detail pages.

Both the guitar detail page and the per-kind gear detail page render a link to
a Google search for the product (``brand`` + ``name``), opening in a new tab.
The query is URL-encoded in-template with the ``urlencode`` filter (which wraps
``urllib.parse.quote``), so a brand name with reserved characters (here ``&``)
and the spaces between words are escaped in the href.
"""

from decimal import Decimal
from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse

from catalog.constants import PublicationStatus
from catalog.models import Brand, Bridge, BridgeType, GuitarModel
from core.models import SiteConfiguration

PUB = PublicationStatus.PUBLISHED


class SearchImagesButtonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # An "&" in the brand makes the urlencode step observable in the href.
        cls.brand = Brand.objects.create(name="Acme & Co", status=PUB)
        cls.bt = BridgeType.objects.create(name="Tremolo", is_tremolo=True)
        cls.bridge = Bridge.objects.create(
            brand=cls.brand, name="Trem-1", bridge_type=cls.bt, status=PUB,
        )
        cls.guitar = GuitarModel.objects.create(
            brand=cls.brand, name="GTR-1", num_strings=6,
            scale_length_min_inches=Decimal("25.5"),
            scale_length_max_inches=Decimal("25.5"),
            status=PUB,
        )

    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def _assert_search_button(self, resp, product_name):
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "https://www.google.com/search?q=")
        self.assertContains(resp, 'target="_blank"')
        self.assertContains(resp, 'rel="noopener"')
        # The product name (brand + name) is present in URL-encoded form, so the
        # full encoded query and the encoded brand both land in the href. The
        # template filter uses quote(): spaces -> %20, "&" -> %26.
        self.assertContains(resp, quote(product_name))  # e.g. Acme%20%26%20Co%20GTR-1
        self.assertContains(resp, "Acme%20%26%20Co")

    def test_guitar_detail_has_search_images_button(self):
        resp = self.client.get(reverse("catalog:detail", args=[self.guitar.pk]))
        self._assert_search_button(resp, "Acme & Co GTR-1")

    def test_gear_detail_has_search_images_button(self):
        resp = self.client.get(
            reverse("catalog:gear_detail", args=["bridge", self.bridge.pk])
        )
        self._assert_search_button(resp, "Acme & Co Trem-1")
