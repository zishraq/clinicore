"""How a request is mapped to an organization.

Split out of the middleware so the policy can change without touching the
activate/reset machinery, and so tests can substitute a resolver.
"""

__all__ = ['resolve_active_membership', 'resolve_active_organization']


def resolve_active_membership(request):
    """Return the Membership this request acts under, or None.

    The session only *suggests* an organization; the Membership query is the
    security boundary and the session value is never trusted on its own. A
    session naming an organization the user no longer belongs to falls back to
    their first active membership rather than keeping the stale one.
    """
    from accounts.services import ACTIVE_ORGANIZATION_SESSION_KEY, active_memberships

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None

    candidates = active_memberships(user)
    requested_id = request.session.get(ACTIVE_ORGANIZATION_SESSION_KEY)
    membership = None
    if requested_id is not None:
        membership = candidates.filter(organization_id=requested_id).first()
    if membership is None:
        membership = candidates.first()
        if membership is not None:
            request.session[ACTIVE_ORGANIZATION_SESSION_KEY] = (
                membership.organization_id
            )
    return membership


def resolve_active_organization(request):
    """Return the Organization for this request, or None."""
    membership = resolve_active_membership(request)
    return membership.organization if membership else None
