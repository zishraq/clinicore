class ActiveOrganizationRequired(RuntimeError):
    """Raised when an org-scoped query runs with no active organization.

    Deliberately loud rather than silently empty; see
    docs/adr/0005-org-scoped-default-manager.md.
    """


class CannotCreateOrganization(ValueError):
    """Raised when the facts given for a new organization do not make one.

    A bad time zone, a name that slugifies to nothing, a slug already taken.
    The management commands that stand a clinic up turn this into a
    ``CommandError`` sentence; nothing in the app catches it.
    """
