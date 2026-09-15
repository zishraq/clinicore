"""The printed prescription: two sections, each present only when it has content."""

import itertools
import re

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


# --- The medicines table: one medicine per row, and never wider than the sheet.
#
# A fixed-layout table with percentage widths that sum to 100 cannot exceed its
# container, which is the guarantee these tests read back out of the markup. The
# paper itself is checked by printing to PDF (see the commit that added them);
# what a status-code test can prove is that every rendered table carries that
# guarantee, at both sizes and for every column set.

_WIDTH_RE = re.compile(r'<th style="width:([\d.]+)%">')


def _tables(body: str) -> list[str]:
    return re.findall(r'<table class="items[^"]*">(.*?)</table>', body, re.S)


def _print(client, prescription, size: str) -> str:
    response = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id]),
        {'size': size},
    )
    assert response.status_code == 200
    return response.content.decode()


def _add_full_medicine(organization, prescription, name='Lycopodium Clavatum'):
    item = _add_medicine(organization, prescription, name=name)
    item.strength = '1M'
    item.pack_size = '1/2 ounce'
    item.preparation = 'Liquid'
    item.duration = '1 month'
    item.instructions = 'In a little water at bedtime, half an hour after food'
    item.save()
    return item


@pytest.mark.parametrize('size', ['A5', 'A4'])
def test_a_medicine_with_all_eight_columns_is_one_row(
    client, practitioner, organization, prescription, size
):
    with organization_context(organization):
        _add_full_medicine(organization, prescription)
        _add_full_medicine(organization, prescription, name='Nux Vomica')

    client.force_login(practitioner)
    body = _print(client, prescription, size)
    assert 'table-layout: fixed' in body
    medicines = _tables(body)[0]
    widths = [float(w) for w in _WIDTH_RE.findall(medicines)]
    assert len(widths) == 8
    assert round(sum(widths), 1) == 100.0
    # One <tr> per medicine in the body, each carrying every column: a value
    # that does not fit wraps inside its cell rather than becoming a second row.
    rows = re.findall(r'<tr>\s*(?:<td[^>]*>.*?</td>\s*)+</tr>', medicines, re.S)
    assert len(rows) == 2
    assert all(row.count('<td') == 8 for row in rows)


@pytest.mark.parametrize('size', ['A5', 'A4'])
def test_the_minimum_column_set_fills_the_row(
    client, practitioner, organization, prescription, size
):
    """Medicine, strength and instructions only — the narrowest realistic case."""
    with organization_context(organization):
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.MEDICATION,
            product=Product.objects.create(organization=organization, name='Sulphur'),
            strength='200C',
            instructions='One dose only, do not repeat',
        )

    client.force_login(practitioner)
    body = _print(client, prescription, size)
    medicines = _tables(body)[0]
    widths = [float(w) for w in _WIDTH_RE.findall(medicines)]
    assert len(widths) == 3
    assert round(sum(widths), 1) == 100.0
    # The sentence gets the room a token column would waste.
    assert widths[2] > widths[1]
    assert medicines.count('<td') == 3
    assert item.strength in medicines


@pytest.mark.parametrize('size', ['A5', 'A4'])
def test_no_table_on_the_sheet_can_be_wider_than_its_column(
    client, practitioner, organization, prescription, size
):
    """Every table — medicines and advice — is fixed-layout with widths summing
    to 100, so nothing on the sheet can push past the right edge of the paper."""
    with organization_context(organization):
        _add_full_medicine(organization, prescription)
        _add_advice(organization, prescription)

    client.force_login(practitioner)
    body = _print(client, prescription, size)
    tables = _tables(body)
    assert len(tables) == 2
    for table in tables:
        widths = [float(w) for w in _WIDTH_RE.findall(table)]
        assert widths, 'a column without a declared width lets the table grow'
        assert round(sum(widths), 1) == 100.0
    assert body.count('table-layout: fixed') == 1  # one rule, on table.items
    assert 'white-space: nowrap' not in body.split('<table')[1]


def test_every_column_set_shares_the_row_exactly():
    """All 128 combinations of the optional columns: the shares sum to 100 and no
    token column outgrows its cap — a fixed table whose columns add up to more
    than its width grows to fit them, which is the overflow being prevented."""
    from clinical.views import MEDICINE_COLUMN_WEIGHTS, MEDICINE_COLUMNS, _column_widths

    for n in range(len(MEDICINE_COLUMNS) + 1):
        for combination in itertools.combinations(MEDICINE_COLUMNS, n):
            keys = ['name', *combination]
            widths = _column_widths(keys)
            assert set(widths) == set(keys)
            assert round(sum(widths.values()), 1) == 100.0
            assert min(widths.values()) > 0
            if 'instructions' in keys:
                # The sentence absorbs whatever the caps free up.
                for key in keys:
                    cap = MEDICINE_COLUMN_WEIGHTS[key][1]
                    if cap is not None:
                        assert widths[key] <= cap + 0.1
