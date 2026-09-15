"""Which paper the printed prescription is offered on.

One setting with three values, defaulting to both so nothing changes for an
existing clinic. It governs what is *offered*; a visit's stored ``print_size``
is never rewritten, so offering both again restores every visit's own choice.
"""

import pytest
from django.urls import reverse

from clinical.models import PrintSize
from organizations.forms import FeatureSettingsForm
from organizations.models import PrescriptionSizes

pytestmark = pytest.mark.django_db


def test_a_new_clinic_offers_both_sizes(organization):
    assert organization.prescription_sizes == PrescriptionSizes.BOTH
    assert organization.print_sizes == ['A5', 'A4']


def test_the_single_size_values_are_spelled_like_the_prescription_field():
    """``print_size_for`` compares a stored ``Prescription.print_size`` against
    these values directly, so the two enums must agree letter for letter."""
    assert PrescriptionSizes.A5.value == PrintSize.A5.value
    assert PrescriptionSizes.A4.value == PrintSize.A4.value
    assert set(PrescriptionSizes.values) - {PrescriptionSizes.BOTH.value} == set(
        PrintSize.values
    )


@pytest.mark.parametrize(
    ('setting', 'requested', 'rendered'),
    [
        (PrescriptionSizes.BOTH, 'A4', 'A4'),
        (PrescriptionSizes.BOTH, 'A5', 'A5'),
        (PrescriptionSizes.BOTH, 'B5', 'A5'),
        (PrescriptionSizes.A5, 'A4', 'A5'),
        (PrescriptionSizes.A5, 'A5', 'A5'),
        (PrescriptionSizes.A4, 'A5', 'A4'),
        (PrescriptionSizes.A4, 'A4', 'A4'),
    ],
)
def test_a_size_that_is_not_offered_renders_at_one_that_is(
    organization, setting, requested, rendered
):
    organization.prescription_sizes = setting
    assert organization.print_size_for(requested) == rendered


def test_the_setting_is_on_the_features_form():
    assert 'prescription_sizes' in FeatureSettingsForm().fields


def test_the_owner_can_go_a5_only(client, organization, owner):
    client.force_login(owner)
    response = client.post(
        reverse('organizations:feature_settings'),
        {
            'billing_enabled': 'on',
            'advice_enabled': 'on',
            'prescription_sizes': PrescriptionSizes.A5,
        },
    )
    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.prescription_sizes == PrescriptionSizes.A5
    assert organization.print_sizes == ['A5']


def test_the_screen_offers_all_three(client, organization, owner):
    client.force_login(owner)
    body = client.get(reverse('organizations:feature_settings')).content.decode()
    assert 'name="prescription_sizes"' in body
    for choice in PrescriptionSizes:
        assert f'value="{choice.value}"' in body


def test_an_omitted_setting_keeps_the_one_the_clinic_already_had(
    client, organization, owner
):
    """The screen's other controls are checkboxes, and absence is how a
    checkbox says no — so a select that is absent must mean unchanged, never
    reset to the default (the temperature unit's rule)."""
    organization.prescription_sizes = PrescriptionSizes.A4
    organization.save(update_fields=['prescription_sizes', 'updated_at'])
    client.force_login(owner)

    response = client.post(
        reverse('organizations:feature_settings'),
        {'billing_enabled': 'on', 'advice_enabled': 'on'},
    )

    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.prescription_sizes == PrescriptionSizes.A4
