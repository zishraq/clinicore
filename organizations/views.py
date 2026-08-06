"""Organization settings screens.

Owner-only, one screen per concern (SPEC §6.8). Both are the same shape — bind a
ModelForm to the active organization, save, redirect — so they share
``_settings_screen`` and one template rather than growing a copy each.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.permissions import owner_required, require_membership
from organizations.forms import BillingSettingsForm, FeatureSettingsForm

__all__ = ['billing_settings', 'feature_settings']


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
