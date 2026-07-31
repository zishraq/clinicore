"""Login, logout, and switching between organizations."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect

from accounts import services
from accounts.forms import PhoneLoginForm

__all__ = ['ClinicoreLoginView', 'ClinicoreLogoutView', 'switch_organization']


class ClinicoreLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = PhoneLoginForm
    redirect_authenticated_user = True


class ClinicoreLogoutView(LogoutView):
    """POST only, which is Django's default and what the topbar form sends."""


@login_required
def switch_organization(request, organization_id: int):
    """Change the active organization, if the user actually belongs to it."""
    membership = (
        services.active_memberships(request.user)
        .filter(organization_id=organization_id)
        .first()
    )
    if membership is None:
        messages.error(request, 'You do not have access to that organization.')
        return redirect('core:dashboard')
    services.set_active_organization(request, membership)
    messages.success(request, f'Switched to {membership.organization.name}.')
    return redirect('core:dashboard')
