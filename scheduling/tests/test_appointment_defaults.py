"""What the "add appointment" modal opens on.

The same two answers as the visit form, from the same two functions —
``organizations.services.default_branch`` and
``accounts.services.default_practitioner``. Two copies of this rule is how the
day list and the visit form would come to disagree about where a patient is
being seen and by whom, which is the mistake ADR 0019 already caught once.

This is also the only screen in the pair a receptionist can reach, so the
"nobody is preselected" half is asserted here rather than on the visit form.
"""

import pytest
from django.urls import reverse

from accounts.models import Role
from core.context import organization_context
from organizations.models import Branch

pytestmark = pytest.mark.django_db


def _modal(client, user):
    client.force_login(user)
    response = client.get(reverse('scheduling:create'))
    assert response.status_code == 200
    return response


def test_the_modal_opens_on_the_chamber_the_clinic_marked(
    client, organization, branch, staff
):
    """Not the one whose name sorts first, which is what this used to be."""
    with organization_context(organization):
        mirpur = Branch.objects.create(
            organization=organization,
            name='Mirpur Chamber',
            code='MIR',
            is_default=True,
        )

    response = _modal(client, staff)

    assert response.context['default_branch'] == mirpur
    # The old rule was "first by name", which is the other one.
    assert branch.name < mirpur.name


def test_the_day_being_filtered_still_wins(client, organization, branch, staff):
    """The receptionist looking at one chamber's list is booking into it."""
    with organization_context(organization):
        Branch.objects.create(
            organization=organization,
            name='Mirpur Chamber',
            code='MIR',
            is_default=True,
        )

    client.force_login(staff)
    response = client.get(reverse('scheduling:create'), {'branch': branch.pk})

    assert response.context['default_branch'] == branch


def test_the_only_practitioner_is_preselected_for_the_receptionist(
    client, organization, practitioner, staff
):
    response = _modal(client, staff)

    assert response.context['default_practitioner'] == practitioner
    assert f'value="{practitioner.pk}" selected' in response.content.decode()


def test_two_practitioners_and_the_receptionist_is_asked(
    client, organization, practitioner, staff, make_member
):
    """The honest answer when the clinic has not said who: Anyone."""
    make_member(organization, role=Role.PRACTITIONER, phone='01700000005')

    response = _modal(client, staff)

    assert response.context['default_practitioner'] is None
    assert 'selected' not in response.content.decode()


def test_a_practitioner_booking_gets_themselves(
    client, organization, practitioner, make_member
):
    make_member(organization, role=Role.PRACTITIONER, phone='01700000005')

    response = _modal(client, practitioner)

    assert response.context['default_practitioner'] == practitioner
