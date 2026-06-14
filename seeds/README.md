# riffhub seed data — CSV contract

This directory holds the **initial-load CSV data** for riffhub's catalog (the collab-db).
Every file maps 1:1 onto a model in `catalog/models/`. A loader (management command) can
read these in the order below and `get_or_create` each row. All rows load as
**`status = "published"`** (this is curated seed data, not community submissions).

## Conventions (apply to every file)

- **Encoding:** UTF-8, comma-separated, `\n` line endings, header row first.
- **Booleans:** the literal lowercase strings `true` / `false`.
- **Empty value = NULL / not-set.** An empty cell means "unknown / leave blank" — it is
  *never* a guess. Nullable model fields stay null; this is deliberate (riffhub's "100%
  true" rule: omit rather than invent).
- **Decimals:** plain numbers, no units. Scale lengths in **inches** (≤3 dp, e.g. `25.5`,
  `24.75`, `25.512`). Neck depths / nut width in **millimetres** (1 dp, e.g. `21.0`).
- **Foreign keys are resolved by natural name** (see each file). The referenced row must
  exist — load order below guarantees that.
- **No commas inside any value** (keeps the CSV quote-free and trivial to parse).

## Load order (FKs only ever point "up" this list)

1. `countries.csv`
2. The plain vocab tables: `fret_materials.csv`, `fretboard_materials.csv`,
   `neck_constructions.csv`, `neck_materials.csv`, `neck_profiles.csv`,
   `body_materials.csv`, `body_shapes.csv`, `headstock_types.csv`,
   `selector_switches.csv`, `nut_materials.csv`
3. The structured vocab tables: `fretboard_radii.csv`, `pickup_types.csv`,
   `bridge_types.csv`
4. `brands.csv`            (FK → countries)
5. Gear: `pickups.csv`, `bridges.csv`, `tuners.csv`, `nuts.csv`
                           (FK → brands + the gear's type vocab)
6. `guitars.csv`           (FK → everything above; inline pickup columns create the
                            `GuitarPickup` through-rows)
7. **After each guitar is created, call `guitar.recompute_derived()`** — the derived
   facet columns (`pickup_combination`, `electronics_type`, `has_hum_cancellation`,
   `has_tremolo`, `has_piezo`, `has_locking_tuners`, `neck_thickness_class`,
   `is_multiscale`) are intentionally **NOT** in any CSV; they are calculated from the
   attached components, exactly as `catalog/signals.py` does.

## File schemas

### Vocabulary (controlled lookup tables → `ControlledVocabulary` subclasses)

| File | Columns |
|------|---------|
| `countries.csv` | `name` |
| `fret_materials.csv` | `name`, `description` |
| `fretboard_materials.csv` | `name`, `description` |
| `neck_constructions.csv` | `name`, `description` |
| `neck_materials.csv` | `name`, `description` |
| `neck_profiles.csv` | `name`, `description` |
| `body_materials.csv` | `name`, `description` |
| `body_shapes.csv` | `name`, `description` |
| `headstock_types.csv` | `name`, `description` |
| `selector_switches.csv` | `name`, `description` |
| `nut_materials.csv` | `name`, `description` |
| `fretboard_radii.csv` | `name`, `radius_min_inches`, `radius_max_inches`, `is_compound`, `is_flat` |
| `pickup_types.csv` | `name`, `symbol`, `is_humbucking` |
| `bridge_types.csv` | `name`, `is_tremolo`, `is_locking` |

`name` is unique per table and is the FK lookup key. `slug` auto-generates on save.
`pickup_types.symbol` is the 1-letter code used to build a guitar's `pickup_combination`
string (`H`=humbucker, `S`=single-coil, `P`=P-90). `is_humbucking` drives the
hum-cancellation facet.

### `brands.csv` → `catalog.Brand`
`name` (unique), `country` (→ `countries.name`, may be empty), `website`, `description`.

### Gear (`name` is unique **within each gear file** — that is the lookup key guitars use)

| File | Columns |
|------|---------|
| `pickups.csv` | `brand`, `name`, `pickup_type`, `is_active`, `description` |
| `bridges.csv` | `brand`, `name`, `bridge_type`, `has_piezo`, `is_locking`, `description` |
| `tuners.csv`  | `brand`, `name`, `is_locking`, `ratio`, `tuner_type`, `description` |
| `nuts.csv`    | `brand`, `name`, `material`, `is_locking`, `description` |

- `pickups.pickup_type` → `pickup_types.name`. `is_active` = active/battery-powered.
- `bridges.bridge_type` → `bridge_types.name`.
- `tuners.tuner_type` → one of the literal `Tuner.TunerType` values:
  `sealed` / `open_gear` / `vintage` / `locking` (or empty). `ratio` e.g. `18:1`.
- `nuts.material` → `nut_materials.name`.

### `guitars.csv` → `catalog.GuitarModel`
Columns (in order). FK columns resolve by name to the tables above; empty = null.

```
brand, name, year_introduced, year_discontinued,
num_strings, scale_length_min_inches, scale_length_max_inches,
num_frets, fret_material, is_fretless, fretboard_material, fretboard_radius,
neck_construction, neck_material, neck_profile,
neck_depth_1st_fret_mm, neck_depth_12th_fret_mm, nut_width_mm,
body_material, body_shape, headstock_type, selector_switch, country_of_origin,
bridge, nut, tuners,
pickup_bridge, pickup_middle, pickup_neck
```

- `brand` → `brands.name`. `num_strings`, `scale_length_min_inches`,
  `scale_length_max_inches` are the only **required** (non-null) fields — everything else
  may be empty. A single-scale guitar has min == max; a multiscale (fan-fret) guitar has
  min ≠ max (this is what derives `is_multiscale`).
- `bridge` → `bridges.name`, `nut` → `nuts.name`, `tuners` → `tuners.name`.
- `pickup_bridge` / `pickup_middle` / `pickup_neck` → `pickups.name` for the pickup in that
  position (empty if no pickup there). The loader creates one `GuitarPickup(position=…)`
  per non-empty column. A guitar may repeat the same pickup in several positions
  (e.g. three identical single-coils in a Strat).

## Provenance
`_raw/` holds the per-brand / per-entity files the research agents produced; the
root-level files are the **canonical, deduped, integrity-checked** merge. Load the
root-level files. `validate_seeds.py` re-checks every FK resolves before you load.

---

# For agents: loading, extending, regenerating

This dataset is **contract-first**: the schema above is the source of truth, the
root-level CSVs are guaranteed to satisfy it (run `python3 seeds/validate_seeds.py` —
it prints `OK …` when every FK resolves), and `_raw/` + `_raw/consolidate.py` let you
rebuild the canonical files reproducibly. Current size: 36 brands, 14 vocab tables,
305 pickups, 108 bridges, 63 tuners, 47 nuts, **205 guitars** (6/7/8-string, 24
multiscale). Everything maps onto `catalog/models/` (`brand.py`, `gear.py`,
`guitar.py`, `vocab.py`, `base.py`).

## Loading it — `manage.py seed_catalog_csv`

The loader is `catalog/management/commands/seed_catalog_csv.py`. Run it with
`manage.py seed_catalog_csv` (optionally `--seeds-dir PATH`). It runs **inside one
`transaction.atomic()`**, reads the root CSVs in the load-order list above, and
`get_or_create`s each row so it is **idempotent** (safe to re-run, composes with the
small `seed_catalog` demo seeder). FKs resolve **by name** via small in-memory caches.
What it does, step by step:

```python
# 1. vocab — get_or_create(name=...) per file. For the 3 structured tables pass extras:
#    FretboardRadius: radius_min_inches/radius_max_inches (Decimal|None), is_compound, is_flat
#    PickupType:      symbol, is_humbucking
#    BridgeType:      is_tremolo, is_locking
#    (slug auto-generates in ControlledVocabulary.save)
# 2. Brand.get_or_create(name=...), country = countries[row.country] or None, +website/description
# 3. Gear, get_or_create(brand=brands[row.brand], name=row.name):
#    Pickup: pickup_type=pickup_types[..], is_active=BOOL
#    Bridge: bridge_type=bridge_types[..], has_piezo=BOOL, is_locking=BOOL
#    Tuner:  is_locking=BOOL, ratio=str, tuner_type=str (one of TunerType or "")
#    Nut:    material=nut_materials[..], is_locking=BOOL
#    Build name->obj maps per gear type (names are unique within a type — see contract).
# 4. GuitarModel.get_or_create(brand=.., name=..) with every spec column:
#    required: num_strings(int), scale_length_min_inches/max(Decimal)
#    nullable FKs resolve by name -> obj or None when the cell is empty
#    bridge=bridges_by_name[..], nut=nuts_by_name[..], tuners=tuners_by_name[..]
#    then for pos in (BRIDGE, MIDDLE, NECK): if row[f"pickup_{pos}"]:
#        GuitarPickup.get_or_create(guitar=g, position=pos,
#                                   defaults={"pickup": pickups_by_name[..]})
#    g.recompute_derived()        # <-- denormalises the facet columns; do NOT skip
# Mark every seed row published: status=PUBLISHED, published_at=reviewed_at=now
# (reuse the `published()` helper pattern from seed_catalog.py).
```

Parsing helpers: `BOOL = value == "true"`; empty string → `None` for nullable
FK/`Decimal`/`int` fields (and `False` for the non-null boolean columns);
`Decimal(value)` for scales/mm, `int(value)` for years/strings/frets.

## Extending it (add gear/guitars) — keep the contract intact

1. Add rows to the relevant `_raw/*.csv` (match the headers exactly). For a **new
   brand of guitars**, drop `guitars_<brand>.csv` plus its
   `pickups_/bridges_/tuners_/nuts_<brand>.csv` companions in `_raw/`.
2. **Rules that keep the merge clean** (a parallel agent must follow these):
   - Use only vocab/brand values that already exist in the root vocab/`brands.csv`
     files (exact string). If none fits, leave the cell **empty** — never invent a
     value, never guess a spec (riffhub's "100% true" rule).
   - Every `bridge/nut/tuners/pickup_*` value a guitar references **must** be emitted
     as a row (by that exact name) in the matching component file — that is what makes
     references resolve. **Gear `name` must be unique within its type** (guitars
     reference gear by name only; a name used by two brands is ambiguous and blocks the
     load — `consolidate.py` flags it as a `NAME COLLISION`).
   - Booleans `true`/`false`; no commas inside a value (or the row's columns shift);
     never add the derived-facet columns to `guitars.csv`.
   - To add a genuinely-new vocab value (a real material/shape/etc.), add a row to the
     vocab CSV first, then reference it.
3. Regenerate + verify:
   ```
   python3 seeds/_raw/consolidate.py            # report-only: lists any problem
   python3 seeds/_raw/consolidate.py --write     # rewrite root CSVs (refuses if blocking problems)
   python3 seeds/validate_seeds.py               # independent re-check of the root files
   ```
   `consolidate.py` dedupes on `(brand, name)`, checks every FK/boolean/required field,
   detects name collisions, and resolves each guitar's gear refs against the same
   source's component files. It writes the root CSVs only when there are **zero**
   blocking problems.

## How this dataset was originally built (the pattern, for re-runs at scale)

Contract-first parallel fan-out (the repo's documented large-feature pattern):
(1) author this README + the base vocab CSVs so foreign keys can't fragment;
(2) spawn **one research subagent per brand/entity** (9 guitar brands + DiMarzio /
Seymour Duncan / EMG / Fishman + Floyd Rose / Gotoh / Wilkinson), each told to read this
contract, scrape its maker's site, and write `_raw/*.csv` — guitar agents also emit the
OEM components their guitars reference so refs resolve by construction; (3) orchestrator
runs `consolidate.py` + `validate_seeds.py`, adds any missing real brands the agents
cited, and ships the canonical root files. Subagents only ever `py_compile`/read+write
under `seeds/` — they never run `manage.py`/`docker`/`makemigrations`.
