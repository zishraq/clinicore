"""Shared fixtures.

Every fixture that touches an org-scoped model opens an explicit
``organization_context`` — the contextvar is never set implicitly outside a
request. See docs/adr/0005-org-scoped-default-manager.md.
"""

import pytest

from accounts.models import Membership, Role, User
from core.context import get_active_organization_id, organization_context
from organizations.models import Branch, Organization


@pytest.fixture(autouse=True)
def _clean_organization_context():
    """A value leaking between tests would make results order-dependent."""
    assert get_active_organization_id() is None
    yield
    assert get_active_organization_id() is None


@pytest.fixture(autouse=True)
def _isolated_media_root(settings, tmp_path):
    """No test writes into the repository's own media/ directory.

    Autouse and global rather than per-module, because the failure is silent:
    a test that saves a ``FileField`` leaves real files behind, nothing fails,
    and they accumulate in a working tree until someone notices stray uploads
    under a path no row points at. Found exactly that way — the tenant-isolation
    builders had been writing one-pixel images into media/ for real.
    """
    settings.MEDIA_ROOT = str(tmp_path / 'media')


@pytest.fixture
def organization(db) -> Organization:
    return Organization.objects.create(name='Northside Clinic', slug='northside')


@pytest.fixture
def other_organization(db) -> Organization:
    return Organization.objects.create(name='Southside Clinic', slug='southside')


@pytest.fixture
def branch(organization) -> Branch:
    with organization_context(organization):
        return Branch.objects.create(
            organization=organization, name='Main Chamber', code='MAIN'
        )


@pytest.fixture
def make_member(db):
    """Build a user with a membership in a given organization and role."""

    def _make(
        organization, *, role=Role.STAFF, phone='01700000000', password='pw-demo-12345'
    ):
        user = User.objects.create_user(
            phone=phone, password=password, full_name=f'{role.title()} User'
        )
        Membership.objects.create(user=user, organization=organization, role=role)
        user.raw_password = password
        return user

    return _make


@pytest.fixture
def owner(organization, make_member) -> User:
    return make_member(organization, role=Role.OWNER, phone='01700000001')


@pytest.fixture
def practitioner(organization, make_member) -> User:
    return make_member(organization, role=Role.PRACTITIONER, phone='01700000002')


@pytest.fixture
def staff(organization, make_member) -> User:
    return make_member(organization, role=Role.STAFF, phone='01700000003')
