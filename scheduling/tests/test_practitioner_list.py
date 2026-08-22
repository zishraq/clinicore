"""Who the day list offers to book a patient with.

The same rule as the visit form's field, and now literally the same function:
``accounts.services.prescribing_users``. The two used to be byte-identical
copies in two apps, which is how one could learn a new rule and the other not
(docs/adr/0019-read-clinical-and-may-be-booked-are-two-facts.md).
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.context import organization_context
from scheduling.models import Appointment

pytestmark = pytest.mark.django_db


def _offered(client, user) -> set:
    client.force_login(user)
    response = client.get(reverse('scheduling:create'))
    assert response.status_code == 200
    return {person.pk for person in response.context['practitioners']}


def test_a_developer_cannot_be_booked(
    client, organization, staff, practitioner, developer
):
    """The receptionist's screen, so the refusal has to hold for STAFF too."""
    offered = _offered(client, staff)

    assert practitioner.pk in offered
    assert developer.pk not in offered


def test_an_owner_can_still_be_booked(client, organization, staff, owner):
    """OWNER is an administrator who does see patients; only DEVELOPER moved."""
    assert owner.pk in _offered(client, staff)


def test_posting_a_developer_as_the_practitioner_is_dropped(
    client, organization, staff, developer, patient, branch
):
    """The list is filtered, so the POST is too — no hidden route back on.

    Booked with nobody rather than refused: a walk-in standing at the desk is
    still a real appointment, and ``Appointment.practitioner`` is nullable for
    exactly that reason.
    """
    today = timezone.localdate()

    client.force_login(staff)
    client.post(
        reverse('scheduling:create'),
        {
            'patient': patient.pk,
            'appointment_branch': branch.pk,
            'appointment_date': today.strftime('%Y-%m-%d'),
            'day_part': 'MORNING',
            'practitioner': developer.pk,
        },
    )

    with organization_context(organization):
        created = Appointment.objects.get()
    assert created.practitioner_id is None
