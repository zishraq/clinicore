"""Batch allocation: first expiry out first, and expired stock never leaves.

Nobody picks a batch at the counter, so the allocation rule is the only thing
standing between a shelf of dated stock and a patient handed the wrong box.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.context import organization_context
from inventory import services
from inventory.models import MovementType, StockBatch

pytestmark = pytest.mark.django_db


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


@pytest.fixture
def three_batches(organization, branch, practitioner, product, receive):
    """Ten each, expiring soon, expiring later, and never."""
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'LATER',
                'expiry_date': _days(180),
            },
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'SOON',
                'expiry_date': _days(30),
            },
            {'product': product, 'quantity': Decimal('10'), 'lot_number': 'UNDATED'},
        ],
    )
    return product


def test_allocation_takes_the_earliest_expiry_first(
    organization, branch, practitioner, three_batches
):
    with organization_context(organization):
        allocation = services.allocate_fefo(
            organization, product=three_batches, branch=branch, quantity=Decimal('15')
        )
    assert [(batch.lot_number, taken) for batch, taken in allocation] == [
        ('SOON', Decimal('10.00')),
        ('LATER', Decimal('5.00')),
    ]


def test_undated_stock_is_drawn_on_last(
    organization, branch, practitioner, three_batches
):
    """A batch with no expiry can sit; a dated one cannot."""
    with organization_context(organization):
        allocation = services.allocate_fefo(
            organization, product=three_batches, branch=branch, quantity=Decimal('25')
        )
    assert [batch.lot_number for batch, _ in allocation] == ['SOON', 'LATER', 'UNDATED']


def test_consuming_writes_one_movement_per_batch_touched(
    organization, branch, practitioner, three_batches
):
    with organization_context(organization):
        movements = services.consume_stock(
            organization,
            product=three_batches,
            branch=branch,
            quantity=Decimal('15'),
            movement_type=MovementType.SALE,
            actor=practitioner,
        )
        assert [movement.quantity for movement in movements] == [
            Decimal('-10.00'),
            Decimal('-5.00'),
        ]
        assert services.on_hand(organization, product=three_batches) == Decimal('15.00')


def test_expired_stock_is_not_allocated(
    organization, branch, practitioner, product, receive
):
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'EXPIRED',
                'expiry_date': _days(-1),
            },
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'GOOD',
                'expiry_date': _days(90),
            },
        ],
    )
    with organization_context(organization):
        allocation = services.allocate_fefo(
            organization, product=product, branch=branch, quantity=Decimal('5')
        )
        assert [batch.lot_number for batch, _ in allocation] == ['GOOD']

        # It is still on the premises, and still has to be written off.
        assert services.on_hand(organization, product=product) == Decimal('15.00')
        assert services.on_hand(
            organization, product=product, usable_only=True
        ) == Decimal('5.00')


def test_selling_more_than_the_usable_stock_is_refused(
    organization, branch, practitioner, product, receive
):
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'EXPIRED',
                'expiry_date': _days(-1),
            },
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'GOOD',
                'expiry_date': _days(90),
            },
        ],
    )
    with organization_context(organization):
        with pytest.raises(services.InsufficientStock, match=r'5\.00'):
            services.consume_stock(
                organization,
                product=product,
                branch=branch,
                quantity=Decimal('6'),
                movement_type=MovementType.SALE,
                actor=practitioner,
            )
        # And the refusal left nothing behind.
        assert services.on_hand(organization, product=product) == Decimal('15.00')


def test_stock_is_counted_per_branch(
    organization, branch, practitioner, product, receive
):
    with organization_context(organization):
        from organizations.models import Branch

        second = Branch.objects.create(
            organization=organization, name='Second Chamber', code='TWO'
        )
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[{'product': product, 'quantity': Decimal('10')}],
    )
    with organization_context(organization):
        assert services.on_hand(organization, product=product, branch=second) == 0
        with pytest.raises(services.InsufficientStock):
            services.consume_stock(
                organization,
                product=product,
                branch=second,
                quantity=Decimal('1'),
                movement_type=MovementType.SALE,
                actor=practitioner,
            )


def test_a_reused_lot_number_lands_in_the_same_batch(
    organization, branch, practitioner, product, receive
):
    """Two deliveries of one lot are one batch; the ledger keeps them apart."""
    for _ in range(2):
        receive(
            organization,
            branch=branch,
            actor=practitioner,
            lines=[
                {
                    'product': product,
                    'quantity': Decimal('10'),
                    'lot_number': 'AB-1234',
                    'expiry_date': _days(365),
                }
            ],
        )
    with organization_context(organization):
        batch = StockBatch.objects.with_on_hand().get(lot_number='AB-1234')
        assert batch.on_hand == Decimal('20.00')
        assert batch.movements.count() == 2
