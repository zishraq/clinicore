"""The ledger itself: what it counts, and what it refuses.

On-hand is a sum of movements, so the tests that matter are the ones proving
nothing can quietly change a movement after it has been counted.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from core.context import organization_context
from inventory import services
from inventory.models import (
    LedgerIsAppendOnly,
    MovementType,
    StockBatch,
    StockMovement,
)

pytestmark = pytest.mark.django_db


def _batch(organization, product):
    with organization_context(organization):
        return StockBatch.objects.get(product=product)


def test_receiving_stock_creates_a_batch_a_line_and_a_movement(
    organization, branch, practitioner, product, receive
):
    receipt = receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('20'),
                'cost_price': Decimal('1.20'),
            }
        ],
        supplier='Ashraf Traders',
    )
    with organization_context(organization):
        assert receipt.items.count() == 1
        batch = StockBatch.objects.with_on_hand().get(product=product)
        assert batch.on_hand == Decimal('20.00')
        movement = StockMovement.objects.get(batch=batch)
        assert movement.movement_type == MovementType.PURCHASE
        assert movement.quantity == Decimal('20.00')
        # The movement points back at the line that caused it.
        assert movement.goods_receipt_item.receipt == receipt


def test_on_hand_is_the_sum_of_the_ledger_not_a_column(
    organization, branch, practitioner, stocked
):
    assert not hasattr(StockBatch, 'quantity')

    with organization_context(organization):
        services.consume_stock(
            organization,
            product=stocked,
            branch=branch,
            quantity=Decimal('7'),
            movement_type=MovementType.SALE,
            actor=practitioner,
        )
        assert services.on_hand(organization, product=stocked) == Decimal('13.00')

        # And again, through the annotation the list pages use.
        batch = StockBatch.objects.with_on_hand().get(product=stocked)
        assert batch.on_hand == Decimal('13.00')


def test_goods_receipt_numbers_run_without_gaps(
    organization, branch, practitioner, product, receive
):
    numbers = [
        receive(
            organization,
            branch=branch,
            actor=practitioner,
            lines=[{'product': product, 'quantity': Decimal('5')}],
        ).number
        for _ in range(3)
    ]
    assert [number.rsplit('-', 1)[1] for number in numbers] == ['0001', '0002', '0003']


def test_a_recorded_movement_cannot_be_edited_or_deleted(
    organization, branch, practitioner, stocked
):
    """The whole point of a ledger. A mistake is another movement, not an UPDATE."""
    with organization_context(organization):
        movement = StockMovement.objects.get(batch__product=stocked)

        movement.quantity = Decimal('999')
        with pytest.raises(LedgerIsAppendOnly):
            movement.save()

        with pytest.raises(LedgerIsAppendOnly):
            movement.delete()

        # Nothing landed.
        assert services.on_hand(organization, product=stocked) == Decimal('20.00')


def test_a_correction_is_posted_as_an_adjustment(
    organization, branch, practitioner, stocked
):
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
        services.record_movement(
            organization,
            batch=batch,
            movement_type=MovementType.ADJUSTMENT,
            quantity=Decimal('-3'),
            actor=practitioner,
            reason='Stock count: three fewer on the shelf.',
        )
        assert services.on_hand(organization, product=stocked) == Decimal('17.00')


@pytest.mark.parametrize(
    'movement_type', [MovementType.ADJUSTMENT, MovementType.WASTAGE]
)
def test_adjustments_and_wastage_need_a_reason(
    organization, branch, practitioner, stocked, movement_type
):
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
        with pytest.raises(services.InventoryError, match='reason'):
            services.record_movement(
                organization,
                batch=batch,
                movement_type=movement_type,
                quantity=Decimal('-1'),
                actor=practitioner,
            )


def test_the_service_refuses_a_movement_pointing_the_wrong_way(
    organization, branch, practitioner, stocked
):
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
        with pytest.raises(services.InventoryError):
            services.record_movement(
                organization,
                batch=batch,
                movement_type=MovementType.PURCHASE,
                quantity=Decimal('-5'),
                actor=practitioner,
            )


def test_the_database_refuses_a_movement_pointing_the_wrong_way(
    organization, branch, practitioner, stocked
):
    """Belt and braces: the constraint holds even if the service is bypassed."""
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
        with pytest.raises(IntegrityError), transaction.atomic():
            StockMovement.objects.create(
                organization=organization,
                batch=batch,
                movement_type=MovementType.SALE,
                quantity=Decimal('5'),
            )


def test_the_database_refuses_a_source_document_of_the_wrong_kind(
    organization, branch, practitioner, stocked
):
    with organization_context(organization):
        batch = StockBatch.objects.get(product=stocked)
        receipt_item = StockMovement.objects.get(batch=batch).goods_receipt_item
        with pytest.raises(IntegrityError), transaction.atomic():
            # A sale citing a goods receipt line is nonsense, and is rejected
            # by the database rather than left to the caller to notice.
            StockMovement.objects.create(
                organization=organization,
                batch=batch,
                movement_type=MovementType.SALE,
                quantity=Decimal('-1'),
                goods_receipt_item=receipt_item,
            )


def test_stock_does_not_leak_between_organizations(
    organization, other_organization, branch, practitioner, stocked
):
    with organization_context(other_organization):
        assert StockBatch.objects.count() == 0
        assert StockMovement.objects.count() == 0
