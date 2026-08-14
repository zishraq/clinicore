"""Membership lookups, the session handshake, and team administration.

Authorisation is at the view boundary, not here
(docs/adr/0012-authorisation-at-the-view-boundary.md): nothing below checks who
is calling it.
"""

from accounts.models import Membership, Role, User

__all__ = [
    'ACTIVE_ORGANIZATION_SESSION_KEY',
    'active_memberships',
    'add_member',
    'create_member',
    'organization_members',
    'set_active_organization',
    'set_membership_active',
    'set_temporary_password',
]

#: Session key holding the organization the user last worked in. Never trusted
#: on its own — resolve_active_membership re-checks Membership on every request.
ACTIVE_ORGANIZATION_SESSION_KEY = 'active_organization_id'


def active_memberships(user: User):
    """Every organization this user may currently work in."""
    return (
        Membership.objects.select_related('organization')
        .filter(user=user, is_active=True, organization__is_active=True)
        .order_by('organization__name')
    )


def set_active_organization(request, membership: Membership) -> None:
    """Remember the chosen organization for subsequent requests."""
    request.session[ACTIVE_ORGANIZATION_SESSION_KEY] = membership.organization_id


def organization_members(organization):
    """Every membership of one organization, active or not.

    The organization is an argument rather than an implicit scope: ``Membership``
    is not an ``OrgOwnedModel`` (the middleware reads it to *establish* the
    active organization), so it has a plain manager and no automatic filter.
    Every caller therefore has to pass the boundary in by hand.
    """
    return Membership.objects.select_related('user').filter(organization=organization)


def add_member(
    *,
    organization,
    phone: str,
    full_name: str,
    role: str,
    password: str,
    email: str = '',
) -> Membership:
    """Register a new person and grant them access to one organization.

    The phone must be free — ``MemberCreateForm`` checks it and reports a
    collision as a field error. Attaching an *existing* account to a second
    organization is deliberately not reachable from the app: the administrator
    typing the number would learn the name behind it, which is a tenant leak
    dressed up as a convenience.

    The password is temporary by construction — somebody other than the account
    holder chose it and read it out — so the flag that forces a change is set
    here rather than left to the caller.
    """
    user = User.objects.create_user(
        phone=phone,
        password=password,
        full_name=full_name,
        email=email,
        must_change_password=True,
    )
    return Membership.objects.create(
        user=user, organization=organization, role=role or Role.STAFF
    )


def set_temporary_password(user: User, password: str) -> None:
    """Give somebody who is locked out a password to be told, then replaced."""
    user.set_password(password)
    user.must_change_password = True
    user.save(update_fields=['password', 'must_change_password'])


def set_membership_active(membership: Membership, *, active: bool) -> None:
    """Grant or withdraw access. Never a delete.

    Visits, bills and stock movements point at the user as ``created_by`` and
    ``actor``, and that attribution has to outlive the person leaving.
    """
    membership.is_active = active
    membership.save(update_fields=['is_active', 'updated_at'])


def create_member(
    *,
    organization,
    phone: str,
    full_name: str,
    role: str,
    password: str,
    email: str = '',
) -> Membership:
    """Idempotent seeding path for ``bootstrap_demo``, not the app's own.

    ``add_member`` is what the team screen calls: this one reuses an existing
    account and membership rather than failing, which is what re-running the
    demo loader needs and what user administration must never do.
    """
    user, created = User.objects.get_or_create(
        phone=phone, defaults={'full_name': full_name, 'email': email}
    )
    if created:
        user.set_password(password)
        user.save(update_fields=['password'])
    membership, _ = Membership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={'role': role or Role.STAFF},
    )
    return membership
