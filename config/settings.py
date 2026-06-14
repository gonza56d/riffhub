"""
Django settings for the riffhub project.

Configuration is environment-driven (12-factor style) via django-environ, so the
*same settings module* runs three ways without code changes:

  * local Docker Compose       — DJANGO_ENV=dev, db host = "db"        (default)
  * local venv / bare metal    — DJANGO_ENV=dev, db host = "localhost"
  * PythonAnywhere (paid + PG) — DJANGO_ENV=prod, db host = PA Postgres

The single switch is **DJANGO_ENV** ("dev" | "prod"). It defaults to "dev" and in
turn defaults DEBUG, the production hardening, logging, and the static storage.
Every value can still be overridden by its own env var. See `.env.example` and
`deploy/PYTHONANYWHERE.md`.
"""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# Load a local .env when present. Under Docker Compose the variables are injected
# by the compose file; on PythonAnywhere you create a .env in the project root
# (or export the vars from the WSGI file). A missing file is a no-op.
environ.Env.read_env(BASE_DIR / ".env")

# --- Environment switch -----------------------------------------------------
# "dev" (default) or "prod". Drives DEBUG and the production hardening below.
DJANGO_ENV = env("DJANGO_ENV", default="dev").strip().lower()
if DJANGO_ENV not in {"dev", "prod"}:
    raise ImproperlyConfigured(
        f"DJANGO_ENV must be 'dev' or 'prod', got {DJANGO_ENV!r}."
    )
IS_PROD = DJANGO_ENV == "prod"

# DEBUG follows the environment unless explicitly overridden.
DEBUG = env.bool("DEBUG", default=not IS_PROD)

# --- Core -------------------------------------------------------------------

# Origins trusted for CSRF on HTTPS/cross-scheme POSTs — include the scheme,
# e.g. CSRF_TRUSTED_ORIGINS=https://riffhub.onrender.com
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

if IS_PROD:
    # No insecure fallback in production — fail loudly if these are unset.
    SECRET_KEY = env("SECRET_KEY")
    ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
    # Render injects the service's public hostname at runtime — trust it
    # automatically (also covers the random suffix Render may add to the URL).
    # Harmless on other hosts, where the variable is simply absent.
    _render_host = env("RENDER_EXTERNAL_HOSTNAME", default="")
    if _render_host:
        ALLOWED_HOSTS.append(_render_host)
        CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set when DJANGO_ENV=prod "
            "(e.g. ALLOWED_HOSTS=riffhub.onrender.com)."
        )
else:
    SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-me")
    ALLOWED_HOSTS = env.list(
        "ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"]
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Riffhub apps
    "core",
    "accounts",
    "catalog",
    "forum",
    "moderation",
    "messaging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves collected static straight from the WSGI app so the site
    # renders with DEBUG off on any host. Must sit right after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "moderation.context_processors.moderation_flags",
                "messaging.context_processors.messaging_flags",
                "accounts.context_processors.theme",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------------
# Always driven by DATABASE_URL. Examples:
#   Docker Compose : postgres://riffhub:riffhub@db:5432/riffhub
#   Local venv     : postgres://riffhub:riffhub@localhost:5432/riffhub
#   PythonAnywhere : postgres://USER:PASS@USER-NNN.postgres.pythonanywhere-services.com:NNNNN/DBNAME
# In dev we default to the Compose "db" service; in prod it must be set.
if IS_PROD:
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": env.db(
            "DATABASE_URL",
            default="postgres://riffhub:riffhub@db:5432/riffhub",
        ),
    }
# Reuse DB connections between requests (PA workers are long-lived); health-check
# them so a recycled-by-Postgres connection doesn't surface as an error.
DATABASES["default"]["CONN_MAX_AGE"] = env.int(
    "CONN_MAX_AGE", default=60 if IS_PROD else 0
)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = bool(
    DATABASES["default"]["CONN_MAX_AGE"]
)

# --- Auth -------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

LOGIN_REDIRECT_URL = "/forum/"
LOGOUT_REDIRECT_URL = "/forum/"

# --- Email ------------------------------------------------------------------
# Dev prints e-mails to the console. For real delivery in prod set
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend and the EMAIL_* vars
# below (PythonAnywhere paid accounts allow outbound SMTP).
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="riffhub <no-reply@riffhub.local>"
)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18N / TZ --------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static & media ---------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# In prod, compress + hash collected static for cache-busting and let WhiteNoise
# serve them. In dev (runserver) the staticfiles app serves the raw files, so we
# keep the plain backend and don't require a collectstatic run.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
    },
}
# Don't 500 if a template references a static file missing from the manifest.
WHITENOISE_MANIFEST_STRICT = False

# --- Production hardening ----------------------------------------------------
# Applied only when DJANGO_ENV=prod and DEBUG is off. PythonAnywhere terminates
# TLS at its proxy and forwards X-Forwarded-Proto, so Django must trust it to
# know the request is secure.
if IS_PROD and not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # HSTS is opt-in: only switch it on once HTTPS is permanent for the host.
    # Be careful enabling include-subdomains/preload on a *.pythonanywhere.com
    # subdomain — prefer it on your own custom domain.
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
    )
    SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)

# --- Logging ----------------------------------------------------------------
# In prod, send logs to stderr so they land in the host's server/error log
# (PythonAnywhere captures stderr). Dev keeps Django's default logging so the
# test suite output stays quiet.
if IS_PROD:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "{asctime} {levelname} {name}: {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "verbose",
            },
        },
        "root": {
            "handlers": ["console"],
            "level": env("LOG_LEVEL", default="WARNING"),
        },
        "loggers": {
            "django.request": {
                "handlers": ["console"],
                "level": "ERROR",
                "propagate": False,
            },
        },
    }

# --- Misc -------------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
