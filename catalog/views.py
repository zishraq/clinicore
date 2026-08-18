"""Catalog autocomplete, quick-add, and settings CRUD.

Catalog maintenance is a PRACTITIONER/OWNER job (SPEC §6.1), so every view here
sits behind the same role check as the clinical app.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import clinical_access_required, require_membership
from catalog import services
from catalog.forms import AdviceTemplateForm, ProductForm
from catalog.models import AdviceTemplate, Product
from clinical.models import ItemType

__all__ = [
    'advice_create',
    'advice_list',
    'advice_toggle_active',
    'advice_update',
    'product_create',
    'product_list',
    'product_toggle_active',
    'product_update',
    'quick_add',
    'suggestions',
]

PAGE_SIZE = 25


def _typed_query(request) -> str:
    """The text the practitioner typed.

    ``q`` when a caller sets it explicitly (quick-add, tests, curl). Otherwise
    the search box's own value, which htmx already includes under its formset
    name — ``items-3-display_name``.

    Renaming it to ``q`` with ``hx-vals`` does not work: ``event`` is gone by the
    time a ``delay:``ed trigger fires (it throws), and ``this`` is not bound to
    the element inside the hx-vals eval either — verified against htmx 2.0.4,
    where it silently sends the string "undefined". Reading the parameter here
    keeps the row working with no per-row JavaScript at all.
    """
    if request.GET.get('q'):
        return request.GET['q'].strip()
    for key, value in request.GET.items():
        if key.endswith('-display_name'):
            return value.strip()
    return ''


@login_required
@clinical_access_required
def suggestions(request):
    """One dropdown over both catalogs, grouped and labelled by type.

    Returns an HTML fragment, not JSON: the markup carries the defaults for each
    entry as data attributes, so selecting a row needs no second request.

    ``?only=products`` is the billing caller: it searches substances only, and
    without the quick-add offer. Quick-add exists because a missing catalog
    entry blocks a prescription mid-consultation; a bill line can simply be
    typed, so there is nothing to unblock.

    A clinic with ``advice_enabled`` off never sees the advice half (A3). The
    rows stay, and stay readable where they were already prescribed — they just
    stop being offered.
    """
    require_membership(request)
    products_only = request.GET.get('only') == 'products'
    advice_on = request.organization.advice_enabled
    context = services.search_catalogs(
        request.organization,
        _typed_query(request),
        include_advice=not products_only and advice_on,
    )
    context['allow_quick_add'] = not products_only
    context['allow_advice'] = advice_on
    return render(request, 'catalog/_suggestions.html', context)


@login_required
@clinical_access_required
@require_POST
def quick_add(request):
    """Create a catalog entry from typed text and hand it straight back.

    This is what keeps the catalogs from going stale: the alternative is
    abandoning the encounter to go and maintain a list, which nobody does
    mid-consultation.
    """
    membership = require_membership(request)
    text = request.POST.get('q', '').strip()
    item_type = request.POST.get('item_type', ItemType.MEDICATION)
    if not text:
        return render(
            request,
            'catalog/_suggestions.html',
            {
                'query': '',
                'products': [],
                'advice': [],
                'has_results': False,
                'allow_quick_add': True,
            },
        )

    # The offer is not rendered when advice is off, so this is the direct-POST
    # case. A clinic that has the feature off should not acquire advice rows.
    if item_type == ItemType.ADVICE and not request.organization.advice_enabled:
        raise PermissionDenied('Advice is not enabled for this organization.')

    if item_type == ItemType.ADVICE:
        entry = services.quick_add_advice(
            request.organization, actor=membership.user, text=text
        )
    else:
        entry = services.quick_add_product(
            request.organization, actor=membership.user, name=text
        )
    return render(
        request,
        'catalog/_quick_added.html',
        {'entry': entry, 'item_type': item_type},
    )


def _page(request, queryset):
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))


@login_required
@clinical_access_required
def product_list(request):
    require_membership(request)
    query = request.GET.get('q', '').strip()
    products = Product.objects.all()
    if query:
        products = services.search_products(request.organization, query, limit=None)
    return render(
        request,
        'catalog/product_list.html',
        {'products': _page(request, products), 'query': query},
    )


@login_required
@clinical_access_required
def product_create(request):
    membership = require_membership(request)
    form = ProductForm(request.POST or None, organization=request.organization)
    if request.method == 'POST' and form.is_valid():
        product = form.save(commit=False)
        product.organization = request.organization
        product.created_by = membership.user
        product.save()
        messages.success(request, f'{product.name} added to the medicine catalog.')
        return redirect('catalog:product_list')
    return render(request, 'catalog/product_form.html', {'form': form})


@login_required
@clinical_access_required
def product_update(request, pk: int):
    require_membership(request)
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(
        request.POST or None, instance=product, organization=request.organization
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Medicine updated.')
        return redirect('catalog:product_list')
    return render(
        request, 'catalog/product_form.html', {'form': form, 'product': product}
    )


@login_required
@clinical_access_required
@require_POST
def product_toggle_active(request, pk: int):
    """Deactivate, never delete — prescriptions reference these rows."""
    require_membership(request)
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request,
        f'{product.name} {"reactivated" if product.is_active else "deactivated"}.',
    )
    return redirect('catalog:product_list')


@login_required
@clinical_access_required
def advice_list(request):
    require_membership(request)
    query = request.GET.get('q', '').strip()
    advice = AdviceTemplate.objects.all()
    if query:
        advice = services.search_advice(request.organization, query, limit=None)
    return render(
        request,
        'catalog/advice_list.html',
        {'advice_items': _page(request, advice), 'query': query},
    )


@login_required
@clinical_access_required
def advice_create(request):
    membership = require_membership(request)
    form = AdviceTemplateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        advice = form.save(commit=False)
        advice.organization = request.organization
        advice.created_by = membership.user
        advice.save()
        messages.success(request, 'Advice added to the catalog.')
        return redirect('catalog:advice_list')
    return render(request, 'catalog/advice_form.html', {'form': form})


@login_required
@clinical_access_required
def advice_update(request, pk: int):
    require_membership(request)
    advice = get_object_or_404(AdviceTemplate, pk=pk)
    form = AdviceTemplateForm(request.POST or None, instance=advice)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Advice updated.')
        return redirect('catalog:advice_list')
    return render(request, 'catalog/advice_form.html', {'form': form, 'advice': advice})


@login_required
@clinical_access_required
@require_POST
def advice_toggle_active(request, pk: int):
    """Deactivate, never delete — prescriptions reference these rows."""
    require_membership(request)
    advice = get_object_or_404(AdviceTemplate, pk=pk)
    advice.is_active = not advice.is_active
    advice.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request, f'Advice {"reactivated" if advice.is_active else "deactivated"}.'
    )
    return redirect('catalog:advice_list')
