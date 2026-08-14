"""Backup staleness, and the banner that makes it visible.

The realistic disaster is not a dramatic one — it is a nightly job that stopped
three weeks ago on a box with no email, noticed on the day someone needs a
restore. Every case here therefore checks that the app errs towards alarm:
missing, unreadable and never-succeeded all report danger, never silence.
"""

import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.backups import (
    BACKUP_DANGER_HOURS,
    BACKUP_WARN_HOURS,
    backup_status,
    restore_check_status,
)


@pytest.fixture(autouse=True)
def status_dir(settings, tmp_path):
    settings.BACKUP_STATUS_DIR = tmp_path
    return tmp_path


def _write(status_dir, name, **fields):
    (status_dir / name).write_text(json.dumps(fields))


def _hours_ago(hours):
    return (timezone.now() - timedelta(hours=hours)).isoformat()


# --- The absent and the broken ---------------------------------------------


def test_a_server_that_has_never_backed_up_is_not_reported_as_fine(status_dir):
    """A fresh box with no backups configured must not look healthy."""
    status = backup_status()

    assert status.level == 'danger'
    assert status.last_success is None
    assert 'has ever run' in status.summary


def test_an_unreadable_status_file_reports_danger_not_silence(status_dir):
    """Half-written or corrupt is no information, and no information is alarm."""
    (status_dir / 'backup-status.json').write_text('{ this is not json')

    assert backup_status().level == 'danger'


def test_a_job_that_has_run_but_never_succeeded_is_danger(status_dir):
    _write(
        status_dir,
        'backup-status.json',
        ok=False,
        last_attempt=_hours_ago(1),
        last_success='',
    )

    status = backup_status()

    assert status.level == 'danger'
    assert status.last_success is None


# --- Ageing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ('hours', 'expected'),
    [
        (1, 'ok'),
        (BACKUP_WARN_HOURS - 1, 'ok'),
        (BACKUP_WARN_HOURS + 1, 'warning'),
        (BACKUP_DANGER_HOURS - 1, 'warning'),
        (BACKUP_DANGER_HOURS + 1, 'danger'),
        (24 * 21, 'danger'),
    ],
)
def test_the_level_follows_the_age_of_the_last_success(status_dir, hours, expected):
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(hours),
        last_success=_hours_ago(hours),
    )

    assert backup_status().level == expected


def test_a_failure_tonight_warns_even_while_last_night_is_still_fresh(status_dir):
    """The run is broken *now*.

    Waiting for a fresh success to age into danger throws away the two days in
    which the problem is easiest to fix.
    """
    _write(
        status_dir,
        'backup-status.json',
        ok=False,
        last_attempt=_hours_ago(1),
        last_success=_hours_ago(2),
    )

    status = backup_status()

    assert status.level == 'warning'
    assert status.last_attempt_failed
    assert 'failed' in status.summary.lower()


def test_a_naive_timestamp_does_not_crash_the_dashboard(status_dir):
    """Belt and braces: the scripts write an offset, but a hand-edited file
    could easily not, and this must never be the thing that 500s the app."""
    naive = (timezone.now() - timedelta(hours=2)).replace(tzinfo=None)
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=naive.isoformat(),
        last_success=naive.isoformat(),
    )

    assert backup_status().level == 'ok'


# --- The restore check -----------------------------------------------------


def test_an_unverified_backup_is_a_warning_not_an_error(status_dir):
    """Unproven is not the same as missing, and reads differently."""
    status = restore_check_status()

    assert status.level == 'warning'
    assert 'has ever been test-restored' in status.summary


def test_a_recent_verification_is_quiet(status_dir):
    _write(
        status_dir,
        'restore-check.json',
        ok=True,
        last_attempt=_hours_ago(24),
        last_success=_hours_ago(24),
    )

    assert restore_check_status().level == 'ok'


def test_a_verification_older_than_a_month_and_a_bit_warns(status_dir):
    _write(
        status_dir,
        'restore-check.json',
        ok=True,
        last_attempt=_hours_ago(24 * 45),
        last_success=_hours_ago(24 * 45),
    )

    assert restore_check_status().level == 'warning'


# --- The banner ------------------------------------------------------------


@pytest.mark.django_db
def test_the_administrator_sees_a_stale_backup_on_the_dashboard(
    client, owner, status_dir
):
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(24 * 21),
        last_success=_hours_ago(24 * 21),
    )
    client.force_login(owner)

    body = client.get(reverse('core:dashboard')).content.decode()

    assert 'Backups are not running' in body


@pytest.mark.django_db
def test_a_healthy_backup_says_nothing_at_all(client, owner, status_dir):
    """A banner that is always there stops being read."""
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(2),
        last_success=_hours_ago(2),
    )
    _write(
        status_dir,
        'restore-check.json',
        ok=True,
        last_attempt=_hours_ago(48),
        last_success=_hours_ago(48),
    )
    client.force_login(owner)

    body = client.get(reverse('core:dashboard')).content.decode()

    assert 'Backups are not running' not in body
    assert 'Check the backups' not in body
    assert 'Backups are unproven' not in body


@pytest.mark.django_db
def test_a_practitioner_is_not_shown_backup_state(client, practitioner, status_dir):
    """Operational, not clinical. It is also not something they can act on."""
    client.force_login(practitioner)

    body = client.get(reverse('core:dashboard')).content.decode()

    assert 'Backups are not running' not in body
    assert 'Backups are unproven' not in body


@pytest.mark.django_db
def test_the_dashboard_survives_a_missing_status_directory(
    client, owner, settings, tmp_path
):
    """Development, and the first boot of a new server, both look like this."""
    settings.BACKUP_STATUS_DIR = tmp_path / 'does-not-exist'
    client.force_login(owner)

    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 200
    assert 'has ever run' in response.content.decode()
