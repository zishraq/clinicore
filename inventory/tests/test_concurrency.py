"""Two people selling the last box at once.

FEFO allocation reads the shelf and then writes against it, which is a
read-modify-write and therefore a race. The batch rows are locked for the
duration, so this is the test that the lock is actually doing something.
"""

import threading
from decimal import Decimal

import pytest
from django.db import connection

from core.context import organization_context
from inventory import services
from inventory.models import MovementType

pytestmark = pytest.mark.django_db

#: Five sellers, three units each, against a shelf holding ten.
WORKERS = 5
EACH = Decimal('3')
STOCKED = Decimal('10')


@pytest.mark.django_db(transaction=True)
def test_concurrent_sales_cannot_take_stock_below_zero(
    organization, branch, practitioner, product, receive
):
    """SQLite has no ``SELECT … FOR UPDATE``, so it would test the wrong thing.

    CI runs against Postgres (SPEC §9); locally, use the compose database.
    """
    if connection.vendor != 'postgresql':
        pytest.skip('Row-level locking is a Postgres guarantee; SQLite has none.')

    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[{'product': product, 'quantity': STOCKED}],
    )

    sold: list[Decimal] = []
    refused: list[BaseException] = []
    unexpected: list[BaseException] = []
    start = threading.Barrier(WORKERS)

    def worker():
        try:
            # Every thread lines up first, so the allocations actually overlap
            # rather than politely following one another.
            start.wait(timeout=10)
            with organization_context(organization):
                services.consume_stock(
                    organization,
                    product=product,
                    branch=branch,
                    quantity=EACH,
                    movement_type=MovementType.SALE,
                    actor=practitioner,
                )
            sold.append(EACH)
        except services.InsufficientStock as error:
            refused.append(error)
        except BaseException as error:  # reported after the join below
            unexpected.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not unexpected, f'concurrent sale raised: {unexpected}'
    # Ten units, three at a time: three sales fit and the rest are refused.
    assert len(sold) == 3
    assert len(refused) == WORKERS - 3

    with organization_context(organization):
        remaining = services.on_hand(organization, product=product)
    assert remaining == Decimal('1.00')
