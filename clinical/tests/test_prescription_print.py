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
    return re.findall(r'<table class="items[^"]*"[^>]*>(.*?)</table>', body, re.S)


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


# --- The sidebar: what was found, and room to write when nothing was.


def _rules(body: str, heading: str) -> int:
    """How many ruled lines the sheet prints under ``heading``, 0 if none."""
    section = re.search(
        rf'<h4>{heading}</h4>\s*(?:<p>.*?</p>|<div class="rules">(.*?)</div>)',
        body,
        re.S,
    )
    assert section, f'{heading} is not on the sheet'
    return (section.group(1) or '').count('<span>')


@pytest.mark.parametrize('size', ['A4', 'A5'])
def test_an_empty_sidebar_section_keeps_its_heading_and_rules(
    client, practitioner, organization, prescription, size
):
    """Nothing recorded under a heading is not a reason to drop the heading:
    the doctor writes there by hand, which is what the rules are for. The
    paper form's counts — three, two, one — are what an empty section prints,
    at both sizes: the design's structure is not size-conditional, and A5
    once lost the whole rail to fitting work."""
    client.force_login(practitioner)
    body = _print(client, prescription, size)
    assert _rules(body, 'Clinical Findings') == 3
    assert _rules(body, 'Investigation') == 2
    assert _rules(body, 'Diagnosis') == 1
    # Complaint is not a heading on the paper design; it appears only when a
    # complaint was recorded.
    assert '<h4>Complaint</h4>' not in body


def test_a_recorded_section_prints_its_text_instead_of_rules(
    client, practitioner, organization, prescription
):
    encounter = prescription.encounter
    encounter.chief_complaint = 'Recurrent acidity after meals'
    encounter.assessment = 'Gastro-oesophageal reflux'
    encounter.save()

    client.force_login(practitioner)
    body = _print(client, prescription, 'A4')
    assert '<h4>Complaint</h4>' in body
    assert 'Recurrent acidity after meals' in body
    assert _rules(body, 'Diagnosis') == 0
    assert 'Gastro-oesophageal reflux' in body
    # Untouched sections still offer their lines.
    assert _rules(body, 'Clinical Findings') == 3


@pytest.mark.parametrize('size', ['A4', 'A5'])
def test_the_body_is_the_designs_two_columns_at_both_sizes(
    client, practitioner, organization, prescription, size
):
    """The reference design's structure, pinned: the rail and the ℞ column
    side by side, the signature at the foot of the ℞ column, and the notice
    and chambers below the body as one block. A5 was once flattened to plain
    blocks so a spilled sheet could pull its footer back — a change to every
    sheet for the rare one, and the wrong trade."""
    organization.prescription_notice = 'Bring this sheet next time.'
    organization.save()
    with organization_context(organization):
        _add_medicine(organization, prescription)

    client.force_login(practitioner)
    body = _print(client, prescription, size)
    css = re.search(r'<style>(.*?)</style>', body, re.S).group(1)
    body_rule = re.search(r'\.body \{(.*?)\}', css, re.S).group(1)
    assert 'display: grid' in body_rule
    assert re.search(r'grid-template-columns: \d+mm 1fr', body_rule)
    assert 'border-right: 1px solid var(--border)' in css
    # No size-conditional break avoidance that only works on a flattened body.
    assert 'break-before: avoid' not in css

    rx = re.search(r'<div class="rx-area">(.*?)\n    </div>\n  </div>', body, re.S)
    assert rx and 'class="signature"' in rx.group(1)
    tail = re.search(r'<div class="band tail">(.*?)</article>', body, re.S).group(1)
    assert 'class="signature"' not in tail
    assert 'Bring this sheet next time.' in tail
    assert 'class="footer"' in tail
    assert 'table.items thead { display: table-header-group; }' in body
    assert 'table.items tr { break-inside: avoid;' in body


def test_the_derived_tones_are_plain_hex_not_color_mix(
    client, practitioner, organization, prescription
):
    """A custom property is never validated at parse time, so a ``color-mix``
    declaration wins the cascade even where the browser cannot evaluate it —
    and every colour reading it then inherits. The doctor's name printed in
    body black and the patient bar lost its tint. The tones come from Python
    (``organizations.models.mix_hex``) and reach the sheet as hex."""
    organization.branding = {**organization.branding, 'palette': {'primary': '#007791'}}
    organization.save()

    client.force_login(practitioner)
    body = _print(client, prescription, 'A5')
    assert not re.search(r'--primary-\w+: color-mix', body)
    assert '--primary: #007791;' in body
    assert re.search(r'--primary-dark: #[0-9A-F]{6};', body)
    assert re.search(r'--primary-tint: #[0-9A-F]{6};', body)


# --- Which sizes the clinic offers (Organization.prescription_sizes).
#
# The stored size is the doctor's intent, the setting is the clinic's current
# capability, and the sheet is the intersection: coerced at render, never
# rewritten in the database.


def _size_links(body: str) -> list[str]:
    return re.findall(r'href="\?size=(A[45])"', body)


@pytest.mark.parametrize(
    ('setting', 'links', 'requested', 'rendered'),
    [
        ('BOTH', ['A5', 'A4'], 'A4', 'A4'),
        ('A5', [], 'A4', 'A5'),
        ('A4', [], 'A5', 'A4'),
    ],
)
def test_each_size_setting_gives_the_right_toolbar_and_the_right_sheet(
    client,
    practitioner,
    organization,
    prescription,
    setting,
    links,
    requested,
    rendered,
):
    """One size means no toggle at all rather than a single dead button, and a
    ``?size=`` for the size that is off renders the size that is on."""
    organization.prescription_sizes = setting
    organization.save(update_fields=['prescription_sizes', 'updated_at'])

    client.force_login(practitioner)
    body = _print(client, prescription, requested)
    assert _size_links(body) == links
    assert f'size: {rendered};' in body
    other = 'A5' if rendered == 'A4' else 'A4'
    assert f'size: {other};' not in body


def test_a_visit_that_chose_a4_keeps_it_while_the_clinic_is_a5_only(
    client, practitioner, organization, prescription
):
    """Coerced at render, never at save: the stored choice is untouched, so it
    is exactly what prints again the day the clinic offers both sizes."""
    prescription.print_size = 'A4'
    prescription.save(update_fields=['print_size', 'updated_at'])
    organization.prescription_sizes = 'A5'
    organization.save(update_fields=['prescription_sizes', 'updated_at'])

    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    ).content.decode()
    assert 'size: A5;' in body
    prescription.refresh_from_db()
    assert prescription.print_size == 'A4'

    organization.prescription_sizes = 'BOTH'
    organization.save(update_fields=['prescription_sizes', 'updated_at'])
    body = client.get(
        reverse('clinical:prescription_print', args=[prescription.encounter_id])
    ).content.decode()
    assert 'size: A4;' in body


def test_the_visit_form_drops_the_size_box_when_one_size_is_offered(
    organization, prescription
):
    """And a save through that form leaves the stored size alone — dropping
    the field is what keeps ``construct_instance`` off the column."""
    from clinical.forms import PrescriptionForm

    assert 'print_size' in PrescriptionForm(organization=organization).fields

    prescription.print_size = 'A4'
    prescription.save(update_fields=['print_size', 'updated_at'])
    organization.prescription_sizes = 'A5'
    organization.save(update_fields=['prescription_sizes', 'updated_at'])

    form = PrescriptionForm(
        {'general_instructions': 'Nothing after 9pm.'},
        instance=prescription,
        organization=organization,
    )
    assert 'print_size' not in form.fields
    assert form.is_valid(), form.errors
    with organization_context(organization):
        form.save()
    prescription.refresh_from_db()
    assert prescription.general_instructions == 'Nothing after 9pm.'
    assert prescription.print_size == 'A4'
