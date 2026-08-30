"""Settings → Backups: what it says, and who may read it.

This was a banner on the dashboard, which meant the clinic was greeted at every
sign-in with "Backups are unproven" — a sentence about the server, in front of
people who can neither fix a backup that stopped nor judge whether one matters.
It is a DEVELOPER screen now, and the dashboard is the clinic's again.

The role gate is asserted at the URL rather than on the link, because that is
where authorisation lives (docs/adr/0012-authorisation-at-the-view-boundary.md);
the link is asserted separately, as presentation.
"""

import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

SCREEN = 'core:backup_settings'


@pytest.fixture(autouse=True)
def status_dir(settings, tmp_path):
    settings.BACKUP_STATUS_DIR = tmp_path
    return tmp_path


def _write(status_dir, name, **fields):
    (status_dir / name).write_text(json.dumps(fields))


def _hours_ago(hours):
    return (timezone.now() - timedelta(hours=hours)).isoformat()


def _healthy(status_dir):
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(2),
        last_success=_hours_ago(2),
        offsite=True,
        disk_free_bytes=42_000_000_000,
    )
    _write(
        status_dir,
        'restore-check.json',
        ok=True,
        last_attempt=_hours_ago(48),
        last_success=_hours_ago(48),
    )


# --- Who may see it ---------------------------------------------------------


def test_the_developer_sees_the_screen(client, developer, status_dir):
    _healthy(status_dir)
    client.force_login(developer)

    response = client.get(reverse(SCREEN))

    assert response.status_code == 200
    assert 'Backups are running' in response.content.decode()


@pytest.mark.parametrize('who', ['owner', 'practitioner', 'staff'])
def test_everybody_else_is_refused_at_the_url(client, request, who, status_dir):
    """403, not a hidden link. Bookmarks outlive nav templates."""
    client.force_login(request.getfixturevalue(who))

    assert client.get(reverse(SCREEN)).status_code == 403


def test_the_developer_is_offered_the_link(client, developer, status_dir):
    client.force_login(developer)

    body = client.get(reverse('core:dashboard')).content.decode()

    assert reverse(SCREEN) in body


@pytest.mark.parametrize('who', ['owner', 'practitioner', 'staff'])
def test_nobody_else_is_offered_the_link(client, request, who, status_dir):
    client.force_login(request.getfixturevalue(who))

    body = client.get(reverse('core:dashboard')).content.decode()

    assert reverse(SCREEN) not in body


# --- The dashboard is the clinic's again ------------------------------------


@pytest.mark.parametrize('who', ['owner', 'developer', 'practitioner', 'staff'])
def test_no_backup_state_reaches_the_dashboard(client, request, who, status_dir):
    """Not even a degraded line, and not even for the person who can act on it.

    The status file here is the worst case — nothing has ever run — because the
    banner it replaced was loudest in exactly that state.
    """
    client.force_login(request.getfixturevalue(who))

    body = client.get(reverse('core:dashboard')).content.decode()

    for sentence in (
        'Backups are not running',
        'Backups are unproven',
        'Check the backups',
        'has ever run',
        'test-restored',
    ):
        assert sentence not in body


def test_the_dashboard_does_not_even_look_the_status_up(client, owner, status_dir):
    """Hidden means not looked up, the posture billing's switch already takes.

    A template that forgot a conditional then has nothing to leak.
    """
    client.force_login(owner)

    context = client.get(reverse('core:dashboard')).context

    assert 'backup' not in context
    assert 'restore_check' not in context


# --- What it says -----------------------------------------------------------


def test_a_healthy_backup_still_says_so(client, developer, status_dir):
    """The screen always speaks, unlike the banner.

    A banner that renders nothing when all is well cannot distinguish "working"
    from "nothing is configured", which on a fresh box is the wrong half.
    """
    _healthy(status_dir)
    client.force_login(developer)

    body = client.get(reverse(SCREEN)).content.decode()

    assert 'Backups are running' in body
    assert 'Backups are unproven' not in body


def test_the_card_carries_all_five_facts(client, developer, status_dir):
    _healthy(status_dir)
    client.force_login(developer)

    body = client.get(reverse(SCREEN)).content.decode()

    assert 'Last good backup' in body
    assert 'Last attempt' in body
    assert 'Copied off this server' in body
    assert 'Last test restore' in body
    assert 'Free disk' in body
    # filesizeformat, so the operator reads a size rather than a byte count.
    # It joins the two with a non-breaking space, hence \xa0 rather than ' '.
    assert '39.1\xa0GB' in body


def test_a_stale_backup_is_shouted_about(client, developer, status_dir):
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(24 * 21),
        last_success=_hours_ago(24 * 21),
    )
    client.force_login(developer)

    body = client.get(reverse(SCREEN)).content.decode()

    assert 'Backups are not running' in body


def test_an_unproven_backup_is_said_here_and_only_here(client, developer, status_dir):
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(2),
        last_success=_hours_ago(2),
    )
    client.force_login(developer)

    body = client.get(reverse(SCREEN)).content.decode()

    assert 'Backups are unproven' in body
    assert 'A backup nobody has restored is a guess.' in body


def test_a_backup_that_never_left_the_box_says_so(client, developer, status_dir):
    """The copy that counts. Theft, the disk and the flood each take both."""
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(2),
        last_success=_hours_ago(2),
        offsite=False,
    )
    client.force_login(developer)

    body = client.get(reverse(SCREEN)).content.decode()

    assert 'only on this box' in body


def test_a_run_that_reported_no_disk_says_that_rather_than_zero(
    client, developer, status_dir
):
    """An older run predates the scripts measuring it, and 0 bytes free is a
    different and much worse claim than "not reported"."""
    _write(
        status_dir,
        'backup-status.json',
        ok=True,
        last_attempt=_hours_ago(2),
        last_success=_hours_ago(2),
    )
    client.force_login(developer)

    body = client.get(reverse(SCREEN)).content.decode()

    assert 'Not reported by the last run' in body


def test_the_screen_survives_a_missing_status_directory(
    client, developer, settings, tmp_path
):
    """Development, and the first boot of a new server, both look like this."""
    settings.BACKUP_STATUS_DIR = tmp_path / 'does-not-exist'
    client.force_login(developer)

    response = client.get(reverse(SCREEN))

    assert response.status_code == 200
    assert 'has ever run' in response.content.decode()
