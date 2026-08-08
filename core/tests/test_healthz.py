"""The deployment healthcheck.

The point of each test is a way the endpoint could pass its own smoke test and
still be useless to a container healthcheck: needing a login, needing an active
organization, or answering 200 from Python while the database is gone.
"""

from unittest.mock import patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_healthz_answers_without_a_login(client):
    response = client.get(reverse('core:healthz'))
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_healthz_answers_without_an_active_organization(client, staff):
    """It runs before anyone signs in, so org scoping must not reach it.

    Every other page in the app resolves a membership first. A healthcheck that
    inherited that would report a fresh deployment with no memberships as
    unhealthy and never come into rotation.
    """
    client.force_login(staff)
    response = client.get(reverse('core:healthz'))
    assert response.status_code == 200


def test_healthz_fails_when_the_database_is_unreachable(client):
    """A process accepting sockets with a dead pool is the case this exists for."""
    with patch('core.views.connection.cursor', side_effect=OSError('no route to db')):
        response = client.get(reverse('core:healthz'))

    assert response.status_code == 503
    assert response.json() == {'status': 'error'}


def test_healthz_leaks_no_detail_to_the_public(client):
    """It answers the internet unless something upstream says otherwise."""
    with patch('core.views.connection.cursor', side_effect=OSError('db at 10.0.0.4')):
        response = client.get(reverse('core:healthz'))

    assert b'10.0.0.4' not in response.content
    assert set(response.json()) == {'status'}
