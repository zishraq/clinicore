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
    """The branch to preselect on forms; single-branch clinics are the norm."""
    return active_branches(organization).first()


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
