"""The practitioner's half of the printed letterhead.

Per membership, not per user: somebody working at two clinics presents
differently at each, and editing one must leave the other alone. See
``accounts.models.PractitionerProfile``.
"""

import pytest
from django.urls import reverse

from accounts.models import Membership, PractitionerProfile, Role

pytestmark = pytest.mark.django_db

LETTERHEAD = {
    'save_letterhead': '1',
    'print_name': 'ডা. রফিকুল ইসলাম',
    'degrees': 'BHMS (DU), MPH',
    'designation': 'Head of Community Medicine\nGovt. Homeopathic Medical College',
    'additional_note': 'International seminars: India, Thailand, Malaysia',
    'registration_number': 'H-1001',
    'contact_phone': '01700000000',
}


def _profile_of(user, organization) -> PractitionerProfile | None:
    membership = Membership.objects.get(user=user, organization=organization)
    return getattr(membership, 'practitioner_profile', None)


def test_a_practitioner_saves_their_own_letterhead(client, practitioner, organization):
    client.force_login(practitioner)
    response = client.post(reverse('accounts:profile'), LETTERHEAD)
    assert response.status_code == 302

    profile = _profile_of(practitioner, organization)
    assert profile.print_name == 'ডা. রফিকুল ইসলাম'
    assert profile.registration_number == 'H-1001'
    assert profile.has_details is True


def test_the_saved_letterhead_comes_back_onto_the_page(
    client, practitioner, organization
):
    client.force_login(practitioner)
    client.post(reverse('accounts:profile'), LETTERHEAD)

    body = client.get(reverse('accounts:profile')).content.decode()
    assert 'H-1001' in body
    assert 'BHMS (DU), MPH' in body


def test_saving_your_name_does_not_wipe_the_letterhead(
    client, practitioner, organization
):
    """Two forms on one page. Without the submit-name check the unposted one is
    bound to an empty QueryDict and saved as blank."""
    client.force_login(practitioner)
    client.post(reverse('accounts:profile'), LETTERHEAD)

    client.post(
        reverse('accounts:profile'), {'full_name': 'Dr Rafiqul Islam', 'email': ''}
    )

    practitioner.refresh_from_db()
    assert practitioner.full_name == 'Dr Rafiqul Islam'
    assert _profile_of(practitioner, organization).registration_number == 'H-1001'


def test_saving_the_letterhead_does_not_wipe_your_name(
    client, practitioner, organization
):
    client.force_login(practitioner)
    original = practitioner.full_name

    client.post(reverse('accounts:profile'), LETTERHEAD)

    practitioner.refresh_from_db()
    assert practitioner.full_name == original


def test_the_owner_gets_the_letterhead_block_too(client, owner):
    """OWNER is in PRESCRIBING_ROLES — an administrator who also treats people."""
    client.force_login(owner)
    assert client.get(reverse('accounts:profile')).context['letterhead_form']


@pytest.mark.parametrize('role', ['staff', 'developer'])
def test_somebody_who_never_prescribes_is_not_offered_one(client, request, role):
    """A DEVELOPER reads every consultation note and is never recorded as
    treating anybody, so they have no letterhead (ADR 0019)."""
    user = request.getfixturevalue(role)
    client.force_login(user)
    response = client.get(reverse('accounts:profile'))
    assert response.context['letterhead_form'] is None
    assert 'On printed prescriptions' not in response.content.decode()


def test_a_posted_letterhead_from_a_role_without_one_is_ignored(
    client, staff, organization
):
    """The block is absent from the page, so it has to be absent from the POST
    handler too — hiding a form is not refusing it."""
    client.force_login(staff)
    client.post(reverse('accounts:profile'), LETTERHEAD)
    assert _profile_of(staff, organization) is None


def test_two_clinics_hold_two_letterheads_for_one_account(
    client, practitioner, organization, other_organization
):
    """The whole reason this is not on ``User``."""
    second = Membership.objects.create(
        user=practitioner, organization=other_organization, role=Role.PRACTITIONER
    )
    client.force_login(practitioner)
    client.post(reverse('accounts:profile'), LETTERHEAD)

    client.post(reverse('accounts:switch_organization', args=[other_organization.pk]))
    client.post(
        reverse('accounts:profile'), {**LETTERHEAD, 'registration_number': 'H-999'}
    )

    assert _profile_of(practitioner, organization).registration_number == 'H-1001'
    assert second.practitioner_profile.registration_number == 'H-999'


def test_a_profile_with_nothing_below_the_name_reports_no_details(
    client, practitioner, organization
):
    """The printed header degrades to a bare name rather than to four empty rows."""
    client.force_login(practitioner)
    client.post(
        reverse('accounts:profile'),
        {'save_letterhead': '1', 'print_name': 'Dr R Islam'},
    )
    profile = _profile_of(practitioner, organization)
    assert profile.has_details is False
    assert profile.display_name == 'Dr R Islam'


def test_a_blank_print_name_falls_back_to_the_account_name(
    client, practitioner, organization
):
    client.force_login(practitioner)
    client.post(reverse('accounts:profile'), {'save_letterhead': '1'})
    profile = _profile_of(practitioner, organization)
    assert profile.display_name == practitioner.full_name
