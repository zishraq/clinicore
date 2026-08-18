"""The per-organization strength capability (docs/adr/0015-prescribed-strength.md).

``Organization.strength_enabled`` is the second capability column, and it
behaves like ``advice_enabled``: off means the field is not offered anywhere,
never that strengths already recorded disappear. The read surfaces gate on the
data instead of on the switch, which is what most of this file is about.

The column is ``strength`` and the clinic that prescribes potencies calls it
"Potency" through the terminology map — so the tests use a non-default label
throughout, the way the timezone tests use a non-UTC zone. A test run entirely
under the default label would pass with the label hardcoded.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
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
def homeopathy(organization):
    """A clinic that records potencies, with the clinic's own word for them."""
    organization.strength_enabled = True
    organization.strength_options = ['Q', '6C', '30C', '200C', '1M']
    organization.terminology = {**organization.terminology, 'strength': 'Potency'}
    organization.save(
        update_fields=[
            'strength_enabled',
            'strength_options',
            'terminology',
            'updated_at',
        ]
    )
    return organization


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


@pytest.fixture
def visit_with_potency(organization, branch, practitioner, patient):
    """A visit written while the clinic was still recording potencies."""
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
            strength='200C',
            dosage='4 pills',
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
        'items-0-dosage': '4 pills',
        'items-0-sort_order': '0',
    }
    payload.update(overrides)
    return payload


# --- the capability itself -------------------------------------------------


def test_the_capability_ships_off(organization):
    """The opposite default to advice: most clinics put the strength in the name."""
    assert organization.strength_enabled is False
    assert organization.strengths == []
    # And the schema stays specialty-neutral until a clinic says otherwise.
    assert organization.terms['strength'] == 'Strength'


def test_the_clinics_own_word_reaches_the_prescription_row(
    client, practitioner, homeopathy, patient, branch
):
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    assert 'Potency' in body
    assert 'name="items-0-strength"' in body
    # The suggestions are offered as a native datalist, so an unusual potency
    # can still be typed; there is no "Other…" option to explain.
    assert '<datalist id="strength-options">' in body
    assert '<option value="200C">' in body


def test_the_field_is_absent_when_the_capability_is_off(
    client, practitioner, organization, patient, branch
):
    client.force_login(practitioner)
    body = client.get(reverse('clinical:encounter_create')).content.decode()
    assert 'name="items-0-strength"' not in body
    assert '<datalist id="strength-options">' not in body


def test_a_posted_strength_is_ignored_when_the_capability_is_off(
    client, practitioner, organization, patient, branch
):
    """The field is dropped from the form, not merely left out of the template.

    A field that is only hidden in markup is still settable by anyone who can
    build a POST — which is everyone.
    """
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'), _payload(patient, branch, practitioner)
    )
    with organization_context(organization):
        assert PrescriptionItem.objects.get().strength == ''


def test_a_potency_is_recorded_when_the_capability_is_on(
    client, practitioner, homeopathy, patient, branch
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner),
        follow=True,
    )
    assert response.status_code == 200
    with organization_context(homeopathy):
        item = PrescriptionItem.objects.get()
        assert item.strength == '200C'
        # The two facts that were sharing one column are now apart.
        assert item.dosage == '4 pills'


def test_an_unusual_potency_is_not_blocked(
    client, practitioner, homeopathy, patient, branch
):
    """The list guides; it never constrains. LM potencies are not on it."""
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, **{'items-0-strength': 'LM3'}),
    )
    with organization_context(homeopathy):
        assert PrescriptionItem.objects.get().strength == 'LM3'


# --- the catalog default ---------------------------------------------------


def test_the_catalog_default_rides_along_with_the_suggestion(
    client, practitioner, homeopathy
):
    """Selecting a remedy prefills its usual potency (the JS reads this)."""
    with organization_context(homeopathy):
        Product.objects.create(
            organization=homeopathy, name='Arsenicum album', default_strength='200C'
        )
    client.force_login(practitioner)
    body = client.get(reverse('catalog:suggestions'), {'q': 'ars'}).content.decode()
    assert 'data-strength="200C"' in body


def test_the_product_form_drops_the_default_when_the_capability_is_off(
    client, practitioner, organization
):
    client.force_login(practitioner)
    body = client.get(reverse('catalog:product_create')).content.decode()
    assert 'name="default_strength"' not in body


def test_the_product_form_offers_the_default_when_it_is_on(
    client, practitioner, homeopathy
):
    client.force_login(practitioner)
    body = client.get(reverse('catalog:product_create')).content.decode()
    assert 'name="default_strength"' in body
    assert 'Usual potency' in body


# --- advice has no strength ------------------------------------------------


def test_advice_cannot_carry_a_strength(organization, branch, practitioner, patient):
    """Advice is not a substance, so the strength is blanked like the dosage."""
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
        )
        item.refresh_from_db()
        assert item.strength == ''
        assert item.dosage is None


# --- turning it off must not hide what was recorded ------------------------


def test_a_recorded_potency_still_shows_on_the_visit(
    client, practitioner, organization, visit_with_potency
):
    """The switch is off here: the read surface gates on the data, not the flag."""
    assert organization.strength_enabled is False
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:encounter_detail', args=[visit_with_potency.pk])
    ).content.decode()
    assert '200C' in body


def test_a_recorded_potency_still_prints(
    client, practitioner, organization, visit_with_potency
):
    """Reprinting a visit must reproduce what the patient was handed."""
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[visit_with_potency.pk])
    ).content.decode()
    assert '200C' in body


def test_the_column_stays_away_when_nothing_recorded_one(
    client, practitioner, homeopathy, branch, patient
):
    """On, but this visit has no potencies: no empty column on the printout."""
    with organization_context(homeopathy):
        encounter = Encounter.objects.create(
            organization=homeopathy,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            status=EncounterStatus.FINALIZED,
            finalized_at=timezone.now(),
        )
        prescription = Prescription.objects.create(
            organization=homeopathy, encounter=encounter, issued_at=timezone.now()
        )
        PrescriptionItem.objects.create(
            organization=homeopathy,
            prescription=prescription,
            item_type=ItemType.MEDICATION,
            free_text_name='Calendula ointment',
        )
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[encounter.pk])
    ).content.decode()
    assert '<th>Potency</th>' not in body


def test_editing_a_visit_with_the_capability_off_keeps_the_potency(
    client, practitioner, organization, visit_with_potency, patient, branch
):
    """The reason the field is popped rather than hidden.

    A hidden-but-present field is rebuilt as empty by ``construct_instance`` on
    every later save, so turning the capability off would quietly erase what was
    already recorded the next time anyone touched the visit.
    """
    with organization_context(organization):
        item = PrescriptionItem.objects.get()
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_update', args=[visit_with_potency.pk]),
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
                'items-0-dosage': '2 pills',
            },
        ),
    )
    item.refresh_from_db()
    assert item.dosage == '2 pills'
    assert item.strength == '200C'


# --- the owner can set all of it without a developer -----------------------


def test_the_owner_turns_it_on_names_it_and_lists_the_values(
    client, owner, organization
):
    url = reverse('organizations:feature_settings')
    client.force_login(owner)
    response = client.post(
        url,
        {
            'advice_enabled': 'on',
            'strength_enabled': 'on',
            'strength_label': 'Potency',
            'strength_options': '30C\n200C\n1M\n',
        },
    )
    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.strength_enabled is True
    assert organization.strengths == ['30C', '200C', '1M']
    assert organization.terms['strength'] == 'Potency'


def test_a_blank_label_clears_the_override(client, owner, homeopathy):
    url = reverse('organizations:feature_settings')
    client.force_login(owner)
    client.post(url, {'strength_enabled': 'on', 'strength_label': ''})
    homeopathy.refresh_from_db()
    assert homeopathy.terms['strength'] == 'Strength'
    # Cleared, not stored blank: an empty override is a dead key in the JSON.
    assert 'strength' not in homeopathy.terminology


def test_the_settings_screen_shows_what_is_already_set(client, owner, homeopathy):
    client.force_login(owner)
    body = client.get(reverse('organizations:feature_settings')).content.decode()
    assert 'value="Potency"' in body
    # One per line, in the clinic's own order rather than alphabetically.
    assert 'Q\n6C\n30C\n200C\n1M' in body


def test_turning_it_off_keeps_the_values_for_next_time(client, owner, homeopathy):
    """Off hides the feature; it does not throw away the clinic's setup."""
    client.force_login(owner)
    client.post(
        reverse('organizations:feature_settings'),
        {'strength_label': 'Potency', 'strength_options': 'Q\n6C\n30C\n200C\n1M'},
    )
    homeopathy.refresh_from_db()
    assert homeopathy.strength_enabled is False
    assert homeopathy.strengths == ['Q', '6C', '30C', '200C', '1M']


# --- the stored list is cleaned on the way out -----------------------------


@pytest.mark.parametrize(
    ('stored', 'expected'),
    [
        (['30C', '', '  ', '200C'], ['30C', '200C']),
        (['30C', '30c', '30C'], ['30C']),
        ([' 30C ', '200C'], ['30C', '200C']),
        (['1M', 'Q', '6C'], ['1M', 'Q', '6C']),
        (None, []),
        (['x' * 60], ['x' * 40]),
    ],
)
def test_the_suggested_values_are_cleaned(organization, stored, expected):
    """The column is org-editable JSON, so a datalist must survive anything in it."""
    organization.strength_options = stored
    assert organization.strengths == expected
