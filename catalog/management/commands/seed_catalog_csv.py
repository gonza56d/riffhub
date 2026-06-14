"""Load the bulk catalog seed data from the CSV files in ``seeds/``.

This is the large, real-world dataset (hundreds of guitars + gear scraped from the
makers) — distinct from ``seed_catalog``, which loads ~6 illustrative guitars. The CSV
schema, foreign-key-by-name conventions and load order are documented in
``seeds/README.md``; ``seeds/validate_seeds.py`` checks the files before you load.

Idempotent: every row is ``get_or_create``d by its natural key, so re-running is safe
and it composes with ``seed_catalog``. Everything loads already **published** (curated
seed data, not a community submission). Run with::

    manage.py seed_catalog_csv [--seeds-dir PATH]
"""

import csv
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog import models
from catalog.constants import PickupPosition, PublicationStatus


def _bool(value: "str | None") -> bool:
    return (value or "").strip().lower() == "true"


def _decimal(value: "str | None"):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - guarded by validate_seeds.py
        raise CommandError(f"invalid decimal {value!r}") from exc


def _int(value: "str | None"):
    value = (value or "").strip()
    return int(value) if value else None


class Command(BaseCommand):
    help = "Load the bulk catalog seed data (brands, gear, guitars) from seeds/*.csv."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seeds-dir",
            default=str(Path(settings.BASE_DIR) / "seeds"),
            help="Directory holding the seed CSVs (default: <BASE_DIR>/seeds).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.seeds = Path(options["seeds_dir"])
        if not self.seeds.is_dir():
            raise CommandError(f"seeds directory not found: {self.seeds}")
        self.now = timezone.now()
        self.created: Counter = Counter()

        self._load_vocab()
        self._load_brands()
        self._load_gear()
        self._load_guitars()
        self._report()

    # --- IO + lookup helpers ----------------------------------------------
    def _read(self, filename: str):
        path = self.seeds / filename
        if not path.exists():
            raise CommandError(f"missing seed file: {path}")
        with path.open(newline="", encoding="utf-8-sig") as f:
            for raw in csv.DictReader(f):
                yield {k.strip(): (v or "").strip() for k, v in raw.items() if k}

    def _published(self) -> dict:
        return {
            "status": PublicationStatus.PUBLISHED,
            "published_at": self.now,
            "reviewed_at": self.now,
        }

    def _lookup(self, cache: dict, value, field: str, owner: str, *, required: bool = False):
        """Resolve a name -> object reference, or None for an empty optional cell."""
        value = (value or "").strip()
        if not value:
            if required:
                raise CommandError(f"{owner!r}: {field} is required but the cell is empty")
            return None
        try:
            return cache[value]
        except KeyError:
            raise CommandError(
                f"{owner!r}: {field}={value!r} does not match any loaded "
                f"{'brand' if cache is self.brands else 'seed'} row"
            )

    # --- Controlled vocabularies ------------------------------------------
    def _vocab(self, filename: str, model, extra=None) -> dict:
        cache: dict = {}
        for row in self._read(filename):
            name = row["name"]
            if not name:
                continue
            defaults = {"description": row.get("description", "")}
            if extra:
                defaults.update(extra(row))
            obj, created = model.objects.get_or_create(name=name, defaults=defaults)
            self.created[model.__name__] += int(created)
            cache[name] = obj
        return cache

    def _load_vocab(self):
        self.countries = self._vocab("countries.csv", models.Country)
        self.fret_materials = self._vocab("fret_materials.csv", models.FretMaterial)
        self.fretboard_materials = self._vocab("fretboard_materials.csv", models.FretboardMaterial)
        self.neck_constructions = self._vocab("neck_constructions.csv", models.NeckConstruction)
        self.neck_materials = self._vocab("neck_materials.csv", models.NeckMaterial)
        self.neck_profiles = self._vocab("neck_profiles.csv", models.NeckProfile)
        self.body_materials = self._vocab("body_materials.csv", models.BodyMaterial)
        self.body_shapes = self._vocab("body_shapes.csv", models.BodyShape)
        self.headstock_types = self._vocab("headstock_types.csv", models.HeadstockType)
        self.selector_switches = self._vocab("selector_switches.csv", models.SelectorSwitch)
        self.nut_materials = self._vocab("nut_materials.csv", models.NutMaterial)
        self.fretboard_radii = self._vocab(
            "fretboard_radii.csv", models.FretboardRadius,
            extra=lambda r: {
                "radius_min_inches": _decimal(r.get("radius_min_inches")),
                "radius_max_inches": _decimal(r.get("radius_max_inches")),
                "is_compound": _bool(r.get("is_compound")),
                "is_flat": _bool(r.get("is_flat")),
            },
        )
        self.pickup_types = self._vocab(
            "pickup_types.csv", models.PickupType,
            extra=lambda r: {
                "symbol": r.get("symbol", ""),
                "is_humbucking": _bool(r.get("is_humbucking")),
            },
        )
        self.bridge_types = self._vocab(
            "bridge_types.csv", models.BridgeType,
            extra=lambda r: {
                "is_tremolo": _bool(r.get("is_tremolo")),
                "is_locking": _bool(r.get("is_locking")),
            },
        )

    # --- Brands -----------------------------------------------------------
    def _load_brands(self):
        self.brands: dict = {}
        for row in self._read("brands.csv"):
            name = row["name"]
            obj, created = models.Brand.objects.get_or_create(
                name=name,
                defaults={
                    "country": self._lookup(self.countries, row.get("country"), "country", name),
                    "website": row.get("website", ""),
                    "description": row.get("description", ""),
                    **self._published(),
                },
            )
            self.created["Brand"] += int(created)
            self.brands[name] = obj

    # --- Gear (names are unique within each type — guitars reference them) -
    def _gear(self, filename: str, model, extra) -> dict:
        cache: dict = {}
        for row in self._read(filename):
            name = row["name"]
            brand = self._lookup(self.brands, row.get("brand"), "brand", name, required=True)
            if name in cache:
                raise CommandError(
                    f"duplicate {model.__name__} name {name!r} in {filename} — gear "
                    f"names must be unique within a type (guitars reference gear by name)"
                )
            defaults = {"description": row.get("description", ""), **self._published()}
            defaults.update(extra(row))
            obj, created = model.objects.get_or_create(brand=brand, name=name, defaults=defaults)
            self.created[model.__name__] += int(created)
            cache[name] = obj
        return cache

    def _load_gear(self):
        self.pickups = self._gear(
            "pickups.csv", models.Pickup,
            lambda r: {
                "pickup_type": self._lookup(
                    self.pickup_types, r.get("pickup_type"), "pickup_type", r["name"], required=True
                ),
                "is_active": _bool(r.get("is_active")),
            },
        )
        self.bridges = self._gear(
            "bridges.csv", models.Bridge,
            lambda r: {
                "bridge_type": self._lookup(
                    self.bridge_types, r.get("bridge_type"), "bridge_type", r["name"], required=True
                ),
                "has_piezo": _bool(r.get("has_piezo")),
                "is_locking": _bool(r.get("is_locking")),
            },
        )
        self.tuners = self._gear(
            "tuners.csv", models.Tuner,
            lambda r: {
                "is_locking": _bool(r.get("is_locking")),
                "ratio": r.get("ratio", ""),
                "tuner_type": r.get("tuner_type", ""),
            },
        )
        self.nuts = self._gear(
            "nuts.csv", models.Nut,
            lambda r: {
                "material": self._lookup(
                    self.nut_materials, r.get("material"), "material", r["name"], required=True
                ),
                "is_locking": _bool(r.get("is_locking")),
            },
        )

    # --- Guitars ----------------------------------------------------------
    def _load_guitars(self):
        positions = (
            ("pickup_bridge", PickupPosition.BRIDGE),
            ("pickup_middle", PickupPosition.MIDDLE),
            ("pickup_neck", PickupPosition.NECK),
        )
        for row in self._read("guitars.csv"):
            name = row["name"]
            brand = self._lookup(self.brands, row.get("brand"), "brand", name, required=True)

            num_strings = _int(row.get("num_strings"))
            scale_min = _decimal(row.get("scale_length_min_inches"))
            scale_max = _decimal(row.get("scale_length_max_inches"))
            if num_strings is None or scale_min is None or scale_max is None:
                raise CommandError(
                    f"guitar {name!r}: num_strings and scale_length_min/max_inches are required"
                )

            defaults = {
                "year_introduced": _int(row.get("year_introduced")),
                "year_discontinued": _int(row.get("year_discontinued")),
                "num_strings": num_strings,
                "scale_length_min_inches": scale_min,
                "scale_length_max_inches": scale_max,
                "num_frets": _int(row.get("num_frets")),
                "fret_material": self._lookup(self.fret_materials, row.get("fret_material"), "fret_material", name),
                "is_fretless": _bool(row.get("is_fretless")),
                "fretboard_material": self._lookup(self.fretboard_materials, row.get("fretboard_material"), "fretboard_material", name),
                "fretboard_radius": self._lookup(self.fretboard_radii, row.get("fretboard_radius"), "fretboard_radius", name),
                "neck_construction": self._lookup(self.neck_constructions, row.get("neck_construction"), "neck_construction", name),
                "neck_material": self._lookup(self.neck_materials, row.get("neck_material"), "neck_material", name),
                "neck_profile": self._lookup(self.neck_profiles, row.get("neck_profile"), "neck_profile", name),
                "neck_depth_1st_fret_mm": _decimal(row.get("neck_depth_1st_fret_mm")),
                "neck_depth_12th_fret_mm": _decimal(row.get("neck_depth_12th_fret_mm")),
                "nut_width_mm": _decimal(row.get("nut_width_mm")),
                "body_material": self._lookup(self.body_materials, row.get("body_material"), "body_material", name),
                "body_shape": self._lookup(self.body_shapes, row.get("body_shape"), "body_shape", name),
                "headstock_type": self._lookup(self.headstock_types, row.get("headstock_type"), "headstock_type", name),
                "selector_switch": self._lookup(self.selector_switches, row.get("selector_switch"), "selector_switch", name),
                "country_of_origin": self._lookup(self.countries, row.get("country_of_origin"), "country_of_origin", name),
                "bridge": self._lookup(self.bridges, row.get("bridge"), "bridge", name),
                "nut": self._lookup(self.nuts, row.get("nut"), "nut", name),
                "tuners": self._lookup(self.tuners, row.get("tuners"), "tuners", name),
                **self._published(),
            }

            guitar, created = models.GuitarModel.objects.get_or_create(
                brand=brand, name=name, defaults=defaults
            )
            self.created["GuitarModel"] += int(created)

            for column, position in positions:
                pickup = self._lookup(self.pickups, row.get(column), column, name)
                if pickup is not None:
                    _, made = models.GuitarPickup.objects.get_or_create(
                        guitar=guitar, position=position, defaults={"pickup": pickup}
                    )
                    self.created["GuitarPickup"] += int(made)

            # Denormalise the filterable facets from the attached components
            # (same as catalog.signals does on a normal save).
            guitar.recompute_derived()

    # --- Report -----------------------------------------------------------
    def _report(self):
        pub = models.GuitarModel.objects.published()
        self.stdout.write(self.style.SUCCESS(
            f"\nSeed load complete — created {self.created.total()} new row(s) this run."
        ))
        self.stdout.write(
            f"Catalog now holds {models.Brand.objects.count()} brands, "
            f"{models.Pickup.objects.count()} pickups, {models.Bridge.objects.count()} bridges, "
            f"{models.Tuner.objects.count()} tuners, {models.Nut.objects.count()} nuts, "
            f"{models.GuitarModel.objects.count()} guitars."
        )
        self.stdout.write(
            "Derived facets recomputed — published guitars: "
            f"{pub.filter(has_tremolo=True).count()} with tremolo, "
            f"{pub.filter(is_multiscale=True).count()} multiscale, "
            f"{pub.filter(electronics_type='active').count()} active, "
            f"{pub.filter(has_locking_tuners=True).count()} locking tuners, "
            f"{pub.exclude(pickup_combination='').count()} with a pickup combination."
        )
