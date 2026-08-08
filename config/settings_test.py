"""Test settings: in-memory SQLite by default, and a fast password hasher.

``POSTGRES_DB`` in the environment hands the database back to config.settings,
which is how CI (SPEC §9) and a local ``docker compose up -d db`` run the suite
against a real Postgres. That matters for more than fidelity: the invoice
numbering and FEFO allocation tests need row-level locking and skip themselves
on SQLite.
"""

import os

# config.settings refuses to start without a real key once DEBUG is off, and
# DEBUG is off here. Declaring a throwaway one explicitly is the point: the
# suite says out loud that it is not carrying a production secret, rather than
# inheriting a committed default that a deployment could also inherit.
os.environ.setdefault('DJANGO_SECRET_KEY', 'test-only-key-not-used-outside-pytest')

from config.settings import *  # noqa: F403

if not os.environ.get('POSTGRES_DB'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

DEBUG = False

# The manifest storage config.settings selects for production reads
# staticfiles.json, which only exists after collectstatic. Every template that
# renders {% static %} would raise ValueError instead of failing the assertion
# it was written for.
STORAGES = {
    **STORAGES,  # noqa: F405
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

# WhiteNoise indexes STATIC_ROOT at startup and warns once per test that the
# directory is missing, because collectstatic has not run and should not have to
# for the suite. Autorefresh resolves files on demand instead of indexing.
WHITENOISE_AUTOREFRESH = True

# Lockouts are a feature with its own tests; leaving them armed for the whole
# suite would make any test that logs in wrong five times poison the next one.
# accounts/tests/test_login_lockout.py re-enables it per test.
AXES_ENABLED = False
