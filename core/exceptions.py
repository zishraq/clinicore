class ActiveOrganizationRequired(RuntimeError):
    """Raised when an org-scoped query runs with no active organization.

    Deliberately loud rather than silently empty; see
    docs/adr/0005-org-scoped-default-manager.md.
    """
