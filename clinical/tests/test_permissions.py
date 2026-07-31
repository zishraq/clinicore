"""STAFF must not reach clinical data by direct URL (SPEC §6.1).

The role check runs before the view body, so these are 403s, not empty pages —
which is the difference between access control and a hidden template block.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.models import Encounter, Prescription, PrescriptionItem
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def encounter(organization, branch, practitioner):
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            chief_complaint='Persistent cough for two weeks',
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter
        )
        PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            free_text_name='Ambroxol syrup',
            dosage='10 ml',
        )
        return encounter


@pytest.mark.parametrize(
    'url_name',
    ['encounter_list', 'encounter_create'],
)
def test_staff_is_forbidden_from_clinical_index_views(client, staff, url_name):
    client.force_login(staff)
    assert client.get(reverse(f'clinical:{url_name}')).status_code == 403


@pytest.mark.parametrize(
    'url_name',
    ['encounter_detail', 'encounter_update', 'prescription_print'],
)
def test_staff_is_forbidden_from_a_specific_encounter(
    client, staff, encounter, url_name
):
    client.force_login(staff)
    response = client.get(reverse(f'clinical:{url_name}', args=[encounter.pk]))
    assert response.status_code == 403
    assert b'Persistent cough' not in response.content


def test_practitioner_can_read_the_encounter(client, practitioner, encounter):
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_detail', args=[encounter.pk]))
    assert response.status_code == 200
    assert b'Persistent cough' in response.content


def test_prescription_print_renders_both_paper_sizes(client, practitioner, encounter):
    client.force_login(practitioner)
    for size in ['A5', 'A4']:
        response = client.get(
            reverse('clinical:prescription_print', args=[encounter.pk]), {'size': size}
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert f'size: {size}' in body
        assert 'Ambroxol syrup' in body
        # No app chrome on the print page.
        assert 'drawer-side' not in body
