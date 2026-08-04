"""Invoices, their lines, and payments.

Two rules shape this module, both from
docs/adr/0008-invoice-numbering-and-derived-balances.md:

* **No stored balance and no paid flag.** The amount due is the sum of the line
  snapshots, the amount paid is the sum of the payments that were not voided,
  and the balance is the difference. A stored copy is one crashed request away
  from disagreeing with the ledger it summarizes.
* **Snapshots, not lookups.** A line keeps the name and the unit price it was
  billed at. A price change next month must not alter a receipt printed today —
  the same reasoning as ``PrescriptionItem.name_snapshot``.

Nothing here is hard-deleted. A mistake is voided, with a reason and an actor.
"""

from django.conf import settings
from django.db import models
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from simple_history.models import HistoricalRecords

from billing.money import ZERO, to_money
from core.managers import OrgScopedManager, OrgScopedQuerySet
from core.models import OrgOwnedModel

__all__ = [
    'Invoice',
    'InvoiceItem',
    'InvoiceState',
    'LineType',
    'Payment',
    'PaymentMethod',
    'PaymentStatus',
]

#: Amounts and prices. Wide enough for currencies with no minor unit inflation.
_AMOUNT = {'max_digits': 12, 'decimal_places': 2}
#: Quantities are fractional: half a bottle, 2.5 ml.
_QUANTITY = {'max_digits': 10, 'decimal_places': 2}

_SUM_FIELD = DecimalField(max_digits=14, decimal_places=2)


class LineType(models.TextChoices):
    """What a line is for. The consultation is its own line, never merged."""

    CONSULTATION = 'CONSULTATION', 'Consultation'
    PRODUCT = 'PRODUCT', 'Product'
    OTHER = 'OTHER', 'Other'


class PaymentMethod(models.TextChoices):
    CASH = 'CASH', 'Cash'
    CARD = 'CARD', 'Card'
    MOBILE = 'MOBILE', 'Mobile'
    BANK = 'BANK', 'Bank'
    OTHER = 'OTHER', 'Other'


class InvoiceState(models.TextChoices):
    """The only lifecycle a user sets by hand. Payment state is derived.

    Deliberately just two values: an invoice exists, or it was issued in error
    and voided. Anything about money owed is computed from the payments.
    """

    ISSUED = 'ISSUED', 'Issued'
    VOID = 'VOID', 'Void'


class PaymentStatus(models.TextChoices):
    """Derived, never stored. Rendered through the terminology map."""

    UNPAID = 'UNPAID', 'Unpaid'
    PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially paid'
    PAID = 'PAID', 'Paid'
    VOID = 'VOID', 'Void'


class VoidableModel(models.Model):
    """Correction by reversal: nothing financial is edited away or deleted.

    A wrongly recorded payment or invoice keeps its row, gains a reason and the
    person who voided it, and stops counting towards any total.
    """

    voided_at = models.DateTimeField(null=True, blank=True, db_index=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    void_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        abstract = True

    @property
    def is_void(self) -> bool:
        return self.voided_at is not None


class InvoiceQuerySet(OrgScopedQuerySet):
    """Totals arrive as annotations so a list page is one query, not N."""

    def with_totals(self):
        """Annotate ``annotated_due``, ``annotated_paid``, ``annotated_balance``.

        Two correlated subqueries rather than two joins: joining items and
        payments in one query multiplies the rows and both sums come out wrong.

        The subqueries read ``all_objects`` because they are already correlated
        to an organization-scoped invoice — adding the ambient filter a second
        time would only make this unusable from a management command.
        """
        if 'annotated_due' in self.query.annotations:
            return self
        due = Subquery(
            InvoiceItem.all_objects.filter(invoice=OuterRef('pk'))
            .values('invoice')
            .annotate(total=Sum('line_total'))
            .values('total')[:1],
            output_field=_SUM_FIELD,
        )
        paid = Subquery(
            Payment.all_objects.filter(invoice=OuterRef('pk'), voided_at__isnull=True)
            .values('invoice')
            .annotate(total=Sum('amount'))
            .values('total')[:1],
            output_field=_SUM_FIELD,
        )
        return self.annotate(
            annotated_due=Coalesce(due, Value(ZERO), output_field=_SUM_FIELD),
            annotated_paid=Coalesce(paid, Value(ZERO), output_field=_SUM_FIELD),
        ).annotate(
            annotated_balance=F('annotated_due') - F('annotated_paid'),
        )

    def with_payment_status(self, status: str):
        """Filter by a status no column holds (SPEC §6.6 list filters)."""
        queryset = self.with_totals()
        if status == PaymentStatus.VOID:
            return queryset.filter(status=InvoiceState.VOID)
        queryset = queryset.exclude(status=InvoiceState.VOID)
        if status == PaymentStatus.PAID:
            return queryset.filter(annotated_balance__lte=ZERO)
        if status == PaymentStatus.UNPAID:
            return queryset.filter(annotated_paid__lte=ZERO, annotated_balance__gt=ZERO)
        if status == PaymentStatus.PARTIALLY_PAID:
            return queryset.filter(annotated_paid__gt=ZERO, annotated_balance__gt=ZERO)
        return queryset

    def outstanding(self):
        """Invoices with money still owed on them."""
        return (
            self.with_totals()
            .exclude(status=InvoiceState.VOID)
            .filter(annotated_balance__gt=ZERO)
        )


class InvoiceManager(OrgScopedManager.from_queryset(InvoiceQuerySet)):
    """Organization-scoped, with the totals helpers attached."""


class Invoice(OrgOwnedModel, VoidableModel):
    patient = models.ForeignKey(
        'patients.Patient', on_delete=models.PROTECT, related_name='invoices'
    )
    # Nullable: a bill can be a straight product sale with no consultation
    # behind it, and that is a normal counter transaction, not an anomaly.
    encounter = models.ForeignKey(
        'clinical.Encounter',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='invoices',
    )
    # Where the money was taken and, more to the point, which shelf a product
    # line comes off: stock is held per branch. Nullable because every invoice
    # raised before inventory existed has none, and because a single-branch
    # clinic should never be asked. Resolved in services.resolve_invoice_branch.
    branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='invoices',
    )
    number = models.CharField(max_length=32, editable=False)
    issued_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=8, choices=InvoiceState.choices, default=InvoiceState.ISSUED
    )
    # Snapshot, not a lookup: an organization that switches currency must not
    # restate every receipt it ever printed.
    currency = models.CharField(max_length=3)
    notes = models.TextField(blank=True)

    objects = InvoiceManager()
    all_objects = models.Manager()  # noqa: DJ012

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta:
        ordering = ['-issued_at', '-id']
        base_manager_name = 'all_objects'
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'number'], name='invoice_number_unique_per_org'
            )
        ]
        indexes = [
            models.Index(fields=['organization', '-issued_at']),
            models.Index(fields=['organization', 'patient']),
        ]

    def __str__(self) -> str:
        return self.number

    @property
    def amount_due(self):
        """Sum of the line snapshots. Uses ``with_totals()`` when annotated."""
        annotated = self.__dict__.get('annotated_due')
        if annotated is not None:
            return to_money(annotated)
        return to_money(self.items.aggregate(total=Sum('line_total'))['total'] or ZERO)

    @property
    def amount_paid(self):
        """Payments received, ignoring voided ones."""
        annotated = self.__dict__.get('annotated_paid')
        if annotated is not None:
            return to_money(annotated)
        return to_money(
            self.payments.filter(voided_at__isnull=True).aggregate(total=Sum('amount'))[
                'total'
            ]
            or ZERO
        )

    @property
    def balance(self):
        """What the patient still owes. Never a column."""
        annotated = self.__dict__.get('annotated_balance')
        if annotated is not None:
            return to_money(annotated)
        return to_money(self.amount_due - self.amount_paid)

    @property
    def payment_status(self) -> str:
        """UNPAID / PARTIALLY_PAID / PAID, derived from the payments."""
        if self.status == InvoiceState.VOID:
            return PaymentStatus.VOID
        if self.balance <= ZERO:
            return PaymentStatus.PAID
        if self.amount_paid > ZERO:
            return PaymentStatus.PARTIALLY_PAID
        return PaymentStatus.UNPAID

    @property
    def has_payments(self) -> bool:
        return self.payments.filter(voided_at__isnull=True).exists()

    @property
    def is_editable(self) -> bool:
        """Money already changed hands, so the lines stop being editable.

        Void it and issue a replacement instead; a receipt in a patient's hand
        must keep matching the row it was printed from.
        """
        return not self.is_void and not self.has_payments


class InvoiceItem(OrgOwnedModel):
    """One billed line, with its name and price frozen at issue time."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    line_type = models.CharField(
        max_length=12, choices=LineType.choices, default=LineType.PRODUCT
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='invoice_items',
    )
    # What was billed, frozen. Never resolved through the live catalog row.
    name_snapshot = models.CharField(max_length=300)
    quantity = models.DecimalField(**_QUANTITY, default=1)
    unit_price = models.DecimalField(**_AMOUNT)
    discount = models.DecimalField(**_AMOUNT, default=ZERO)
    line_total = models.DecimalField(**_AMOUNT, editable=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            # A catalog line names its product; the consultation and ad-hoc
            # lines have none. Enforced, not left to the form.
            models.CheckConstraint(
                condition=(Q(line_type=LineType.PRODUCT) | Q(product__isnull=True)),
                name='invoice_item_product_only_on_product_line',
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name='invoice_item_quantity_positive'
            ),
            models.CheckConstraint(
                condition=Q(unit_price__gte=0) & Q(discount__gte=0),
                name='invoice_item_amounts_not_negative',
            ),
            models.CheckConstraint(
                condition=Q(line_total__gte=0), name='invoice_item_total_not_negative'
            ),
        ]

    def __str__(self) -> str:
        return self.name_snapshot

    def resolve_name(self) -> str:
        """The name to freeze into ``name_snapshot``."""
        if self.name_snapshot:
            return self.name_snapshot[:300]
        if self.product_id:
            return self.product.name[:300]
        return ''

    @property
    def gross(self):
        """Line value before the discount — what the discount is checked against."""
        return to_money(self.quantity * self.unit_price)

    def save(self, *args, **kwargs):
        self.name_snapshot = self.resolve_name()
        # The one place a line is rounded (billing/money.py).
        self.line_total = to_money(self.gross - to_money(self.discount))
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = {*update_fields, 'name_snapshot', 'line_total'}
        super().save(*args, **kwargs)


class Payment(OrgOwnedModel, VoidableModel):
    """Money received against an invoice. Several per invoice is the norm."""

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='payments'
    )
    amount = models.DecimalField(**_AMOUNT)
    method = models.CharField(
        max_length=8, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    received_at = models.DateTimeField(default=timezone.now)
    # Who took the money, which is not always who typed it in later.
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payments_received',
    )
    note = models.CharField(max_length=300, blank=True)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta:
        ordering = ['received_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name='payment_amount_positive'
            )
        ]
        indexes = [models.Index(fields=['organization', '-received_at'])]

    def __str__(self) -> str:
        return f'{self.amount} on {self.invoice.number}'
