"""The per-organization advice capability (A3).

``Organization.advice_enabled`` is a column rather than a terminology key
because terminology names things that exist and this decides whether they exist
at all. Off means the feature is not offered anywhere; it does not mean advice
already recorded disappears. That distinction is what most of this file is
about — hiding a feature must never hide data.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.models import AdviceTemplate, Product
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
def advice_off(organization):
    organization.advice_enabled = False
    organization.save(update_fields=['advice_enabled', 'updated_at'])
    return organization


@pytest.fixture
def visit_with_advice(organization, branch, practitioner):
    """A visit recorded back when advice was still being prescribed."""
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
            status=EncounterStatus.FINALIZED,
            finalized_at=timezone.now(),
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter, issued_at=timezone.now()
        )
        advice = AdviceTemplate.objects.create(
            organization=organization, text='Walk 30 minutes daily.'
        )
        PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.ADVICE,
            advice_template=advice,
        )
        return encounter


def test_the_capability_ships_on(organization):
    """Default is on for the product; a seed is what turns it off."""
    assert organization.advice_enabled is True


def test_advice_is_dropped_from_the_autocomplete(client, practitioner, advice_off):
    with organization_context(advice_off):
        Product.objects.create(organization=advice_off, name='Amoxicillin')
        AdviceTemplate.objects.create(
            organization=advice_off, text='Avoid late meals always'
        )

    client.force_login(practitioner)
    response = client.get(reverse('catalog:suggestions'), {'q': 'a'})
    body = response.content.decode()
    assert 'Amoxicillin' in body
    assert 'Avoid late meals' not in body
    # And the offer to create more of it is gone too.
    assert 'as advice' not in body


def test_the_advice_quick_add_is_refused_by_direct_post(
    client, practitioner, advice_off
):
    """The button is not rendered, so this is somebody posting the URL."""
    client.force_login(practitioner)
    response = client.post(
        reverse('catalog:quick_add'),
        {'q': 'Sleep by 10pm', 'item_type': ItemType.ADVICE},
    )
    assert response.status_code == 403
    assert not AdviceTemplate.all_objects.filter(organization=advice_off).exists()


def test_medicines_still_quick_add_when_advice_is_off(client, practitioner, advice_off):
    client.force_login(practitioner)
    response = client.post(
        reverse('catalog:quick_add'),
        {'q': 'Novel syrup', 'item_type': ItemType.MEDICATION},
    )
    assert response.status_code == 200
    assert Product.all_objects.filter(organization=advice_off).count() == 1


def test_the_advice_catalog_link_is_hidden(client, practitioner, organization):
    url = reverse('clinical:encounter_list')
    advice_url = reverse('catalog:advice_list')

    client.force_login(practitioner)
    assert advice_url in client.get(url).content.decode()

    organization.advice_enabled = False
    organization.save(update_fields=['advice_enabled', 'updated_at'])
    assert advice_url not in client.get(url).content.decode()


def test_recorded_advice_still_shows_on_the_visit(
    client, practitioner, advice_off, visit_with_advice
):
    """The rule that stops this from being a data-hiding switch."""
    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:encounter_detail', args=[visit_with_advice.pk])
    )
    assert response.status_code == 200
    assert 'Walk 30 minutes daily.' in response.content.decode()


def test_recorded_advice_still_prints(
    client, practitioner, advice_off, visit_with_advice
):
    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:prescription_print', args=[visit_with_advice.pk])
    )
    assert response.status_code == 200
    assert 'Walk 30 minutes daily.' in response.content.decode()


def test_the_advice_rows_are_not_deleted(advice_off):
    """Another clinic will want this, and it is already built."""
    with organization_context(advice_off):
        AdviceTemplate.objects.create(organization=advice_off, text='Still here.')
        assert AdviceTemplate.objects.count() == 1


def test_the_owner_can_turn_advice_back_on(client, owner, advice_off):
    """A capability only reachable by shell is not a product feature."""
    url = reverse('organizations:feature_settings')
    client.force_login(owner)
    assert client.get(url).status_code == 200

    response = client.post(url, {'advice_enabled': 'on'})
    assert response.status_code == 302
    advice_off.refresh_from_db()
    assert advice_off.advice_enabled is True

    # And it takes effect: the catalog link is back.
    body = client.get(reverse('clinical:encounter_list')).content.decode()
    assert reverse('catalog:advice_list') in body


def test_the_owner_can_turn_advice_off(client, owner, organization):
    url = reverse('organizations:feature_settings')
    client.force_login(owner)
    # An unchecked checkbox posts nothing at all.
    assert client.post(url, {}).status_code == 302
    organization.refresh_from_db()
    assert organization.advice_enabled is False


def test_feature_settings_are_owner_only(client, practitioner, staff):
    url = reverse('organizations:feature_settings')
    for user in (practitioner, staff):
        client.force_login(user)
        assert client.get(url).status_code == 403
        assert client.post(url, {'advice_enabled': 'on'}).status_code == 403
