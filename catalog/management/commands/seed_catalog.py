"""Seed reference vocabularies + illustrative gear and guitars.

Idempotent: safe to run repeatedly. The guitar specs here are *illustrative
starter data* to demonstrate the model and the derived facets — the real
collab-db is community-curated. Run with: ``manage.py seed_catalog``.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog import models
from catalog.constants import PickupPosition, PublicationStatus, TunerType


class Command(BaseCommand):
    help = "Seed reference vocabularies and illustrative gear/guitars."

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()

        def vocab(model, name, **extra):
            obj, _ = model.objects.get_or_create(name=name, defaults=extra)
            return obj

        def published(model, lookup, **defaults):
            defaults.update(
                status=PublicationStatus.PUBLISHED,
                published_at=now,
                reviewed_at=now,
            )
            obj, _ = model.objects.get_or_create(**lookup, defaults=defaults)
            return obj

        # --- Controlled vocabularies --------------------------------------
        countries = {n: vocab(models.Country, n) for n in [
            "USA", "Mexico", "Japan", "Indonesia", "China", "South Korea",
            "Germany", "Sweden",
        ]}
        fret_materials = {n: vocab(models.FretMaterial, n) for n in [
            "Nickel-silver", "Stainless steel", "EVO Gold",
        ]}
        fb_materials = {n: vocab(models.FretboardMaterial, n) for n in [
            "Rosewood", "Maple", "Ebony", "Pau Ferro", "Richlite", "Roasted Maple",
        ]}
        neck_constructions = {n: vocab(models.NeckConstruction, n) for n in [
            "Bolt-on", "Set-neck", "Neck-through",
        ]}
        neck_materials = {n: vocab(models.NeckMaterial, n) for n in [
            "Maple", "Roasted Maple", "Mahogany", "Wenge",
        ]}
        neck_profiles = {n: vocab(models.NeckProfile, n) for n in [
            "Modern C", "'59 Rounded", "Wizard", "Wizard II", "Thin U", "EndurNeck",
        ]}
        body_materials = {n: vocab(models.BodyMaterial, n) for n in [
            "Alder", "Ash", "Swamp Ash", "Mahogany", "Basswood", "Korina", "Poplar",
        ]}
        body_shapes = {n: vocab(models.BodyShape, n) for n in [
            "Stratocaster", "Telecaster", "Les Paul", "SG", "Superstrat",
            "Single-cut", "Explorer", "Flying V", "Boden",
        ]}
        headstocks = {n: vocab(models.HeadstockType, n) for n in [
            "6-in-line", "7-in-line", "3+3", "Reverse 6-in-line", "Headless",
        ]}
        selectors = {n: vocab(models.SelectorSwitch, n) for n in [
            "None", "3-way", "5-way", "Special",
        ]}
        nut_materials = {n: vocab(models.NutMaterial, n) for n in [
            "Bone", "TUSQ", "Graphite", "Brass", "Locking",
        ]}

        # Fretboard radii — Fender/Gibson classics + 16"/17"/20", flat, composed.
        def radius(name, lo=None, hi=None, compound=False, flat=False):
            return vocab(
                models.FretboardRadius, name,
                radius_min_inches=Decimal(str(lo)) if lo is not None else None,
                radius_max_inches=Decimal(str(hi)) if hi is not None else None,
                is_compound=compound, is_flat=flat,
            )

        radii = {
            "7.25": radius('7.25"', 7.25, 7.25),
            "9.5": radius('9.5"', 9.5, 9.5),
            "12": radius('12"', 12, 12),
            "16": radius('16"', 16, 16),
            "17": radius('17"', 17, 17),
            "20": radius('20"', 20, 20),
            "compound_fender": radius('Compound 9.5"–14"', 9.5, 14, compound=True),
            "flat": radius("Flat", flat=True),
            "composed": radius("Composed (compound)", compound=True),
        }

        # Pickup types carry the combination symbol + hum-cancelling flag.
        pu_types = {
            "H": vocab(models.PickupType, "Humbucker", symbol="H", is_humbucking=True),
            "S": vocab(models.PickupType, "Single-coil", symbol="S", is_humbucking=False),
            "P": vocab(models.PickupType, "P-90", symbol="P", is_humbucking=False),
            "mini": vocab(models.PickupType, "Mini-humbucker", symbol="H", is_humbucking=True),
        }

        # Bridge types carry the tremolo/locking flags that derive guitar facets.
        bridge_types = {
            "hardtail": vocab(models.BridgeType, "Hardtail (fixed)", is_tremolo=False),
            "tom": vocab(models.BridgeType, "Tune-o-matic & Stopbar", is_tremolo=False),
            "wrap": vocab(models.BridgeType, "Wraparound", is_tremolo=False),
            "vintage_trem": vocab(models.BridgeType, "Vintage synchronized tremolo", is_tremolo=True),
            "floyd": vocab(models.BridgeType, "Locking tremolo (Floyd Rose)", is_tremolo=True, is_locking=True),
            "bigsby": vocab(models.BridgeType, "Bigsby vibrato", is_tremolo=True),
        }

        # --- Brands -------------------------------------------------------
        def brand(name, country=None, website=""):
            return published(
                models.Brand, {"name": name},
                country=countries.get(country) if country else None,
                website=website,
            )

        brands = {
            "Fender": brand("Fender", "USA"),
            "Gibson": brand("Gibson", "USA"),
            "Ibanez": brand("Ibanez", "Japan"),
            "Strandberg": brand("Strandberg", "Sweden"),
            "ESP LTD": brand("ESP LTD", "South Korea"),
            "Seymour Duncan": brand("Seymour Duncan", "USA"),
            "DiMarzio": brand("DiMarzio", "USA"),
            "EMG": brand("EMG", "USA"),
            "Floyd Rose": brand("Floyd Rose", "Germany"),
            "Gotoh": brand("Gotoh", "Japan"),
            "Graph Tech": brand("Graph Tech"),
            "Hipshot": brand("Hipshot", "USA"),
            "Sperzel": brand("Sperzel", "USA"),
            "Generic": brand("Generic"),
        }

        # --- Gear ---------------------------------------------------------
        def pickup(brand_name, name, type_key, active=False):
            return published(
                models.Pickup, {"brand": brands[brand_name], "name": name},
                pickup_type=pu_types[type_key], is_active=active,
            )

        pickups = {
            "59": pickup("Seymour Duncan", "SH-1 '59", "H"),
            "jb": pickup("Seymour Duncan", "SH-4 JB", "H"),
            "ssl1": pickup("Seymour Duncan", "SSL-1 Vintage Staggered", "S"),
            "evo": pickup("DiMarzio", "Evolution", "H"),
            "emg81": pickup("EMG", "81", "H", active=True),
            "emg60": pickup("EMG", "60", "H", active=True),
            "v7": pickup("Ibanez", "V7", "H"),
            "v8": pickup("Ibanez", "V8", "H"),
            "strandberg_hb": pickup("Strandberg", "Custom Humbucker", "H"),
            "p90": pickup("Gibson", "P-90", "P"),
        }

        def bridge(brand_name, name, type_key, locking=False, piezo=False):
            return published(
                models.Bridge, {"brand": brands[brand_name], "name": name},
                bridge_type=bridge_types[type_key], is_locking=locking, has_piezo=piezo,
            )

        bridges = {
            "fender_trem": bridge("Fender", "American Vintage Synchronized Tremolo", "vintage_trem"),
            "gibson_tom": bridge("Gibson", "ABR-1 Tune-o-matic + Stopbar", "tom"),
            "generic_tom": bridge("Generic", "Tune-o-matic + Stopbar", "tom"),
            "floyd": bridge("Floyd Rose", "1000 Series Tremolo", "floyd", locking=True),
            "hipshot": bridge("Hipshot", "US Contour Hardtail", "hardtail"),
            "strandberg": bridge("Strandberg", "EGS Rev 7 Fixed", "hardtail"),
        }

        def tuner(brand_name, name, locking=False, ttype=""):
            return published(
                models.Tuner, {"brand": brands[brand_name], "name": name},
                is_locking=locking, tuner_type=ttype,
            )

        tuners = {
            "fender_vintage": tuner("Fender", "Vintage 'F' Tuners", ttype=TunerType.VINTAGE),
            "gibson_vintage": tuner("Gibson", "Vintage Kluson-style", ttype=TunerType.VINTAGE),
            "gotoh_sealed": tuner("Gotoh", "SG381", ttype=TunerType.SEALED),
            "sperzel": tuner("Sperzel", "Trim-Lok", locking=True, ttype=TunerType.LOCKING),
            "gotoh_lock": tuner("Gotoh", "MG-T Magnum Lock", locking=True, ttype=TunerType.LOCKING),
        }

        def nut(brand_name, name, material, locking=False):
            return published(
                models.Nut, {"brand": brands[brand_name], "name": name},
                material=nut_materials[material], is_locking=locking,
            )

        nuts = {
            "bone": nut("Generic", "Bone Nut", "Bone"),
            "tusq": nut("Graph Tech", "TUSQ XL", "TUSQ"),
            "floyd_lock": nut("Floyd Rose", "R2 Locking Nut", "Locking", locking=True),
        }

        # --- Guitars ------------------------------------------------------
        def guitar(brand_name, name, *, strings, scale_lo, scale_hi, frets, fret,
                   fb, rad, neckc, neckm, profile, depth1, nut_w, body, shape,
                   head, sel, origin, bridge_obj, nut_obj, tuner_obj, pickup_spec):
            g = published(
                models.GuitarModel, {"brand": brands[brand_name], "name": name},
                num_strings=strings,
                scale_length_min_inches=Decimal(str(scale_lo)),
                scale_length_max_inches=Decimal(str(scale_hi)),
                num_frets=frets,
                fret_material=fret_materials[fret],
                fretboard_material=fb_materials[fb],
                fretboard_radius=radii[rad],
                neck_construction=neck_constructions[neckc],
                neck_material=neck_materials[neckm],
                neck_profile=neck_profiles[profile],
                neck_depth_1st_fret_mm=Decimal(str(depth1)),
                nut_width_mm=Decimal(str(nut_w)),
                body_material=body_materials[body],
                body_shape=body_shapes[shape],
                headstock_type=headstocks[head],
                selector_switch=selectors[sel],
                country_of_origin=countries[origin],
                bridge=bridges[bridge_obj] if bridge_obj else None,
                nut=nuts[nut_obj] if nut_obj else None,
                tuners=tuners[tuner_obj] if tuner_obj else None,
            )
            for position, pu_key in pickup_spec:
                models.GuitarPickup.objects.get_or_create(
                    guitar=g, position=position, defaults={"pickup": pickups[pu_key]},
                )
            g.recompute_derived()
            return g

        guitar(
            "Fender", "Stratocaster (American Professional II)",
            strings=6, scale_lo=25.5, scale_hi=25.5, frets=22, fret="Nickel-silver",
            fb="Rosewood", rad="9.5", neckc="Bolt-on", neckm="Maple", profile="Modern C",
            depth1=21.0, nut_w=42.8, body="Alder", shape="Stratocaster", head="6-in-line",
            sel="5-way", origin="USA", bridge_obj="fender_trem", nut_obj="bone",
            tuner_obj="fender_vintage",
            pickup_spec=[(PickupPosition.BRIDGE, "ssl1"), (PickupPosition.MIDDLE, "ssl1"), (PickupPosition.NECK, "ssl1")],
        )
        guitar(
            "Gibson", "Les Paul Standard '60s",
            strings=6, scale_lo=24.75, scale_hi=24.75, frets=22, fret="Nickel-silver",
            fb="Rosewood", rad="12", neckc="Set-neck", neckm="Mahogany", profile="'59 Rounded",
            depth1=22.0, nut_w=43.0, body="Mahogany", shape="Les Paul", head="3+3",
            sel="3-way", origin="USA", bridge_obj="gibson_tom", nut_obj="bone",
            tuner_obj="gibson_vintage",
            pickup_spec=[(PickupPosition.BRIDGE, "59"), (PickupPosition.NECK, "59")],
        )
        guitar(
            "Ibanez", "RG550",
            strings=6, scale_lo=25.5, scale_hi=25.5, frets=24, fret="Nickel-silver",
            fb="Maple", rad="17", neckc="Bolt-on", neckm="Maple", profile="Wizard",
            depth1=18.0, nut_w=43.0, body="Basswood", shape="Superstrat", head="6-in-line",
            sel="5-way", origin="Japan", bridge_obj="floyd", nut_obj="floyd_lock",
            tuner_obj="gotoh_sealed",
            pickup_spec=[(PickupPosition.BRIDGE, "evo"), (PickupPosition.MIDDLE, "ssl1"), (PickupPosition.NECK, "evo")],
        )
        guitar(
            "Ibanez", "RG7321",
            strings=7, scale_lo=25.5, scale_hi=25.5, frets=24, fret="Nickel-silver",
            fb="Rosewood", rad="17", neckc="Bolt-on", neckm="Maple", profile="Wizard II",
            depth1=19.0, nut_w=48.0, body="Basswood", shape="Superstrat", head="7-in-line",
            sel="3-way", origin="Indonesia", bridge_obj="hipshot", nut_obj="tusq",
            tuner_obj="gotoh_sealed",
            pickup_spec=[(PickupPosition.BRIDGE, "v8"), (PickupPosition.NECK, "v7")],
        )
        guitar(
            "ESP LTD", "EC-1000 (EMG)",
            strings=6, scale_lo=24.75, scale_hi=24.75, frets=24, fret="Stainless steel",
            fb="Ebony", rad="12", neckc="Set-neck", neckm="Mahogany", profile="Thin U",
            depth1=21.0, nut_w=42.0, body="Mahogany", shape="Single-cut", head="3+3",
            sel="3-way", origin="South Korea", bridge_obj="generic_tom", nut_obj="tusq",
            tuner_obj="sperzel",
            pickup_spec=[(PickupPosition.BRIDGE, "emg81"), (PickupPosition.NECK, "emg60")],
        )
        guitar(
            "Strandberg", "Boden Original 6",
            strings=6, scale_lo=25.0, scale_hi=25.5, frets=24, fret="Stainless steel",
            fb="Ebony", rad="20", neckc="Bolt-on", neckm="Roasted Maple", profile="EndurNeck",
            depth1=20.0, nut_w=48.0, body="Alder", shape="Boden", head="Headless",
            sel="5-way", origin="Indonesia", bridge_obj="strandberg", nut_obj="bone",
            tuner_obj=None,
            pickup_spec=[(PickupPosition.BRIDGE, "strandberg_hb"), (PickupPosition.NECK, "strandberg_hb")],
        )

        self._report()

    def _report(self):
        guitars = models.GuitarModel.objects.published()
        self.stdout.write(self.style.SUCCESS(
            f"\nSeeded {models.Brand.objects.count()} brands, "
            f"{models.Pickup.objects.count()} pickups, {models.Bridge.objects.count()} bridges, "
            f"{models.Tuner.objects.count()} tuners, {models.Nut.objects.count()} nuts, "
            f"{guitars.count()} guitars.\n"
        ))
        self.stdout.write("Derived facets (calculated from components):")
        for g in guitars:
            lo, hi = g.scale_length_min_inches, g.scale_length_max_inches
            scale = f'{lo}"' if lo == hi else f'{lo}–{hi}"'
            self.stdout.write(
                f"  • {g.brand} {g.name}: {g.num_strings}-str, {scale}, "
                f"combo={g.pickup_combination or '—'}, {g.electronics_type}, "
                f"trem={g.has_tremolo}, piezo={g.has_piezo}, "
                f"lockTuners={g.has_locking_tuners}, humCancel={g.has_hum_cancellation}, "
                f"multiscale={g.is_multiscale}, neck={g.neck_thickness_class}"
            )
