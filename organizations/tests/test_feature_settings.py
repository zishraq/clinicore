"""The Features screen, and the one control on it that is not a switch.

``temperature_unit`` is settable by the owner for the same reason every other
capability on this screen is: a setting only a developer can reach is not a
product feature (A3, ADR 0015).
"""

import pytest
from django.urls import reverse

from core.temperature import TemperatureUnit
from organizations.forms import FeatureSettingsForm

pytestmark = pytest.mark.django_db


def test_a_new_clinic_works_in_fahrenheit(organization):
    assert organization.temperature_unit == TemperatureUnit.FAHRENHEIT
    assert organization.temperature_symbol == '°F'


def test_the_unit_is_on_the_features_form():
    assert 'temperature_unit' in FeatureSettingsForm().fields


def test_the_owner_can_change_it(client, organization, owner):
    client.force_login(owner)

    response = client.post(
        reverse('organizations:feature_settings'),
        {
            'billing_enabled': 'on',
            'advice_enabled': 'on',
            'temperature_unit': TemperatureUnit.CELSIUS,
        },
    )

    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.temperature_unit == TemperatureUnit.CELSIUS
    assert organization.temperature_symbol == '°C'


def test_the_screen_offers_both_units(client, organization, owner):
    client.force_login(owner)

    body = client.get(reverse('organizations:feature_settings')).content.decode()

    assert 'name="temperature_unit"' in body
    for unit in TemperatureUnit:
        assert f'value="{unit.value}"' in body


def test_staff_cannot_reach_the_screen(client, organization, staff):
    client.force_login(staff)

    assert client.get(reverse('organizations:feature_settings')).status_code == 403


def test_an_omitted_unit_keeps_the_one_the_clinic_already_had(
    client, organization, owner
):
    """The field is optional, so the assertion above is what protects it.

    Every other control on this screen is a checkbox, and an unticked checkbox
    posts nothing — absence is how the screen says "no". Making the one select
    required would turn a missing key into a refusal to save the switches the
    owner *did* toggle, so absence keeps the stored unit instead. What must not
    happen is a silent reset to the default.
    """
    organization.temperature_unit = TemperatureUnit.CELSIUS
    organization.save(update_fields=['temperature_unit', 'updated_at'])
    client.force_login(owner)

    response = client.post(
        reverse('organizations:feature_settings'), {'advice_enabled': 'on'}
    )

    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.temperature_unit == TemperatureUnit.CELSIUS
