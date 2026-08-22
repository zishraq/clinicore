"""Login, logout, the team screen, and the account holder's own settings.

Every team view is administrator-only and every one of them scopes its lookup to
``request.organization`` by hand. ``Membership`` is not an ``OrgOwnedModel`` —
the middleware reads it to *establish* the active organization, so it carries a
plain manager and no automatic filter (docs/MVP-NOTES.md). Forgetting the filter
here would be a cross-tenant leak rather than an empty page, which is why
``accounts/tests/test_team.py`` asserts it directly.

Authorisation is at the view boundary by decision, not oversight — see
docs/adr/0012-authorisation-at-the-view-boundary.md.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts import services
from accounts.forms import (
    MemberCreateForm,
    MemberUpdateForm,
    PhoneLoginForm,
    ProfileForm,
    TemporaryPasswordForm,
)
from accounts.permissions import owner_required, require_membership

__all__ = [
    'ClinicoreLoginView',
    'logout_view',
    'member_create',
    'member_list',
    'member_reset_password',
    'member_toggle_active',
    'member_update',
    'password_change',
    'profile',
    'switch_organization',
]


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


# --- The team screen ------------------------------------------------------


def _member_or_404(request, pk: int):
    """One membership of the *active* organization, or nothing.

    The organization filter is the tenant boundary. Without it the pk is a
    global identifier and an administrator could reach — and edit — somebody at
    another clinic on the same deployment.
    """
    return get_object_or_404(services.organization_members(request.organization), pk=pk)


@login_required
@owner_required
def member_list(request):
    """Everyone who can sign in here, active or not. One list, no filters.

    Deactivated people stay on it rather than moving to a second screen: a
    clinic of five needs to see that the receptionist who left is off, and a
    list you have to switch views to check is one nobody checks.
    """
    require_membership(request)
    return render(
        request,
        'accounts/member_list.html',
        {'members': services.organization_members(request.organization)},
    )


@login_required
@owner_required
def member_create(request):
    """Register somebody and give them a password to be told and replaced."""
    require_membership(request)
    form = MemberCreateForm(request.POST or None, terms=request.organization.terms)
    if request.method == 'POST' and form.is_valid():
        member = services.add_member(
            organization=request.organization,
            phone=form.cleaned_data['phone'],
            full_name=form.cleaned_data['full_name'],
            email=form.cleaned_data['email'],
            role=form.cleaned_data['role'],
            password=form.cleaned_data['password'],
        )
        messages.success(
            request,
            f'{member.user.full_name} can now sign in with {member.user.phone}. '
            'Give them the password you just set.',
        )
        return redirect('accounts:member_list')
    return render(request, 'accounts/member_form.html', {'form': form})


@login_required
@owner_required
def member_update(request, pk: int):
    """Edit somebody's name, phone, email, and role."""
    require_membership(request)
    member = _member_or_404(request, pk)
    form = MemberUpdateForm(
        request.POST or None,
        instance=member.user,
        initial={'role': member.role},
        terms=request.organization.terms,
        editing_self=member.user_id == request.user.pk,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        # ``role`` lives on the membership, not the user. When an administrator
        # is editing their own row the form has already narrowed the choices to
        # the roles that keep administering, so this can only ever be one of
        # those (ADR 0019).
        member.role = form.cleaned_data['role']
        member.save(update_fields=['role', 'updated_at'])
        messages.success(request, f'{member.user.full_name} updated.')
        return redirect('accounts:member_list')
    return render(
        request, 'accounts/member_form.html', {'form': form, 'member': member}
    )


@login_required
@owner_required
@require_POST
def member_toggle_active(request, pk: int):
    """Withdraw or restore access. Never a delete.

    Visits, bills and stock movements carry this user as ``created_by`` and
    ``actor``; deleting the row would either destroy that attribution or refuse
    on a PROTECT, and both are worse than an inactive account.

    An administrator cannot switch themselves off — an organization whose only
    administrator has no access is unrecoverable without a shell. See
    ``MemberUpdateForm`` for why guarding the self case is enough.
    """
    require_membership(request)
    member = _member_or_404(request, pk)
    if member.user_id == request.user.pk and member.is_active:
        messages.error(
            request,
            'You cannot remove your own access. Another administrator can do it.',
        )
        return redirect('accounts:member_list')

    services.set_membership_active(member, active=not member.is_active)
    verb = 'can sign in again' if member.is_active else 'can no longer sign in'
    messages.success(request, f'{member.user.full_name} {verb}.')
    return redirect('accounts:member_list')


@login_required
@owner_required
def member_reset_password(request, pk: int):
    """Set a temporary password for somebody who has forgotten theirs.

    The whole password-recovery story, deliberately (docs/adr/0013). The person
    is standing in the building; the administrator types a password, says it out
    loud, and the flag set here makes them replace it on the next request.
    """
    require_membership(request)
    member = _member_or_404(request, pk)
    form = TemporaryPasswordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        services.set_temporary_password(member.user, form.cleaned_data['password'])
        messages.success(
            request,
            f'Temporary password set for {member.user.full_name}. Tell them what '
            'it is — they will have to choose a new one when they sign in.',
        )
        return redirect('accounts:member_list')
    return render(
        request, 'accounts/member_password.html', {'form': form, 'member': member}
    )


# --- Your own account -----------------------------------------------------


@login_required
def profile(request):
    """Your own name and email. Any role, no membership needed.

    Deliberately not behind ``require_membership``: somebody whose access was
    withdrawn, or who is signed in before an administrator adds them, can still
    reach their own account.
    """
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Your details are saved.')
        return redirect('accounts:profile')
    return render(request, 'accounts/profile.html', {'form': form})


@login_required
def password_change(request):
    """Change your own password.

    ``update_session_auth_hash`` is not optional. Saving a password rotates the
    hash the session is keyed on, so without it the very next request signs you
    out — you would change your password and land back on the login screen,
    which reads as the change having failed.
    """
    form = PasswordChangeForm(request.user, request.POST or None)
    forced = request.user.must_change_password
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        if user.must_change_password:
            user.must_change_password = False
            user.save(update_fields=['must_change_password'])
        update_session_auth_hash(request, user)
        messages.success(request, 'Your password has been changed.')
        return redirect('core:dashboard')
    _plain_password_form(form)
    return render(
        request, 'accounts/password_change.html', {'form': form, 'forced': forced}
    )


#: Django's own wording for ``PasswordChangeForm``, in the clinic's words.
#: "Old password" and "New password confirmation" are accurate and stiff; these
#: are three instructions somebody reads once and follows.
_PASSWORD_LABELS = {
    'old_password': 'Your current password',
    'new_password1': 'New password',
    'new_password2': 'Type the new password again',
}


def _plain_password_form(form) -> None:
    """Relabel and style Django's form, which builds its own widgets.

    Not a ``PasswordChangeForm`` subclass: the fields are declared in Django's
    class body and half the point of using it is that the password rules,
    the mismatch check and the old-password check stay Django's to maintain.
    """
    for name, field in form.fields.items():
        field.label = _PASSWORD_LABELS.get(name, field.label)
        field.widget.attrs.setdefault('class', 'input input-bordered w-full')
    # "Enter the same password as before, for verification" restates the label.
    form.fields['new_password2'].help_text = ''
