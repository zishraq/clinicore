"""The one-page consultation form: encounter, prescription, and items in one POST."""

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.models import Encounter, EncounterStatus
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


def _payload(patient, branch, practitioner, **overrides):
    payload = {
        'patient': patient.pk,
        'branch': branch.pk,
        'practitioner': practitioner.pk,
        'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        'chief_complaint': 'Persistent cough',
        'examination': 'Chest clear',
        'assessment': 'Viral URTI',
        'plan': 'Rest and fluids',
        'general_instructions': 'Return if fever persists',
        'print_size': 'A5',
        'items-TOTAL_FORMS': '1',
        'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        'items-0-free_text_name': 'Ambroxol syrup',
        'items-0-dosage': '10 ml',
        'items-0-frequency': 'Twice daily',
        'items-0-duration': '5 days',
        'items-0-instructions': 'After meals',
        'items-0-sort_order': '0',
    }
    payload.update(overrides)
    return payload


def test_practitioner_creates_an_encounter_with_a_prescription(
    client, practitioner, patient, branch, organization
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner),
        follow=True,
    )
    assert response.status_code == 200

    with organization_context(organization):
        encounter = Encounter.objects.get()
        assert encounter.organization_id == organization.pk
        assert encounter.status == EncounterStatus.DRAFT
        items = list(encounter.prescription.items.all())
        assert [item.free_text_name for item in items] == ['Ambroxol syrup']
        # Child rows inherit the tenant, so nothing lands unscoped.
        assert items[0].organization_id == organization.pk


def test_finalizing_locks_the_encounter(
    client, practitioner, patient, branch, organization
):
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'), _payload(patient, branch, practitioner)
    )
    with organization_context(organization):
        encounter = Encounter.objects.get()

    client.post(reverse('clinical:encounter_finalize', args=[encounter.pk]))
    with organization_context(organization):
        encounter.refresh_from_db()
        assert encounter.status == EncounterStatus.FINALIZED
        assert encounter.finalized_at is not None
        assert encounter.prescription.issued_at is not None

    # A finalized encounter is not editable: the edit view bounces to detail.
    response = client.get(reverse('clinical:encounter_update', args=[encounter.pk]))
    assert response.status_code == 302


def test_add_item_row_renumbers_the_formset_prefix(client, practitioner):
    """The HTMX add-row endpoint must name inputs for the next index."""
    client.force_login(practitioner)
    response = client.get(reverse('clinical:item_row'), {'items-TOTAL_FORMS': '3'})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'items-3-free_text_name' in body
    assert '__prefix__' not in body
