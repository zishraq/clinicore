"""Request-scoped activation of the current organization."""

from core import resolvers
from core.context import organization_context

__all__ = ['ActiveOrganizationMiddleware']


class ActiveOrganizationMiddleware:
    """Activate the request's organization for the duration of the response.

    Leaking the contextvar across requests is a cross-tenant leak, and streaming
    responses are a known caveat — see
    docs/adr/0005-org-scoped-default-manager.md.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        membership = resolvers.resolve_active_membership(request)
        organization = membership.organization if membership else None
        request.membership = membership
        request.organization = organization
        with organization_context(organization):
            return self.get_response(request)
