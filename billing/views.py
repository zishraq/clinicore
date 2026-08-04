"""Invoice list, create, detail, payments, voiding, and the printed receipt.

The whole app is PRACTITIONER/OWNER: in the workflow this was built for the
practitioner raises the bill and takes the money, with no reception handoff, so
``clinical_access_required`` runs before every view here and a STAFF user
hitting any of these URLs directly gets a 403 (SPEC §6.1).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.permissions import clinical_access_required, require_membership
from billing import services
from billing.forms import (
    InvoiceForm,
    InvoiceItemFormSet,
    PaymentForm,
    VoidForm,
    invoice_item_formset_class,
)
from billing.models import Invoice, Payment, PaymentStatus

# The receipt prints on the same paper as the prescription, so it takes the
# same sizes rather than declaring a second, drifting copy of them.
from clinical.models import Encounter, PrintSize
from patients.models import Patient

__all__ = [
    'invoice_create',
    'invoice_detail',
    'invoice_line_row',
    'invoice_list',
    'invoice_update',
    'invoice_void',
    'payment_create',
    'payment_void',
    'receipt_print',
]

PAGE_SIZE = 25


def _invoice(pk: int) -> Invoice:
    """One invoice with its totals annotated, or 404 in another tenant."""
    return get_object_or_404(
        Invoice.objects.select_related('patient', 'encounter').with_totals(), pk=pk
    )


@login_required
@clinical_access_required
def invoice_list(request):
    require_membership(request)
    status = request.GET.get('status', '')
    date_from = parse_date(request.GET.get('from', '') or '')
    date_to = parse_date(request.GET.get('to', '') or '')
    query = request.GET.get('q', '').strip()
    invoices = services.filter_invoices(
        request.organization,
        status=status,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    page = Paginator(invoices, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(
        request,
        'billing/invoice_list.html',
        {
            'invoices': page,
            'status': status,
            'statuses': PaymentStatus.choices,
            'date_from': request.GET.get('from', ''),
            'date_to': request.GET.get('to', ''),
            'query': query,
        },
    )


def _prefill_lines(organization, encounter) -> list[dict]:
    """Opening lines for a new bill: the consultation fee, when there was one."""
    if encounter is None:
        return []
    return [services.consultation_line_defaults(organization)]


@login_required
@clinical_access_required
def invoice_create(request):
    """New bill, either from a visit (``?encounter=``) or standalone."""
    membership = require_membership(request)
    organization = request.organization
    encounter = None
    if request.GET.get('encounter'):
        encounter = Encounter.objects.filter(pk=request.GET['encounter']).first()

    data = request.POST or None
    form = InvoiceForm(data, organization=organization)
    if request.method == 'POST':
        item_formset = InvoiceItemFormSet(data, organization=organization)
        if form.is_valid() and item_formset.is_valid():
            invoice = services.create_invoice(
                organization,
                actor=membership.user,
                form=form,
                item_formset=item_formset,
            )
            label = organization.terms['invoice']
            messages.success(request, f'{label} {invoice.number} created.')
            return redirect('billing:invoice_detail', pk=invoice.pk)
    else:
        lines = _prefill_lines(organization, encounter)
        # One blank row after whatever is prefilled, so a product can be added
        # without reaching for the add-row button first.
        item_formset = invoice_item_formset_class(extra=len(lines) + 1)(
            initial=lines, organization=organization
        )
        if encounter is not None:
            form.initial['encounter'] = encounter.pk
            form.initial['patient'] = encounter.patient_id
        elif request.GET.get('patient'):
            patient = Patient.objects.filter(pk=request.GET['patient']).first()
            if patient is not None:
                form.initial['patient'] = patient.pk

    return render(
        request,
        'billing/invoice_form.html',
        {
            'form': form,
            'item_formset': item_formset,
            'encounter': encounter,
            'is_create': True,
        },
    )


@login_required
@clinical_access_required
def invoice_update(request, pk: int):
    """Edit a bill that has not been paid against; the service enforces that."""
    membership = require_membership(request)
    invoice = _invoice(pk)
    if not invoice.is_editable:
        messages.error(
            request,
            'This bill has payments recorded against it and can no longer be '
            'edited. Void a payment first, or void the bill and issue a new one.',
        )
        return redirect('billing:invoice_detail', pk=invoice.pk)

    data = request.POST or None
    form = InvoiceForm(data, instance=invoice, organization=request.organization)
    item_formset = InvoiceItemFormSet(
        data, instance=invoice, organization=request.organization
    )
    if request.method == 'POST' and form.is_valid() and item_formset.is_valid():
        try:
            services.update_invoice(
                request.organization,
                actor=membership.user,
                invoice=invoice,
                form=form,
                item_formset=item_formset,
            )
        except services.BillingError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f'{invoice.number} updated.')
        return redirect('billing:invoice_detail', pk=invoice.pk)

    return render(
        request,
        'billing/invoice_form.html',
        {
            'form': form,
            'item_formset': item_formset,
            'invoice': invoice,
            'is_create': False,
        },
    )


@login_required
@clinical_access_required
def invoice_detail(request, pk: int):
    require_membership(request)
    invoice = _invoice(pk)
    return render(
        request,
        'billing/invoice_detail.html',
        {
            'invoice': invoice,
            'items': list(invoice.items.all()),
            'payments': list(
                invoice.payments.select_related('received_by', 'voided_by')
            ),
            'payment_form': PaymentForm(balance=invoice.balance),
            'void_form': VoidForm(),
        },
    )


@login_required
@clinical_access_required
@require_POST
def payment_create(request, pk: int):
    """Record money received. Overpayment is refused, not silently accepted."""
    membership = require_membership(request)
    invoice = _invoice(pk)
    form = PaymentForm(request.POST, balance=invoice.balance)
    if form.is_valid():
        try:
            payment = services.record_payment(
                request.organization,
                invoice=invoice,
                actor=membership.user,
                amount=form.cleaned_data['amount'],
                method=form.cleaned_data['method'],
                note=form.cleaned_data['note'],
            )
        except services.BillingError as error:
            messages.error(request, str(error))
        else:
            # Re-read the totals: what the patient still owes is the one thing
            # they will ask about at the counter.
            balance = _invoice(pk).balance
            label = request.organization.terms['payment'].lower()
            messages.success(
                request,
                f'{invoice.currency} {payment.amount} {label} recorded. '
                f'Balance: {invoice.currency} {balance}.',
            )
    else:
        messages.error(request, 'Enter a valid amount.')
    return redirect('billing:invoice_detail', pk=pk)


@login_required
@clinical_access_required
@require_POST
def payment_void(request, pk: int, payment_pk: int):
    """Reverse a payment recorded in error, with a reason and an actor."""
    membership = require_membership(request)
    payment = get_object_or_404(Payment, pk=payment_pk, invoice_id=pk)
    form = VoidForm(request.POST)
    if form.is_valid():
        try:
            services.void_payment(
                payment, actor=membership.user, reason=form.cleaned_data['reason']
            )
        except services.BillingError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f'{payment.amount} payment voided.')
    else:
        messages.error(request, 'Voiding a payment requires a reason.')
    return redirect('billing:invoice_detail', pk=pk)


@login_required
@clinical_access_required
@require_POST
def invoice_void(request, pk: int):
    membership = require_membership(request)
    invoice = _invoice(pk)
    form = VoidForm(request.POST)
    if form.is_valid():
        try:
            services.void_invoice(
                invoice, actor=membership.user, reason=form.cleaned_data['reason']
            )
        except services.BillingError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f'{invoice.number} voided.')
    else:
        messages.error(request, 'Voiding a bill requires a reason.')
    return redirect('billing:invoice_detail', pk=pk)


@login_required
@clinical_access_required
def invoice_line_row(request):
    """One blank formset row for the HTMX 'add line' button.

    The formset's management form counts rows, so the caller sends its current
    TOTAL_FORMS as the index to name the inputs with; the button increments the
    counter afterwards. Same contract as the prescription row endpoint.
    """
    require_membership(request)
    raw = request.GET.get('items-TOTAL_FORMS', '0')
    index = int(raw) if raw.isdigit() else 0
    formset = InvoiceItemFormSet(organization=request.organization)
    html = render_to_string(
        'billing/_line_row.html', {'form': formset.empty_form}, request=request
    )
    return HttpResponse(html.replace('__prefix__', str(index)))


@login_required
@clinical_access_required
def receipt_print(request, pk: int):
    """Chrome-free receipt, same paper geometry as the prescription."""
    require_membership(request)
    invoice = _invoice(pk)
    size = request.GET.get('size', PrintSize.A5).upper()
    if size not in PrintSize.values:
        size = PrintSize.A5
    return render(
        request,
        'print/receipt.html',
        {
            'invoice': invoice,
            'items': list(invoice.items.all()),
            'payments': list(
                invoice.payments.filter(voided_at__isnull=True).select_related(
                    'received_by'
                )
            ),
            'page_size': size,
            # Interpolated into CSS, so it comes from the validated accessor.
            'letterhead_color': request.organization.primary_color,
            'letterhead': request.organization.letterhead,
            'now': timezone.localtime(),
        },
    )
