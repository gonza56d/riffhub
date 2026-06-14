"""Tests for catalog browse + live filtering.

Covers ``catalog.views.filter_guitars``, ``guitar_browse`` and
``guitar_detail`` — the ``/`` browse page (with its HTMX partial) and the
``/guitars/<pk>/`` detail page.

Design notes:
- The filterable facets the view queries (``pickup_combination``,
  ``electronics_type``, ``has_tremolo`` …) are plain *denormalised* columns on
  ``GuitarModel`` (PRODUCT.md: derived facets are "denormalised into indexed
  columns (filterable in SQL)"). The browse view filters those stored columns
  directly, so these tests set them explicitly when building fixtures, which
  gives precise, deterministic control over which guitar matches which filter.
- We build minimal fixtures by hand rather than running ``seed_catalog`` so each
  test exercises exactly the rows it cares about. ``status`` defaults to
  UNDER_REVISION on ``CatalogEntry``, so every published fixture sets it
  explicitly; that also lets the publication-gating tests be precise.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from catalog.constants import ElectronicsType, PublicationStatus
from catalog.models import (
    BodyShape,
    Brand,
    Country,
    GuitarModel,
    NeckConstruction,
)
from catalog.views import PAGE_SIZE, filter_guitars
from core.models import SiteConfiguration


# --- shared fixture helpers ------------------------------------------------

def _published_brand(name="Acme"):
    return Brand.objects.create(name=name, status=PublicationStatus.PUBLISHED)


def make_guitar(brand, name, **overrides):
    """Create a PUBLISHED GuitarModel with sane defaults for the required
    fields, overriding any facet/spec the test cares about.

    Derived facet columns (pickup_combination, electronics_type, the booleans,
    neck_thickness_class, is_multiscale) are recomputed from components by a
    post_save signal, so we write them directly via ``.update()`` (which bypasses
    the signal) to exercise the filter query in isolation.
    """
    defaults = dict(
        status=PublicationStatus.PUBLISHED,
        num_strings=6,
        scale_length_min_inches=Decimal("25.5"),
        scale_length_max_inches=Decimal("25.5"),
        num_frets=22,
        electronics_type=ElectronicsType.PASSIVE,
    )
    defaults.update(overrides)
    derived = {
        "pickup_combination", "electronics_type", "has_hum_cancellation",
        "has_tremolo", "has_piezo", "has_locking_tuners",
        "neck_thickness_class", "is_multiscale",
    }
    facets = {k: defaults.pop(k) for k in list(defaults) if k in derived}
    guitar = GuitarModel.objects.create(brand=brand, name=name, **defaults)
    if facets:
        GuitarModel.objects.filter(pk=guitar.pk).update(**facets)
        guitar.refresh_from_db()
    return guitar


class FilterGuitarsServiceTests(TestCase):
    """Direct unit tests of ``filter_guitars`` via a fake querydict.

    Using the view helper directly (with ``Client().get(...).request.GET`` style
    params) keeps these tests fast and pinpointed on the query construction.
    """

    @classmethod
    def setUpTestData(cls):
        cls.brand = _published_brand()
        cls.country_usa = Country.objects.create(
            name="USA"
        )
        cls.country_japan = Country.objects.create(
            name="Japan"
        )
        cls.neck_bolt = NeckConstruction.objects.create(name="Bolt-on")
        cls.neck_set = NeckConstruction.objects.create(name="Set-neck")
        cls.shape_strat = BodyShape.objects.create(name="Stratocaster")
        cls.shape_lp = BodyShape.objects.create(name="Les Paul")

        # A spread of guitars exercising every facet.
        cls.strat = make_guitar(
            cls.brand, "Strat",
            num_strings=6, num_frets=22,
            scale_length_min_inches=Decimal("25.5"),
            scale_length_max_inches=Decimal("25.5"),
            pickup_combination="SSS",
            electronics_type=ElectronicsType.PASSIVE,
            has_tremolo=True,
            neck_construction=cls.neck_bolt,
            body_shape=cls.shape_strat,
            country_of_origin=cls.country_usa,
        )
        cls.lp = make_guitar(
            cls.brand, "Les Paul",
            num_strings=6, num_frets=22,
            scale_length_min_inches=Decimal("24.75"),
            scale_length_max_inches=Decimal("24.75"),
            pickup_combination="HH",
            electronics_type=ElectronicsType.PASSIVE,
            has_hum_cancellation=True,
            neck_construction=cls.neck_set,
            body_shape=cls.shape_lp,
            country_of_origin=cls.country_usa,
        )
        cls.rg7 = make_guitar(
            cls.brand, "RG7321",
            num_strings=7, num_frets=24,
            scale_length_min_inches=Decimal("25.5"),
            scale_length_max_inches=Decimal("25.5"),
            pickup_combination="HH",
            electronics_type=ElectronicsType.PASSIVE,
            has_hum_cancellation=True,
            neck_construction=cls.neck_bolt,
            body_shape=cls.shape_strat,
            country_of_origin=cls.country_japan,
        )
        cls.emg = make_guitar(
            cls.brand, "EC-1000",
            num_strings=6, num_frets=24,
            scale_length_min_inches=Decimal("25.0"),
            scale_length_max_inches=Decimal("25.0"),
            pickup_combination="HH",
            electronics_type=ElectronicsType.ACTIVE,
            has_hum_cancellation=True,
            has_locking_tuners=True,
            neck_construction=cls.neck_set,
            country_of_origin=cls.country_japan,
        )
        cls.multiscale = make_guitar(
            cls.brand, "Boden",
            num_strings=6, num_frets=24,
            scale_length_min_inches=Decimal("25.0"),
            scale_length_max_inches=Decimal("25.5"),
            is_multiscale=True,
            has_piezo=True,
            electronics_type=ElectronicsType.MIXED,
        )
        cls.fretless = make_guitar(
            cls.brand, "Fretless Wonder",
            num_strings=6,
            scale_length_min_inches=Decimal("26.5"),
            scale_length_max_inches=Decimal("26.5"),
            is_fretless=True,
            electronics_type=ElectronicsType.MIXED,
        )

    def _qs(self, query=""):
        """Return ``filter_guitars`` output for a raw query string."""
        from django.http import QueryDict
        return filter_guitars(QueryDict(query))

    def _names(self, qs):
        return sorted(g.name for g in qs)

    # --- single-facet filters ---------------------------------------------
    def test_no_params_returns_all_published(self):
        self.assertEqual(self._qs().count(), 6)

    def test_filter_by_num_strings(self):
        qs = self._qs("strings=7")
        self.assertEqual(self._names(qs), ["RG7321"])

    def test_filter_by_num_strings_multiple_values(self):
        # getlist support: strings=6&strings=7 -> num_strings IN (6, 7) = all.
        qs = self._qs("strings=6&strings=7")
        self.assertEqual(qs.count(), 6)

    def test_filter_by_num_strings_six_only(self):
        qs = self._qs("strings=6")
        self.assertNotIn("RG7321", self._names(qs))
        self.assertEqual(qs.count(), 5)

    def test_non_digit_strings_param_ignored(self):
        # The view filters out non-numeric values; a junk value => no filter.
        qs = self._qs("strings=abc")
        self.assertEqual(qs.count(), 6)

    def test_filter_by_frets(self):
        qs = self._qs("frets=24")
        self.assertEqual(
            self._names(qs), ["Boden", "EC-1000", "RG7321"]
        )

    def test_filter_by_frets_multiple(self):
        qs = self._qs("frets=22&frets=24")
        self.assertEqual(qs.count(), 6)

    def test_filter_by_scale_single_value(self):
        # scale matches only guitars whose min == max == that value.
        qs = self._qs("scale=24.75")
        self.assertEqual(self._names(qs), ["Les Paul"])

    def test_filter_by_scale_excludes_multiscale(self):
        # The multiscale Boden (25.0-25.5) must NOT match scale=25.5 because
        # the view requires BOTH min and max equal the value.
        qs = self._qs("scale=25.5")
        self.assertEqual(self._names(qs), ["RG7321", "Strat"])
        self.assertNotIn("Boden", self._names(qs))

    def test_filter_by_pickup_combination(self):
        qs = self._qs("combo=HH")
        self.assertEqual(
            self._names(qs), ["EC-1000", "Les Paul", "RG7321"]
        )

    def test_filter_by_pickup_combination_sss(self):
        qs = self._qs("combo=SSS")
        self.assertEqual(self._names(qs), ["Strat"])

    def test_filter_by_pickup_combination_multiple(self):
        qs = self._qs("combo=SSS&combo=HH")
        self.assertEqual(
            self._names(qs), ["EC-1000", "Les Paul", "RG7321", "Strat"]
        )

    def test_filter_by_electronics_active(self):
        qs = self._qs("electronics=active")
        self.assertEqual(self._names(qs), ["EC-1000"])

    def test_filter_by_electronics_passive(self):
        qs = self._qs("electronics=passive")
        self.assertEqual(
            self._names(qs), ["Les Paul", "RG7321", "Strat"]
        )

    def test_filter_by_neck_construction(self):
        qs = self._qs(f"neck={self.neck_set.id}")
        self.assertEqual(self._names(qs), ["EC-1000", "Les Paul"])

    def test_filter_by_body_shape(self):
        qs = self._qs(f"shape={self.shape_strat.id}")
        self.assertEqual(self._names(qs), ["RG7321", "Strat"])

    def test_filter_by_country(self):
        qs = self._qs(f"country={self.country_japan.id}")
        self.assertEqual(self._names(qs), ["EC-1000", "RG7321"])

    # --- boolean facets ----------------------------------------------------
    def test_filter_has_tremolo(self):
        qs = self._qs("has_tremolo=1")
        self.assertEqual(self._names(qs), ["Strat"])

    def test_filter_has_locking_tuners(self):
        qs = self._qs("has_locking_tuners=1")
        self.assertEqual(self._names(qs), ["EC-1000"])

    def test_filter_has_hum_cancellation(self):
        qs = self._qs("has_hum_cancellation=1")
        self.assertEqual(
            self._names(qs), ["EC-1000", "Les Paul", "RG7321"]
        )

    def test_filter_has_piezo(self):
        qs = self._qs("has_piezo=1")
        self.assertEqual(self._names(qs), ["Boden"])

    def test_filter_is_multiscale(self):
        qs = self._qs("is_multiscale=1")
        self.assertEqual(self._names(qs), ["Boden"])

    def test_filter_is_fretless(self):
        qs = self._qs("is_fretless=1")
        self.assertEqual(self._names(qs), ["Fretless Wonder"])

    def test_boolean_facet_accepts_on_true_yes(self):
        # _truthy accepts "1", "on", "true", "yes".
        for token in ("1", "on", "true", "yes"):
            with self.subTest(token=token):
                qs = self._qs(f"has_tremolo={token}")
                self.assertEqual(self._names(qs), ["Strat"])

    def test_boolean_facet_falsey_value_does_not_filter(self):
        # A value not in the truthy set (e.g. "0") leaves the facet unfiltered.
        qs = self._qs("has_tremolo=0")
        self.assertEqual(qs.count(), 6)

    # --- combined & empty --------------------------------------------------
    def test_combined_strings_and_combo(self):
        qs = self._qs("strings=7&combo=HH")
        self.assertEqual(self._names(qs), ["RG7321"])

    def test_combined_country_and_frets(self):
        qs = self._qs(f"country={self.country_japan.id}&frets=24")
        self.assertEqual(self._names(qs), ["EC-1000", "RG7321"])

    def test_combined_filters_narrow_to_one(self):
        qs = self._qs(
            f"strings=6&combo=HH&neck={self.neck_set.id}&electronics=active"
        )
        self.assertEqual(self._names(qs), ["EC-1000"])

    def test_combined_filters_empty_result_rare_combo(self):
        # The signature riffhub case: 7-string at 24.75" doesn't exist here.
        qs = self._qs("strings=7&scale=24.75")
        self.assertEqual(list(qs), [])

    def test_combined_contradictory_booleans_empty(self):
        # No guitar is both fretless and multiscale in the fixtures.
        qs = self._qs("is_fretless=1&is_multiscale=1")
        self.assertEqual(list(qs), [])

    def test_filter_returns_only_published(self):
        # Build an under-revision 7-string; it must never appear.
        make_guitar(
            self.brand, "Secret 7",
            status=PublicationStatus.UNDER_REVISION,
            num_strings=7,
        )
        qs = self._qs("strings=7")
        self.assertEqual(self._names(qs), ["RG7321"])


class GuitarBrowseViewTests(TestCase):
    """HTTP-level tests of the browse view (``/``): template selection, HTMX
    partial vs full page, publication gating, and rendered content."""

    @classmethod
    def setUpTestData(cls):
        cls.brand = _published_brand("Fender")
        cls.country = Country.objects.create(
            name="USA"
        )
        cls.strat = make_guitar(
            cls.brand, "Stratocaster",
            num_strings=6, pickup_combination="SSS",
            has_tremolo=True, country_of_origin=cls.country,
        )
        cls.rg7 = make_guitar(
            cls.brand, "RG7321",
            num_strings=7, pickup_combination="HH",
        )
        cls.url = reverse("catalog:browse")

    def setUp(self):
        # An authenticated full-page render touches the moderation context
        # processor (is_at_least -> level derivation), which reads the
        # collaborator threshold. Configure it so those paths never raise.
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def test_browse_url_resolves_to_root(self):
        self.assertEqual(self.url, "/")

    def test_normal_request_renders_full_page(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/guitar_browse.html")
        self.assertTemplateUsed(resp, "catalog/_guitar_results.html")
        self.assertTemplateUsed(resp, "base.html")

    def test_htmx_request_renders_only_partial(self):
        resp = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/_guitar_results.html")
        # The full-page shell must NOT be rendered for an HTMX fragment.
        self.assertTemplateNotUsed(resp, "catalog/guitar_browse.html")
        self.assertTemplateNotUsed(resp, "base.html")

    def test_htmx_partial_omits_filter_form(self):
        # The filter form lives only in the full page, not the partial.
        full = self.client.get(self.url)
        partial = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertContains(full, 'id="filter-form"')
        self.assertNotContains(partial, 'id="filter-form"')

    def test_full_page_lists_all_published(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, "Stratocaster")
        self.assertContains(resp, "RG7321")
        self.assertContains(resp, "2 guitars")

    def test_filtered_request_via_querystring(self):
        resp = self.client.get(self.url, {"strings": "7"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "RG7321")
        self.assertNotContains(resp, "Stratocaster")
        self.assertContains(resp, "1 guitar")

    def test_filtered_htmx_returns_partial_results(self):
        resp = self.client.get(
            self.url, {"has_tremolo": "1"}, HTTP_HX_REQUEST="true"
        )
        self.assertTemplateUsed(resp, "catalog/_guitar_results.html")
        self.assertTemplateNotUsed(resp, "catalog/guitar_browse.html")
        self.assertContains(resp, "Stratocaster")
        self.assertNotContains(resp, "RG7321")

    def test_empty_result_shows_empty_state(self):
        resp = self.client.get(self.url, {"strings": "7", "scale": "24.75"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No guitars match those filters")
        self.assertContains(resp, "0 guitars")

    def test_under_revision_guitar_excluded_from_browse(self):
        make_guitar(
            self.brand, "Prototype X",
            status=PublicationStatus.UNDER_REVISION,
            num_strings=8,
        )
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "Prototype X")
        # still only the two published rows
        self.assertContains(resp, "2 guitars")

    def test_rejected_guitar_excluded_from_browse(self):
        make_guitar(
            self.brand, "Rejected Junk",
            status=PublicationStatus.REJECTED,
            num_strings=6,
        )
        resp = self.client.get(self.url)
        self.assertNotContains(resp, "Rejected Junk")
        self.assertContains(resp, "2 guitars")

    def test_context_total_and_active_count(self):
        resp = self.client.get(self.url, {"strings": "7", "has_tremolo": "1"})
        self.assertEqual(
            resp.context["total"], resp.context["page_obj"].paginator.count
        )
        # two distinct active facet keys selected
        self.assertEqual(resp.context["active_count"], 2)

    def test_context_no_active_filters(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["active_count"], 0)

    def test_context_facet_option_lists_present(self):
        resp = self.client.get(self.url)
        ctx = resp.context
        # strings options derived from published rows: 6 and 7.
        self.assertEqual(sorted(ctx["strings_options"]), [6, 7])
        # electronics options exclude the UNKNOWN sentinel.
        values = [v for v, _ in ctx["electronics_options"]]
        self.assertNotIn(ElectronicsType.UNKNOWN, values)
        self.assertIn(ElectronicsType.PASSIVE, values)

    def test_context_selection_echoed_back(self):
        resp = self.client.get(self.url, {"strings": "7", "scale": "25.5"})
        self.assertEqual(resp.context["sel_strings"], ["7"])
        self.assertEqual(resp.context["sel_scale"], "25.5")

    def test_anonymous_can_browse(self):
        # PRODUCT.md: anonymous users can SEE content (just not act on it).
        self.client.logout()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_authenticated_full_page_renders(self):
        user = User.objects.create_user(
            username="picker", email="p@example.com", password="pw12345!"
        )
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/guitar_browse.html")


class GuitarBrowsePaginationTests(TestCase):
    """The browse list is paginated (``PAGE_SIZE`` per page). The pager carries
    the active filters and tolerates junk / out-of-range ``?page`` values."""

    # Enough rows to span three pages, so has_next/has_previous and the elided
    # range all have something to exercise.
    TOTAL = PAGE_SIZE * 2 + 3

    @classmethod
    def setUpTestData(cls):
        cls.brand = _published_brand("Bulk")
        cls.url = reverse("catalog:browse")
        # Zero-padded names so the model's ("brand__name", "name") ordering is
        # deterministic and page boundaries are stable.
        for i in range(cls.TOTAL):
            make_guitar(cls.brand, f"Model {i:03d}", num_strings=6)
        cls.last_page = (cls.TOTAL + PAGE_SIZE - 1) // PAGE_SIZE

    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def test_first_page_caps_at_page_size(self):
        page = self.client.get(self.url).context["page_obj"]
        self.assertEqual(page.number, 1)
        self.assertEqual(len(page.object_list), PAGE_SIZE)
        self.assertTrue(page.has_next())
        self.assertFalse(page.has_previous())

    def test_total_reflects_full_match_not_page(self):
        # The headline count is the full match count, not the page slice.
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["total"], self.TOTAL)
        self.assertContains(resp, f"{self.TOTAL} guitars")

    def test_last_page_has_remainder(self):
        page = self.client.get(self.url, {"page": self.last_page}).context["page_obj"]
        self.assertEqual(page.number, self.last_page)
        self.assertFalse(page.has_next())
        self.assertEqual(
            len(page.object_list), self.TOTAL - PAGE_SIZE * (self.last_page - 1)
        )

    def test_pages_do_not_overlap(self):
        p1 = self.client.get(self.url).context["page_obj"]
        p2 = self.client.get(self.url, {"page": 2}).context["page_obj"]
        self.assertEqual({g.pk for g in p1} & {g.pk for g in p2}, set())

    def test_invalid_page_falls_back_to_first(self):
        resp = self.client.get(self.url, {"page": "abc"})
        self.assertEqual(resp.context["page_obj"].number, 1)

    def test_out_of_range_page_clamps_to_last(self):
        resp = self.client.get(self.url, {"page": 9999})
        self.assertEqual(resp.context["page_obj"].number, self.last_page)

    def test_pager_rendered_with_links(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, 'class="pager"')
        self.assertContains(resp, "page=2")  # the Next / page-2 link

    def test_pager_hidden_when_single_page(self):
        # A filter that narrows below one page drops the pager entirely.
        only = make_guitar(self.brand, "Lonely Seven", num_strings=7)
        resp = self.client.get(self.url, {"strings": "7"})
        self.assertContains(resp, only.name)
        self.assertNotContains(resp, 'class="pager"')

    def test_base_qs_excludes_page_keeps_filters(self):
        resp = self.client.get(self.url, {"strings": "6", "page": 2})
        # base_qs drops the page number but keeps the filter, so pager links
        # stay within the current selection.
        self.assertEqual(resp.context["base_qs"], "strings=6")
        self.assertContains(resp, "strings=6")

    def test_filtered_request_without_page_starts_at_first(self):
        resp = self.client.get(self.url, {"strings": "6"})
        self.assertEqual(resp.context["page_obj"].number, 1)

    def test_htmx_page_request_returns_partial_with_pager(self):
        resp = self.client.get(self.url, {"page": 2}, HTTP_HX_REQUEST="true")
        self.assertTemplateUsed(resp, "catalog/_guitar_results.html")
        self.assertTemplateNotUsed(resp, "catalog/guitar_browse.html")
        self.assertEqual(resp.context["page_obj"].number, 2)
        self.assertContains(resp, 'class="pager"')


class GuitarDetailViewTests(TestCase):
    """``guitar_detail`` (``/guitars/<pk>/``): published 200, others 404."""

    @classmethod
    def setUpTestData(cls):
        cls.brand = _published_brand("Gibson")
        cls.country = Country.objects.create(
            name="USA"
        )
        cls.published = make_guitar(
            cls.brand, "Les Paul Standard",
            num_strings=6,
            scale_length_min_inches=Decimal("24.75"),
            scale_length_max_inches=Decimal("24.75"),
            pickup_combination="HH",
            electronics_type=ElectronicsType.PASSIVE,
            has_hum_cancellation=True,
            country_of_origin=cls.country,
        )
        cls.under_revision = make_guitar(
            cls.brand, "Mystery Model",
            status=PublicationStatus.UNDER_REVISION,
            num_strings=6,
        )
        cls.rejected = make_guitar(
            cls.brand, "Bogus Model",
            status=PublicationStatus.REJECTED,
            num_strings=6,
        )

    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def _detail_url(self, pk):
        return reverse("catalog:detail", args=[pk])

    def test_published_guitar_returns_200(self):
        resp = self.client.get(self._detail_url(self.published.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "catalog/guitar_detail.html")

    def test_published_detail_shows_specs(self):
        resp = self.client.get(self._detail_url(self.published.pk))
        self.assertContains(resp, "Les Paul Standard")
        self.assertContains(resp, "Gibson")
        # scale_display renders 24.75 without trailing zeros.
        self.assertContains(resp, '24.75&quot;')

    def test_under_revision_guitar_returns_404(self):
        resp = self.client.get(self._detail_url(self.under_revision.pk))
        self.assertEqual(resp.status_code, 404)

    def test_rejected_guitar_returns_404(self):
        resp = self.client.get(self._detail_url(self.rejected.pk))
        self.assertEqual(resp.status_code, 404)

    def test_missing_pk_returns_404(self):
        resp = self.client.get(self._detail_url(999999))
        self.assertEqual(resp.status_code, 404)

    def test_detail_context_has_guitar_and_pickups(self):
        resp = self.client.get(self._detail_url(self.published.pk))
        self.assertEqual(resp.context["guitar"].pk, self.published.pk)
        # No GuitarPickup rows created -> empty list, not an error.
        self.assertEqual(list(resp.context["pickups"]), [])

    def test_detail_anonymous_can_view(self):
        self.client.logout()
        resp = self.client.get(self._detail_url(self.published.pk))
        self.assertEqual(resp.status_code, 200)

    def test_detail_url_shape(self):
        self.assertEqual(
            self._detail_url(self.published.pk),
            f"/guitars/{self.published.pk}/",
        )


class CatalogBrowseSeedDataTests(TestCase):
    """Sanity tests against the real ``seed_catalog`` reference data, mirroring
    the live-verified examples documented in PRODUCT.md's Frontend section."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command("seed_catalog")
        cls.url = reverse("catalog:browse")

    def setUp(self):
        cfg = SiteConfiguration.get_solo()
        cfg.collaborator_promotion_threshold = 3
        cfg.founder_threshold = 30
        cfg.save()

    def _names(self, params):
        from django.http import QueryDict

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return sorted(g.name for g in filter_guitars(QueryDict(query)))

    def test_seed_loads_six_published_guitars(self):
        self.assertEqual(GuitarModel.objects.published().count(), 6)

    def test_strings_seven_finds_rg7321(self):
        # PRODUCT.md verified: ?strings=7 -> RG7321.
        self.assertEqual(self._names({"strings": "7"}), ["RG7321"])

    def test_has_tremolo_finds_strat_and_rg550(self):
        # PRODUCT.md verified: ?has_tremolo=1 -> Strat + RG550.
        names = self._names({"has_tremolo": "1"})
        self.assertIn("RG550", names)
        self.assertTrue(any(n.startswith("Stratocaster") for n in names))
        self.assertEqual(len(names), 2)

    def test_multiscale_finds_strandberg(self):
        # PRODUCT.md verified: ?is_multiscale=1 -> Strandberg Boden.
        self.assertEqual(self._names({"is_multiscale": "1"}), ["Boden Original 6"])

    def test_seven_string_at_24_75_is_empty(self):
        # PRODUCT.md verified: ?strings=7&scale=24.75 -> empty (the rare combo).
        self.assertEqual(self._names({"strings": "7", "scale": "24.75"}), [])

    def test_active_electronics_finds_emg_ltd(self):
        names = self._names({"electronics": "active"})
        self.assertEqual(names, ["EC-1000 (EMG)"])

    def test_locking_tuners_facet_nonempty(self):
        # The EMG LTD (Sperzel) and RG550-era have locking hardware in seed; at
        # least one guitar must surface for the locking-tuners facet.
        names = self._names({"has_locking_tuners": "1"})
        self.assertIn("EC-1000 (EMG)", names)

    def test_browse_page_renders_with_seed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "6 guitars")
