"""Organization and branch operations.

Per docs/adr/0005, every function takes ``organization`` explicitly and never
reads the ambient contextvar.
"""

from organizations.models import Branch, Organization

__all__ = [
    'active_branches',
    'default_branch',
    'organization_branches',
    'prescription_branches',
]


def active_branches(organization: Organization):
    """Branches a user may pick from, newest config first by name."""
    return Branch.all_objects.filter(organization=organization, is_active=True)


def default_branch(organization: Organization) -> Branch | None:
    """The branch to preselect on a new visit or appointment.

    The chamber the clinic marked, then the lowest ``print_order``, and only
    then the name. **Name is deliberately last.** It used to be the whole rule,
    and the day two Bengali-named chambers were added the default moved to one
    that opens on the second Friday of the month — the collation decided where
    visits were recorded, and nothing on any screen said so. Renaming a chamber
    must not be able to do that either.

    Inactive chambers are excluded, so deactivating the marked one falls back
    rather than preselecting a chamber the clinic has closed.
    """
    return (
        active_branches(organization)
        .order_by('-is_default', 'print_order', 'name')
        .first()
    )


def organization_branches(organization: Organization):
    """Every branch, active or not, in the order they print.

    The settings screen's list. Inactive ones are included because ``is_active``
    is the off switch — there is no delete, ``Patient.registered_branch`` being
    PROTECT — so a deactivated chamber has to stay reachable to be turned back
    on.
    """
    return Branch.all_objects.filter(organization=organization).order_by(
        'print_order', 'name'
    )


def prescription_branches(organization: Organization, *, exclude_pk=None):
    """The chambers listed in a printed prescription's footer.

    ``exclude_pk`` drops the branch the visit happened at: that one is already
    named in full at the top of the sheet, and printing it twice is noise. The
    clinic's own design is the evidence — three chambers, one in the header and
    two in the footer.
    """
    branches = Branch.all_objects.filter(
        organization=organization, is_active=True, show_on_prescription=True
    )
    if exclude_pk is not None:
        branches = branches.exclude(pk=exclude_pk)
    return branches.order_by('print_order', 'name')
