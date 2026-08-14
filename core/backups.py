"""How old the last successful backup is, for the dashboard to shout about.

There is no email on the clinic's box, so a backup job that quietly stopped
three weeks ago has nowhere to report itself. That — not a dramatic failure — is
the realistic disaster: everything looks fine until the day it has to be
restored. So the app reads the status the backup scripts write and puts the age
in front of an administrator every time they sign in.

The scripts run on the host, outside every container, and write JSON into a
directory mounted read-only at ``settings.BACKUP_STATUS_DIR``. Deliberately not
a database row: writing one would mean the backup could only record itself while
the app was up, and "the night the app was down" is exactly the run whose
outcome matters most. A file also cannot be flattered by the app, which mounts
it read-only.

Everything here fails towards alarm. An unreadable, missing, malformed or
truncated file reports *never run* rather than fine, because a fresh box with no
backups configured must not look healthy.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils import timezone

__all__ = [
    'BACKUP_DANGER_HOURS',
    'BACKUP_WARN_HOURS',
    'RESTORE_CHECK_DANGER_DAYS',
    'BackupStatus',
    'backup_status',
    'restore_check_status',
]

logger = logging.getLogger(__name__)

#: A nightly job has missed one night. Worth a word, not yet a crisis.
BACKUP_WARN_HOURS = 36

#: Three nights. Something is broken rather than delayed.
BACKUP_DANGER_HOURS = 72

#: The check runs on the 5th of each month, so 40 days means one was missed.
RESTORE_CHECK_DANGER_DAYS = 40


@dataclass(frozen=True)
class BackupStatus:
    """One job's last-known outcome, already reduced to what a template needs."""

    #: 'ok' | 'warning' | 'danger'. Templates style on this and nothing else.
    level: str
    #: One line for a human. Never a stack trace.
    summary: str
    #: When it last *succeeded*, or None if it never has.
    last_success: datetime | None
    #: Whole hours since that success, or None.
    age_hours: int | None
    #: True when the most recent attempt failed, even if an older one worked.
    last_attempt_failed: bool


def _read(name: str) -> dict | None:
    path = settings.BACKUP_STATUS_DIR / name
    try:
        with path.open() as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Malformed or half-written. Treated as "no information", which this
        # module renders as an alarm rather than as silence.
        logger.warning('Backup status file %s could not be read.', path)
        return None


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # The scripts write local time with an offset; a naive value would blow up
    # on subtraction against an aware now().
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def backup_status() -> BackupStatus:
    """The nightly backup's standing, for the administrator banner."""
    data = _read('backup-status.json')
    if data is None:
        return BackupStatus(
            level='danger',
            summary='No backup has ever run on this server.',
            last_success=None,
            age_hours=None,
            last_attempt_failed=True,
        )

    last_success = _parse(data.get('last_success'))
    failed = not data.get('ok', False)

    if last_success is None:
        return BackupStatus(
            level='danger',
            summary='Backups are configured but none has ever succeeded.',
            last_success=None,
            age_hours=None,
            last_attempt_failed=True,
        )

    age_hours = int((timezone.now() - last_success).total_seconds() // 3600)
    if age_hours >= BACKUP_DANGER_HOURS:
        level = 'danger'
    elif age_hours >= BACKUP_WARN_HOURS or failed:
        # A failure tonight is a warning even while last night's success is
        # still fresh: the run is broken now, and waiting for it to age into
        # danger wastes the two days in which it is easiest to fix.
        level = 'warning'
    else:
        level = 'ok'

    if failed:
        summary = 'The last backup attempt failed.'
    elif level == 'ok':
        summary = 'Backups are running.'
    else:
        summary = 'No backup has succeeded recently.'

    return BackupStatus(
        level=level,
        summary=summary,
        last_success=last_success,
        age_hours=age_hours,
        last_attempt_failed=failed,
    )


def restore_check_status() -> BackupStatus:
    """Whether a backup has recently been proven to restore.

    Quieter than the backup banner by design — a stale verification means the
    backups are unproven, not that they are missing.
    """
    data = _read('restore-check.json')
    if data is None:
        return BackupStatus(
            level='warning',
            summary='No backup has ever been test-restored.',
            last_success=None,
            age_hours=None,
            last_attempt_failed=True,
        )

    last_success = _parse(data.get('last_success'))
    failed = not data.get('ok', False)
    if last_success is None:
        return BackupStatus(
            level='warning',
            summary='No test restore has ever succeeded.',
            last_success=None,
            age_hours=None,
            last_attempt_failed=True,
        )

    age_hours = int((timezone.now() - last_success).total_seconds() // 3600)
    stale = age_hours >= RESTORE_CHECK_DANGER_DAYS * 24
    return BackupStatus(
        level='warning' if (stale or failed) else 'ok',
        summary=(
            'The last test restore failed.'
            if failed
            else 'Backups have not been test-restored recently.'
            if stale
            else 'A backup was test-restored successfully.'
        ),
        last_success=last_success,
        age_hours=age_hours,
        last_attempt_failed=failed,
    )
