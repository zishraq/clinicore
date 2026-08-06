"""Amending a finalized encounter (SPEC §6.4).

Covers the three properties that matter: an amendment adds a revision rather
than overwriting, it cannot happen without a reason, and STAFF cannot do it at
all. Cross-tenant isolation of the history tables lives in
``test_history_isolation.py`` — historical models do not inherit the org-scoped
manager, so that is its own risk.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical import services
from clinical.models import Encounter, EncounterStatus, Prescription, PrescriptionItem
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def finalized_encounter(organization, branch, practitioner):
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
            chief_complaint='Persistent cough',
            assessment='Viral URTI',
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter
        )
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            free_text_name='Ambroxol syrup',
        )
        services.finalize_encounter(encounter, actor=practitioner)
        # Stashed on the instance so payload building needs no org context.
        encounter.prescription_pk = prescription.pk
        encounter.item_pk = item.pk
        return encounter


def _amendment_payload(encounter, **overrides):
    payload = {
        'patient': encounter.patient_id,
        'branch': encounter.branch_id,
        'practitioner': encounter.practitioner_id,
        'occurred_at': timezone.localtime(encounter.occurred_at).strftime(
            '%Y-%m-%dT%H:%M'
        ),
        'chief_complaint': 'Persistent cough',
        'examination': '',
        'assessment': 'Bacterial bronchitis',
        'plan': 'Start antibiotics',
        'general_instructions': '',
        'print_size': 'A5',
        'change_reason': 'Sputum culture came back positive.',
        'items-TOTAL_FORMS': '1',
        'items-INITIAL_FORMS': '1',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        'items-0-id': encounter.item_pk,
        'items-0-prescription': encounter.prescription_pk,
        'items-0-display_name': 'Amoxicillin 500mg',
        'items-0-dosage': '1 capsule',
        'items-0-frequency': 'Three times daily',
        'items-0-duration': '7 days',
        'items-0-instructions': '',
        'items-0-sort_order': '0',
    }
    payload.update(overrides)
    return payload


def test_amendment_adds_a_revision_instead_of_overwriting(
    client, practitioner, finalized_encounter, organization
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
        _amendment_payload(finalized_encounter),
    )
    assert response.status_code == 302

    with organization_context(organization):
        finalized_encounter.refresh_from_db()
        assert finalized_encounter.status == EncounterStatus.AMENDED
        assert finalized_encounter.assessment == 'Bacterial bronchitis'
        assert finalized_encounter.amended_at is not None

        revisions = list(
            services.encounter_revisions(organization, finalized_encounter)
        )
        # Create, finalize, amend — the earlier states are still readable.
        assert len(revisions) == 3
        newest = revisions[0]
        assert newest.history_change_reason == 'Sputum culture came back positive.'
        assert newest.history_user_id == practitioner.pk
        assert revisions[-1].assessment == 'Viral URTI'


def test_amendment_without_a_reason_is_rejected(
    client, practitioner, finalized_encounter, organization
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
        _amendment_payload(finalized_encounter, change_reason=''),
    )
    assert response.status_code == 200
    assert 'change_reason' in response.context['form'].errors

    with organization_context(organization):
        finalized_encounter.refresh_from_db()
        # Nothing was written: still finalized, still the original assessment.
        assert finalized_encounter.status == EncounterStatus.FINALIZED
        assert finalized_encounter.assessment == 'Viral URTI'


def test_service_refuses_a_reasonless_amendment_even_without_the_form(
    practitioner, finalized_encounter, organization
):
    """The form is the first gate; the service is the one that cannot be skipped."""
    from clinical.forms import EncounterForm, PrescriptionForm, PrescriptionItemFormSet

    with organization_context(organization):
        payload = _amendment_payload(finalized_encounter, change_reason='')
        form = EncounterForm(
            payload, instance=finalized_encounter, organization=organization
        )
        prescription_form = PrescriptionForm(
            payload, instance=finalized_encounter.prescription
        )
        item_formset = PrescriptionItemFormSet(
            payload, instance=finalized_encounter.prescription
        )
        assert form.is_valid() and prescription_form.is_valid()
        assert item_formset.is_valid()

        with pytest.raises(services.AmendmentReasonRequired):
            services.save_encounter(
                organization,
                actor=practitioner,
                form=form,
                prescription_form=prescription_form,
                item_formset=item_formset,
                reason='',
            )


def test_the_view_shows_a_reasonless_amendment_as_a_field_error(
    client, practitioner, finalized_encounter, organization, monkeypatch
):
    """The backstop in ``encounter_update`` (B10).

    Unreachable through the form as it stands: the view derives
    ``requires_reason`` from the same ``is_locked`` read the service uses, so
    the two cannot disagree, and the test above already covers the service
    refusing on its own. What is left to prove is the handler — if the two ever
    drift, the practitioner gets their form back with a field error rather than
    a 500. The service is stubbed because nothing else can make it raise here.
    """

    def _refuse(*args, **kwargs):
        raise services.AmendmentReasonRequired(
            'Amending a finalized encounter requires a reason.'
        )

    monkeypatch.setattr(services, 'save_encounter', _refuse)

    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
        _amendment_payload(finalized_encounter),
    )

    assert response.status_code == 200
    errors = response.context['form'].errors
    assert 'change_reason' in errors
    assert 'requires a reason' in str(errors['change_reason'])

    with organization_context(organization):
        finalized_encounter.refresh_from_db()
        # Nothing was written on the way to the error.
        assert finalized_encounter.status == EncounterStatus.FINALIZED
        assert finalized_encounter.assessment == 'Viral URTI'


def test_staff_cannot_amend_or_read_history(client, staff, finalized_encounter):
    client.force_login(staff)
    assert (
        client.get(
            reverse('clinical:encounter_update', args=[finalized_encounter.pk])
        ).status_code
        == 403
    )
    assert (
        client.post(
            reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
            _amendment_payload(finalized_encounter),
        ).status_code
        == 403
    )
    assert (
        client.get(
            reverse('clinical:encounter_history', args=[finalized_encounter.pk])
        ).status_code
        == 403
    )


def test_history_view_shows_who_what_when_and_why(
    client, practitioner, finalized_encounter
):
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
        _amendment_payload(finalized_encounter),
    )
    response = client.get(
        reverse('clinical:encounter_history', args=[finalized_encounter.pk])
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Sputum culture came back positive.' in body
    assert practitioner.full_name in body
    # The diff names the field and both sides of the change.
    assert 'Viral URTI' in body
    assert 'Bacterial bronchitis' in body


def test_a_record_predating_the_history_tables_is_not_labelled_created(
    client, practitioner, finalized_encounter, organization
):
    """Rows created before simple-history existed have no '+' revision.

    Their first revision is an edit, and the timeline must say so rather than
    calling the amendment a creation.
    """
    with organization_context(organization):
        # Simulate the legacy state: drop the revisions the fixture wrote.
        services.encounter_revisions(organization, finalized_encounter).delete()

    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
        _amendment_payload(finalized_encounter),
    )

    with organization_context(organization):
        timeline = services.revision_timeline(organization, finalized_encounter)
        assert len(timeline) == 1
        assert timeline[0]['is_creation'] is False
        assert timeline[0]['has_previous'] is False


def test_prescription_items_carry_the_same_reason(
    client, practitioner, finalized_encounter, organization
):
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_update', args=[finalized_encounter.pk]),
        _amendment_payload(finalized_encounter),
    )
    with organization_context(organization):
        item = finalized_encounter.prescription.items.get()
        assert item.free_text_name == 'Amoxicillin 500mg'
        newest = item.history.order_by('-history_date').first()
        assert newest.history_change_reason == 'Sputum culture came back positive.'
