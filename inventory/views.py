"""Stock levels, batch drill-down, goods receipts, and manual adjustments.

The whole app is PRACTITIONER/OWNER, matching billing: in the workflow this was
built for the practitioner receives the delivery and sells off the same shelf,
with no storeroom handoff. ``clinical_access_required`` runs before every view
here, so a STAFF user hitting any of these URLs directly gets a 403 (SPEC §6.1).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.permissions import clinical_access_required, require_membership
from catalog.models import Product
from inventory import services
from inventory.forms import (
    AdjustmentForm,
    GoodsReceiptForm,
    receipt_item_formset_class,
)
from inventory.models import GoodsReceipt, StockBatch
from organizations.models import Branch

__all__ = [
    'adjustment_create',
    'batch_options',
    'product_stock',
    'receipt_create',
    'receipt_detail',
    'receipt_list',
    'receipt_row',
    'stock_list',
]

PAGE_SIZE = 25
#: Blank rows on a new delivery. One, like the visit form and the bill: three
#: empty rows read as three things to fill in, and "Add another item" is how
#: every other formset in the project grows.
RECEIPT_ROWS = 1


def _selected_branch(request):
    """The branch filter, shared by the stock pages. ``None`` means all."""
    raw = request.GET.get('branch', '')
    if not raw.isdigit():
        return None
    return Branch.objects.filter(pk=int(raw)).first()


def _branches(organization):
    return Branch.objects.for_organization(organization).filter(is_active=True)


@login_required
@clinical_access_required
def stock_list(request):
    """On-hand per product, filtered to one branch or summed across them."""
    require_membership(request)
    branch = _selected_branch(request)
    query = request.GET.get('q', '').strip()
    levels = services.stock_levels(request.organization, branch=branch, query=query)
    page = Paginator(levels, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(
        request,
        'inventory/stock_list.html',
        {
            'products': page,
            'branches': _branches(request.organization),
            'branch': branch,
            'query': query,
        },
    )


@login_required
@clinical_access_required
def product_stock(request, pk: int):
    """One product: its batches, and every movement against it."""
    require_membership(request)
    product = get_object_or_404(Product, pk=pk)
    branch = _selected_branch(request)
    movements = services.movement_history(
        request.organization, product=product, branch=branch
    )
    page = Paginator(movements, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(
        request,
        'inventory/product_stock.html',
        {
            'product': product,
            'batches': list(
                services.batches_for(
                    request.organization, product=product, branch=branch
                )
            ),
            'movements': page,
            'branches': _branches(request.organization),
            'branch': branch,
            'on_hand': services.on_hand(
                request.organization, product=product, branch=branch
            ),
            'usable': services.on_hand(
                request.organization, product=product, branch=branch, usable_only=True
            ),
        },
    )


@login_required
@clinical_access_required
def receipt_list(request):
    require_membership(request)
    receipts = (
        GoodsReceipt.objects.for_organization(request.organization)
        .select_related('branch')
        .prefetch_related('items')
    )
    page = Paginator(receipts, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'inventory/receipt_list.html', {'receipts': page})


@login_required
@clinical_access_required
def receipt_create(request):
    """Book a delivery in. The batches and their movements follow from it."""
    membership = require_membership(request)
    organization = request.organization
    data = request.POST or None
    form = GoodsReceiptForm(data, organization=organization)
    formset_class = receipt_item_formset_class(extra=RECEIPT_ROWS)
    item_formset = formset_class(data, prefix='items', organization=organization)

    if request.method == 'POST':
        if form.is_valid() and item_formset.is_valid():
            try:
                receipt = services.receive_stock(
                    organization,
                    branch=form.cleaned_data['branch'],
                    actor=membership.user,
                    lines=item_formset.lines,
                    supplier=form.cleaned_data['supplier'],
                    reference=form.cleaned_data['reference'],
                    received_at=form.cleaned_data['received_at'],
                    notes=form.cleaned_data['notes'],
                )
            except services.InventoryError as error:
                messages.error(request, str(error))
            else:
                label = organization.terms['goods_receipt']
                messages.success(request, f'{label} {receipt.number} recorded.')
                return redirect('inventory:receipt_detail', pk=receipt.pk)
    else:
        form.initial['received_at'] = timezone.localtime()

    return render(
        request,
        'inventory/receipt_form.html',
        {'form': form, 'item_formset': item_formset},
    )


@login_required
@clinical_access_required
def receipt_row(request):
    """One blank formset row for the HTMX 'add line' button.

    The formset's management form counts rows, so the caller sends its current
    TOTAL_FORMS as the index to name the inputs with; the button increments the
    counter afterwards. Same contract as the bill and prescription rows.
    """
    require_membership(request)
    raw = request.GET.get('items-TOTAL_FORMS', '0')
    index = int(raw) if raw.isdigit() else 0
    formset = receipt_item_formset_class(extra=1)(
        prefix='items', organization=request.organization
    )
    html = render_to_string(
        'inventory/_receipt_row.html', {'form': formset.empty_form}, request=request
    )
    return HttpResponse(html.replace('__prefix__', str(index)))


@login_required
@clinical_access_required
def receipt_detail(request, pk: int):
    require_membership(request)
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related('branch', 'created_by'), pk=pk
    )
    return render(
        request,
        'inventory/receipt_detail.html',
        {
            'receipt': receipt,
            'items': list(receipt.items.select_related('product', 'batch')),
        },
    )


def _line_product(request):
    """The product on the bill line asking for batches.

    Read off whichever key ends in ``-product``, because htmx sends the row's
    hidden input under its formset name — ``items-3-product``. Same trick, and
    the same reason, as ``catalog.views._typed_query``.
    """
    for key, value in request.GET.items():
        if key.endswith('-product') and value.isdigit():
            return Product.objects.filter(pk=int(value)).first()
    return None


def _line_branch(request):
    """Which shelf the line comes off, mirroring the invoice form's own rule.

    The bill's branch select when the organization has more than one; the only
    active branch when it has one, because then the form does not ask.
    """
    branch = _selected_branch(request)
    if branch is not None:
        return branch
    branches = _branches(request.organization)
    return branches.first() if branches.count() == 1 else None


@login_required
@clinical_access_required
def batch_options(request):
    """``<option>``s for the batch override on one bill line.

    Markup rather than JSON, like the catalog suggestions: it drops straight
    into the row's select. Everything with stock left is offered, expired lots
    included and labelled — a practitioner has to be able to see the box that
    is physically there and be told why it cannot go out, which the service
    does on submit (``inventory.services.consume_from_batch``).
    """
    require_membership(request)
    product = _line_product(request)
    branch = _line_branch(request)
    batches = []
    if product is not None and product.is_stock_tracked and branch is not None:
        batches = list(
            services.sellable_batches(
                request.organization, product=product, branch=branch
            )
        )
    return render(
        request,
        'inventory/_batch_options.html',
        {'batches': batches, 'selected': request.GET.get('selected', '')},
    )


@login_required
@clinical_access_required
def adjustment_create(request, pk: int):
    """Correct one batch against the shelf, with a reason and an actor."""
    membership = require_membership(request)
    batch = get_object_or_404(
        StockBatch.objects.select_related('product', 'branch').with_on_hand(), pk=pk
    )
    form = AdjustmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.record_adjustment(
                request.organization,
                batch=batch,
                actor=membership.user,
                quantity=form.cleaned_data['quantity'],
                reason=form.cleaned_data['reason'],
            )
        except services.InventoryError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f'{batch} adjusted.')
            return redirect('inventory:product_stock', pk=batch.product_id)

    return render(
        request, 'inventory/adjustment_form.html', {'form': form, 'batch': batch}
    )
