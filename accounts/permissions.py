"""Role checks for views.

MVP: replace with permission layer. SPEC §6.1 wants a data-driven, per-org
``RolePermission`` table resolved through a custom auth backend so that
``user.has_perm('clinical.view_narrative')`` works. Until then these are plain
role comparisons, deliberately concentrated here and greppable by the marker
comment above so the swap is mechanical.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

from accounts.models import ADMIN_ROLES, CLINICAL_ROLES

__all__ = [
    'clinical_access_required',
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
