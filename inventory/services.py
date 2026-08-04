"""Stock operations. Every function takes ``organization`` explicitly.

The invariants live here rather than in the views, because there is more than
one way into each of them (a form, a management command, the billing hook):

* stock leaves by first-expiry-first-out, and expired batches never leave at all;
* stock cannot go below zero — a sale that outruns the shelf is refused with
  the figure that is actually there;
* nothing is edited or deleted, only posted. Corrections are movements too.

Sign convention, because it is the one thing easy to get wrong here:
``record_movement`` takes a **signed** quantity and checks it against the type,
mirroring the column. ``consume_stock`` takes a **positive** magnitude and
negates it, because an outflow has only one possible direction.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from catalog.models import Product
from core.services import current_period, next_document_number
from inventory.models import (
    DECREASING_TYPES,
    INCREASING_TYPES,
    REASON_REQUIRED_TYPES,
    ZERO,
    GoodsReceipt,
    GoodsReceiptItem,
    MovementType,
    StockBatch,
    StockMovement,
)

__all__ = [
    'InsufficientStock',
    'InventoryError',
    'allocate_fefo',
    'batches_for',
    'consume_stock',
    'movement_history',
    'on_hand',
    'receive_stock',
    'record_adjustment',
    'record_movement',
    'stock_levels',
]

#: ``DocumentSequence.kind`` for the goods receipt run.
RECEIPT_SEQUENCE = 'GOODS_RECEIPT'
RECEIPT_PREFIX = 'GRN'

#: Quantities carry two places, like the invoice line they often come from.
_QUANTITY_EXPONENT = Decimal('0.01')

#: Output field for the ledger sums annotated onto catalog rows.
_SUM_FIELD = DecimalField(max_digits=14, decimal_places=2)


class InventoryError(ValueError):
    """Base for the refusals a caller is expected to show to a user."""


class InsufficientStock(InventoryError):
    """Raised when an outflow is larger than what the branch actually holds."""


def _to_quantity(value) -> Decimal:
    """Quantize to the stock scale. Quantities are not money, but they round."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_QUANTITY_EXPONENT, rounding=ROUND_HALF_UP)


def _next_receipt_number(organization) -> str:
    """Next gap-free number for this organization's goods receipt run."""
    return next_document_number(
        organization,
        kind=RECEIPT_SEQUENCE,
        prefix=RECEIPT_PREFIX,
        period=current_period(),
    )


def record_movement(
    organization,
    *,
    batch: StockBatch,
    movement_type: str,
    quantity,
    actor,
    reason: str = '',
    occurred_at=None,
    goods_receipt_item=None,
    invoice_item=None,
    prescription_item=None,
) -> StockMovement:
    """Post one line to the ledger. ``quantity`` is signed.

    The sign and the reason are checked here as well as by the database, so a
    caller gets a sentence it can show a user rather than an IntegrityError.
    """
    quantity = _to_quantity(quantity)
    reason = (reason or '').strip()

    if quantity == ZERO:
        raise InventoryError('A stock movement of zero changes nothing.')
    if movement_type in INCREASING_TYPES and quantity < ZERO:
        raise InventoryError(
            f'A {movement_type.lower()} must add stock, not remove it.'
        )
    if movement_type in DECREASING_TYPES and quantity > ZERO:
        raise InventoryError(
            f'A {movement_type.lower()} must remove stock, not add it.'
        )
    if movement_type in REASON_REQUIRED_TYPES and not reason:
        raise InventoryError(f'A {movement_type.lower()} needs a reason.')

    return StockMovement.objects.create(
        organization=organization,
        created_by=actor,
        batch=batch,
        movement_type=movement_type,
        quantity=quantity,
        reason=reason[:300],
        occurred_at=occurred_at or timezone.now(),
        goods_receipt_item=goods_receipt_item,
        invoice_item=invoice_item,
        prescription_item=prescription_item,
    )


def _batch_for_line(organization, *, branch, actor, line) -> StockBatch:
    """The batch a receipt line lands in, reusing a lot already on the shelf.

    An unnumbered line always opens a new batch: without a lot number there is
    nothing to say two deliveries are the same stock, and they may well differ
    in expiry and cost.
    """
    lot_number = (line.get('lot_number') or '').strip()
    defaults = {
        'organization': organization,
        'created_by': actor,
        'product': line['product'],
        'branch': branch,
        'lot_number': lot_number,
        'expiry_date': line.get('expiry_date'),
        'cost_price': line.get('cost_price') or ZERO,
    }
    if not lot_number:
        return StockBatch.objects.create(**defaults)
    batch, _ = StockBatch.objects.get_or_create(
        organization=organization,
        product=line['product'],
        branch=branch,
        lot_number=lot_number,
        defaults=defaults,
    )
    return batch


@transaction.atomic
def receive_stock(
    organization,
    *,
    branch,
    actor,
    lines,
    supplier: str = '',
    reference: str = '',
    received_at=None,
    notes: str = '',
) -> GoodsReceipt:
    """Book a delivery in: a numbered receipt, its batches, and its movements.

    ``lines`` is an iterable of dicts with ``product`` and ``quantity``, and
    optionally ``cost_price``, ``lot_number`` and ``expiry_date``.

    The number is allocated inside this transaction on purpose: the counter row
    stays locked until it commits, so a rollback returns the number to the run
    instead of leaving a hole in it.
    """
    lines = list(lines)
    if not lines:
        raise InventoryError('A goods receipt needs at least one line.')

    receipt = GoodsReceipt.objects.create(
        organization=organization,
        created_by=actor,
        branch=branch,
        number=_next_receipt_number(organization),
        supplier=supplier,
        reference=reference,
        received_at=received_at or timezone.now(),
        notes=notes,
    )
    for index, line in enumerate(lines):
        quantity = _to_quantity(line['quantity'])
        if quantity <= ZERO:
            raise InventoryError('A received quantity must be greater than zero.')
        batch = _batch_for_line(organization, branch=branch, actor=actor, line=line)
        item = GoodsReceiptItem.objects.create(
            organization=organization,
            created_by=actor,
            receipt=receipt,
            product=line['product'],
            batch=batch,
            quantity=quantity,
            cost_price=line.get('cost_price') or ZERO,
            sort_order=index,
        )
        record_movement(
            organization,
            batch=batch,
            movement_type=MovementType.PURCHASE,
            quantity=quantity,
            actor=actor,
            occurred_at=receipt.received_at,
            goods_receipt_item=item,
        )
    return receipt


@transaction.atomic
def allocate_fefo(organization, *, product, branch, quantity, on_date=None):
    """Which batches to draw ``quantity`` from, earliest expiry first.

    Returns ``[(batch, positive_quantity), …]``. Expired batches are skipped
    entirely rather than counted and refused, so stock that is past date reads
    as unavailable everywhere rather than only at the point of sale.

    Call inside the transaction that writes the movements: the batch rows are
    locked, so two people selling the last box cannot both be told it is there.
    """
    quantity = _to_quantity(quantity)
    if quantity <= ZERO:
        raise InventoryError('The quantity to take out must be greater than zero.')

    # Lock first, count second, in two statements. Doing both in one
    # ``SELECT … FOR UPDATE`` is the trap: under READ COMMITTED a waiting query
    # re-evaluates its WHERE clause when the lock frees, but a subquery in the
    # select list keeps the pre-lock snapshot — so every seller queueing for the
    # last box reads the shelf as full. See ADR 0009.
    locked_ids = list(
        StockBatch.objects.for_organization(organization)
        .filter(product=product, branch=branch)
        .not_expired(on_date=on_date)
        .fefo()
        .select_for_update()
        .values_list('pk', flat=True)
    )
    counted = {
        batch.pk: batch
        for batch in StockBatch.objects.for_organization(organization)
        .filter(pk__in=locked_ids)
        .with_on_hand()
    }

    allocation = []
    remaining = quantity
    for batch_id in locked_ids:
        batch = counted[batch_id]
        available = batch.on_hand
        if available <= ZERO:
            continue
        taken = min(available, remaining)
        allocation.append((batch, taken))
        remaining -= taken
        if remaining <= ZERO:
            break

    if remaining > ZERO:
        held = quantity - remaining
        raise InsufficientStock(
            f'{product.name} has {held} in usable stock at {branch.name}, '
            f'and {quantity} was asked for.'
        )
    return allocation


@transaction.atomic
def consume_stock(
    organization,
    *,
    product,
    branch,
    quantity,
    movement_type: str,
    actor,
    reason: str = '',
    occurred_at=None,
    invoice_item=None,
    prescription_item=None,
):
    """Take ``quantity`` (positive) off the shelf, splitting across batches.

    One movement per batch touched, so the ledger says which lot actually left
    even though nobody chose it by hand.
    """
    if movement_type not in DECREASING_TYPES:
        raise InventoryError(f'{movement_type} does not take stock out.')

    allocation = allocate_fefo(
        organization, product=product, branch=branch, quantity=quantity
    )
    return [
        record_movement(
            organization,
            batch=batch,
            movement_type=movement_type,
            quantity=-taken,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
            invoice_item=invoice_item,
            prescription_item=prescription_item,
        )
        for batch, taken in allocation
    ]


def on_hand(organization, *, product, branch=None, usable_only: bool = False):
    """Units of ``product`` in stock, optionally at one branch.

    ``usable_only`` excludes expired batches — what can be sold, as opposed to
    what is physically on the premises and still has to be written off.
    """
    batches = StockBatch.objects.for_organization(organization).filter(product=product)
    if branch is not None:
        batches = batches.filter(branch=branch)
    if usable_only:
        batches = batches.not_expired()
    totals = StockMovement.objects.for_organization(organization).filter(
        batch__in=batches
    )
    return totals.aggregate(total=Sum('quantity'))['total'] or ZERO


@transaction.atomic
def record_adjustment(organization, *, batch, actor, quantity, reason: str):
    """Correct one batch against what is actually on the shelf.

    Signed: negative for a shortfall, positive for stock found. Refused if it
    would take the batch below zero, which is a typo rather than a count.
    """
    locked = (
        StockBatch.objects.for_organization(organization)
        .select_for_update()
        .get(pk=batch.pk)
    )
    # Counted after the lock, in its own statement — see allocate_fefo.
    current = on_hand(organization, product=locked.product, branch=locked.branch)
    quantity = _to_quantity(quantity)
    if current + quantity < ZERO:
        raise InsufficientStock(
            f'That would leave {current + quantity} in stock. There is {current}.'
        )
    return record_movement(
        organization,
        batch=locked,
        movement_type=MovementType.ADJUSTMENT,
        quantity=quantity,
        actor=actor,
        reason=reason,
    )


def _product_on_hand(*, branch=None):
    """Correlated on-hand subquery for a ``Product`` row.

    Counts everything physically on the shelf, expired batches included: the
    stock list reports what is there, not what may leave.
    """
    movements = StockMovement.all_objects.filter(batch__product=OuterRef('pk'))
    if branch is not None:
        movements = movements.filter(batch__branch=branch)
    return Coalesce(
        Subquery(
            movements.values('batch__product')
            .annotate(sum=Sum('quantity'))
            .values('sum'),
            output_field=_SUM_FIELD,
        ),
        Value(ZERO),
        output_field=_SUM_FIELD,
    )


def stock_levels(organization, *, branch=None, query: str = ''):
    """Stock-tracked products with their on-hand total, for the stock page.

    Annotates rather than looping, so the page is one query however long the
    catalog gets. Lives here rather than on ``Product`` because catalog knows
    nothing about inventory and should keep it that way.
    """
    products = Product.objects.for_organization(organization).filter(
        is_stock_tracked=True
    )
    query = (query or '').strip()
    if query:
        products = products.filter(Q(name__icontains=query) | Q(sku__icontains=query))
    return products.annotate(
        annotated_on_hand=_product_on_hand(branch=branch)
    ).order_by('name')


def batches_for(organization, *, product, branch=None):
    """One product's batches with what is left on each, earliest expiry first."""
    batches = (
        StockBatch.objects.for_organization(organization)
        .filter(product=product)
        .select_related('branch')
        .with_on_hand()
        .fefo()
    )
    return batches.filter(branch=branch) if branch is not None else batches


def movement_history(organization, *, product, branch=None):
    """Every movement for one product, newest first (SPEC §6.5)."""
    movements = (
        StockMovement.objects.for_organization(organization)
        .filter(batch__product=product)
        .select_related('batch', 'batch__branch', 'created_by')
    )
    return movements.filter(batch__branch=branch) if branch is not None else movements
