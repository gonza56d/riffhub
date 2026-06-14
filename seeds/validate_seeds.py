#!/usr/bin/env python3
"""Validate the canonical riffhub seed CSVs (the root-level files in this directory)
against the catalog model contract described in README.md.

Pure stdlib, reads only — run it before loading the seed:

    python3 seeds/validate_seeds.py

Exits 0 and prints a summary when every foreign key resolves, every controlled value
is known, every required guitar field is present, all booleans are true/false, and
gear names are unique within their table (guitars reference gear by name). Exits 1 and
lists problems otherwise.
"""
import csv
import os
import sys
from collections import defaultdict

SEEDS = os.path.dirname(os.path.abspath(__file__))
BOOL = {"true", "false", ""}
TUNER_TYPES = {"sealed", "open_gear", "vintage", "locking", ""}
problems = []


def rows(fname):
    with open(os.path.join(SEEDS, fname), newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            yield {k: (v.strip() if isinstance(v, str) else (v or "")) for k, v in r.items()}


def names(fname, col="name"):
    return {r[col] for r in rows(fname) if r.get(col)}


VOCAB = {
    "fret_materials": "fret_materials.csv", "fretboard_materials": "fretboard_materials.csv",
    "neck_constructions": "neck_constructions.csv", "neck_materials": "neck_materials.csv",
    "neck_profiles": "neck_profiles.csv", "body_materials": "body_materials.csv",
    "body_shapes": "body_shapes.csv", "headstock_types": "headstock_types.csv",
    "selector_switches": "selector_switches.csv", "nut_materials": "nut_materials.csv",
    "countries": "countries.csv", "fretboard_radii": "fretboard_radii.csv",
    "pickup_types": "pickup_types.csv", "bridge_types": "bridge_types.csv",
}
vocab = {k: names(v) for k, v in VOCAB.items()}
brands = names("brands.csv")


def check_unique_names(fname, gtype):
    seen = defaultdict(set)
    for r in rows(fname):
        seen[r.get("name", "")].add(r.get("brand", ""))
    dupes = {n: sorted(bs) for n, bs in seen.items() if len(bs) > 1}
    for n, bs in dupes.items():
        problems.append(f"[{fname}] gear name '{n}' is not unique (brands {bs}) — "
                        f"guitars reference {gtype} by name, so this is ambiguous")
    return set(seen)


def check_brand(fname, r):
    b = r.get("brand", "")
    if not b or b not in brands:
        problems.append(f"[{fname}] '{r.get('name')}' brand '{b}' not in brands.csv")


pickup_names = check_unique_names("pickups.csv", "pickups")
for r in rows("pickups.csv"):
    check_brand("pickups.csv", r)
    pt = r.get("pickup_type", "")
    if pt and pt not in vocab["pickup_types"]:
        problems.append(f"[pickups.csv] '{r.get('name')}' pickup_type '{pt}' unknown")
    if r.get("is_active", "") not in BOOL:
        problems.append(f"[pickups.csv] '{r.get('name')}' is_active invalid")

bridge_names = check_unique_names("bridges.csv", "bridges")
for r in rows("bridges.csv"):
    check_brand("bridges.csv", r)
    bt = r.get("bridge_type", "")
    if bt and bt not in vocab["bridge_types"]:
        problems.append(f"[bridges.csv] '{r.get('name')}' bridge_type '{bt}' unknown")
    for c in ("has_piezo", "is_locking"):
        if r.get(c, "") not in BOOL:
            problems.append(f"[bridges.csv] '{r.get('name')}' {c} invalid")

tuner_names = check_unique_names("tuners.csv", "tuners")
for r in rows("tuners.csv"):
    check_brand("tuners.csv", r)
    if r.get("tuner_type", "") not in TUNER_TYPES:
        problems.append(f"[tuners.csv] '{r.get('name')}' tuner_type invalid")
    if r.get("is_locking", "") not in BOOL:
        problems.append(f"[tuners.csv] '{r.get('name')}' is_locking invalid")

nut_names = check_unique_names("nuts.csv", "nuts")
for r in rows("nuts.csv"):
    check_brand("nuts.csv", r)
    m = r.get("material", "")
    if m and m not in vocab["nut_materials"]:
        problems.append(f"[nuts.csv] '{r.get('name')}' material '{m}' unknown")
    if r.get("is_locking", "") not in BOOL:
        problems.append(f"[nuts.csv] '{r.get('name')}' is_locking invalid")

FK_VOCAB = {
    "fret_material": "fret_materials", "fretboard_material": "fretboard_materials",
    "fretboard_radius": "fretboard_radii", "neck_construction": "neck_constructions",
    "neck_material": "neck_materials", "neck_profile": "neck_profiles",
    "body_material": "body_materials", "body_shape": "body_shapes",
    "headstock_type": "headstock_types", "selector_switch": "selector_switches",
    "country_of_origin": "countries",
}
FK_GEAR = {"bridge": bridge_names, "nut": nut_names, "tuners": tuner_names,
           "pickup_bridge": pickup_names, "pickup_middle": pickup_names, "pickup_neck": pickup_names}

guitar_count = 0
for r in rows("guitars.csv"):
    guitar_count += 1
    nm = r.get("name", "")
    if not nm:
        problems.append("[guitars.csv] row with empty name")
    check_brand("guitars.csv", r)
    for req in ("num_strings", "scale_length_min_inches", "scale_length_max_inches"):
        if not r.get(req):
            problems.append(f"[guitars.csv] '{nm}' missing required {req}")
    if r.get("is_fretless", "") not in BOOL:
        problems.append(f"[guitars.csv] '{nm}' is_fretless invalid")
    for col, vk in FK_VOCAB.items():
        val = r.get(col, "")
        if val and val not in vocab[vk]:
            problems.append(f"[guitars.csv] '{nm}' {col}='{val}' not in {vk}")
    for col, valid in FK_GEAR.items():
        val = r.get(col, "")
        if val and val not in valid:
            problems.append(f"[guitars.csv] '{nm}' {col}='{val}' not found")

print("riffhub seed validation")
print(f"  brands {len(brands)}  vocab tables {len(vocab)}")
print(f"  pickups {len(pickup_names)}  bridges {len(bridge_names)}  "
      f"tuners {len(tuner_names)}  nuts {len(nut_names)}  guitars {guitar_count}")
if problems:
    print(f"\nFAILED — {len(problems)} problem(s):")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("\nOK — every foreign key resolves; ready to load.")
