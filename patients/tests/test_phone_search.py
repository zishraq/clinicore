"""Searching by number and registering the miss must not name someone "01712345678".

The picker's one box takes a name or a number, and whichever was typed seeds the
registration form. It seeded ``full_name`` unconditionally, so every screen with
a picker — the visit form, the appointment modal, the walk-in — wrote phone
numbers into the name field. Fixed once, in the one view they all reach.
"""

import pytest
from django.urls import reverse

from core.context import organization_context
from patients.models import Patient
from patients.phone import dial_string, looks_like_phone

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    'typed',
    [
        '01712345678',
        '017 1234 5678',
        '+8801712345678',
        '(017) 1234-5678',
        '017.123.4567',
        '123456',
    ],
)
def test_a_typed_number_is_recognised(typed):
    assert looks_like_phone(typed)


@pytest.mark.parametrize(
    'typed',
    [
        'Rahima Begum',
        'Ward 7',
        # Short digit runs are more likely an age or a dose than a number.
        '12345',
        '7',
        '',
        '   ',
        # A name that happens to contain digits is still a name.
        'Room 101 Patient',
    ],
)
def test_anything_with_letters_or_too_few_digits_is_a_name(typed):
    assert not looks_like_phone(typed)


def test_dial_string_keeps_the_plus_and_drops_the_decoration():
    assert dial_string('+880 (17) 1234-5678') == '+8801712345678'


def test_a_patient_exposes_a_dial_string_for_the_tel_link(organization):
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization,
            code='P-0001',
            full_name='Rahima Begum',
            phone='017 1234-5678',
        )
    assert patient.dial == '01712345678'
    # What was typed is still what is displayed.
    assert patient.phone == '017 1234-5678'


def test_the_suggestions_offer_seeds_phone_for_a_number(client, staff, organization):
    client.force_login(staff)
    body = client.get(
        reverse('patients:suggestions'), {'q': '01712345678'}
    ).content.decode()

    assert '"phone": "01712345678"' in body
    assert 'full_name' not in body


def test_the_suggestions_offer_still_seeds_the_name_for_a_name(
    client, staff, organization
):
    client.force_login(staff)
    body = client.get(reverse('patients:suggestions'), {'q': 'Kamal'}).content.decode()

    assert '"full_name": "Kamal"' in body


def test_quick_create_puts_a_number_in_phone_and_leaves_the_name_empty(
    client, staff, organization
):
    client.force_login(staff)
    response = client.get(reverse('patients:quick_create'), {'phone': '01712345678'})

    form = response.context['form']
    assert form.initial['phone'] == '01712345678'
    assert form.initial['full_name'] == ''


def test_quick_create_corrects_a_number_sent_as_a_name(client, staff, organization):
    """The guard that makes this fix reach every caller.

    A template that was not updated, or a hand-built URL, still sends the typed
    text as ``full_name``. This view is the single path all of them take, so it
    re-checks rather than trusting the key it was given.
    """
    client.force_login(staff)
    response = client.get(
        reverse('patients:quick_create'), {'full_name': '01712345678'}
    )

    form = response.context['form']
    assert form.initial['phone'] == '01712345678'
    assert form.initial['full_name'] == ''


def test_quick_create_leaves_a_real_name_alone(client, staff, organization):
    client.force_login(staff)
    response = client.get(
        reverse('patients:quick_create'), {'full_name': 'Kamal Hossain'}
    )

    form = response.context['form']
    assert form.initial['full_name'] == 'Kamal Hossain'
    assert form.initial['phone'] == ''
