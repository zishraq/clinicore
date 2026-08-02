"""Test settings: in-memory SQLite by default, and a fast password hasher.

``POSTGRES_DB`` in the environment hands the database back to config.settings,
which is how CI (SPEC §9) and a local ``docker compose up -d db`` run the suite
against a real Postgres. That matters for more than fidelity: the invoice
numbering test needs row-level locking and skips itself on SQLite.
"""

import os

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
