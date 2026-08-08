"""Single-module settings, environment-driven.

SPEC §4 calls for a base/dev/prod split. It has not happened because nothing
here needs it: every environment-specific value is read from the environment,
so the two deployments differ by `.env`, not by module. See `.env.example`.

The security posture is fail-safe rather than fail-open — `DEBUG` is off unless
asked for, and a production boot without a real `SECRET_KEY` raises instead of
starting. Reasoning in docs/MVP-NOTES.md.
"""

import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_list(name: str, default: str = '') -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


#: Off unless explicitly enabled. The asymmetry is the whole argument: a
#: development machine that forgets the flag looks broken and gets fixed in
#: seconds, while a deployment that forgets it serves tracebacks and settings
#: to the internet and nobody finds out. `docker compose up` sets it.
DEBUG = _env_bool('DJANGO_DEBUG', False)

#: Only ever used when DEBUG is on; production raises rather than fall back to
#: a key that is committed to a public repository.
_DEV_SECRET_KEY = 'django-insecure-dev-only-key-do-not-use-outside-local-development'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off. Generate one '
            "with: python -c 'from django.core.management.utils import "
            "get_random_secret_key as k; print(k())'"
        )
    SECRET_KEY = _DEV_SECRET_KEY
elif not DEBUG and SECRET_KEY == _DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is the development key, which is published in this '
        'repository. Generate a real one before running with DJANGO_DEBUG off.'
    )

ALLOWED_HOSTS = _env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]')

CSRF_TRUSTED_ORIGINS = _env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'axes',
    'simple_history',
    'core',
    'organizations',
    'accounts',
    'patients',
    'catalog',
    'scheduling',
    'clinical',
    'billing',
    'inventory',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Directly after SecurityMiddleware, per WhiteNoise's documented position:
    # it must see the request before anything can redirect or shortcut it, and
    # gunicorn will not serve /static/ on its own.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.ActiveOrganizationMiddleware',
    # Last, per django-axes: it turns the PermissionDenied raised by the
    # backend into the lockout response, so everything else has already run.
    'axes.middleware.AxesMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.organization',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

if os.environ.get('POSTGRES_DB'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ['POSTGRES_DB'],
            'USER': os.environ.get('POSTGRES_USER', 'clinicore'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'clinicore'),
            'HOST': os.environ.get('POSTGRES_HOST', 'db'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        }
    }
else:
    # Local runs without Postgres available; docker compose sets POSTGRES_DB.
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_USER_MODEL = 'accounts.User'

# django-axes wraps authentication rather than replacing it: the standalone
# backend only vetoes, so ModelBackend still does the password check. Order
# matters — the veto has to run first.
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# Lock on the pair, not on either alone. Phone numbers are the login identifier
# and they are guessable in blocks, but a clinic is one or two public IPs: a
# lockout keyed on IP alone lets one attacker shut the whole practice out of its
# own records, which is a denial of service dressed up as a security control.
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
# The *form* field, not the model's USERNAME_FIELD. django-axes defaults this
# to `get_user_model().USERNAME_FIELD`, which is 'phone' here — but
# AuthenticationForm names its field 'username' whatever the model calls it,
# and passes `username=` to authenticate(). Left at the default, axes finds no
# 'phone' key, records every attempt as username=None, and the lockout key
# silently collapses to the IP alone: one attacker then locks out the whole
# clinic. Verified by watching AccessAttempt rows, and pinned by
# accounts/tests/test_login_lockout.py.
AXES_USERNAME_FORM_FIELD = 'username'
AXES_FAILURE_LIMIT = int(os.environ.get('DJANGO_AXES_FAILURE_LIMIT', '5'))
AXES_COOLOFF_TIME = timedelta(
    minutes=int(os.environ.get('DJANGO_AXES_COOLOFF_MINUTES', '15'))
)
# Expires the lockout without anyone having to unlock it by hand. Reception has
# no administrator on site and a fifteen-minute wait is not worth a phone call.
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = 'accounts/lockout.html'
# Attempts are stored in the database, so a lockout holds across gunicorn
# workers and survives a restart. A per-process cache would multiply the limit
# by the worker count, silently.
AXES_HANDLER = 'axes.handlers.database.AxesDatabaseHandler'

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.'
        'UserAttributeSimilarityValidator'
    },
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'core:dashboard'
LOGOUT_REDIRECT_URL = 'accounts:login'

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = _env_bool('DJANGO_SECURE_COOKIES', not DEBUG)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# --- Behind a TLS-terminating reverse proxy -------------------------------
#
# Opt-in, and it must stay opt-in. X-Forwarded-Proto is a request header like
# any other: trusting it while nothing upstream overwrites it lets a client
# send `X-Forwarded-Proto: https` over plain HTTP and have Django agree, which
# defeats SECURE_SSL_REDIRECT and every `request.is_secure()` decision below
# it. Set this only when a proxy you control sets the header itself.
BEHIND_PROXY = _env_bool('DJANGO_BEHIND_PROXY', False)
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Client IP arrives via X-Forwarded-For; without this every attempt looks
    # like it came from the proxy and the lockout key collapses to one host.
    AXES_IPWARE_PROXY_COUNT = int(os.environ.get('DJANGO_PROXY_COUNT', '1'))

SECURE_SSL_REDIRECT = _env_bool('DJANGO_SSL_REDIRECT', BEHIND_PROXY)
# The container healthcheck talks plain HTTP to itself; redirecting it to a
# scheme it cannot follow would report a healthy app as down.
SECURE_REDIRECT_EXEMPT = [r'^healthz/?$']
# Opt-in and off by default because HSTS is close to irreversible: browsers
# cache the policy for the full duration, so turning it on before certificates
# are reliably in place makes the site unreachable rather than insecure.
SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('DJANGO_HSTS_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = _env_bool('DJANGO_HSTS_PRELOAD', False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
# Signing in rotates the CSRF token, so a page left open in another tab posts a
# stale one. On a shared reception machine that is routine, not an attack, and
# Django's default answer to it is a page of debug text.
CSRF_FAILURE_VIEW = 'core.views.csrf_failure'

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise serves STATIC_ROOT from the application process, which is what
# makes `gunicorn config.wsgi` a complete deployment. Compressed: it writes
# .gz/.br siblings at collectstatic time so nothing is compressed per request.
# Manifest: filenames carry a content hash, so `app.css` can be cached forever
# and a rebrand is still visible immediately.
#
# The cost is that collectstatic becomes mandatory — a missing entry raises at
# render time rather than 404ing quietly. That is the right trade here: the
# failure this replaces was three JS files 404ing while the CDN kept the page
# looking correct, so the patient picker and invoice lines were dead on a page
# that appeared to have loaded.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'
    },
}
# In development the manifest does not exist and files are read from
# STATICFILES_DIRS as they change.
if DEBUG:
    STORAGES['staticfiles'] = {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'
    }
    WHITENOISE_AUTOREFRESH = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

# --- Logging ---------------------------------------------------------------
#
# There was no LOGGING block at all until now, and "no configuration" did not
# mean "Django's sensible defaults". It meant two different silences:
#
#   - Application warnings — `core.context` reporting a tenant whose timezone
#     is unusable — reached stderr only through `logging.lastResort`, the
#     fallback used when no handler exists anywhere in the hierarchy. That
#     prints the bare message: no timestamp, no level, no logger name. In a
#     gunicorn error log it is an unattributable sentence among request lines.
#   - Unhandled 500s were lost completely. `django.request` propagates to the
#     `django` logger, which Django's defaults give two handlers: a console one
#     filtered by `require_debug_true`, and AdminEmailHandler, which mails
#     ADMINS — empty here. Because handlers exist, `lastResort` never fires;
#     because both drop the record, nothing is written anywhere.
#
# One console handler on the root logger fixes both. Containers are expected to
# log to stdout/stderr and let the platform collect it, so there is no file
# handler and no rotation to get wrong.
LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO').upper()

LOGGING = {
    'version': 1,
    # The apps' module-level `getLogger` calls run at import, before this is
    # applied. Disabling them would silence exactly the loggers we want.
    'disable_existing_loggers': False,
    'formatters': {
        'clinicore': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stderr',
            'formatter': 'clinicore',
        },
    },
    # Everything without a logger of its own ends up here, which is how
    # `core.context` and the feature apps get a real handler.
    'root': {'handlers': ['console'], 'level': LOG_LEVEL},
    'loggers': {
        # Overrides Django's default handlers deliberately. Without this,
        # `django.request` keeps resolving to the filtered console handler and
        # the empty mail_admins, and 500s stay invisible with DEBUG off.
        'django': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        # Query logging is unreadable in a clinic's log and can contain patient
        # data; it stays off even when the rest is turned down to DEBUG.
        'django.db.backends': {'level': 'INFO', 'propagate': True},
        # Lockouts are an operational event someone will be asked about.
        'axes': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
