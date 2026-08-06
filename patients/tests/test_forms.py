"""Registration form behaviour (A2).

Two ways to record one fact is a second source of truth, so ``approx_age_years``
is off the form: reception records a date of birth, and the estimate survives
only on rows that already carry one. Where the two meet, the real date wins and
the estimate is cleared rather than colliding with the check constraint.
"""

import datetime

import pytest

from core.context import organization_context
from organizations.models import Branch
from patients.forms import PatientForm
from patients.models import Patient

pytestmark = pytest.mark.django_db


def _payload(**overrides) -> dict:
    payload = {
        'full_name': 'Rahima Begum',
        'phone': '01812345678',
        'sex': 'F',
        'date_of_birth': '',
        'address': '',
        'registered_branch': '',
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def estimated_patient(organization, branch):
    """On file with an estimate and no date of birth — the pre-A2 shape."""
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization,
            code='P-0001',
            full_name='Rahima Begum',
            approx_age_years=46,
            registered_branch=branch,
        )


def test_the_estimate_is_not_on_the_form(organization):
    assert 'approx_age_years' not in PatientForm(organization=organization).fields


def test_the_column_and_its_existing_values_are_untouched(estimated_patient):
    """A2 hides the field; it does not migrate the data away."""
    estimated_patient.refresh_from_db()
    assert estimated_patient.approx_age_years == 46
    assert estimated_patient.age_display == '~46 yrs'


def test_a_date_of_birth_supersedes_a_stored_estimate(organization, estimated_patient):
    with organization_context(organization):
        form = PatientForm(
            data=_payload(date_of_birth='1979-04-02'),
            instance=estimated_patient,
            organization=organization,
        )
        assert form.is_valid(), form.errors
        form.save()

    estimated_patient.refresh_from_db()
    assert estimated_patient.date_of_birth == datetime.date(1979, 4, 2)
    # Cleared, not left to collide with patient_dob_xor_approx_age.
    assert estimated_patient.approx_age_years is None
    assert estimated_patient.age_display.endswith('yrs')


def test_the_estimate_survives_an_unrelated_edit(organization, estimated_patient):
    with organization_context(organization):
        form = PatientForm(
            data=_payload(full_name='Rahima Begum Chowdhury'),
            instance=estimated_patient,
            organization=organization,
        )
        assert form.is_valid(), form.errors
        form.save()

    estimated_patient.refresh_from_db()
    assert estimated_patient.full_name == 'Rahima Begum Chowdhury'
    assert estimated_patient.approx_age_years == 46


def test_registration_preselects_the_branch(organization, branch):
    """One building, one desk: asking every time is a dropdown nobody reads."""
    with organization_context(organization):
        form = PatientForm(organization=organization)
        assert form.initial['registered_branch'] == branch.pk


def test_an_existing_patients_branch_is_not_overwritten(
    organization, estimated_patient
):
    with organization_context(organization):
        other = Branch.objects.create(
            organization=organization, name='Uttara Chamber', code='UTT'
        )
        estimated_patient.registered_branch = other
        estimated_patient.save(update_fields=['registered_branch', 'updated_at'])

        form = PatientForm(instance=estimated_patient, organization=organization)
        assert form.initial['registered_branch'] == other.pk
