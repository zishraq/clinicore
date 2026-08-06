"""Catalog behaviour that a prescription depends on.

The theme is that a prescription is a record of what was handed to a patient on
a day, and the catalog is a live, editable list — so the two must not be
coupled at read time.
"""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from catalog import services
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


def test_name_snapshot_survives_a_catalog_rename(organization, prescription):
    """Renaming a medicine must not rewrite prescriptions already issued."""
    with organization_context(organization):
        product = Product.objects.create(
            organization=organization, name='Paracetamol 500mg'
        )
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.MEDICATION,
            product=product,
            dosage='1 tablet',
        )
        assert item.name_snapshot == 'Paracetamol 500mg'

        product.name = 'Paracetamol 650mg'
        product.save(update_fields=['name', 'updated_at'])

        item.refresh_from_db()
        assert item.name_snapshot == 'Paracetamol 500mg'
        # The live FK still resolves — it just is not what gets printed.
        assert item.product.name == 'Paracetamol 650mg'


def test_name_snapshot_survives_deactivation(organization, prescription):
    with organization_context(organization):
        advice = AdviceTemplate.objects.create(
            organization=organization, text='Walk 30 minutes daily.'
        )
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.ADVICE,
            advice_template=advice,
        )
        advice.is_active = False
        advice.save(update_fields=['is_active', 'updated_at'])

        item.refresh_from_db()
        assert item.name_snapshot == 'Walk 30 minutes daily.'


def test_advice_items_carry_no_dosage(organization, prescription):
    with organization_context(organization):
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.ADVICE,
            free_text_name='Avoid late meals',
            dosage='2 tablets',  # nonsense for advice; must be dropped
        )
        item.refresh_from_db()
        assert item.dosage is None


@pytest.mark.parametrize(
    ('label', 'kwargs'),
    [
        (
            'medication with both a product and a free text name',
            {'item_type': ItemType.MEDICATION, 'free_text_name': 'Something'},
        ),
        (
            'advice pointing at a product',
            {'item_type': ItemType.ADVICE},
        ),
        (
            'item with no source at all',
            {'item_type': ItemType.MEDICATION, 'free_text_name': ''},
        ),
    ],
)
def test_the_single_source_constraint_holds(organization, prescription, label, kwargs):
    """The database refuses an item whose source disagrees with its type."""
    with organization_context(organization):
        product = Product.objects.create(organization=organization, name='Amoxicillin')
        fields = {
            'organization': organization,
            'prescription': prescription,
            **kwargs,
        }
        if label != 'item with no source at all':
            fields['product'] = product

        with pytest.raises(IntegrityError), transaction.atomic():
            PrescriptionItem.objects.create(**fields)


def test_suggestions_only_return_the_active_organizations_entries(
    client, practitioner, organization, other_organization
):
    with organization_context(organization):
        Product.objects.create(organization=organization, name='Mine-a-cillin')
        AdviceTemplate.objects.create(organization=organization, text='Mine advice')
    with organization_context(other_organization):
        Product.objects.create(organization=other_organization, name='Theirs-a-cillin')
        AdviceTemplate.objects.create(
            organization=other_organization, text='Theirs advice'
        )

    client.force_login(practitioner)
    response = client.get(reverse('catalog:suggestions'), {'q': 'a'})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Mine-a-cillin' in body
    assert 'Mine advice' in body
    assert 'Theirs' not in body


def test_suggestions_exclude_deactivated_entries(client, practitioner, organization):
    with organization_context(organization):
        Product.objects.create(
            organization=organization, name='Retired tablet', is_active=False
        )
    client.force_login(practitioner)
    response = client.get(reverse('catalog:suggestions'), {'q': 'Retired'})
    assert b'Retired tablet' not in response.content


def test_quick_add_creates_in_the_active_organization(
    client, practitioner, organization, other_organization
):
    client.force_login(practitioner)
    response = client.post(
        reverse('catalog:quick_add'),
        {'q': 'Novel syrup', 'item_type': ItemType.MEDICATION},
    )
    assert response.status_code == 200
    assert b'Novel syrup' in response.content

    created = Product.all_objects.get(name='Novel syrup')
    assert created.organization_id == organization.pk
    assert created.created_by_id == practitioner.pk
    assert not Product.all_objects.filter(organization=other_organization).exists()


def test_quick_add_advice_creates_an_advice_template(
    client, practitioner, organization
):
    client.force_login(practitioner)
    client.post(
        reverse('catalog:quick_add'),
        {'q': 'Sleep by 10pm', 'item_type': ItemType.ADVICE},
    )
    created = AdviceTemplate.all_objects.get(text='Sleep by 10pm')
    assert created.organization_id == organization.pk


def test_quick_add_reuses_an_existing_entry(organization, practitioner):
    """Two practitioners typing the same thing must not fork the catalog."""
    with organization_context(organization):
        first = services.quick_add_product(
            organization, actor=practitioner, name='Cetirizine 10mg'
        )
        second = services.quick_add_product(
            organization, actor=practitioner, name='cetirizine 10MG'
        )
        assert first.pk == second.pk


def test_quick_add_converges_when_both_callers_miss_the_read(
    organization, practitioner, monkeypatch
):
    """The read is a fast path; the constraint is what decides (B8).

    Two practitioners quick-adding in the same instant both find nothing and
    both try to insert. Forcing the existence check to miss reproduces that
    deterministically — the loser must come back with the winner's row, not an
    IntegrityError and not a second copy.
    """
    from django.db.models import QuerySet

    with organization_context(organization):
        winner = services.quick_add_product(
            organization, actor=practitioner, name='Cetirizine 10mg'
        )
        monkeypatch.setattr(QuerySet, 'first', lambda self: None)
        loser = services.quick_add_product(
            organization, actor=practitioner, name='Cetirizine 10mg'
        )
        assert loser.pk == winner.pk
        assert Product.all_objects.filter(organization=organization).count() == 1


def _product_payload(**overrides) -> dict:
    payload = {
        'name': 'Paracetamol 500mg',
        'sku': '',
        'category': '',
        'unit': '',
        'sale_price': '12.00',
        'reorder_level': '0',
        'is_stock_tracked': '',
        'is_sellable': '',
        'is_active': 'on',
    }
    payload.update(overrides)
    return payload


def test_the_database_refuses_a_second_row_for_one_medicine(organization):
    """Case-insensitively, because that is the lookup quick-add matches on."""
    with organization_context(organization):
        Product.objects.create(organization=organization, name='Paracetamol 500mg')
        with pytest.raises(IntegrityError), transaction.atomic():
            Product.objects.create(organization=organization, name='paracetamol 500MG')


def test_the_database_refuses_a_second_copy_of_one_piece_of_advice(organization):
    with organization_context(organization):
        AdviceTemplate.objects.create(
            organization=organization, text='Walk 30 minutes daily.'
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            AdviceTemplate.objects.create(
                organization=organization, text='walk 30 MINUTES daily.'
            )


def test_the_same_medicine_name_is_free_in_another_clinic(
    organization, other_organization
):
    """The constraint names the organization; it is not a global namespace."""
    with organization_context(organization):
        Product.objects.create(organization=organization, name='Paracetamol 500mg')
    with organization_context(other_organization):
        Product.objects.create(
            organization=other_organization, name='paracetamol 500mg'
        )
        assert Product.objects.count() == 1


def test_a_duplicate_medicine_is_a_field_error_not_a_500(
    client, practitioner, organization
):
    """The constraint names the organization, which a ModelForm excludes from
    constraint validation — so without ``clean_name`` this arrives as a 500."""
    with organization_context(organization):
        Product.objects.create(organization=organization, name='Paracetamol 500mg')

    client.force_login(practitioner)
    response = client.post(
        reverse('catalog:product_create'), _product_payload(name='paracetamol 500mg')
    )
    assert response.status_code == 200
    assert 'name' in response.context['form'].errors
    with organization_context(organization):
        assert Product.objects.count() == 1


def test_a_duplicate_piece_of_advice_is_a_field_error_not_a_500(
    client, practitioner, organization
):
    with organization_context(organization):
        AdviceTemplate.objects.create(
            organization=organization, text='Walk 30 minutes daily.'
        )

    client.force_login(practitioner)
    response = client.post(
        reverse('catalog:advice_create'),
        {'text': 'walk 30 minutes daily.', 'category': 'EXERCISE', 'is_active': 'on'},
    )
    assert response.status_code == 200
    assert 'text' in response.context['form'].errors
    with organization_context(organization):
        assert AdviceTemplate.objects.count() == 1


def test_editing_a_medicine_does_not_collide_with_itself(
    client, practitioner, organization
):
    """The duplicate check excludes the row being edited."""
    with organization_context(organization):
        product = Product.objects.create(
            organization=organization, name='Paracetamol 500mg'
        )

    client.force_login(practitioner)
    response = client.post(
        reverse('catalog:product_update', args=[product.pk]),
        _product_payload(name='Paracetamol 500mg', unit='Tablet'),
    )
    assert response.status_code == 302
    product.refresh_from_db()
    assert product.unit == 'Tablet'


def test_staff_cannot_reach_the_catalog(client, staff):
    client.force_login(staff)
    for url in [
        reverse('catalog:suggestions'),
        reverse('catalog:product_list'),
        reverse('catalog:advice_list'),
    ]:
        assert client.get(url).status_code == 403
    assert client.post(reverse('catalog:quick_add'), {'q': 'X'}).status_code == 403
