"""What is dispensed, and the row that collapsed around it.

docs/adr/0017-dispensing-details.md. ``pack_size`` and ``preparation`` follow
``strength`` exactly (docs/adr/0015-prescribed-strength.md), so the parts that
matter here are the ones that are new: three capabilities instead of one, the
four original fields hidden by a disclosure rather than dropped, and every
optional print column gated on the data.

The clinic's own words are used throughout — "Potency", "Quantity", "Type" —
the way the timezone tests use a non-UTC zone. A run under the default labels
would pass with a label hardcoded in a template.
"""

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.models import (
    Encounter,
    EncounterStatus,
    ItemType,
    Prescription,
    PrescriptionItem,
)
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def dispensing(organization):
    """A clinic that records all three, in its own words."""
    organization.strength_enabled = True
    organization.strength_options = ['30C', '200C', '1M']
    organization.pack_size_enabled = True
    organization.pack_size_options = [
        '2D',
        '1/2 ounce',
        '1 ounce',
        '2 ounce',
        '4 ounce',
    ]
    organization.preparation_enabled = True
    organization.preparation_options = ['Globule', 'Liquid']
    organization.terminology = {
        **organization.terminology,
        'strength': 'Potency',
        'pack_size': 'Quantity',
        'preparation': 'Type',
    }
    organization.save()
    return organization


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


def _visit(organization, patient, branch, practitioner, **item_fields):
    """A finished visit carrying one medicine with the given fields."""
    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            status=EncounterStatus.FINALIZED,
            finalized_at=timezone.now(),
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter, issued_at=timezone.now()
        )
        PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.MEDICATION,
            free_text_name='Arsenicum album',
            **item_fields,
        )
        return encounter


def _payload(patient, branch, practitioner, **overrides):
    payload = {
        'patient': patient.pk,
        'branch': branch.pk,
        'practitioner': practitioner.pk,
        'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        'chief_complaint': 'Restlessness after midnight',
        'general_instructions': '',
        'print_size': 'A5',
        'items-TOTAL_FORMS': '1',
        'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        'items-0-display_name': 'Arsenicum album',
        'items-0-strength': '200C',
        'items-0-pack_size': '1 ounce',
        'items-0-preparation': 'Liquid',
        'items-0-sort_order': '0',
    }
    payload.update(overrides)
    return payload


# --- the two new capabilities ----------------------------------------------


def test_both_capabilities_ship_off(organization):
    """A dental practice must never see either field."""
    assert organization.pack_size_enabled is False
    assert organization.preparation_enabled is False
    assert organization.suggestions('pack_size') == []
    assert organization.suggestions('preparation') == []
    # And the schema stays specialty-neutral until a clinic says otherwise.
    assert organization.terms['pack_size'] == 'Pack size'
    assert organization.terms['preparation'] == 'Preparation'


def test_the_clinics_own_words_reach_the_prescription_row(
    client, practitioner, dispensing, patient, branch
):
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    for label in ('Potency', 'Quantity', 'Type'):
        assert f'<span class="label-text text-xs">{label}</span>' in body
    assert 'name="items-0-pack_size"' in body
    assert 'name="items-0-preparation"' in body
    # All three are closed lists, so all three are selects and the page holds
    # no datalist at all (ADR 0015 amended, ADR 0017 amended).
    assert '<datalist' not in body
    assert _select_options(body, 'items-0-strength') == ['', '30C', '200C', '1M']
    assert _select_options(body, 'items-0-pack_size') == [
        '',
        '2D',
        '1/2 ounce',
        '1 ounce',
        '2 ounce',
        '4 ounce',
    ]
    assert _select_options(body, 'items-0-preparation') == ['', 'Globule', 'Liquid']
    assert 'select select-bordered' in body


def test_they_appear_in_the_order_the_clinic_reads_them(
    client, practitioner, dispensing, patient, branch
):
    """Item, then potency, then quantity, then type."""
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    positions = [
        body.index('name="items-0-strength"'),
        body.index('name="items-0-pack_size"'),
        body.index('name="items-0-preparation"'),
    ]
    assert positions == sorted(positions)


def test_the_fields_are_absent_when_the_capabilities_are_off(
    client, practitioner, organization, patient, branch
):
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    assert 'name="items-0-pack_size"' not in body
    assert 'name="items-0-preparation"' not in body


def test_posted_values_are_ignored_when_the_capabilities_are_off(
    client, practitioner, organization, patient, branch
):
    """Dropped from the form, not merely left out of the template."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'), _payload(patient, branch, practitioner)
    )
    with organization_context(organization):
        item = PrescriptionItem.objects.get()
        assert item.pack_size == ''
        assert item.preparation == ''


def test_all_three_are_recorded_when_the_capabilities_are_on(
    client, practitioner, dispensing, patient, branch
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner),
        follow=True,
    )
    assert response.status_code == 200
    with organization_context(dispensing):
        item = PrescriptionItem.objects.get()
        assert item.strength == '200C'
        assert item.pack_size == '1 ounce'
        assert item.preparation == 'Liquid'


def test_an_unusual_value_is_not_blocked(
    client, practitioner, dispensing, patient, branch
):
    """The list guides; it never constrains."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, **{'items-0-pack_size': '8 ounce'}),
    )
    with organization_context(dispensing):
        assert PrescriptionItem.objects.get().pack_size == '8 ounce'


def test_editing_with_a_capability_off_keeps_what_was_recorded(
    client, practitioner, organization, patient, branch
):
    """The reason the fields are popped rather than hidden.

    A hidden-but-present field is rebuilt as empty by ``construct_instance`` on
    every later save, so turning a capability off would quietly erase what was
    already recorded the next time anyone touched the visit.
    """
    encounter = _visit(
        organization,
        patient,
        branch,
        practitioner,
        strength='200C',
        pack_size='1 ounce',
        preparation='Globule',
    )
    with organization_context(organization):
        item = PrescriptionItem.objects.get()
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_update', args=[encounter.pk]),
        _payload(
            patient,
            branch,
            practitioner,
            change_reason='Corrected the dose',
            **{
                'items-TOTAL_FORMS': '1',
                'items-INITIAL_FORMS': '1',
                'items-0-id': item.pk,
                'items-0-prescription': item.prescription_id,
                'items-0-display_name': 'Arsenicum album',
                'items-0-free_text_name': 'Arsenicum album',
                'items-0-strength': '',
                'items-0-pack_size': '',
                'items-0-preparation': '',
                'items-0-dosage': '2 pills',
            },
        ),
    )
    item.refresh_from_db()
    assert item.dosage == '2 pills'
    assert (item.strength, item.pack_size, item.preparation) == (
        '200C',
        '1 ounce',
        'Globule',
    )


def test_advice_carries_none_of_the_three(organization, branch, practitioner, patient):
    """Advice is not a substance, so it has no strength, size or preparation."""
    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter
        )
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.ADVICE,
            free_text_name='Walk 30 minutes daily',
            strength='30C',
            pack_size='1 ounce',
            preparation='Liquid',
        )
        item.refresh_from_db()
        assert (item.strength, item.pack_size, item.preparation) == ('', '', '')
        assert item.dosage is None


# --- the disclosure --------------------------------------------------------

#: The four the clinic handles verbally. Collapsed, never removed.
DETAIL_FIELDS = ('dosage', 'frequency', 'duration', 'instructions')


def _select_options(html: str, name: str) -> list[str]:
    """The option values of one <select>, in order."""
    block = re.search(rf'<select[^>]*name="{name}".*?</select>', html, re.S)
    assert block, f'no <select name="{name}"> in the page'
    return re.findall(r'<option value="([^"]*)"', block.group(0))


def _browser_would_post(html: str, name: str) -> str:
    """What a browser would submit for one <select>, selected or not.

    A form control with no option marked selected posts its *first* option —
    which is how a stored value that has dropped off the organization's list
    gets silently erased. Modelling that is the whole point of the test below;
    hardcoding the expected value would test nothing.
    """
    block = re.search(rf'<select[^>]*name="{name}".*?</select>', html, re.S)
    assert block, f'no <select name="{name}"> in the page'
    options = re.findall(r'<option value="([^"]*)"([^>]*)>', block.group(0))
    for value, rest in options:
        if 'selected' in rest:
            return value
    return options[0][0]


def _details_tag(html: str, prefix: str = 'items-0') -> str:
    """The <details> element belonging to one row, opening tag only."""
    rows = html.split('data-item-row')
    row = next(part for part in rows if f'name="{prefix}-' in part)
    return re.search(r'<details[^>]*data-row-details[^>]*>', row).group(0)


def test_a_new_row_starts_collapsed_but_carries_every_field(
    client, practitioner, dispensing, patient, branch
):
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    assert 'open' not in _details_tag(body)
    # In the DOM regardless: a closed <details> still posts what is inside it,
    # and dropping the inputs is what would erase them on the next save.
    for name in DETAIL_FIELDS:
        assert f'name="items-0-{name}"' in body


def test_the_disclosure_opens_on_a_row_that_already_holds_one(
    client, practitioner, dispensing, patient, branch
):
    """Editing an older visit never hides what is on it."""
    encounter = _visit(
        dispensing, patient, branch, practitioner, instructions='After meals'
    )
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:encounter_update', args=[encounter.pk])
    ).content.decode()
    assert 'open' in _details_tag(body)
    assert 'After meals' in body


def test_the_disclosure_stays_shut_for_a_row_with_none_of_them(
    client, practitioner, dispensing, patient, branch
):
    encounter = _visit(dispensing, patient, branch, practitioner, strength='200C')
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:encounter_update', args=[encounter.pk])
    ).content.decode()
    assert 'open' not in _details_tag(body)


def test_a_row_added_by_htmx_carries_all_three_selects(
    client, practitioner, dispensing
):
    """The second row is where a load-time-only binding breaks."""
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:item_row'), {'items-TOTAL_FORMS': '1'}
    ).content.decode()
    assert _select_options(body, 'items-1-strength') == ['', '30C', '200C', '1M']
    assert _select_options(body, 'items-1-pack_size') == [
        '',
        '2D',
        '1/2 ounce',
        '1 ounce',
        '2 ounce',
        '4 ounce',
    ]
    assert _select_options(body, 'items-1-preparation') == ['', 'Globule', 'Liquid']
    for name in DETAIL_FIELDS:
        assert f'name="items-1-{name}"' in body
    assert 'open' not in _details_tag(body, prefix='items-1')


def test_the_four_fields_are_never_dropped_from_the_form(organization):
    """No capability, no template branch, removes them — only the disclosure hides."""
    from clinical.forms import PrescriptionItemFormSet

    with organization_context(organization):
        formset = PrescriptionItemFormSet(organization=organization)
    for name in DETAIL_FIELDS:
        assert name in formset.forms[0].fields


# --- a closed list that changed under an existing row -----------------------


def test_blank_stays_selectable(client, practitioner, dispensing, patient, branch):
    """Both fields are optional; a row may record neither."""
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    assert _select_options(body, 'items-0-preparation')[0] == ''
    assert _browser_would_post(body, 'items-0-preparation') == ''


def test_a_value_the_clinic_no_longer_offers_survives_a_resave(
    client, practitioner, dispensing, patient, branch
):
    """The trap in turning an open field into a closed one.

    An organization can drop a value from its list at any time, and rows
    recorded while it was offered still hold it. A <select> that does not
    contain the stored value renders with nothing selected, so the browser
    posts the first option — blank — and re-saving the visit for an unrelated
    reason erases it. Same data loss as ADR 0015's popped field, different
    route. See docs/adr/0017-dispensing-details.md.
    """
    encounter = _visit(dispensing, patient, branch, practitioner, preparation='Globule')
    with organization_context(dispensing):
        item = PrescriptionItem.objects.get()
        # The clinic stops dispensing globules.
        dispensing.preparation_options = ['Liquid']
        dispensing.save(update_fields=['preparation_options', 'updated_at'])

    client.force_login(practitioner)
    url = reverse('clinical:encounter_update', args=[encounter.pk])
    body = client.get(url).content.decode()
    # Offered even though the clinic no longer lists it, and still selected.
    assert 'Globule' in _select_options(body, 'items-0-preparation')
    assert _browser_would_post(body, 'items-0-preparation') == 'Globule'

    # Re-save exactly what the rendered page would send.
    client.post(
        url,
        _payload(
            patient,
            branch,
            practitioner,
            change_reason='Fixed a typo in the note',
            **{
                'items-TOTAL_FORMS': '1',
                'items-INITIAL_FORMS': '1',
                'items-0-id': item.pk,
                'items-0-prescription': item.prescription_id,
                'items-0-display_name': 'Arsenicum album',
                'items-0-free_text_name': 'Arsenicum album',
                'items-0-strength': '',
                'items-0-pack_size': _browser_would_post(body, 'items-0-pack_size'),
                'items-0-preparation': _browser_would_post(body, 'items-0-preparation'),
            },
        ),
    )
    item.refresh_from_db()
    assert item.preparation == 'Globule'


def test_an_unlisted_value_is_accepted_rather_than_refused(
    client, practitioner, dispensing, patient, branch
):
    """The field stays a plain CharField on purpose.

    Choice validation would turn the case above into a refusal to save the row
    at all, which is a worse failure than the one it prevents: the practitioner
    would be blocked from correcting a note by a settings change months old.
    """
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, **{'items-0-preparation': 'Powder'}),
        follow=True,
    )
    assert response.status_code == 200
    with organization_context(dispensing):
        assert PrescriptionItem.objects.get().preparation == 'Powder'


# --- what prints, and what does not ----------------------------------------


def test_the_printout_carries_only_the_columns_this_visit_filled_in(
    client, practitioner, dispensing, patient, branch
):
    """Seven optional columns do not fit an A5 sheet; four of them empty is worse."""
    encounter = _visit(
        dispensing,
        patient,
        branch,
        practitioner,
        strength='200C',
        pack_size='1 ounce',
        preparation='Globule',
    )
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[encounter.pk])
    ).content.decode()
    assert '<th>Potency</th>' in body
    assert '<th>Quantity</th>' in body
    assert '<th>Type</th>' in body
    # Handled verbally at this clinic, so they are not on the sheet at all.
    for label in ('Dosage', 'Frequency', 'Duration', 'Instructions'):
        assert f'<th>{label}</th>' not in body


def test_a_visit_that_recorded_a_dosage_still_prints_it(
    client, practitioner, dispensing, patient, branch
):
    """The gate is the data, so the old four are unaffected where they were used."""
    encounter = _visit(
        dispensing, patient, branch, practitioner, dosage='4 pills', frequency='Twice'
    )
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[encounter.pk])
    ).content.decode()
    assert '<th>Dosage</th>' in body
    assert '4 pills' in body
    assert '<th>Duration</th>' not in body


def test_recorded_values_still_print_after_the_switches_go_off(
    client, practitioner, organization, patient, branch
):
    """Reprinting a visit must reproduce what the patient was handed."""
    encounter = _visit(
        organization,
        patient,
        branch,
        practitioner,
        pack_size='1 ounce',
        preparation='Globule',
    )
    assert organization.pack_size_enabled is False
    client.force_login(practitioner)
    for url in ('clinical:prescription_print', 'clinical:encounter_detail'):
        body = client.get(reverse(url, args=[encounter.pk])).content.decode()
        assert '1 ounce' in body
        assert 'Globule' in body


# --- the owner sets all of it without a developer --------------------------


def test_the_owner_turns_them_on_names_them_and_lists_the_values(
    client, owner, organization
):
    client.force_login(owner)
    response = client.post(
        reverse('organizations:feature_settings'),
        {
            'advice_enabled': 'on',
            'pack_size_enabled': 'on',
            'pack_size_label': 'Quantity',
            'pack_size_options': '2D\n1 ounce\n1 ounce\n\n4 ounce\n',
            'preparation_enabled': 'on',
            'preparation_label': 'Type',
            'preparation_options': 'Globule\nLiquid',
        },
    )
    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.pack_size_enabled is True
    assert organization.preparation_enabled is True
    # Cleaned on the way in: blanks and case-insensitive duplicates dropped,
    # the clinic's own order kept.
    assert organization.suggestions('pack_size') == ['2D', '1 ounce', '4 ounce']
    assert organization.terms['pack_size'] == 'Quantity'
    assert organization.terms['preparation'] == 'Type'
    # The strength capability is untouched by the same POST going through a loop.
    assert organization.strength_enabled is False
    assert organization.terms['strength'] == 'Strength'


def test_a_blank_label_clears_the_override(client, owner, dispensing):
    client.force_login(owner)
    client.post(
        reverse('organizations:feature_settings'),
        {'pack_size_enabled': 'on', 'pack_size_label': ''},
    )
    dispensing.refresh_from_db()
    assert dispensing.terms['pack_size'] == 'Pack size'
    # Cleared, not stored blank: an empty override is a dead key in the JSON.
    assert 'pack_size' not in dispensing.terminology


def test_the_settings_screen_shows_what_is_already_set(client, owner, dispensing):
    client.force_login(owner)
    body = client.get(reverse('organizations:feature_settings')).content.decode()
    assert 'value="Quantity"' in body
    assert 'value="Type"' in body
    assert '2D\n1/2 ounce\n1 ounce\n2 ounce\n4 ounce' in body


def test_turning_one_off_keeps_its_values_for_next_time(client, owner, dispensing):
    """Off hides the feature; it does not throw away the clinic's setup."""
    client.force_login(owner)
    client.post(
        reverse('organizations:feature_settings'),
        {'preparation_options': 'Globule\nLiquid'},
    )
    dispensing.refresh_from_db()
    assert dispensing.preparation_enabled is False
    assert dispensing.suggestions('preparation') == ['Globule', 'Liquid']
