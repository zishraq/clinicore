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
        'items-0-display_name': 'Ambroxol syrup',
        'items-0-dosage': '10 ml',
        'items-0-frequency': 'Twice daily',
        'items-0-duration': '5 days',
        'items-0-instructions': 'After meals',
        'items-0-sort_order': '0',
    }
    payload.update(overrides)
    return payload


def test_saving_a_visit_completes_it(
    client, practitioner, patient, branch, organization
):
    """A4: the doctor writes the note at the end, so saving is completing.

    Open/Completed was bookkeeping he had to carry for no benefit. The state
    machine is unchanged — only which state a plain save lands in.
    """
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
        assert encounter.status == EncounterStatus.FINALIZED
        assert encounter.finalized_at is not None
        # Finalizing issues the prescription; saving must do the whole thing.
        assert encounter.prescription.issued_at is not None
        items = list(encounter.prescription.items.all())
        assert [item.free_text_name for item in items] == ['Ambroxol syrup']
        # Child rows inherit the tenant, so nothing lands unscoped.
        assert items[0].organization_id == organization.pk


def test_save_as_draft_leaves_the_visit_open(
    client, practitioner, patient, branch, organization
):
    """The interruption path. Still there, just no longer the default."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, save_draft='1'),
    )
    with organization_context(organization):
        encounter = Encounter.objects.get()
        assert encounter.status == EncounterStatus.DRAFT
        assert encounter.finalized_at is None
        assert encounter.prescription.issued_at is None


def test_a_completed_visit_edits_as_an_amendment(
    client, practitioner, patient, branch, organization
):
    """The intended consequence of A4: there is no un-reasoned second edit."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'), _payload(patient, branch, practitioner)
    )
    with organization_context(organization):
        encounter = Encounter.objects.get()

    response = client.get(reverse('clinical:encounter_update', args=[encounter.pk]))
    assert response.status_code == 200
    assert response.context['is_amendment'] is True


def test_finishing_a_draft_completes_it(
    client, practitioner, patient, branch, organization
):
    """Saving a draft from the edit form takes the same completing path."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, save_draft='1'),
    )
    with organization_context(organization):
        encounter = Encounter.objects.get()
        prescription_pk = encounter.prescription.pk
        item_pk = encounter.prescription.items.first().pk

    client.post(
        reverse('clinical:encounter_update', args=[encounter.pk]),
        _payload(
            patient,
            branch,
            practitioner,
            **{
                'items-INITIAL_FORMS': '1',
                'items-0-id': item_pk,
                'items-0-prescription': prescription_pk,
            },
        ),
    )
    with organization_context(organization):
        encounter.refresh_from_db()
        assert encounter.status == EncounterStatus.FINALIZED
        assert encounter.finalized_at is not None


def test_finalizing_by_hand_still_works(
    client, practitioner, patient, branch, organization
):
    """The explicit transition is kept — a draft still needs somewhere to go."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, save_draft='1'),
    )
    with organization_context(organization):
        encounter = Encounter.objects.get()

    client.post(reverse('clinical:encounter_finalize', args=[encounter.pk]))
    with organization_context(organization):
        encounter.refresh_from_db()
        assert encounter.status == EncounterStatus.FINALIZED
        assert encounter.finalized_at is not None
        assert encounter.prescription.issued_at is not None


def test_add_item_row_renumbers_the_formset_prefix(client, practitioner):
    """The HTMX add-row endpoint must name inputs for the next index."""
    client.force_login(practitioner)
    response = client.get(reverse('clinical:item_row'), {'items-TOTAL_FORMS': '3'})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'items-3-free_text_name' in body
    assert '__prefix__' not in body
