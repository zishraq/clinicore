"""Your own name, email, and password — and the forced change after a reset.

There is no email-based reset (docs/adr/0013-user-management-without-email.md),
so the forced change is the only thing standing between a password read out in a
waiting room and a permanent credential. It gets tested as a mechanism, not as a
status code.
"""

import pytest
from django.urls import reverse

from accounts.models import Membership, Role

pytestmark = pytest.mark.django_db

NEW_PASSWORD = 'chosen-pass-55213'


def test_any_role_can_edit_their_own_name_and_email(client, organization, staff):
    client.force_login(staff)
    response = client.post(
        reverse('accounts:profile'),
        {'full_name': 'Nadia S. Sultana', 'email': 'nadia@example.com'},
    )

    assert response.status_code == 302
    staff.refresh_from_db()
    assert staff.full_name == 'Nadia S. Sultana'
    assert staff.email == 'nadia@example.com'


def test_the_profile_cannot_change_the_sign_in_phone(client, organization, staff):
    """Phone is USERNAME_FIELD and unique deployment-wide.

    A collision has to be reported by somebody who can see both sides of it, so
    it is an administrator's job on the team screen. Posting it here is ignored
    rather than refused — the field is simply not on the form.
    """
    client.force_login(staff)
    client.post(
        reverse('accounts:profile'),
        {'full_name': staff.full_name, 'email': '', 'phone': '01755555555'},
    )

    staff.refresh_from_db()
    assert staff.phone != '01755555555'


def test_changing_your_password_does_not_sign_you_out(client, organization, staff):
    """Saving a password rotates the hash the session is keyed on.

    Without ``update_session_auth_hash`` the next request logs you out, which
    reads as the change having failed and sends people round the loop again.
    """
    client.force_login(staff)
    response = client.post(
        reverse('accounts:password_change'),
        {
            'old_password': staff.raw_password,
            'new_password1': NEW_PASSWORD,
            'new_password2': NEW_PASSWORD,
        },
    )

    assert response.status_code == 302
    still_in = client.get(reverse('core:dashboard'))
    assert still_in.status_code == 200
    assert still_in.wsgi_request.user.is_authenticated

    staff.refresh_from_db()
    assert staff.check_password(NEW_PASSWORD)


def test_a_forced_user_is_held_on_the_password_screen(client, organization, staff):
    staff.must_change_password = True
    staff.save(update_fields=['must_change_password'])

    client.force_login(staff)
    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:password_change')


def test_the_forced_screen_and_the_way_off_it_stay_reachable(
    client, organization, staff
):
    """A redirect that catches its own destination is a trap with no exit."""
    staff.must_change_password = True
    staff.save(update_fields=['must_change_password'])
    client.force_login(staff)

    assert client.get(reverse('accounts:password_change')).status_code == 200
    # Somebody who cannot remember the password they were just given has to be
    # able to get off this screen and ask for another one.
    assert client.post(reverse('accounts:logout')).status_code == 302


def test_changing_the_password_releases_the_forced_user(client, organization, staff):
    staff.must_change_password = True
    staff.save(update_fields=['must_change_password'])

    client.force_login(staff)
    client.post(
        reverse('accounts:password_change'),
        {
            'old_password': staff.raw_password,
            'new_password1': NEW_PASSWORD,
            'new_password2': NEW_PASSWORD,
        },
    )

    staff.refresh_from_db()
    assert not staff.must_change_password
    assert client.get(reverse('core:dashboard')).status_code == 200


def test_the_reset_and_the_forced_change_work_end_to_end(
    client, organization, owner, staff
):
    """The whole recovery story, since there is no email in it.

    Administrator sets a temporary password, the account holder signs in with
    it, and the first thing they can reach is the screen that replaces it.
    """
    membership = Membership.objects.get(user=staff, organization=organization)
    temporary = 'temp-pass-91847'

    client.force_login(owner)
    client.post(
        reverse('accounts:member_reset_password', args=[membership.pk]),
        {'password': temporary},
    )
    client.post(reverse('accounts:logout'))

    signed_in = client.post(
        reverse('accounts:login'), {'username': staff.phone, 'password': temporary}
    )
    assert signed_in.status_code == 302

    held = client.get(reverse('core:dashboard'))
    assert held['Location'] == reverse('accounts:password_change')

    client.post(
        reverse('accounts:password_change'),
        {
            'old_password': temporary,
            'new_password1': NEW_PASSWORD,
            'new_password2': NEW_PASSWORD,
        },
    )
    assert client.get(reverse('core:dashboard')).status_code == 200


def test_a_member_less_account_can_still_change_its_password(client, staff):
    """base.html draws no chrome without an organization.

    A withdrawn membership plus a forced change would otherwise land on a blank
    page with no form on it and no way off — see the bare_content block in
    accounts/password_change.html.
    """
    Membership.objects.filter(user=staff).update(is_active=False)
    staff.must_change_password = True
    staff.save(update_fields=['must_change_password'])

    client.force_login(staff)
    response = client.get(reverse('accounts:password_change'))

    assert response.status_code == 200
    assert b'name="new_password1"' in response.content


def test_changing_a_phone_number_does_not_strand_an_old_lockout(
    client, organization, owner, staff, settings
):
    """django-axes keys attempts on the phone that was typed.

    Renaming the account leaves the old number's row behind, which matters only
    if it can follow the person to the new number. It cannot: the key is the
    string that was posted, so the new number starts clean and the stale row
    expires on its own cool-off.
    """
    from axes.models import AccessAttempt

    settings.AXES_ENABLED = True
    settings.AXES_FAILURE_LIMIT = 2
    old_phone, new_phone = staff.phone, '01766666666'

    for _ in range(settings.AXES_FAILURE_LIMIT):
        client.post(
            reverse('accounts:login'),
            {'username': old_phone, 'password': 'wrong'},
            REMOTE_ADDR='203.0.113.10',
        )
    assert AccessAttempt.objects.filter(username=old_phone).exists()
    # The lockout is genuinely in force, or the rest of this proves nothing:
    # 429 rather than 302 even with the right password.
    locked = client.post(
        reverse('accounts:login'),
        {'username': old_phone, 'password': staff.raw_password},
        REMOTE_ADDR='203.0.113.10',
    )
    assert locked.status_code == 429

    membership = Membership.objects.get(user=staff, organization=organization)
    client.force_login(owner)
    client.post(
        reverse('accounts:member_update', args=[membership.pk]),
        {
            'full_name': staff.full_name,
            'phone': new_phone,
            'email': '',
            'role': Role.STAFF,
        },
    )
    client.post(reverse('accounts:logout'))

    response = client.post(
        reverse('accounts:login'),
        {'username': new_phone, 'password': staff.raw_password},
        REMOTE_ADDR='203.0.113.10',
    )
    assert response.status_code == 302
    assert response.wsgi_request.user.is_authenticated
