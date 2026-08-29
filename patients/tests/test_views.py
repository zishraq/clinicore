"""STAFF can work the patient list; the clinical profile is not theirs to read."""

import pytest
from django.urls import reverse

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


def test_staff_can_search_patients(client, staff, patient):
    client.force_login(staff)
    response = client.get(reverse('patients:search'), {'q': 'Rahima'})
    assert response.status_code == 200
    assert b'Rahima Begum' in response.content


def test_staff_is_denied_the_case_record(client, staff, patient):
    client.force_login(staff)
    response = client.get(reverse('patients:case_record', args=[patient.pk]))
    assert response.status_code == 403


def test_another_organizations_patient_is_not_found(
    client, staff, other_organization, patient
):
    """Org scoping turns a cross-tenant direct URL hit into a 404, not a 403."""
    with organization_context(other_organization):
        theirs = Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Someone Else'
        )
    client.force_login(staff)
    assert client.get(reverse('patients:detail', args=[theirs.pk])).status_code == 404
