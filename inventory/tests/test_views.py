"""The stock screens, through the views.

Permission boundaries first — stock is a PRACTITIONER/OWNER surface like
billing — then the two flows that write to the ledger.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from core.context import organization_context
from inventory import services
from inventory.models import GoodsReceipt, MovementType, StockBatch

pytestmark = pytest.mark.django_db


def _receipt_payload(product, **overrides) -> dict:
    payload = {
        'branch': '',
        'supplier': 'Ashraf Traders',
        'reference': 'AT-9912',
        'received_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        'notes': '',
        'items-TOTAL_FORMS': '1',
        'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        'items-0-product': product.pk,
        'items-0-lot_number': 'AB-1234',
        'items-0-expiry_date': (timezone.localdate() + timedelta(days=365)).isoformat(),
        'items-0-quantity': '20',
        'items-0-cost_price': '1.20',
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    'view_name',
    ['inventory:stock_list', 'inventory:receipt_list', 'inventory:receipt_create'],
)
def test_staff_are_refused_the_stock_screens(client, staff, view_name):
    """Hiding a nav link is presentation; the 403 is the access control."""
    client.force_login(staff)
    assert client.get(reverse(view_name)).status_code == 403


def test_a_practitioner_sees_the_stock_list(
    client, practitioner, organization, stocked
):
    client.force_login(practitioner)
    response = client.get(reverse('inventory:stock_list'))
    assert response.status_code == 200
    products = list(response.context['products'])
    assert [product.annotated_on_hand for product in products] == [Decimal('20.00')]


def test_receiving_a_delivery_creates_the_batch_and_the_movement(
    client, practitioner, organization, branch, product
):
    client.force_login(practitioner)
    response = client.post(
        reverse('inventory:receipt_create'),
        _receipt_payload(product, branch=branch.pk),
        follow=True,
    )
    assert response.status_code == 200

    with organization_context(organization):
        receipt = GoodsReceipt.objects.get()
        assert receipt.number.startswith('GRN-')
        assert receipt.supplier == 'Ashraf Traders'
        batch = StockBatch.objects.with_on_hand().get(lot_number='AB-1234')
        assert batch.on_hand == Decimal('20.00')
        assert batch.movements.get().movement_type == MovementType.PURCHASE


def test_a_tracked_product_without_an_expiry_is_refused(
    client, practitioner, organization, branch, product
):
    """Otherwise the expired-stock block silently stops applying to it."""
    client.force_login(practitioner)
    response = client.post(
        reverse('inventory:receipt_create'),
        _receipt_payload(product, branch=branch.pk, **{'items-0-expiry_date': ''}),
    )
    assert response.status_code == 200
    assert 'needs an expiry date' in response.content.decode()
    with organization_context(organization):
        assert not GoodsReceipt.objects.exists()


def test_the_product_page_shows_batches_and_history(
    client, practitioner, organization, stocked
):
    client.force_login(practitioner)
    response = client.get(reverse('inventory:product_stock', args=[stocked.pk]))
    assert response.status_code == 200
    assert response.context['on_hand'] == Decimal('20.00')
    assert len(response.context['batches']) == 1
    assert len(list(response.context['movements'])) == 1


def test_an_adjustment_needs_a_reason_and_lands_on_the_ledger(
    client, practitioner, organization, stocked
):
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
    url = reverse('inventory:adjustment_create', args=[batch.pk])
    client.force_login(practitioner)

    # No reason: refused by the form, nothing posted.
    response = client.post(url, {'quantity': '-3', 'reason': ''})
    assert response.status_code == 200
    with organization_context(organization):
        assert services.on_hand(organization, product=stocked) == Decimal('20.00')

    client.post(
        url, {'quantity': '-3', 'reason': 'Counted short at close.'}, follow=True
    )
    with organization_context(organization):
        assert services.on_hand(organization, product=stocked) == Decimal('17.00')
        movement = batch.movements.filter(movement_type=MovementType.ADJUSTMENT).get()
        assert movement.reason == 'Counted short at close.'
        assert movement.created_by == practitioner


def test_an_adjustment_cannot_take_stock_negative(
    client, practitioner, organization, stocked
):
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
    client.force_login(practitioner)
    response = client.post(
        reverse('inventory:adjustment_create', args=[batch.pk]),
        {'quantity': '-50', 'reason': 'Typo.'},
        follow=True,
    )
    assert response.status_code == 200
    with organization_context(organization):
        assert services.on_hand(organization, product=stocked) == Decimal('20.00')
