"""Who the visit form offers as the treating practitioner.

A DEVELOPER reads every consultation note and is never on this list — that split
is the whole of docs/adr/0019-read-clinical-and-may-be-booked-are-two-facts.md.
The second half of this file is the trap that comes with filtering it: a visit
already recorded against somebody who is no longer offered must still save.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from clinical.forms import EncounterForm
from clinical.models import Encounter
from core.context import organization_context
from organizations.models import Branch
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient(organization) -> Patient:
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


def _offered(organization, instance=None) -> set:
    """The pks the visit form would offer, built inside the org scope."""
    with organization_context(organization):
        form = EncounterForm(organization=organization, instance=instance)
        return set(form.fields['practitioner'].queryset.values_list('pk', flat=True))


def test_a_developer_is_not_offered_as_the_practitioner(
    organization, owner, practitioner, developer
):
    offered = _offered(organization)
    assert practitioner.pk in offered
    assert owner.pk in offered
    assert developer.pk not in offered


def test_a_staff_member_is_not_offered_either(organization, practitioner, staff):
    """Unchanged by ADR 0019, and asserted so the split cannot widen the list."""
    assert staff.pk not in _offered(organization)


def test_a_visit_keeps_offering_the_practitioner_it_already_names(
    organization, branch, patient, developer
):
    """The regression the filter would otherwise introduce.

    A ``ModelChoiceField`` whose stored value is outside its queryset renders
    unselected and then refuses the save with "Select a valid choice". Without
    this, changing your own role would lock every visit you had ever recorded.
    """
    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=developer,
            branch=branch,
            occurred_at=timezone.now(),
            chief_complaint='Persistent cough for two weeks',
        )

    assert developer.pk in _offered(organization, instance=encounter)


def test_that_visit_still_saves(client, organization, branch, patient, developer):
    """End to end, because the queryset is only half the failure.

    Asserted through the view rather than the form alone: a 302 back to the
    visit is what the practitioner sees, and a 200 with a field error is the
    bug wearing the same status code as a validation refusal they caused.
    """
    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=developer,
            branch=branch,
            occurred_at=timezone.now(),
            chief_complaint='Persistent cough for two weeks',
        )

    client.force_login(developer)
    response = client.post(
        reverse('clinical:encounter_update', args=[encounter.pk]),
        {
            'patient': patient.pk,
            'practitioner': developer.pk,
            'branch': branch.pk,
            'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'chief_complaint': 'Persistent cough, now three weeks',
            'print_size': 'A5',
            'items-TOTAL_FORMS': '0',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '20',
        },
    )

    assert response.status_code == 302, getattr(
        response.context.get('form'), 'errors', None
    )
    encounter.refresh_from_db()
    assert encounter.chief_complaint == 'Persistent cough, now three weeks'
    assert encounter.practitioner_id == developer.pk


def test_the_form_does_not_prefill_a_practitioner_it_cannot_offer(
    client, organization, developer
):
    """An unanswered field, not one that looks broken.

    Prefilling the signed-in user would put a value outside the queryset into
    ``initial``, and the select would render with nothing chosen.
    """
    client.force_login(developer)
    response = client.get(reverse('clinical:encounter_create'))

    assert response.status_code == 200
    assert 'practitioner' not in response.context['form'].initial


def test_a_practitioner_is_still_prefilled(client, organization, practitioner):
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))

    assert response.context['form'].initial['practitioner'] == practitioner.pk


# --- What the form opens on -------------------------------------------------
#
# Rule-based, never a "default doctor" column: such a column would be wrong the
# day the clinic hires a second practitioner, and it would go on being wrong
# quietly. See accounts.services.default_practitioner.


def test_the_only_practitioner_is_preselected_for_somebody_who_treats_nobody(
    client, organization, practitioner, developer
):
    """The single-doctor clinic, which is most of them.

    A developer (or, on the day list, a receptionist) opening the form is not
    the one treating, but there is only one person who could be — so the field
    is answered rather than asked.
    """
    client.force_login(developer)
    response = client.get(reverse('clinical:encounter_create'))

    assert response.context['form'].initial['practitioner'] == practitioner.pk


def test_two_practitioners_and_the_field_is_left_empty(
    client, organization, practitioner, developer, make_member
):
    """Genuinely conditional on the count.

    Preselecting the wrong name on a prescription is worse than an empty field:
    the field is required, so nothing can be saved without an answer, and the
    empty one asks the question out loud.
    """
    make_member(organization, role=Role.PRACTITIONER, phone='01700000005')

    client.force_login(developer)
    response = client.get(reverse('clinical:encounter_create'))

    assert 'practitioner' not in response.context['form'].initial


def test_the_signed_in_practitioner_still_wins_over_the_count(
    client, organization, practitioner, make_member
):
    """Two prescribers, and the one writing the visit is preselected."""
    make_member(organization, role=Role.PRACTITIONER, phone='01700000005')

    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))

    assert response.context['form'].initial['practitioner'] == practitioner.pk


def test_the_visit_form_opens_on_the_chamber_the_clinic_marked(
    client, organization, branch, practitioner
):
    """Not the one whose name sorts first (organizations.services.default_branch)."""
    with organization_context(organization):
        mirpur = Branch.objects.create(
            organization=organization,
            name='Mirpur Chamber',
            code='MIR',
            is_default=True,
        )

    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))

    assert response.context['form'].initial['branch'] == mirpur.pk
    # The old rule was "first by name", which is the other one.
    assert branch.name < mirpur.name
