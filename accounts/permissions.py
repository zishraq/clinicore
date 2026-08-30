"""View-boundary gates: who may do a thing, and whether the thing exists here.

Two different questions live in this module and must not be confused.

- **Role checks** answer "may this member do this?" and refuse with 403.
  MVP: replace with permission layer. SPEC §6.1 wants a data-driven, per-org
  ``RolePermission`` table resolved through a custom auth backend so that
  ``user.has_perm('clinical.view_narrative')`` works. Until then these are
  plain role comparisons, deliberately concentrated here and greppable by the
  marker comment above so the swap is mechanical.
- **Capability checks** answer "does this clinic run this feature at all?" and
  refuse with 404. They are org configuration, not permission, and they will
  not be replaced by the permission layer.

Both are here because ADR 0012 puts authorisation at the view boundary and this
is the one file to read to find out what guards a view. Every gate below is a
decorator for exactly that reason: a check written inside a view body is one a
new view can forget.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404

from accounts.models import ADMIN_ROLES, CLINICAL_ROLES, Role

__all__ = [
    'billing_enabled_required',
    'capability_required',
    'clinical_access_required',
    'developer_required',
    'owner_required',
    'require_membership',
    'role_required',
]


def require_membership(request):
    """Return the request's membership or refuse the request."""
    membership = getattr(request, 'membership', None)
    if membership is None:
        raise PermissionDenied('No active organization for this user.')
    return membership


def role_required(*roles: str):
    """Refuse anyone whose role in the active organization isn't listed.

    Enforced before the view runs, so a direct URL hit is a 403 rather than a
    hidden template block (SPEC §6.1).
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            membership = require_membership(request)
            # MVP: replace with permission layer
            if membership.role not in roles:
                raise PermissionDenied(
                    f'Role {membership.role} may not access this page.'
                )
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


# Both read the named sets rather than listing roles, so adding a fourth role is
# an edit to ``accounts.models`` and nothing else. The pair used to be inlined
# here, which is how "may read clinical data" and "may treat a patient" stayed
# one fact for the whole MVP (ADR 0019).

# MVP: replace with permission layer
clinical_access_required = role_required(*CLINICAL_ROLES)

# MVP: replace with permission layer
owner_required = role_required(*ADMIN_ROLES)

#: Whoever looks after the server, which is not the clinic. The one screen
#: behind this is the backup health card: an administrator can neither fix a
#: backup that stopped nor tell whether one matters, so putting it in front of
#: them is noise they cannot act on. A single role rather than a named set,
#: because there is one question here — "does this person run the box?" — and
#: inventing ``OPERATIONS_ROLES = {DEVELOPER}`` would be a set with nothing to
#: distinguish it from the role itself (ADR 0019's rule is about facts that
#: differ, not about never naming a role).
# MVP: replace with permission layer
developer_required = role_required(Role.DEVELOPER)


def capability_required(field: str):
    """Refuse the view when the organization has this capability switched off.

    **404, not 403, and the difference is the whole point.** 403 says the thing
    exists and you may not have it, which invites the user to go and ask for
    access to a feature their clinic has deliberately not turned on. 404 says
    there is nothing here, which is true: the switch is off, so the URL is not
    part of this clinic's application.

    Hiding the nav link is presentation, never access control —
    ``templates/partials/_sidebar.html`` says so in its own comment, and a
    hidden link is one bookmark away from being reached anyway.

    Runs *before* any role check on the views it guards, so a STAFF user gets
    the same 404 as everybody else rather than a 403 that reveals the feature.
    A request with no active organization falls through untouched: there is no
    flag to read, and ``require_membership`` already refuses it.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            organization = getattr(request, 'organization', None)
            if organization is not None and not getattr(organization, field):
                raise Http404(f'{field} is off for this organization.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


#: Billing is a whole app behind one switch: a clinic that is not ready to put
#: money in the system turns it off, and nothing is deleted (A3's rule at app
#: scale). Applied to every view in ``billing/views.py``.
billing_enabled_required = capability_required('billing_enabled')
