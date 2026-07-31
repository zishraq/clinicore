"""Login smoke tests: the phone/password path and the session handshake."""

import pytest
from django.urls import reverse

from accounts.services import ACTIVE_ORGANIZATION_SESSION_KEY

pytestmark = pytest.mark.django_db


def test_login_page_renders_without_an_organization(client):
    response = client.get(reverse('accounts:login'))
    assert response.status_code == 200
    assert b'Sign in' in response.content


def test_wrong_password_is_rejected(client, staff):
    response = client.post(
        reverse('accounts:login'), {'username': staff.phone, 'password': 'wrong'}
    )
    assert response.status_code == 200
    assert not response.wsgi_request.user.is_authenticated


def test_login_activates_the_users_organization(client, staff, organization):
    response = client.post(
        reverse('accounts:login'),
        {'username': staff.phone, 'password': staff.raw_password},
    )
    assert response.status_code == 302
    # The middleware resolves membership on the *next* request, since the login
    # view authenticates after the middleware has already run.
    client.get(reverse('accounts:login'))
    assert client.session[ACTIVE_ORGANIZATION_SESSION_KEY] == organization.pk
