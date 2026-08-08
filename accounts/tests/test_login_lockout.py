"""Login rate limiting (django-axes).

Disarmed for the suite at large in config/settings_test.py — a shared limiter is
order-dependent state, and any test that signs in wrong would poison the next
one. Each test here re-arms it explicitly.
"""

import pytest
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

#: Three rather than the configured five, so the loops stay readable.
LOCKOUT = {'AXES_ENABLED': True, 'AXES_FAILURE_LIMIT': 3}

#: django-axes answers a locked-out attempt with 429, not 403: this is rate
#: limiting, and the difference is whether the client is told to come back.
LOCKED_OUT = 429


def _sign_in(client, phone, password, ip='203.0.113.10'):
    return client.post(
        reverse('accounts:login'),
        {'username': phone, 'password': password},
        REMOTE_ADDR=ip,
    )


@override_settings(**LOCKOUT)
def test_repeated_failures_lock_the_account(client, staff):
    """The attempt that reaches the limit is itself refused, not the one after."""
    for _ in range(LOCKOUT['AXES_FAILURE_LIMIT'] - 1):
        assert _sign_in(client, staff.phone, 'wrong').status_code == 200

    response = _sign_in(client, staff.phone, 'wrong')
    assert response.status_code == LOCKED_OUT
    assert b'Too many sign-in attempts' in response.content


@override_settings(**LOCKOUT)
def test_attempts_are_recorded_against_the_phone_number(client, staff):
    """Guards a silent default that made the lockout key the IP alone.

    AXES_USERNAME_FORM_FIELD defaults to the *model's* USERNAME_FIELD, 'phone',
    while AuthenticationForm posts a field called 'username' regardless. Axes
    then found no key it recognised and stored username=None on every row, so
    every member of staff shared one bucket. Nothing above this line notices —
    the lockout still fires, at the wrong granularity.
    """
    from axes.models import AccessAttempt

    _sign_in(client, staff.phone, 'wrong')

    assert AccessAttempt.objects.filter(username=staff.phone).exists()


@override_settings(**LOCKOUT)
def test_the_lockout_refuses_the_correct_password_too(client, staff):
    """The control is worthless if the attacker's next guess is let through."""
    for _ in range(LOCKOUT['AXES_FAILURE_LIMIT']):
        _sign_in(client, staff.phone, 'wrong')

    response = _sign_in(client, staff.phone, staff.raw_password)
    assert response.status_code == LOCKED_OUT
    assert not response.wsgi_request.user.is_authenticated


@override_settings(**LOCKOUT)
def test_locking_one_phone_does_not_shut_the_clinic_out(client, staff, practitioner):
    """Why AXES_LOCKOUT_PARAMETERS pairs username with IP rather than using IP.

    A clinic is one public address. Locking on IP alone would let anyone who can
    reach the login page deny every member of staff access to their own records
    by failing four times — a denial of service wearing a security control's
    clothes.
    """
    for _ in range(LOCKOUT['AXES_FAILURE_LIMIT'] + 1):
        _sign_in(client, staff.phone, 'wrong')

    response = _sign_in(client, practitioner.phone, practitioner.raw_password)
    assert response.status_code == 302
    assert response.wsgi_request.user.is_authenticated


@override_settings(**LOCKOUT)
def test_signing_in_clears_the_count(client, staff):
    """AXES_RESET_ON_SUCCESS: a mistyped password must not accumulate all week.

    Without it the limit is cumulative over the cool-off window rather than
    consecutive, and a receptionist who fumbles one password a day is locked out
    on Thursday for no reason she can see.
    """
    for _ in range(LOCKOUT['AXES_FAILURE_LIMIT'] - 1):
        _sign_in(client, staff.phone, 'wrong')

    assert _sign_in(client, staff.phone, staff.raw_password).status_code == 302
    client.logout()

    for _ in range(LOCKOUT['AXES_FAILURE_LIMIT'] - 1):
        assert _sign_in(client, staff.phone, 'wrong').status_code == 200
