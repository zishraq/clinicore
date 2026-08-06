"""Billing operations. Every function takes ``organization`` explicitly.

The invariants live here rather than in the views, because there is more than
one way into each of them (a form, a management command, a future API):

* an invoice number is allocated from a locked counter, inside the same
  transaction that writes the invoice, so the run has no gaps and no duplicates;
* a payment can never exceed the balance;
* nothing financial is deleted — a mistake is voided with a reason and an actor.

Issuing a bill is also the event that takes stock off the shelf
(docs/adr/0009-ledger-based-stock.md), so ``create_invoice`` posts the ledger
movements and ``void_invoice`` posts the compensating ones. An outflow the
branch cannot cover takes the whole invoice down with it: the transaction that
writes the bill is the transaction that writes the movements.
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
from clinical.models import Encounter
from core.services import current_period, next_document_number
from inventory import services as inventory
from organizations.models import Branch

__all__ = [
    'BillingError',
    'InvoiceLocked',
    'Overpayment',
    'consultation_line_defaults',
    'create_invoice',
    'editing_blocked_reason',
    'filter_invoices',
    'invoice_for_encounter',
    'next_invoice_number',
    'outstanding_balance',
    'patient_invoices',
    'prescribed_product_lines',
    'record_payment',
    'resolve_invoice_branch',
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


def prescribed_product_lines(organization, encounter, *, branch=None) -> list[dict]:
    """Bill lines for what was prescribed and can actually be sold (A5).

    A convenience copy, never a link: these are ordinary editable lines, and
    nothing here writes back to the prescription. Removing one is just removing
    a line.

    Quantity is 1 because a prescription carries no quantity and none should be
    invented — ADR 0009 keeps quantity on the invoice, which is the stock event.
    One is a starting point the practitioner adjusts, not a claim about what was
    prescribed.

    Advice and anything untracked or unsellable never appear: they are not
    things that come off a shelf.
    """
    if encounter is None:
        return []
    prescription = getattr(encounter, 'prescription', None)
    if prescription is None:
        return []

    items = [
        item
        for item in prescription.items.select_related('product')
        if item.product_id and not item.is_advice
    ]
    # One query for the shelf, whatever the prescription's length.
    sellable = inventory.sellable_now(
        organization, [item.product for item in items], branch=branch
    )

    lines = []
    seen = set()
    for item in items:
        # The same medicine written twice is one line to price, not two.
        if item.product_id not in sellable or item.product_id in seen:
            continue
        seen.add(item.product_id)
        lines.append(
            {
                'line_type': LineType.PRODUCT,
                'product': item.product_id,
                'display_name': item.product.name,
                'quantity': 1,
                'unit_price': to_money(item.product.sale_price),
                'discount': ZERO,
            }
        )
    return lines


def resolve_invoice_branch(organization, *, actor=None, encounter=None):
    """Which branch a bill belongs to, without asking when it is obvious.

    A product line comes off a particular shelf, so the branch has to be right;
    it also must not become a dropdown a practitioner clears every day. In
    order: the visit's branch, then where this practitioner last worked, then
    the only branch there is.

    ``Membership`` carries no branch — SPEC §5 wants per-branch access and it is
    not built — so "the practitioner's branch" is read off their most recent
    encounter. Returns ``None`` when a multi-branch organization gives no
    signal, and the form asks.
    """
    if encounter is not None and encounter.branch_id:
        return encounter.branch

    active = Branch.objects.for_organization(organization).filter(is_active=True)
    if actor is not None:
        recent_id = (
            Encounter.objects.for_organization(organization)
            .filter(practitioner=actor)
            .order_by('-occurred_at')
            .values_list('branch_id', flat=True)
            .first()
        )
        if recent_id is not None:
            recent = active.filter(pk=recent_id).first()
            if recent is not None:
                return recent

    return active.first() if active.count() == 1 else None


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
    """Issue an invoice, its lines, and the stock movements they cause.

    The number is allocated inside this transaction on purpose: the counter row
    stays locked until it commits, so a rollback here returns the number to the
    run instead of leaving a hole in it. The same applies to the ledger — a
    line the shelf cannot cover raises ``InsufficientStock`` and no bill exists
    at all, rather than one that promised stock nobody has.
    """
    invoice = form.save(commit=False)
    invoice.organization = organization
    invoice.created_by = actor
    invoice.currency = organization.currency
    if invoice.branch_id is None:
        invoice.branch = resolve_invoice_branch(
            organization, actor=actor, encounter=invoice.encounter
        )
    invoice.number = next_invoice_number(organization)
    invoice.save()
    save_invoice_items(invoice, actor=actor, item_formset=item_formset)
    inventory.post_sale_movements(organization, invoice=invoice, actor=actor)
    return invoice


def editing_blocked_reason(invoice: Invoice) -> str:
    """Why this bill's lines are frozen, in a sentence, or '' if they are not.

    One place, because the view needs it to decide whether to offer the form
    and the service needs it to refuse a post that got there anyway.
    """
    if invoice.is_void:
        return 'This bill was voided and can no longer be edited.'
    if invoice.has_payments:
        return (
            'This bill has payments recorded against it. Void a payment first, '
            'or void the bill and issue a new one.'
        )
    if invoice.has_stock_movements:
        return (
            'This bill has already taken stock off the shelf, and the ledger is '
            'append-only. Void it and issue a new one — voiding puts the stock '
            'back.'
        )
    return ''


@transaction.atomic
def update_invoice(organization, *, actor, invoice: Invoice, form, item_formset):
    """Edit an invoice that has not been paid against or dispensed from.

    Once money has been received the lines are frozen: the patient is holding a
    receipt that has to keep matching this row. The same freeze applies once
    the bill has moved stock, because a quantity that was already counted out
    of a batch cannot change without restating the ledger. Voiding and
    re-issuing is the correction path either way, and it leaves both documents
    on the record — with the stock returned by the void.

    An edit may still be the first thing that sells stock: a fee-only bill that
    grows a product line has moved nothing yet, so the posting call belongs
    here as well as in ``create_invoice``. It is the same call, and it is safe
    on both sides of that boundary because it skips any line that already
    carries a movement.
    """
    blocked = editing_blocked_reason(invoice)
    if blocked:
        raise InvoiceLocked(blocked)
    invoice = form.save(commit=False)
    invoice.organization = organization
    invoice.save()
    save_invoice_items(invoice, actor=actor, item_formset=item_formset)
    inventory.post_sale_movements(organization, invoice=invoice, actor=actor)
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
    """Reverse a payment recorded in error. The row stays, the money stops counting.

    The row is locked before it is read, as in ``void_invoice``. Two clicks on
    the void button write the same values today, so the asymmetry was harmless —
    but it is the kind of harmless that stops being true the moment voiding
    grows a side effect, which is exactly what happened to the invoice.
    """
    reason = (reason or '').strip()
    if not reason:
        raise BillingError('Voiding a payment requires a reason.')

    locked = Payment.all_objects.select_for_update().get(
        pk=payment.pk, organization_id=payment.organization_id
    )
    if locked.is_void:
        return locked
    locked.voided_at = timezone.now()
    locked.voided_by = actor
    locked.void_reason = reason[:300]
    locked.save(update_fields=['voided_at', 'voided_by', 'void_reason', 'updated_at'])
    return locked


@transaction.atomic
def void_invoice(invoice: Invoice, *, actor, reason: str) -> Invoice:
    """Void a whole invoice issued in error, and put its stock back.

    Refused while live payments hang off it: money that was actually collected
    has to be dealt with first, one payment at a time, so the reversal of each
    is recorded rather than swept up by a single click.

    The row is locked before anything is read off it. Two clicks on the void
    button used to be harmless — the second wrote the same values again — but
    now that voiding returns stock, a double post would return it twice.
    """
    reason = (reason or '').strip()
    if not reason:
        raise BillingError('Voiding a bill requires a reason.')

    locked = Invoice.all_objects.select_for_update().get(
        pk=invoice.pk, organization_id=invoice.organization_id
    )
    if locked.is_void:
        return locked
    if locked.has_payments:
        raise InvoiceLocked(
            'Void the payments recorded against this bill before voiding the bill.'
        )
    locked.status = InvoiceState.VOID
    locked.voided_at = timezone.now()
    locked.voided_by = actor
    locked.void_reason = reason[:300]
    locked.save(
        update_fields=[
            'status',
            'voided_at',
            'voided_by',
            'void_reason',
            'updated_at',
        ]
    )
    # Compensating movements, never a delete: the sale happened and the ledger
    # keeps saying so, with the return alongside it.
    inventory.reverse_sale_movements(
        locked.organization,
        invoice=locked,
        actor=actor,
        reason=f'{locked.number} voided: {reason}'[:300],
    )
    return locked


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
