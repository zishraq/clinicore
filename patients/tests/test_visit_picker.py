"""Choosing or registering a patient from the visit form (A1).

The doctor goes to Visits first, so a dropdown of every patient in the clinic is
the wrong control and leaving the form to register someone is the wrong flow.
The duplicate guard stays live in this path deliberately: at speed,
mid-consultation, is exactly when a second record for one person gets created.
"""

import pytest
from django.urls import reverse

from clinical.forms import EncounterForm
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization,
            code='P-0001',
            full_name='Rahima Begum',
            phone='01812345678',
        )


def _quick_create_payload(**overrides) -> dict:
    payload = {
        'full_name': 'Kamal Hossain',
        'phone': '01911111111',
        'sex': 'M',
        'date_of_birth': '',
        'address': '',
        'registered_branch': '',
    }
    payload.update(overrides)
    return payload


def test_the_patient_field_is_not_a_dropdown(organization):
    """A clinic's whole patient list is unusable as a <select>."""
    with organization_context(organization):
        form = EncounterForm(organization=organization)
    assert form.fields['patient'].widget.input_type == 'hidden'


def test_the_hidden_field_still_validates_and_scopes(
    organization, other_organization, branch, practitioner
):
    """Changing the widget must not soften the field behind it."""
    with organization_context(other_organization):
        theirs = Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Someone Else'
        )
    with organization_context(organization):
        form = EncounterForm(
            {
                'patient': theirs.pk,
                'branch': branch.pk,
                'practitioner': practitioner.pk,
            },
            organization=organization,
        )
        assert not form.is_valid()
        assert 'patient' in form.errors


def test_suggestions_find_a_patient_by_name(client, practitioner, patient):
    client.force_login(practitioner)
    response = client.get(reverse('patients:suggestions'), {'q': 'Rahima'})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Rahima Begum' in body
    assert 'P-0001' in body


def test_suggestions_find_a_patient_by_phone(client, practitioner, patient):
    client.force_login(practitioner)
    response = client.get(reverse('patients:suggestions'), {'q': '01812345678'})
    assert b'Rahima Begum' in response.content


def test_suggestions_stay_inside_the_organization(
    client, practitioner, other_organization
):
    with organization_context(other_organization):
        Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Theirs Patient'
        )
    client.force_login(practitioner)
    response = client.get(reverse('patients:suggestions'), {'q': 'Theirs'})
    assert b'Theirs Patient' not in response.content


def test_a_miss_still_offers_registration(client, practitioner):
    """The only useful action when nothing matched."""
    client.force_login(practitioner)
    response = client.get(reverse('patients:suggestions'), {'q': 'Nobody Here'})
    body = response.content.decode()
    assert 'data-add-patient' in body
    assert 'as a new patient' in body


def test_the_modal_seeds_the_name_that_was_typed(client, practitioner):
    client.force_login(practitioner)
    response = client.get(
        reverse('patients:quick_create'), {'full_name': 'Kamal Hossain'}
    )
    assert response.status_code == 200
    assert response.context['form'].initial['full_name'] == 'Kamal Hossain'


def test_registering_creates_the_patient_and_selects_them(
    client, practitioner, organization
):
    client.force_login(practitioner)
    response = client.post(reverse('patients:quick_create'), _quick_create_payload())
    assert response.status_code == 200

    with organization_context(organization):
        created = Patient.objects.get(full_name='Kamal Hossain')
        assert created.code == 'P-0001'
    body = response.content.decode()
    # Handed back to the picker as an event, not a swapped fragment.
    assert 'patient-picked' in body
    assert str(created.pk) in body


def test_the_duplicate_guard_runs_in_this_path(client, practitioner, patient):
    """The fastest way to corrupt this dataset is two records for one person."""
    client.force_login(practitioner)
    response = client.post(
        reverse('patients:quick_create'),
        _quick_create_payload(full_name='Rahima Begum', phone='01812345678'),
    )
    assert response.status_code == 200
    assert response.context['duplicates']
    body = response.content.decode()
    # Offered as something to pick, so taking the existing record is less work.
    assert 'P-0001' in body
    assert 'may already be on file' in body
    assert Patient.all_objects.filter(full_name='Rahima Begum').count() == 1


def test_acknowledging_the_warning_registers_anyway(
    client, practitioner, patient, organization
):
    """Two people really can share a name; the guard warns once."""
    client.force_login(practitioner)
    client.post(
        reverse('patients:quick_create'),
        _quick_create_payload(
            full_name='Rahima Begum',
            phone='01812345678',
            duplicates_acknowledged='1',
        ),
    )
    with organization_context(organization):
        assert Patient.objects.filter(full_name='Rahima Begum').count() == 2


def test_the_modal_inherits_the_registration_defaults(
    client, practitioner, organization, branch
):
    """One form definition: A2's branch default applies here without restating."""
    client.force_login(practitioner)
    response = client.get(reverse('patients:quick_create'))
    assert response.context['form'].initial['registered_branch'] == branch.pk
    assert 'approx_age_years' not in response.context['form'].fields


def test_staff_cannot_register_from_the_visit_form(client, staff):
    """The visit form is PRACTITIONER/OWNER, and so is its modal."""
    client.force_login(staff)
    assert client.get(reverse('patients:quick_create')).status_code == 403
    assert (
        client.post(
            reverse('patients:quick_create'), _quick_create_payload()
        ).status_code
        == 403
    )


def test_the_visit_form_renders_the_picker(client, practitioner, patient):
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))
    body = response.content.decode()
    assert 'patientPicker()' in body
    assert 'data-role="patient-id"' in body
    assert reverse('patients:suggestions') in body
    # The modal has to sit outside the visit form; a <form> cannot nest.
    assert 'add_patient_modal' in body


def test_a_prefilled_patient_shows_their_name_in_the_box(client, practitioner, patient):
    """?patient= and the edit form both have to fill the visible box, not just
    the hidden pk — otherwise it reads empty above a real selection."""
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'), {'patient': patient.pk})
    assert response.context['selected_patient'] == patient
    assert 'value="Rahima Begum"' in response.content.decode()
