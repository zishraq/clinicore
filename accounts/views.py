"""Login, logout, and switching between organizations."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts import services
from accounts.forms import PhoneLoginForm

__all__ = ['ClinicoreLoginView', 'logout_view', 'switch_organization']


@csrf_exempt
@require_POST
def logout_view(request):
    """Sign out. POST only, and deliberately exempt from the CSRF token.

    Signing in rotates the CSRF token, so every page already open in another
    tab carries one that no longer validates. Three people share the machine at
    reception: the second to sign in left the first with a Log out button that
    answered "CSRF verification failed" — and the remedy for that state is to
    sign out, the one thing that had stopped working.

    The trade is deliberate and small. Forging this request signs somebody out:
    it reads nothing, writes nothing, leaks nothing, and the remedy is to sign
    in again. A logout that cannot be relied on is the worse failure. POST-only
    still stands, so a link, an image or a prefetch cannot trigger it, which is
    the reason Django requires POST here at all.

    Written out rather than subclassing ``LogoutView`` because that class is
    decorated with ``csrf_protect`` on ``dispatch``, so it enforces the token
    from inside no matter what the middleware is told. Four lines under our own
    control beat fighting a decorator we cannot remove.
    """
    auth_logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


class ClinicoreLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = PhoneLoginForm
    redirect_authenticated_user = True


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
