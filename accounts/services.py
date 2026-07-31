"""Membership lookups and the session handshake that selects an organization."""

from accounts.models import Membership, Role, User

__all__ = [
    'ACTIVE_ORGANIZATION_SESSION_KEY',
    'active_memberships',
    'create_member',
    'set_active_organization',
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


def create_member(
    *,
    organization,
    phone: str,
    full_name: str,
    role: str,
    password: str,
    email: str = '',
) -> Membership:
    """Create a user and their membership. Owners add staff this way, not admin."""
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
