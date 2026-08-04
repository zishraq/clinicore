"""Billing operations. Every function takes ``organization`` explicitly.

The invariants live here rather than in the views, because there is more than
one way into each of them (a form, a management command, a future API):

* an invoice number is allocated from a locked counter, inside the same
  transaction that writes the invoice, so the run has no gaps and no duplicates;
* a payment can never exceed the balance;
* nothing financial is deleted — a mistake is voided with a reason and an actor.
"""

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from billing.models import (
    Invoice,
    InvoiceState,
    LineType,
    Payment,
    PaymentStatus,
)
from billing.money import ZERO, to_money
from core.services import current_period, next_document_number

__all__ = [
    'BillingError',
    'InvoiceLocked',
    'Overpayment',
    'consultation_line_defaults',
    'create_invoice',
    'filter_invoices',
    'invoice_for_encounter',
    'next_invoice_number',
    'outstanding_balance',
    'patient_invoices',
    'record_payment',
    'save_invoice_items',
    'update_invoice',
    'void_invoice',
    'void_payment',
]

#: ``DocumentSequence.kind`` for the invoice run.
INVOICE_SEQUENCE = 'INVOICE'
INVOICE_PREFIX = 'INV'


class BillingError(ValueError):
    """Base for the refusals a caller is expected to show to a user."""


class Overpayment(BillingError):
    """Raised when a payment would take the invoice past its balance."""


class InvoiceLocked(BillingError):
    """Raised when an invoice with payments against it is edited or voided."""


def next_invoice_number(organization) -> str:
    """Next gap-free number for this organization's invoice run."""
    return next_document_number(
        organization,
        kind=INVOICE_SEQUENCE,
        prefix=INVOICE_PREFIX,
        period=current_period(),
    )


def consultation_line_defaults(organization) -> dict:
    """Prefill for the consultation line of a new bill (SPEC §6.6).

    Keyed for the form, not the model: ``display_name`` is the visible box, and
    the name is snapshotted from it when the line is saved.
    """
    return {
        'line_type': LineType.CONSULTATION,
        'display_name': organization.terms['consultation_fee'],
        'quantity': 1,
        'unit_price': to_money(organization.default_consultation_fee),
        'discount': ZERO,
    }


def save_invoice_items(invoice: Invoice, *, actor, item_formset) -> None:
    """Persist the line formset against ``invoice``, tenant and order intact."""
    items = item_formset.save(commit=False)
    for index, item in enumerate(items):
        item.invoice = invoice
        item.organization_id = invoice.organization_id
        item.created_by = item.created_by or actor
        item.sort_order = item.sort_order or index
        item.save()
    for deleted in item_formset.deleted_objects:
        deleted.delete()


@transaction.atomic
def create_invoice(organization, *, actor, form, item_formset) -> Invoice:
    """Issue an invoice and its lines in one transaction.

    The number is allocated inside this transaction on purpose: the counter row
    stays locked until it commits, so a rollback here returns the number to the
    run instead of leaving a hole in it.
    """
    invoice = form.save(commit=False)
    invoice.organization = organization
    invoice.created_by = actor
    invoice.currency = organization.currency
    invoice.number = next_invoice_number(organization)
    invoice.save()
    save_invoice_items(invoice, actor=actor, item_formset=item_formset)
    return invoice


@transaction.atomic
def update_invoice(organization, *, actor, invoice: Invoice, form, item_formset):
    """Edit an invoice that has not been paid against.

    Once money has been received the lines are frozen: the patient is holding a
    receipt that has to keep matching this row. Voiding and re-issuing is the
    correction path, and it leaves both documents on the record.
    """
    if invoice.is_void:
        raise InvoiceLocked('This bill was voided and can no longer be edited.')
    if invoice.has_payments:
        raise InvoiceLocked(
            'This bill has payments recorded against it. Void a payment first, '
            'or void the bill and issue a new one.'
        )
    invoice = form.save(commit=False)
    invoice.organization = organization
    invoice.save()
    save_invoice_items(invoice, actor=actor, item_formset=item_formset)
    return invoice


@transaction.atomic
def record_payment(
    organization,
    *,
    invoice: Invoice,
    actor,
    amount,
    method: str,
    received_at=None,
    note: str = '',
) -> Payment:
    """Take a payment against ``invoice``, refusing anything over the balance.

    The invoice row is locked first so two people collecting at once cannot
    both be told there is room for the money.
    """
    locked = Invoice.all_objects.select_for_update().get(
        pk=invoice.pk, organization=organization
    )
    if locked.status == InvoiceState.VOID:
        raise BillingError('This bill was voided; no payment can be recorded on it.')

    amount = to_money(amount)
    if amount <= ZERO:
        raise BillingError('A payment must be greater than zero.')

    balance = locked.balance
    if amount > balance:
        raise Overpayment(
            f'That is more than the {locked.currency} {balance} still owed. '
            f'Record {locked.currency} {balance} or less.'
        )

    payment = Payment.objects.create(
        organization=organization,
        created_by=actor,
        invoice=locked,
        amount=amount,
        method=method,
        received_at=received_at or timezone.now(),
        received_by=actor,
        note=note,
    )
    return payment


@transaction.atomic
def void_payment(payment: Payment, *, actor, reason: str) -> Payment:
    """Reverse a payment recorded in error. The row stays, the money stops counting."""
    reason = (reason or '').strip()
    if not reason:
        raise BillingError('Voiding a payment requires a reason.')
    if payment.is_void:
        return payment
    payment.voided_at = timezone.now()
    payment.voided_by = actor
    payment.void_reason = reason[:300]
    payment.save(update_fields=['voided_at', 'voided_by', 'void_reason', 'updated_at'])
    return payment


@transaction.atomic
def void_invoice(invoice: Invoice, *, actor, reason: str) -> Invoice:
    """Void a whole invoice issued in error.

    Refused while live payments hang off it: money that was actually collected
    has to be dealt with first, one payment at a time, so the reversal of each
    is recorded rather than swept up by a single click.
    """
    reason = (reason or '').strip()
    if not reason:
        raise BillingError('Voiding a bill requires a reason.')
    if invoice.is_void:
        return invoice
    if invoice.has_payments:
        raise InvoiceLocked(
            'Void the payments recorded against this bill before voiding the bill.'
        )
    invoice.status = InvoiceState.VOID
    invoice.voided_at = timezone.now()
    invoice.voided_by = actor
    invoice.void_reason = reason[:300]
    invoice.save(
        update_fields=[
            'status',
            'voided_at',
            'voided_by',
            'void_reason',
            'updated_at',
        ]
    )
    return invoice


def filter_invoices(organization, *, status='', date_from=None, date_to=None, query=''):
    """The invoice list, filtered the way SPEC §6.6 asks: state and date range."""
    invoices = (
        Invoice.objects.for_organization(organization)
        .select_related('patient')
        .with_totals()
    )
    if status in PaymentStatus.values:
        invoices = invoices.with_payment_status(status)
    if date_from:
        invoices = invoices.filter(issued_at__date__gte=date_from)
    if date_to:
        invoices = invoices.filter(issued_at__date__lte=date_to)
    query = (query or '').strip()
    if query:
        invoices = invoices.filter(
            Q(number__icontains=query)
            | Q(patient__full_name__icontains=query)
            | Q(patient__code__icontains=query)
        )
    return invoices


def patient_invoices(organization, patient):
    """A patient's bills, newest first, with their totals annotated."""
    return (
        Invoice.objects.for_organization(organization)
        .filter(patient=patient)
        .with_totals()
    )


def outstanding_balance(organization, patient):
    """What this patient still owes across every live bill (SPEC §6.2)."""
    totals = (
        Invoice.objects.for_organization(organization)
        .filter(patient=patient)
        .outstanding()
        .aggregate(total=Sum('annotated_balance'))
    )
    return to_money(totals['total'] or ZERO)


def invoice_for_encounter(organization, encounter):
    """The live bill already raised for a visit, if there is one."""
    return (
        Invoice.objects.for_organization(organization)
        .filter(encounter=encounter)
        .exclude(status=InvoiceState.VOID)
        .with_totals()
        .first()
    )
