"""Tests for the ``seed_catalog_csv`` bulk loader (the real CSV dataset in ``seeds/``).

These load the actual shipped CSVs into the test DB and assert the loader wires every
foreign key, recomputes the derived facets, marks everything published, and is
idempotent.
"""

import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from catalog import models

SEEDS = Path(settings.BASE_DIR) / "seeds"


def _rows(name):
    with (SEEDS / name).open(newline="", encoding="utf-8-sig") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


class SeedCatalogCsvTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_catalog_csv", verbosity=0)

    def test_row_counts_match_the_csv_files(self):
        # The loader creates exactly one row per (deduped) CSV line.
        self.assertEqual(models.Brand.objects.count(), len(_rows("brands.csv")))
        self.assertEqual(models.Pickup.objects.count(), len(_rows("pickups.csv")))
        self.assertEqual(models.Bridge.objects.count(), len(_rows("bridges.csv")))
        self.assertEqual(models.Tuner.objects.count(), len(_rows("tuners.csv")))
        self.assertEqual(models.Nut.objects.count(), len(_rows("nuts.csv")))
        self.assertEqual(models.GuitarModel.objects.count(), len(_rows("guitars.csv")))

    def test_everything_loads_published(self):
        total = models.GuitarModel.objects.count()
        self.assertEqual(models.GuitarModel.objects.published().count(), total)
        self.assertEqual(models.GuitarModel.objects.under_revision().count(), 0)
        self.assertEqual(
            models.Brand.objects.published().count(), models.Brand.objects.count()
        )

    def test_is_idempotent(self):
        before = {
            m.__name__: m.objects.count()
            for m in (models.Brand, models.Pickup, models.GuitarModel, models.GuitarPickup)
        }
        call_command("seed_catalog_csv", verbosity=0)
        after = {
            m.__name__: m.objects.count()
            for m in (models.Brand, models.Pickup, models.GuitarModel, models.GuitarPickup)
        }
        self.assertEqual(before, after)

    def test_multiscale_facet_matches_csv(self):
        # is_multiscale is derived (scale min != max) — verify recompute ran for every row.
        expected = sum(
            1 for r in _rows("guitars.csv")
            if r["scale_length_min_inches"] != r["scale_length_max_inches"]
        )
        self.assertGreater(expected, 0)
        self.assertEqual(models.GuitarModel.objects.filter(is_multiscale=True).count(), expected)

    def test_component_driven_facets_were_recomputed(self):
        # These only become true if the bridge/pickup/tuner FKs resolved and recompute ran.
        self.assertGreater(models.GuitarModel.objects.filter(has_tremolo=True).count(), 0)
        self.assertGreater(models.GuitarModel.objects.filter(electronics_type="active").count(), 0)
        self.assertGreater(models.GuitarModel.objects.filter(has_locking_tuners=True).count(), 0)
        self.assertGreater(models.GuitarModel.objects.exclude(pickup_combination="").count(), 0)

    def test_first_csv_row_loaded_faithfully(self):
        row = _rows("guitars.csv")[0]
        g = models.GuitarModel.objects.get(brand__name=row["brand"], name=row["name"])
        self.assertEqual(g.num_strings, int(row["num_strings"]))
        self.assertEqual(g.scale_length_min_inches, Decimal(row["scale_length_min_inches"]))
        self.assertEqual(g.scale_length_max_inches, Decimal(row["scale_length_max_inches"]))

    def test_inline_pickups_become_positioned_through_rows(self):
        # A guitar with a bridge pickup in the CSV gets a GuitarPickup at that position.
        row = next(r for r in _rows("guitars.csv") if r["pickup_bridge"])
        g = models.GuitarModel.objects.get(brand__name=row["brand"], name=row["name"])
        link = g.guitar_pickups.get(position="bridge")
        self.assertEqual(link.pickup.name, row["pickup_bridge"])
