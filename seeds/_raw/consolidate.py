#!/usr/bin/env python3
"""Consolidate seeds/_raw/*.csv into canonical seeds/*.csv and validate referential
integrity against the riffhub catalog models.

Report-only by default; pass --write to (re)generate the merged root CSVs.
Stdlib only. Reads/writes ONLY under seeds/ (never touches app code or the DB)."""
import csv
import glob
import os
import sys
from collections import OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))   # seeds/_raw
SEEDS = os.path.dirname(HERE)                        # seeds
WRITE = "--write" in sys.argv

BOOL = {"true", "false", ""}
TUNER_TYPES = {"sealed", "open_gear", "vintage", "locking", ""}

problems = []
def prob(msg):
    problems.append(msg)


def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            yield {
                (k.strip() if k else k): (v.strip() if isinstance(v, str) else (v or ""))
                for k, v in r.items()
            }


def names_of(fname, col="name"):
    return {r[col] for r in rows(os.path.join(SEEDS, fname)) if r.get(col)}


VOCAB = {
    "fret_materials": "fret_materials.csv", "fretboard_materials": "fretboard_materials.csv",
    "neck_constructions": "neck_constructions.csv", "neck_materials": "neck_materials.csv",
    "neck_profiles": "neck_profiles.csv", "body_materials": "body_materials.csv",
    "body_shapes": "body_shapes.csv", "headstock_types": "headstock_types.csv",
    "selector_switches": "selector_switches.csv", "nut_materials": "nut_materials.csv",
    "countries": "countries.csv", "fretboard_radii": "fretboard_radii.csv",
    "pickup_types": "pickup_types.csv", "bridge_types": "bridge_types.csv",
}
vocab = {k: names_of(v) for k, v in VOCAB.items()}
brands = names_of("brands.csv")

GEAR = {
    "pickups": ("pickups_*.csv", ["brand", "name", "pickup_type", "is_active", "description"]),
    "bridges": ("bridges_*.csv", ["brand", "name", "bridge_type", "has_piezo", "is_locking", "description"]),
    "tuners":  ("tuners_*.csv",  ["brand", "name", "is_locking", "ratio", "tuner_type", "description"]),
    "nuts":    ("nuts_*.csv",    ["brand", "name", "material", "is_locking", "description"]),
}


def srckey(path, prefix):
    return os.path.basename(path)[:-4][len(prefix) + 1:]


merged = {}                       # gtype -> OrderedDict[(brand,name)] = row
per_source = defaultdict(dict)     # (gtype, sourcekey) -> {name: brand}

for gtype, (pat, hdr) in GEAR.items():
    md = OrderedDict()
    for path in sorted(glob.glob(os.path.join(HERE, pat))):
        bn = os.path.basename(path)
        sk = srckey(path, gtype)
        for r in rows(path):
            b, n = r.get("brand", ""), r.get("name", "")
            if not n:
                prob(f"[{bn}] {gtype} row with empty name")
                continue
            if not b:
                prob(f"[{bn}] {gtype} '{n}' empty brand")
            elif b not in brands:
                prob(f"[{bn}] brand NOT IN brands.csv: '{b}' ({gtype} '{n}')")
            md.setdefault((b, n), r)
            per_source[(gtype, sk)][n] = b
            if gtype == "pickups":
                pt = r.get("pickup_type", "")
                if pt and pt not in vocab["pickup_types"]:
                    prob(f"[{bn}] pickup_type '{pt}' not in pickup_types ({n})")
                if r.get("is_active", "") not in BOOL:
                    prob(f"[{bn}] is_active invalid '{r.get('is_active')}' ({n})")
            elif gtype == "bridges":
                bt = r.get("bridge_type", "")
                if bt and bt not in vocab["bridge_types"]:
                    prob(f"[{bn}] bridge_type '{bt}' not in bridge_types ({n})")
                for c in ("has_piezo", "is_locking"):
                    if r.get(c, "") not in BOOL:
                        prob(f"[{bn}] {c} invalid ({n})")
            elif gtype == "tuners":
                if r.get("tuner_type", "") not in TUNER_TYPES:
                    prob(f"[{bn}] tuner_type invalid '{r.get('tuner_type')}' ({n})")
                if r.get("is_locking", "") not in BOOL:
                    prob(f"[{bn}] is_locking invalid ({n})")
            elif gtype == "nuts":
                m = r.get("material", "")
                if m and m not in vocab["nut_materials"]:
                    prob(f"[{bn}] nut material '{m}' not in nut_materials ({n})")
                if r.get("is_locking", "") not in BOOL:
                    prob(f"[{bn}] is_locking invalid ({n})")
    merged[gtype] = md

# name collisions (same name, different brand) — break name-based FK resolution
collisions = {}
gear_names = {}
for gtype, md in merged.items():
    byname = defaultdict(set)
    for (b, n) in md:
        byname[n].add(b)
    gear_names[gtype] = set(byname)
    coll = {n: sorted(bs) for n, bs in byname.items() if len(bs) > 1}
    collisions[gtype] = coll
    for n, bs in coll.items():
        prob(f"[{gtype}] NAME COLLISION '{n}' across brands {bs}")

GUITAR_HDR = [
    "brand", "name", "year_introduced", "year_discontinued", "num_strings",
    "scale_length_min_inches", "scale_length_max_inches", "num_frets", "fret_material",
    "is_fretless", "fretboard_material", "fretboard_radius", "neck_construction",
    "neck_material", "neck_profile", "neck_depth_1st_fret_mm", "neck_depth_12th_fret_mm",
    "nut_width_mm", "body_material", "body_shape", "headstock_type", "selector_switch",
    "country_of_origin", "bridge", "nut", "tuners", "pickup_bridge", "pickup_middle",
    "pickup_neck",
]
FK_VOCAB = {
    "fret_material": "fret_materials", "fretboard_material": "fretboard_materials",
    "fretboard_radius": "fretboard_radii", "neck_construction": "neck_constructions",
    "neck_material": "neck_materials", "neck_profile": "neck_profiles",
    "body_material": "body_materials", "body_shape": "body_shapes",
    "headstock_type": "headstock_types", "selector_switch": "selector_switches",
    "country_of_origin": "countries",
}
FK_GEAR = {"bridge": "bridges", "nut": "nuts", "tuners": "tuners",
           "pickup_bridge": "pickups", "pickup_middle": "pickups", "pickup_neck": "pickups"}

guitars = OrderedDict()
for path in sorted(glob.glob(os.path.join(HERE, "guitars_*.csv"))):
    bn = os.path.basename(path)
    for r in rows(path):
        b, n = r.get("brand", ""), r.get("name", "")
        if not n:
            prob(f"[{bn}] guitar with empty name")
            continue
        for req in ("num_strings", "scale_length_min_inches", "scale_length_max_inches"):
            if not r.get(req):
                prob(f"[{bn}] guitar '{n}' missing required {req}")
        if b not in brands:
            prob(f"[{bn}] guitar '{n}' brand '{b}' not in brands.csv")
        if r.get("is_fretless", "") not in BOOL:
            prob(f"[{bn}] guitar '{n}' is_fretless invalid '{r.get('is_fretless')}'")
        for col, vk in FK_VOCAB.items():
            val = r.get(col, "")
            if val and val not in vocab[vk]:
                prob(f"[{bn}] guitar '{n}' {col}='{val}' not in {vk}")
        for col, gt in FK_GEAR.items():
            val = r.get(col, "")
            if not val:
                continue
            if val not in gear_names[gt]:
                prob(f"[{bn}] guitar '{n}' {col}='{val}' NOT FOUND in {gt}")
            elif val in collisions[gt]:
                prob(f"[{bn}] guitar '{n}' {col}='{val}' AMBIGUOUS in {gt} {collisions[gt][val]}")
        guitars.setdefault((b, n), r)


def write_csv(fname, hdr, rowdicts):
    with open(os.path.join(SEEDS, fname), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=hdr, extrasaction="ignore")
        w.writeheader()
        for r in rowdicts:
            w.writerow({k: r.get(k, "") for k in hdr})


print("=" * 70)
print("MERGE TALLY (deduped on brand+name)")
for gtype in GEAR:
    print(f"  {gtype:8} {len(merged[gtype]):4}   distinct names: {len(gear_names[gtype])}")
print(f"  guitars  {len(guitars):4}")
print("=" * 70)
if problems:
    print(f"\n{len(problems)} PROBLEM(S):\n")
    for p in problems:
        print("  -", p)
else:
    print("\nNO PROBLEMS — all FK references resolve, no collisions, all booleans/required fields valid.")

if WRITE:
    if any("NAME COLLISION" in p or "NOT FOUND" in p or "not in brands" in p
           or "NOT IN brands" in p or "missing required" in p for p in problems):
        print("\nNOT writing root CSVs — fix blocking problems first.")
        sys.exit(1)
    write_csv("pickups.csv", GEAR["pickups"][1], merged["pickups"].values())
    write_csv("bridges.csv", GEAR["bridges"][1], merged["bridges"].values())
    write_csv("tuners.csv", GEAR["tuners"][1], merged["tuners"].values())
    write_csv("nuts.csv", GEAR["nuts"][1], merged["nuts"].values())
    write_csv("guitars.csv", GUITAR_HDR, guitars.values())
    print("\nWROTE root CSVs: pickups.csv bridges.csv tuners.csv nuts.csv guitars.csv")
