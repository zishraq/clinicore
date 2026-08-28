"""Organization settings screens.

Owner-only, one screen per concern (SPEC §6.8). Three of them are the same shape
— bind a ModelForm to the active organization, save, redirect — so they share
``_settings_screen`` and one template rather than growing a copy each.

Branches are the exception and cannot use it: the form binds a *branch*, there
are several of them, and one of them may not exist yet. They get their own two
views and reuse the same field-rendering template.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import owner_required, require_membership
from organizations.forms import (
    BillingSettingsForm,
    BranchForm,
    FeatureSettingsForm,
    PrescriptionSettingsForm,
)
from organizations.models import Branch
from organizations.services import organization_branches

__all__ = [
    'billing_settings',
    'branch_create',
    'branch_list',
    'branch_update',
    'feature_settings',
    'prescription_settings',
]


def _settings_screen(request, *, form_class, heading: str, saved: str, url_name: str):
    """Bind one settings form to the active organization and save it."""
    membership = require_membership(request)
    # Bound on the method, not on ``request.POST or None``: an unticked
    # checkbox posts nothing, so a form whose only field is one arrives as an
    # empty QueryDict. That is falsy, and the usual idiom would quietly rebuild
    # the form unbound and save nothing — turning a feature off would look like
    # it worked and change no data.
    data = request.POST if request.method == 'POST' else None
    form = form_class(data, instance=membership.organization)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, saved)
        return redirect(f'organizations:{url_name}')
    return render(
        request,
        'organizations/settings_form.html',
        {'form': form, 'heading': heading},
    )


@login_required
@owner_required
def billing_settings(request):
    """The fee that prefills every new bill, changeable without a developer."""
    return _settings_screen(
        request,
        form_class=BillingSettingsForm,
        heading='Billing settings',
        saved='Billing settings saved.',
        url_name='billing_settings',
    )


@login_required
@owner_required
def feature_settings(request):
    """Which optional capabilities this clinic runs (A3)."""
    return _settings_screen(
        request,
        form_class=FeatureSettingsForm,
        heading='Features',
        saved='Feature settings saved.',
        url_name='feature_settings',
    )


@login_required
@owner_required
def prescription_settings(request):
    """What the printed prescription says, beyond the visit and the chamber."""
    return _settings_screen(
        request,
        form_class=PrescriptionSettingsForm,
        heading='Prescription',
        saved='Prescription settings saved.',
        url_name='prescription_settings',
    )


@login_required
@owner_required
def branch_list(request):
    """Every chamber, in the order it prints.

    There is no delete, and that is a decision rather than an omission:
    ``Patient.registered_branch`` and ``Encounter.branch`` are both PROTECT, so
    a delete button would be a 500 waiting for its first click. ``is_active`` is
    the off switch and it already existed.
    """
    membership = require_membership(request)
    return render(
        request,
        'organizations/branch_list.html',
        {'branches': organization_branches(membership.organization)},
    )


def _branch_screen(request, branch=None):
    """Create or edit one chamber. Both halves of the same short form."""
    membership = require_membership(request)
    organization = membership.organization
    data = request.POST if request.method == 'POST' else None
    form = BranchForm(data, instance=branch, organization=organization)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        saved.organization = organization
        saved.created_by = saved.created_by or membership.user
        saved.save()
        messages.success(request, f'{saved.name} saved.')
        return redirect('organizations:branch_list')
    return render(
        request,
        'organizations/branch_form.html',
        {'form': form, 'branch': branch},
    )


@login_required
@owner_required
def branch_create(request):
    return _branch_screen(request)


@login_required
@owner_required
def branch_update(request, pk: int):
    # Through the org-scoped manager, so another clinic's chamber is a 404
    # rather than a permission message.
    return _branch_screen(request, get_object_or_404(Branch, pk=pk))
