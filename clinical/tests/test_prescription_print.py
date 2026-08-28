"""The printed prescription: two sections, each present only when it has content."""

import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.models import AdviceTemplate, Product
from clinical.models import (
    Encounter,
    ItemType,
    Prescription,
    PrescriptionItem,
)
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def prescription(organization, branch, practitioner):
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
        )
        return Prescription.objects.create(
            organization=organization, encounter=encounter
        )


def _add_medicine(organization, prescription, name='Amoxicillin 500mg'):
    product = Product.objects.create(organization=organization, name=name)
    return PrescriptionItem.objects.create(
        organization=organization,
        prescription=prescription,
        item_type=ItemType.MEDICATION,
        product=product,
        dosage='1 capsule',
        frequency='Three times daily',
    )


def _add_advice(organization, prescription, text='Walk 30 minutes daily.'):
    advice = AdviceTemplate.objects.create(organization=organization, text=text)
    return PrescriptionItem.objects.create(
        organization=organization,
        prescription=prescription,
        item_type=ItemType.ADVICE,
        advice_template=advice,
        frequency='Daily',
    )


def test_advice_only_prescription_prints_without_a_medicines_table(
    client, practitioner, organization, prescription
):
    with organization_context(organization):
        _add_advice(organization, prescription)

    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    )
    assert response.status_code == 200
    body = response.content.decode()

    assert 'Walk 30 minutes daily.' in body
    assert '>Advice</th>' in body
    # No medicines section at all — not a header over an empty table.
    assert '>Medicine</th>' not in body
    assert 'No items prescribed' not in body
    # The ℞ mark *is* here, and this assertion was reversed on 2026-08-28 when
    # the sheet was rebuilt to the clinic's own design. It used to read
    # `'℞' not in body`, which was a proxy for "the medicines section is
    # entirely absent" back when the mark lived inside that section. It now
    # heads the whole right-hand column, so the proxy no longer measures what it
    # was written to measure — and advice is half of what a practitioner
    # prescribes (SPEC §5), so an advice-only sheet is a prescription.
    assert '℞' in body


def test_medicine_only_prescription_prints_without_an_advice_table(
    client, practitioner, organization, prescription
):
    with organization_context(organization):
        _add_medicine(organization, prescription)

    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    )
    body = response.content.decode()

    assert '>Medicine</th>' in body
    assert 'Amoxicillin 500mg' in body
    assert '>Advice</th>' not in body


def test_both_sections_render_and_advice_carries_no_dosage_column(
    client, practitioner, organization, prescription
):
    with organization_context(organization):
        _add_medicine(organization, prescription)
        _add_advice(organization, prescription)

    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    )
    body = response.content.decode()

    assert '>Medicine</th>' in body
    assert '>Advice</th>' in body
    # One dosage column, in the medicines table only.
    assert body.count('>Dosage</th>') == 1
    # The A5/A4 geometry is untouched by the split.
    assert 'size: A5' in body


def test_the_printed_name_is_the_snapshot_not_the_live_catalog_row(
    client, practitioner, organization, prescription
):
    with organization_context(organization):
        item = _add_medicine(organization, prescription, name='Original name')
        product = item.product
        product.name = 'Renamed later'
        product.save(update_fields=['name', 'updated_at'])

    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    )
    body = response.content.decode()
    assert 'Original name' in body
    assert 'Renamed later' not in body


def test_a_prescription_with_nothing_on_it_carries_no_rx_mark(
    client, practitioner, organization, prescription
):
    """The other half of the assertion above: the mark says something was
    prescribed, so an empty sheet must not claim one."""
    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    )
    body = response.content.decode()
    assert 'No items prescribed' in body
    assert '℞' not in body
