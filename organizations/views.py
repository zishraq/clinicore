"""Organization settings screens.

Owner-only, and only the billing settings so far: the fee that prefills every
new bill has to be changeable without a developer, or onboarding a second clinic
means editing code (SPEC §6.8).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts.permissions import owner_required, require_membership
from organizations.forms import BillingSettingsForm

__all__ = ['billing_settings']


@login_required
@owner_required
def billing_settings(request):
    membership = require_membership(request)
    form = BillingSettingsForm(request.POST or None, instance=membership.organization)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Billing settings saved.')
        return redirect('organizations:billing_settings')
    return render(request, 'organizations/billing_settings.html', {'form': form})
