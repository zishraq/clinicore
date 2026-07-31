"""Organization and branch operations.

Per docs/adr/0005, every function takes ``organization`` explicitly and never
reads the ambient contextvar.
"""

from organizations.models import Branch, Organization

__all__ = ['active_branches', 'default_branch']


def active_branches(organization: Organization):
    """Branches a user may pick from, newest config first by name."""
    return Branch.all_objects.filter(organization=organization, is_active=True)


def default_branch(organization: Organization) -> Branch | None:
    """The branch to preselect on forms; single-branch clinics are the norm."""
    return active_branches(organization).first()
