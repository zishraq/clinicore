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

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
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
    'BatchExpired',
    'InsufficientStock',
    'InventoryError',
    'allocate_fefo',
    'batches_for',
    'consume_from_batch',
    'consume_stock',
    'movement_history',
    'on_hand',
    'post_sale_movements',
    'receive_stock',
    'record_adjustment',
    'record_movement',
    'reverse_sale_movements',
    'sellable_batches',
    'stock_alerts',
    'stock_levels',
]

#: ``DocumentSequence.kind`` for the goods receipt run.
RECEIPT_SEQUENCE = 'GOODS_RECEIPT'
RECEIPT_PREFIX = 'GRN'

#: How far ahead the "expiring soon" alert looks (SPEC §6.5's "within N days").
#: A month is one ordering cycle for a small clinic — long enough to use the
#: stock or send it back, short enough that the list stays worth reading.
EXPIRY_HORIZON_DAYS = 30

#: Quantities carry two places, like the invoice line they often come from.
_QUANTITY_EXPONENT = Decimal('0.01')

#: Output field for the ledger sums annotated onto catalog rows.
_SUM_FIELD = DecimalField(max_digits=14, decimal_places=2)


class InventoryError(ValueError):
    """Base for the refusals a caller is expected to show to a user."""


class InsufficientStock(InventoryError):
    """Raised when an outflow is larger than what the branch actually holds."""


class BatchExpired(InventoryError):
    """Raised when a past-date batch is chosen by hand for a sale (SPEC §6.5).

    Automatic allocation never reaches this: ``allocate_fefo`` leaves expired
    batches out entirely. It exists for the override, where a human named the
    lot.
    """


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


def _lot_label(batch: StockBatch) -> str:
    """How a batch is named in a refusal the practitioner has to act on."""
    return f'lot {batch.lot_number}' if batch.lot_number else 'the unnumbered lot'


@transaction.atomic
def consume_from_batch(
    organization,
    *,
    batch: StockBatch,
    quantity,
    movement_type: str,
    actor,
    reason: str = '',
    occurred_at=None,
    invoice_item=None,
    prescription_item=None,
):
    """Take ``quantity`` (positive) off one named batch, bypassing FEFO.

    The override behind the batch column on a bill line. Expired stock is
    refused here rather than quietly skipped: automatic allocation leaves
    expired batches out, but someone who named this lot by hand is owed the
    reason. Only a write-off may take stock off a past-date batch.
    """
    if movement_type not in DECREASING_TYPES:
        raise InventoryError(f'{movement_type} does not take stock out.')
    quantity = _to_quantity(quantity)
    if quantity <= ZERO:
        raise InventoryError('The quantity to take out must be greater than zero.')

    locked = (
        StockBatch.objects.for_organization(organization)
        .select_related('product', 'branch')
        .select_for_update()
        .get(pk=batch.pk)
    )
    if locked.is_expired and movement_type != MovementType.WASTAGE:
        raise BatchExpired(
            f'{locked.product.name} — {_lot_label(locked)} expired on '
            f'{locked.expiry_date:%d %b %Y} and cannot go out. Clear the batch '
            f'to take the earliest usable one instead.'
        )
    # Counted after the lock, in its own statement — see allocate_fefo.
    available = _to_quantity(
        StockMovement.objects.for_organization(organization)
        .filter(batch=locked)
        .aggregate(total=Sum('quantity'))['total']
        or ZERO
    )
    if available < quantity:
        raise InsufficientStock(
            f'{locked.product.name} — {_lot_label(locked)} has {available} left '
            f'at {locked.branch.name}, and {quantity} was asked for.'
        )
    return [
        record_movement(
            organization,
            batch=locked,
            movement_type=movement_type,
            quantity=-quantity,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
            invoice_item=invoice_item,
            prescription_item=prescription_item,
        )
    ]


def _stock_lines(invoice) -> list:
    """The bill lines that move stock: tracked catalog products, nothing else.

    Takes the invoice by duck typing rather than importing ``billing`` — the
    ledger has no business knowing how a bill is shaped beyond its lines, its
    branch, and the quantity on each row.
    """
    return [
        item
        for item in invoice.items.select_related('product', 'batch', 'batch__branch')
        if item.product_id is not None and item.product.is_stock_tracked
    ]


@transaction.atomic
def post_sale_movements(organization, *, invoice, actor):
    """Take what a bill sold off the shelf. Exactly once per line, ever.

    The invoice is the stock event (ADR 0009), so this runs when one is issued.
    Idempotent by construction: a line that already carries a movement is
    skipped, so a double-submit, a retry, or a second call posts nothing.
    Reversal is ``reverse_sale_movements``, and nothing re-posts a reversed
    line — a voided bill is the end of that document.

    Call inside the transaction that writes the invoice: an outflow it cannot
    cover must take the bill down with it rather than leave a half-billed sale.
    """
    lines = _stock_lines(invoice)
    if not lines:
        return []
    if invoice.branch_id is None:
        raise InventoryError(
            'This bill sells stock-tracked products, so it needs a branch to '
            'take them off. Choose one and try again.'
        )

    posted = []
    for item in lines:
        # The one idempotency guard: a line posts its stock once and never
        # again, whatever calls this or how often.
        if item.stock_movements.exists():
            continue
        common = {
            'movement_type': MovementType.SALE,
            'actor': actor,
            'occurred_at': invoice.issued_at,
            'invoice_item': item,
        }
        if item.batch_id is None:
            posted += consume_stock(
                organization,
                product=item.product,
                branch=invoice.branch,
                quantity=item.quantity,
                **common,
            )
            continue
        if item.batch.branch_id != invoice.branch_id:
            raise InventoryError(
                f'{item.product.name} — {_lot_label(item.batch)} is held at '
                f'{item.batch.branch.name}, not at {invoice.branch.name}.'
            )
        posted += consume_from_batch(
            organization, batch=item.batch, quantity=item.quantity, **common
        )
    return posted


@transaction.atomic
def reverse_sale_movements(organization, *, invoice, actor, reason: str):
    """Put back what a bill took, one ``RETURN`` per batch it came off.

    A compensating movement, never a delete: the sale happened, and the ledger
    keeps saying so. Idempotent, because what goes back is whatever is still
    out — run it twice and the second pass finds nothing outstanding.
    """
    outstanding = (
        StockMovement.objects.for_organization(organization)
        .filter(invoice_item__invoice=invoice)
        .values('invoice_item', 'batch')
        .annotate(net=Sum('quantity'))
        .order_by('invoice_item', 'batch')
    )
    rows = [row for row in outstanding if row['net'] < ZERO]
    if not rows:
        return []

    items = {item.pk: item for item in invoice.items.all()}
    batches = {
        batch.pk: batch
        for batch in StockBatch.objects.for_organization(organization).filter(
            pk__in={row['batch'] for row in rows}
        )
    }
    return [
        record_movement(
            organization,
            batch=batches[row['batch']],
            movement_type=MovementType.RETURN,
            quantity=-_to_quantity(row['net']),
            actor=actor,
            reason=reason,
            invoice_item=items[row['invoice_item']],
        )
        for row in rows
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


def _product_on_hand(*, branch=None, usable_only: bool = False, on_date=None):
    """Correlated on-hand subquery for a ``Product`` row.

    ``usable_only`` leaves expired batches out of the total. The reorder alert
    wants that — a box that is past date is not cover for the one about to run
    out — while the stock list wants everything that is physically there.
    """
    movements = StockMovement.all_objects.filter(batch__product=OuterRef('pk'))
    if branch is not None:
        movements = movements.filter(batch__branch=branch)
    if usable_only:
        on_date = on_date or timezone.localdate()
        movements = movements.filter(
            Q(batch__expiry_date__isnull=True) | Q(batch__expiry_date__gte=on_date)
        )
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


def stock_alerts(organization, *, branch=None, within_days: int = EXPIRY_HORIZON_DAYS):
    """The three things a clinic has to be told about its shelf (SPEC §6.5).

    Below reorder level, expiring inside ``within_days``, and already expired.
    Querysets, not lists: the dashboard slices them and the caller counts them.

    A reorder level of zero means "no alert" rather than "warn at nothing",
    which is what a clinic that has not thought about a level yet actually
    wants (``catalog.Product.reorder_level``).
    """
    today = timezone.localdate()
    below_reorder = (
        Product.objects.for_organization(organization)
        .filter(is_stock_tracked=True, is_active=True, reorder_level__gt=ZERO)
        .annotate(annotated_on_hand=_product_on_hand(branch=branch, usable_only=True))
        .filter(annotated_on_hand__lte=F('reorder_level'))
        .order_by('annotated_on_hand', 'name')
    )
    batches = StockBatch.objects.for_organization(organization).select_related(
        'product', 'branch'
    )
    if branch is not None:
        batches = batches.filter(branch=branch)
    # Only batches with something left on them: an empty past-date batch is
    # history, not a job for someone.
    batches = batches.in_stock()
    return {
        'below_reorder': below_reorder,
        'expiring': batches.filter(
            expiry_date__gte=today, expiry_date__lte=today + timedelta(days=within_days)
        ).fefo(),
        'expired': batches.filter(expiry_date__lt=today).fefo(),
        'within_days': within_days,
    }


def sellable_batches(organization, *, product, branch):
    """Batches offered as the batch override on a bill line.

    Everything with stock left, expired lots included: the practitioner has to
    be able to see the box that is sitting there and be told why it cannot go
    out, rather than have it silently missing from the list.
    """
    return (
        StockBatch.objects.for_organization(organization)
        .filter(product=product, branch=branch)
        .in_stock()
        .fefo()
    )


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
