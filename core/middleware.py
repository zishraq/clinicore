"""Request-scoped activation of the current organization and its clock."""

from core import resolvers
from core.context import organization_context, organization_timezone

__all__ = ['ActiveOrganizationMiddleware']


class ActiveOrganizationMiddleware:
    """Activate the request's organization and timezone for the response.

    Leaking the contextvar across requests is a cross-tenant leak, and streaming
    responses are a known caveat — see
    docs/adr/0005-org-scoped-default-manager.md.

    The timezone is activated here, on the same lifecycle, because the two
    answer one question: which clinic is this, and therefore what time is it
    there. Splitting them is what produced the defect in
    docs/adr/0011-organization-timezone-per-request.md — the zone was stored on
    the row and never read, so every datetime rendered six hours out and
    correcting one by hand wrote it wrong.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        membership = resolvers.resolve_active_membership(request)
        organization = membership.organization if membership else None
        request.membership = membership
        request.organization = organization
        with organization_context(organization), organization_timezone(organization):
            return self.get_response(request)
