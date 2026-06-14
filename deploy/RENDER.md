# Deploying riffhub to Render

riffhub runs from one settings module switched by **`DJANGO_ENV`** (`dev` | `prod`).
Locally you run `dev` (Docker Compose or the venv). On [Render](https://render.com)
you run `prod`: a Python web service (gunicorn + WhiteNoise) against Render's
**managed PostgreSQL**, all described as code in **`render.yaml`** at the repo root.

We picked Render because it's **fixed-price** (no usage metering), keeps us on
**Postgres** (no MySQL migration), and is about as simple as PythonAnywhere.

## What it costs (fixed, ~$13–14/mo)

| Resource | Plan | ~Price |
| --- | --- | --- |
| Web service | Starter (always-on) | $7/mo |
| PostgreSQL | Basic-256mb | ~$6/mo |
| Persistent disk (media) | 1 GB | ~$0.25/mo |
| Cron (hourly sweeper) | per-second | a few ¢/mo |

> Verify current plan slugs/prices in the dashboard — Render renames tiers
> occasionally. The **free** web tier spins down after inactivity and the **free**
> Postgres is deleted after 30 days, so use the paid tiers above for anything real.

## Prerequisites

- A Render account.
- This repo pushed to GitHub or GitLab.
- `render.yaml` committed (it is).

## 1. Edit `render.yaml`

Open `render.yaml` and change the spots marked `← CHANGE`:

- **`region`** on all three resources — pick the one closest to your users
  (e.g. `oregon`, `ohio`, `virginia`, `frankfurt`, `singapore`). Keep the
  database, web service, and cron in the **same** region.
- **`ALLOWED_HOSTS`** — set it to the URL you expect (`yourname.onrender.com`).
  Don't worry if Render appends a random suffix: the app also reads Render's
  injected `RENDER_EXTERNAL_HOSTNAME` at runtime and trusts the real host
  automatically.
- **`branch`** — defaults to `master`; change if you deploy from another branch.

Commit and push.

## 2. Create the Blueprint

In Render: **New → Blueprint**, connect the repo, and apply. Render provisions:

- `riffhub-db` (Postgres) — and injects its `DATABASE_URL` into the services,
- `riffhub` (web) — builds (`pip install` + `collectstatic`), runs migrations via
  the **pre-deploy** step, then starts gunicorn,
- `riffhub-evaluate` (cron) — hourly `evaluate_pending`.

`SECRET_KEY` is generated for you. Watch the first deploy's logs until it's live,
then open `https://<your-app>.onrender.com`.

## 3. First-time setup (Render Shell)

Open the web service → **Shell** tab and run:

```bash
python manage.py createsuperuser
# Optional initial data:
python manage.py seed_catalog_csv   # full 205-guitar dataset (see seeds/)
python manage.py seed_forum         # predefined topics/subtopics
```

> Migrations already ran automatically (pre-deploy). The Shell runs inside the
> live service with the same env vars, so these commands hit the real database.

Then, in **/admin → SiteConfiguration**, set the Collaborator/Founder promotion
thresholds — until you do, everyone derives as Regular (by design).

## 4. Optional configuration

**Real e-mail.** The default backend prints confirmation mails to the log. For
real delivery, add these env vars to the web service (Environment tab):

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=riffhub <no-reply@yourdomain.com>
```

**Custom domain.** Add it in the web service's **Settings → Custom Domains**,
then add the domain to `ALLOWED_HOSTS` and `https://yourdomain` to
`CSRF_TRUSTED_ORIGINS` (env vars). Consider enabling HSTS then
(`SECURE_HSTS_SECONDS=31536000`).

## Redeploying

Render auto-deploys on every push to the configured branch: it rebuilds, runs
`migrate` (pre-deploy), and restarts gunicorn. Nothing else to do. You can also
hit **Manual Deploy** in the dashboard.

## Troubleshooting

- **Logs:** the web service's **Logs** tab (the app logs warnings/errors to
  stderr in prod).
- **Build fails on `collectstatic` with `ImproperlyConfigured`:** a required prod
  env var is missing — `ALLOWED_HOSTS` must have a value (it's set in
  `render.yaml`). In prod the app refuses to start without `SECRET_KEY`,
  `ALLOWED_HOSTS`, and `DATABASE_URL` — this is intentional.
- **`DisallowedHost`:** set `ALLOWED_HOSTS` to your actual `*.onrender.com` URL.
- **CSRF "Origin checking failed" on a custom domain:** add `https://yourdomain`
  to `CSRF_TRUSTED_ORIGINS`. (The `*.onrender.com` host is handled automatically.)
- **Health check fails right after deploy:** keep `SECURE_SSL_REDIRECT=false`
  (set in `render.yaml`) — Render's edge already forces HTTPS, and a Django-level
  redirect turns the internal HTTP health check into a 301.
- **Uploaded images vanish after a deploy:** the persistent `disk` block in
  `render.yaml` must be present and its `mountPath` must equal `MEDIA_ROOT`
  (`/opt/render/project/src/media`).

## Notes & limits

- **Media on a persistent disk** means the web service runs as a **single
  instance** (Render won't scale a service that has a disk). That's fine to
  start. To scale out later, move uploads to **S3-compatible object storage**
  (Cloudflare R2 / Backblaze B2 / S3) via `django-storages` — no schema change,
  as anticipated in `PRODUCT.md`.
- Static files (CSS/JS) are served by **WhiteNoise** from the app, so they need
  no disk or external mapping.
