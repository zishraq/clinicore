"""The prescription letterhead's organization-level half.

Everything the clinic's supplied design puts on paper that is not a visit, a
chamber or a practitioner: the notice, the contact strip, the watermark and the
colour the whole sheet is drawn in. None of it is in the code — onboarding a
second clinic must not mean touching a template (SPEC §6.8).
"""

import pytest
from django.urls import reverse

from organizations.models import WATERMARK_MAX_LENGTH, Organization

pytestmark = pytest.mark.django_db

URL = '/settings/prescription/'


def _post(client, organization, **overrides):
    data = {
        'prescription_notice': 'Bring this sheet to your next visit.',
        'prescription_contacts': '',
        'watermark_text': '',
        'primary_color': organization.primary_color,
    }
    data.update(overrides)
    return client.post(reverse('organizations:prescription_settings'), data)


def test_the_contact_strip_is_one_line_per_contact(client, owner, organization):
    client.force_login(owner)
    response = _post(
        client,
        organization,
        prescription_contacts=(
            'Facebook: /DrRafiqulIslam\n'
            '\n'
            '  YouTube: Dr. Rafiqul Islam  \n'
            'facebook: /drrafiqulislam\n'
        ),
    )
    assert response.status_code == 302

    organization.refresh_from_db()
    # Blanks dropped, whitespace trimmed, a case-only repeat dropped, order kept
    # — the same rule the suggestion lists are cleaned by.
    assert organization.contacts == [
        'Facebook: /DrRafiqulIslam',
        'YouTube: Dr. Rafiqul Islam',
    ]


def test_the_saved_contacts_come_back_into_the_textarea(client, owner, organization):
    client.force_login(owner)
    _post(client, organization, prescription_contacts='Clinic: /GlobalHomeopathyClinic')

    response = client.get(reverse('organizations:prescription_settings'))
    assert 'Clinic: /GlobalHomeopathyClinic' in response.content.decode()


def test_the_watermark_is_stored_in_branding_and_capped(client, owner, organization):
    client.force_login(owner)
    _post(client, organization, watermark_text='GHC')

    organization.refresh_from_db()
    # Reuses the branding key that already existed and had never been read.
    assert organization.branding['logo_text'] == 'GHC'
    assert organization.watermark_text == 'GHC'

    # It renders very large inside a fixed circle, so the field refuses a name.
    response = _post(client, organization, watermark_text='X' * 40)
    assert response.status_code == 200
    assert response.context['form'].errors['watermark_text']

    organization.refresh_from_db()
    assert len(organization.watermark_text) <= WATERMARK_MAX_LENGTH


def test_the_brand_colour_is_written_into_the_palette(client, owner, organization):
    client.force_login(owner)
    _post(client, organization, primary_color='#336699')

    organization.refresh_from_db()
    assert organization.primary_color == '#336699'
    # The rest of the seed palette survives being written through.
    assert organization.palette['success']


def test_a_colour_that_is_not_hex_is_refused_rather_than_ignored(
    client, owner, organization
):
    """This value is interpolated into a <style> block, where escaping does not
    protect you. ``hex_color_or`` would silently fall back; a settings screen
    that silently ignores what was typed reads as broken."""
    client.force_login(owner)
    response = _post(client, organization, primary_color='red; } body { display:none')
    assert response.status_code == 200
    assert response.context['form'].errors['primary_color']

    organization.refresh_from_db()
    assert organization.primary_color == Organization().primary_color


def test_staff_cannot_reach_the_screen(client, staff):
    client.force_login(staff)
    assert client.get(reverse('organizations:prescription_settings')).status_code == 403


def test_a_practitioner_cannot_reach_the_screen(client, practitioner):
    """Letterhead is an administrator's job; a practitioner edits only their own
    half of it, on their account page."""
    client.force_login(practitioner)
    assert client.get(reverse('organizations:prescription_settings')).status_code == 403
