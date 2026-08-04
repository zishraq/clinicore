"""Stock batches and the movement ledger they are counted from.

One rule shapes this module, from SPEC §5 and
docs/adr/0009-ledger-based-stock.md: **on-hand is never a column.** A
``StockBatch`` is identity — product, branch, lot, expiry, cost — and every
change to it is an immutable ``StockMovement`` row. Quantity is the sum of that
ledger, the same way an invoice balance is the sum of its payments.

Movements are append-only. A receipt keyed wrongly is corrected by an
``ADJUSTMENT`` with a reason, never by editing or deleting the row that was
already counted.
"""

from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.managers import OrgScopedManager, OrgScopedQuerySet
from core.models import OrgOwnedModel

__all__ = [
    'GoodsReceipt',
    'GoodsReceiptItem',
    'LedgerIsAppendOnly',
    'MovementType',
    'StockBatch',
    'StockMovement',
]

#: Quantities are fractional and signed: half a bottle, 2.5 ml, -10 dispensed.
_QUANTITY = {'max_digits': 12, 'decimal_places': 2}
_AMOUNT = {'max_digits': 12, 'decimal_places': 2}

_SUM_FIELD = DecimalField(max_digits=14, decimal_places=2)

ZERO = Decimal('0.00')


class LedgerIsAppendOnly(ValueError):
    """Raised when something tries to edit or delete a recorded movement."""


class MovementType(models.TextChoices):
    """SPEC §5. The sign each one carries is enforced by a check constraint."""

    PURCHASE = 'PURCHASE', 'Purchase'
    SALE = 'SALE', 'Sale'
    DISPENSE = 'DISPENSE', 'Dispense'
    ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'
    RETURN = 'RETURN', 'Return'
    WASTAGE = 'WASTAGE', 'Wastage'


#: Movements that put stock in, and movements that take it out. ADJUSTMENT is in
#: neither: a stock count corrects in whichever direction the shelf disagrees.
INCREASING_TYPES = (MovementType.PURCHASE, MovementType.RETURN)
DECREASING_TYPES = (MovementType.SALE, MovementType.DISPENSE, MovementType.WASTAGE)

#: Types that must say why. A count that nobody can explain is not a correction.
REASON_REQUIRED_TYPES = (MovementType.ADJUSTMENT, MovementType.WASTAGE)

#: The document behind each automatic movement type. A movement may have no
#: source at all — opening stock and manual corrections are entered by hand —
#: but it may never carry a source belonging to a different type.
SOURCE_FIELDS = {
    MovementType.PURCHASE: 'goods_receipt_item',
    MovementType.SALE: 'invoice_item',
    MovementType.DISPENSE: 'prescription_item',
}


def _source_matches_type() -> Q:
    """At most one source FK, and only on the movement type it belongs to."""
    fields = list(SOURCE_FIELDS.values())
    condition = Q(**{f'{field}__isnull': True for field in fields})
    for movement_type, field in SOURCE_FIELDS.items():
        others = {f'{other}__isnull': True for other in fields if other != field}
        condition |= Q(
            movement_type=movement_type, **{f'{field}__isnull': False}, **others
        )
    return condition


def _quantity_sign_matches_type() -> Q:
    """Purchases add, sales and dispenses subtract, adjustments do either."""
    return (
        (Q(movement_type__in=list(INCREASING_TYPES)) & Q(quantity__gt=0))
        | (Q(movement_type__in=list(DECREASING_TYPES)) & Q(quantity__lt=0))
        | (Q(movement_type=MovementType.ADJUSTMENT) & ~Q(quantity=0))
    )


class StockBatchQuerySet(OrgScopedQuerySet):
    """On-hand arrives as an annotation, so a stock page is one query."""

    def with_on_hand(self):
        """Annotate ``annotated_on_hand`` from the movement ledger.

        A correlated subquery rather than a join: a batch list joined to its
        movements multiplies the rows, and every other aggregate on the page
        comes out wrong with it.

        Reads ``all_objects`` because the subquery is already correlated to an
        organization-scoped batch — applying the ambient filter a second time
        would only make this unusable from a management command.
        """
        if 'annotated_on_hand' in self.query.annotations:
            return self
        total = Subquery(
            StockMovement.all_objects.filter(batch=OuterRef('pk'))
            .values('batch')
            .annotate(total=Sum('quantity'))
            .values('total')[:1],
            output_field=_SUM_FIELD,
        )
        return self.annotate(
            annotated_on_hand=Coalesce(total, Value(ZERO), output_field=_SUM_FIELD)
        )

    def not_expired(self, *, on_date=None):
        """Batches still usable. An undated batch never expires."""
        on_date = on_date or timezone.localdate()
        return self.filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=on_date))

    def expired(self, *, on_date=None):
        """SPEC §6.5 — reported separately and blocked from going out."""
        on_date = on_date or timezone.localdate()
        return self.filter(expiry_date__lt=on_date)

    def in_stock(self):
        """Batches with something left on them."""
        return self.with_on_hand().filter(annotated_on_hand__gt=ZERO)

    def fefo(self):
        """First-expiry-first-out: the order stock is drawn down in.

        Undated batches sort last — a batch with a known expiry should always
        leave the shelf before one that may sit there indefinitely.
        """
        return self.order_by(models.F('expiry_date').asc(nulls_last=True), 'id')


class StockBatchManager(OrgScopedManager.from_queryset(StockBatchQuerySet)):
    """Organization-scoped, with the on-hand helpers attached."""


class StockBatch(OrgOwnedModel):
    """A lot of one product at one branch, bought at one cost.

    Deliberately has no quantity field. Stock in hand is
    ``StockMovement`` summed over the batch — see ``with_on_hand()``.
    """

    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='batches'
    )
    branch = models.ForeignKey(
        'organizations.Branch', on_delete=models.PROTECT, related_name='batches'
    )
    # Blank is normal: plenty of clinics do not track lots at all, and forcing a
    # made-up number would make the unique constraint below meaningless.
    lot_number = models.CharField(max_length=64, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    cost_price = models.DecimalField(**_AMOUNT, default=ZERO)
    notes = models.CharField(max_length=300, blank=True)

    objects = StockBatchManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['expiry_date', 'id']
        base_manager_name = 'all_objects'
        constraints = [
            # Only when a lot number was actually given: unnumbered batches are
            # anonymous by definition and each receipt makes a new one.
            models.UniqueConstraint(
                fields=['organization', 'product', 'branch', 'lot_number'],
                condition=~Q(lot_number=''),
                name='stock_batch_lot_unique_per_product_branch',
            ),
            models.CheckConstraint(
                condition=Q(cost_price__gte=0), name='stock_batch_cost_not_negative'
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'product', 'branch']),
            models.Index(fields=['organization', 'expiry_date']),
        ]

    def __str__(self) -> str:
        label = self.lot_number or 'no lot'
        return f'{self.product.name} ({label})'

    @property
    def on_hand(self):
        """Units left. Uses ``with_on_hand()`` when the queryset annotated it."""
        annotated = self.__dict__.get('annotated_on_hand')
        if annotated is not None:
            return annotated
        return self.movements.aggregate(total=Sum('quantity'))['total'] or ZERO

    @property
    def is_expired(self) -> bool:
        return self.expiry_date is not None and self.expiry_date < timezone.localdate()


class StockMovement(OrgOwnedModel):
    """One immutable line of the stock ledger.

    Never updated and never deleted: the quantity on the shelf is the sum of
    these rows, so editing one silently restates every count taken since. A
    mistake is corrected by posting another movement.
    """

    batch = models.ForeignKey(
        StockBatch, on_delete=models.PROTECT, related_name='movements'
    )
    movement_type = models.CharField(max_length=12, choices=MovementType.choices)
    # Signed: what this row does to the batch, so on-hand is a plain Sum.
    quantity = models.DecimalField(**_QUANTITY)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    reason = models.CharField(max_length=300, blank=True)

    # The document that caused this, where one did. Explicit nullable FKs rather
    # than a generic relation: the set of sources is small and closed, and these
    # can be constrained, joined, and PROTECTed. See ADR 0009.
    goods_receipt_item = models.ForeignKey(
        'inventory.GoodsReceiptItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movements',
    )
    invoice_item = models.ForeignKey(
        'billing.InvoiceItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_movements',
    )
    prescription_item = models.ForeignKey(
        'clinical.PrescriptionItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_movements',
    )

    class Meta:
        ordering = ['-occurred_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=_quantity_sign_matches_type(),
                name='stock_movement_quantity_sign_matches_type',
            ),
            models.CheckConstraint(
                condition=_source_matches_type(),
                name='stock_movement_source_matches_type',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(movement_type__in=list(REASON_REQUIRED_TYPES)) | ~Q(reason='')
                ),
                name='stock_movement_correction_has_reason',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', '-occurred_at']),
            models.Index(fields=['organization', 'batch']),
        ]

    def __str__(self) -> str:
        return f'{self.movement_type} {self.quantity} — {self.batch}'

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise LedgerIsAppendOnly(
                'Stock movements cannot be edited. Post a correcting movement '
                'instead (inventory.services.record_movement).'
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LedgerIsAppendOnly(
            'Stock movements cannot be deleted. Post a correcting movement '
            'instead (inventory.services.record_movement).'
        )


class GoodsReceipt(OrgOwnedModel):
    """A delivery booked in: SPEC §6.5 goods receipt.

    Numbered from the same ``core.DocumentSequence`` machinery as invoices, so
    a clinic reconciling deliveries against supplier paperwork has an unbroken
    run to check. Not voidable: a wrongly booked delivery is corrected with an
    ``ADJUSTMENT`` movement, which leaves both the original and the correction
    on the ledger.
    """

    branch = models.ForeignKey(
        'organizations.Branch', on_delete=models.PROTECT, related_name='goods_receipts'
    )
    number = models.CharField(max_length=32, editable=False)
    supplier = models.CharField(max_length=200, blank=True)
    reference = models.CharField(
        max_length=64, blank=True, help_text="The supplier's own invoice number."
    )
    received_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-received_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'number'],
                name='goods_receipt_number_unique_per_org',
            )
        ]
        indexes = [models.Index(fields=['organization', '-received_at'])]

    def __str__(self) -> str:
        return self.number


class GoodsReceiptItem(OrgOwnedModel):
    """One product on a delivery, and the batch it created."""

    receipt = models.ForeignKey(
        GoodsReceipt, on_delete=models.CASCADE, related_name='items'
    )
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='goods_receipt_items'
    )
    batch = models.ForeignKey(
        StockBatch, on_delete=models.PROTECT, related_name='goods_receipt_items'
    )
    quantity = models.DecimalField(**_QUANTITY)
    cost_price = models.DecimalField(**_AMOUNT, default=ZERO)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name='goods_receipt_item_quantity_positive'
            ),
            models.CheckConstraint(
                condition=Q(cost_price__gte=0),
                name='goods_receipt_item_cost_not_negative',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.product.name} x {self.quantity}'
