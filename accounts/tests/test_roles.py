"""The three role sets, and the DEVELOPER role that proved they were three.

"May read a consultation note", "may be booked to treat a patient" and "may
administer this clinic" were answered by one membership in {OWNER, PRACTITIONER}
plus a hardcoded ``role == OWNER`` for the whole MVP. They are three questions
and they now have three sets — see
docs/adr/0019-read-clinical-and-may-be-booked-are-two-facts.md.
"""

import pytest

from accounts.models import (
    ADMIN_ROLES,
    CLINICAL_ROLES,
    PRESCRIBING_ROLES,
    Membership,
    Role,
)

pytestmark = pytest.mark.django_db


def test_the_sets_have_diverged():
    """The guard against a future edit quietly re-merging them.

    If these ever become equal again, "administers the system" and "may be put
    in front of a patient" are one fact once more and a receptionist can book
    the person who maintains the server.
    """
    assert CLINICAL_ROLES != PRESCRIBING_ROLES
    assert ADMIN_ROLES != PRESCRIBING_ROLES
    assert PRESCRIBING_ROLES < CLINICAL_ROLES


def test_developer_reads_clinical_data_but_is_never_prescribing():
    assert Role.DEVELOPER in CLINICAL_ROLES
    assert Role.DEVELOPER in ADMIN_ROLES
    assert Role.DEVELOPER not in PRESCRIBING_ROLES


def test_the_other_three_roles_did_not_move():
    """A new role must not quietly change what the existing ones can do."""
    assert frozenset({Role.OWNER, Role.PRACTITIONER}) == PRESCRIBING_ROLES
    assert Role.STAFF not in CLINICAL_ROLES
    assert Role.STAFF not in ADMIN_ROLES
    assert Role.PRACTITIONER not in ADMIN_ROLES
    assert Role.OWNER in CLINICAL_ROLES | PRESCRIBING_ROLES | ADMIN_ROLES


def test_the_stored_value_is_developer(organization, developer):
    """SPEC §5: the value never moves; only the label is configurable."""
    membership = Membership.objects.get(user=developer, organization=organization)
    assert membership.role == 'DEVELOPER'


def test_membership_properties_follow_the_sets(organization, developer):
    membership = Membership.objects.get(user=developer, organization=organization)
    assert membership.can_view_clinical is True
    # Named for the role it used to test; it means "may administer" (ADR 0019).
    assert membership.is_owner is True


def test_a_developer_fits_the_role_column(organization, developer):
    """``max_length`` is 20 and the value is 9 characters, so nothing widened."""
    field = Membership._meta.get_field('role')
    assert field.max_length == 20
    assert len(Role.DEVELOPER.value) <= field.max_length
