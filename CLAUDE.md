# riffhub — project context for Claude

riffhub is a **"modern-vintage" web app for guitarists**: a collaborative **guitar gear & specs
database** plus a **forum**. It's a Django monolith, server-rendered with HTMX (no SPA, no separate
front-end server).

**`PRODUCT.md` is the source of truth** — the full product spec plus every engineering decision and
its rationale. Read it for any non-obvious "why". This file is the fast orientation; keep `PRODUCT.md`
updated when you add or change a domain.

## Stack
- **Django 6.0** on Python 3.12, **PostgreSQL 18** (psycopg 3).
- **Docker Compose** for local dev (`web` + `db`). A local **`venv/` mirrors the deps** for IDE/tooling
  and **must stay gitignored** (never commit it).
- Frontend: Django templates + **HTMX** + a little **Alpine.js** — both vendored in
  `static/js/vendor/` (no CDN, **no jQuery**). Theme is "Warm Vintage Workshop" in
  `static/css/riffhub.css` (CSS variables; reuse them).

## Apps (each owns its domain; business rules live in `<app>/services.py`, not views/models)
- **core** — `TimeStampedModel`, `Moderatable` (soft-delete mixin), `SiteConfiguration` singleton.
  The collaborator/founder promotion thresholds have **no default and raise `ImproperlyConfigured`
  until set in /admin** (by design — never silently promote).
- **accounts** — custom `User` (`AUTH_USER_MODEL`), the ordered 6-level `Level` ladder
  (`user.is_at_least(Level.X)`), reputation, e-mail confirmation, profile pages at `/u/<username>/`.
- **catalog** — the collab-db: `Brand`, typed gear (`Bridge`/`Pickup`/`Tuner`/`Nut`), `GuitarModel`,
  and small controlled-vocabulary lookup tables. **Derived guitar facets** (pickup combo, tremolo,
  electronics, …) are recomputed from the attached components via signals and denormalised into indexed
  columns for filtering. Submission/review workflow (`ReviewVote`, `Correction`, `evaluate_submission`);
  browse/filter at `/`, submit UI at `/submit/`, review queue at `/review/`. Bulk **initial seed data**
  (205 real guitars + gear, one CSV per model) lives in **`seeds/`** — read `seeds/README.md` for the
  schema contract + how to extend/regenerate; validate with `python3 seeds/validate_seeds.py`.
  Load it with **`manage.py seed_catalog_csv`** (idempotent; distinct from `manage.py seed_catalog`,
  which loads only ~6 illustrative guitars).
- **forum** — `Topic → Subtopic → Post → Comment`, generic up/down votes + emoji reactions, the Gear
  Market (price + disclaimer), community topic/subtopic proposals, and Creator-only management at
  `/forum/manage/`.
- **moderation** — warnings, escalating silences (1 week → 1 month → permanent), bans, and content
  move / soft-remove; dashboard at `/moderation/`. A context processor exposes `is_moderator` /
  `is_creator` to every template.
- **messaging** — direct messages at `/messages/` (canonical 1:1 `Conversation` + `DirectMessage`,
  unread badge via the `unread_dm_count` context processor) plus DM reporting/moderation
  (`/messages/reports/`).

Cross-cutting gates: `user.is_at_least(Level.X)` for role checks; `moderation.services.can_participate(user)`
gates posting / commenting / DM-sending (silenced & banned users are blocked). CSRF for HTMX is wired
globally via `hx-headers` on `<body>` in `base.html`.

## Run
```
cp .env.example .env
docker compose up --build                                  # web :8000, db :5432
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
docker compose run --rm web python manage.py seed_catalog  # demo gear/guitars
docker compose run --rm web python manage.py seed_forum    # predefined topics/subtopics
docker compose --profile scheduler up                      # opt-in periodic evaluator (proposals/submissions)
# Local tooling via venv (point DATABASE_URL at localhost first — see .env.example):
./venv/bin/python manage.py <command>
```
Configure the promotion thresholds in /admin (SiteConfiguration) to enable Collaborator/Founder
promotion and review-voting; until then everyone derives as Regular.

## Test
- Tests live in the top-level **`tests/`** package — one module per area (`tests/test_<area>.py`).
  There's a broad suite (~1000+ tests); **keep it green**.
- Run all: `docker compose run --rm web python manage.py test tests --noinput`
- Run one: `docker compose run --rm web python manage.py test tests.test_forum_voting --noinput`
- Use `django.test.TestCase` + `Client.force_login`; set `SiteConfiguration` thresholds in `setUp`
  when a test depends on Collaborator/Founder derivation.

## Conventions & gotchas
- **Match the existing style**: 4-space indent, double quotes, type-hinted `__str__`, rules in
  `services.py`, larger model sets as a `<app>/models/` package.
- **Pyright is noisy here**: with no `django-stubs`, the editor flags `.objects`, model field
  descriptors, `TextChoices` members, and `Meta` overrides as errors. These are **false positives** —
  trust `manage.py check` + the test suite, not the inline diagnostics.
- After model changes: `makemigrations <app>` + `migrate`, and add/extend a `tests/` module.
- **Git**: commit & push only when the user asks; **never stage `venv/`** (keep it gitignored); end
  commit messages with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` line.
- E-mail uses the console backend in dev (confirmation links print to the runserver log; surfaced
  under DEBUG).
- **Large features are built with a parallel pattern**: define a tight contract, fan out background
  agents that write against it (`py_compile` only — they must NOT run manage.py/docker/makemigrations,
  to avoid test-DB and reload races), then the orchestrator wires settings/urls, runs
  `makemigrations`/`migrate`, and runs the suite. Write tests against the contract concurrently.
- Heads-up: the sandboxed shell can choke on complex bash (nested `$(...)`/`grep` pipelines, shell
  functions) with "failed to change group ID". For ad-hoc DB checks prefer a `manage.py shell` heredoc
  using `django.test.Client`, and always pass `--noinput` to `manage.py test`.
