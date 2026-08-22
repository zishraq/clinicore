"""The team screen: who may reach it, and the states it must never allow.

The tenant boundary gets more attention here than anywhere else in this file.
``Membership`` is not an ``OrgOwnedModel`` — it is read to *establish* the active
organization, so it has a plain manager and no automatic filter — which makes
this the one surface where forgetting a ``.filter(organization=…)`` shows another
clinic's staff list rather than an empty page.
"""

import pytest
from django.urls import reverse

from accounts.models import Membership, Role, User

pytestmark = pytest.mark.django_db

GOOD_PASSWORD = 'temp-pass-91847'


def _sign_in(client, user):
    client.force_login(user)
    return client


def test_staff_cannot_reach_the_team_screen(client, organization, staff):
    """SPEC §6.1: user management is the administrator's, by direct URL too."""
    _sign_in(client, staff)
    assert client.get(reverse('accounts:member_list')).status_code == 403


def test_practitioner_cannot_reach_the_team_screen(client, organization, practitioner):
    _sign_in(client, practitioner)
    assert client.get(reverse('accounts:member_list')).status_code == 403


def test_the_list_shows_everyone_in_this_organization(
    client, organization, owner, practitioner, staff
):
    _sign_in(client, owner)
    response = client.get(reverse('accounts:member_list'))

    assert response.status_code == 200
    listed = {member.user_id for member in response.context['members']}
    assert listed == {owner.pk, practitioner.pk, staff.pk}


def test_the_list_never_shows_another_organizations_people(
    client, organization, other_organization, owner, make_member
):
    """The cross-tenant surface, asserted directly rather than assumed."""
    outsider = make_member(other_organization, role=Role.STAFF, phone='01799999999')

    _sign_in(client, owner)
    response = client.get(reverse('accounts:member_list'))

    assert outsider.pk not in {m.user_id for m in response.context['members']}
    assert outsider.full_name.encode() not in response.content


def test_another_organizations_member_is_a_404_by_pk(
    client, organization, other_organization, owner, make_member
):
    """The pk is a global identifier; only the organization filter stops it."""
    outsider = make_member(other_organization, role=Role.STAFF, phone='01799999999')
    outside_membership = Membership.objects.get(user=outsider)

    _sign_in(client, owner)

    for name in ('member_update', 'member_reset_password'):
        url = reverse(f'accounts:{name}', args=[outside_membership.pk])
        assert client.get(url).status_code == 404, name

    toggle = reverse('accounts:member_toggle_active', args=[outside_membership.pk])
    assert client.post(toggle).status_code == 404
    outside_membership.refresh_from_db()
    assert outside_membership.is_active


def test_adding_someone_creates_an_account_they_must_re_password(
    client, organization, owner
):
    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_create'),
        {
            'full_name': 'Rumana Haque',
            'phone': '01712345678',
            'email': '',
            'role': Role.STAFF,
            'password': GOOD_PASSWORD,
        },
    )

    assert response.status_code == 302
    created = User.objects.get(phone='01712345678')
    assert created.check_password(GOOD_PASSWORD)
    # Somebody else chose it and said it out loud; it cannot stay in use.
    assert created.must_change_password
    membership = Membership.objects.get(user=created, organization=organization)
    assert membership.role == Role.STAFF
    assert membership.is_active


def test_the_add_form_opens_on_the_least_powerful_role(client, organization, owner):
    """Found in a browser: the dropdown opened on Administrator.

    ``Role``'s first member is OWNER, so an unexamined ChoiceField hands the most
    powerful role to anybody who does not read the dropdown — and the common case
    by a distance is adding a receptionist.
    """
    client.force_login(owner)
    form = client.get(reverse('accounts:member_create')).context['form']

    assert form['role'].initial == Role.STAFF


def test_a_taken_phone_number_is_a_form_error_not_an_integrity_error(
    client, organization, other_organization, owner, make_member
):
    """``phone`` is USERNAME_FIELD and unique across the whole deployment.

    The collision may be with an account at another clinic, so the message must
    not name it — that would leak a name across tenants to anybody willing to
    guess numbers.
    """
    make_member(other_organization, role=Role.STAFF, phone='01799999999')

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_create'),
        {
            'full_name': 'Someone Else',
            'phone': '01799999999',
            'email': '',
            'role': Role.STAFF,
            'password': GOOD_PASSWORD,
        },
    )

    assert response.status_code == 200
    assert 'already in use' in response.context['form'].errors['phone'][0]
    assert User.objects.filter(full_name='Someone Else').count() == 0


def test_a_weak_temporary_password_is_refused(client, organization, owner):
    """A temporary password is a live credential until it is replaced."""
    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_create'),
        {
            'full_name': 'Rumana Haque',
            'phone': '01712345678',
            'email': '',
            'role': Role.STAFF,
            'password': '12345',
        },
    )

    assert response.status_code == 200
    assert response.context['form'].errors['password']
    assert not User.objects.filter(phone='01712345678').exists()


def test_editing_changes_the_user_and_the_role_together(
    client, organization, owner, staff
):
    membership = Membership.objects.get(user=staff, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_update', args=[membership.pk]),
        {
            'full_name': 'Nadia Sultana',
            'phone': '01722222222',
            'email': 'nadia@example.com',
            'role': Role.PRACTITIONER,
        },
    )

    assert response.status_code == 302
    staff.refresh_from_db()
    membership.refresh_from_db()
    assert staff.full_name == 'Nadia Sultana'
    assert staff.phone == '01722222222'
    assert staff.email == 'nadia@example.com'
    assert membership.role == Role.PRACTITIONER


def test_editing_onto_a_taken_phone_number_is_refused(
    client, organization, owner, staff, practitioner
):
    membership = Membership.objects.get(user=staff, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_update', args=[membership.pk]),
        {
            'full_name': staff.full_name,
            'phone': practitioner.phone,
            'email': '',
            'role': Role.STAFF,
        },
    )

    assert response.status_code == 200
    assert response.context['form'].errors['phone']
    staff.refresh_from_db()
    assert staff.phone != practitioner.phone


def test_an_administrator_cannot_demote_themselves(client, organization, owner):
    """An organization with no active administrator needs a shell to recover.

    Your own row offers only the roles that keep administering, and
    ``ChoiceField.validate`` refuses anything outside the offered choices — so
    this hand-built POST is the real attack, not the dropdown (ADR 0019).
    """
    own_membership = Membership.objects.get(user=owner, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_update', args=[own_membership.pk]),
        {
            'full_name': owner.full_name,
            'phone': owner.phone,
            'email': '',
            'role': Role.STAFF,
        },
    )

    # Refused visibly now rather than silently ignored: the form comes back
    # with the field in error instead of redirecting as though it had saved.
    assert response.status_code == 200
    assert response.context['form'].errors['role']
    own_membership.refresh_from_db()
    assert own_membership.role == Role.OWNER


def test_an_administrator_may_change_their_own_role_to_another_that_administers(
    client, organization, owner
):
    """The change ADR 0019 exists to allow.

    An administrator who does not treat patients has to be able to say so, and
    OWNER to DEVELOPER removes nobody's access — the organization still has
    exactly as many people who can administer it.
    """
    own_membership = Membership.objects.get(user=owner, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_update', args=[own_membership.pk]),
        {
            'full_name': owner.full_name,
            'phone': owner.phone,
            'email': '',
            'role': Role.DEVELOPER,
        },
    )

    assert response.status_code == 302
    own_membership.refresh_from_db()
    assert own_membership.role == Role.DEVELOPER
    # And they can still administer, which is what makes it safe.
    assert own_membership.is_owner is True


def test_your_own_row_offers_only_the_administering_roles(client, organization, owner):
    """Read off the rendered form, so the dropdown and the guard cannot drift."""
    own_membership = Membership.objects.get(user=owner, organization=organization)

    _sign_in(client, owner)
    response = client.get(reverse('accounts:member_update', args=[own_membership.pk]))

    offered = {value for value, _ in response.context['form'].fields['role'].choices}
    assert offered == {Role.OWNER.value, Role.DEVELOPER.value}


def test_somebody_elses_row_still_offers_every_role(client, organization, owner, staff):
    """The narrowing is self-only; an administrator still assigns any role."""
    membership = Membership.objects.get(user=staff, organization=organization)

    _sign_in(client, owner)
    response = client.get(reverse('accounts:member_update', args=[membership.pk]))

    offered = {value for value, _ in response.context['form'].fields['role'].choices}
    assert offered == {role.value for role in Role}


def test_a_developer_administers_the_team_screen(client, organization, developer):
    """Full OWNER surface: with no SMTP, a hand-typed reset is the only one."""
    _sign_in(client, developer)

    assert client.get(reverse('accounts:member_list')).status_code == 200
    assert client.get(reverse('accounts:member_create')).status_code == 200


def test_an_administrator_cannot_deactivate_themselves(client, organization, owner):
    own_membership = Membership.objects.get(user=owner, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_toggle_active', args=[own_membership.pk])
    )

    assert response.status_code == 302
    own_membership.refresh_from_db()
    assert own_membership.is_active


def test_another_administrator_may_still_be_demoted(
    client, organization, owner, make_member
):
    """The guard is self-only, and that is enough.

    Only the last administrator could remove the last administrator, and they
    cannot remove themselves — so one always survives without needing a count.
    """
    second = make_member(organization, role=Role.OWNER, phone='01700000009')
    membership = Membership.objects.get(user=second, organization=organization)

    _sign_in(client, owner)
    client.post(
        reverse('accounts:member_update', args=[membership.pk]),
        {
            'full_name': second.full_name,
            'phone': second.phone,
            'email': '',
            'role': Role.STAFF,
        },
    )

    membership.refresh_from_db()
    assert membership.role == Role.STAFF


def test_removing_access_never_deletes_the_account(client, organization, owner, staff):
    """Visits, bills and stock movements point at this user; the name stays."""
    membership = Membership.objects.get(user=staff, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_toggle_active', args=[membership.pk])
    )

    assert response.status_code == 302
    membership.refresh_from_db()
    assert not membership.is_active
    assert User.objects.filter(pk=staff.pk).exists()


def test_access_can_be_restored(client, organization, owner, staff):
    membership = Membership.objects.get(user=staff, organization=organization)
    membership.is_active = False
    membership.save(update_fields=['is_active'])

    _sign_in(client, owner)
    client.post(reverse('accounts:member_toggle_active', args=[membership.pk]))

    membership.refresh_from_db()
    assert membership.is_active


def test_a_deactivated_member_is_refused_the_app_but_told_why(
    client, organization, owner, staff
):
    """The consequence of deactivation must be a page, not a traceback."""
    membership = Membership.objects.get(user=staff, organization=organization)
    membership.is_active = False
    membership.save(update_fields=['is_active'])

    _sign_in(client, staff)
    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 403
    assert b'Ask an administrator' in response.content


def test_an_administrator_can_set_a_temporary_password(
    client, organization, owner, staff
):
    membership = Membership.objects.get(user=staff, organization=organization)

    _sign_in(client, owner)
    response = client.post(
        reverse('accounts:member_reset_password', args=[membership.pk]),
        {'password': GOOD_PASSWORD},
    )

    assert response.status_code == 302
    staff.refresh_from_db()
    assert staff.check_password(GOOD_PASSWORD)
    assert staff.must_change_password
