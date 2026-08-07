"""Signing out, and the click target that made it look broken.

Three people share the machine at reception and switching users is a daily
action, so this path has to work on the first click rather than the first
accurate click.
"""

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_get_does_not_log_anybody_out(client, staff):
    """Django refuses GET on logout, and a stray link or crawler must not sign
    the receptionist out of a shared desk."""
    client.force_login(staff)

    response = client.get(reverse('accounts:logout'))

    assert response.status_code == 405
    assert client.get(reverse('core:dashboard')).wsgi_request.user.is_authenticated


def test_post_logs_out_and_returns_to_the_login_page(client, staff):
    client.force_login(staff)

    response = client.post(reverse('accounts:logout'))

    assert response.status_code == 302
    assert response.url == reverse('accounts:login')
    assert not client.get(reverse('accounts:login')).wsgi_request.user.is_authenticated


def test_the_whole_menu_row_submits_the_logout(client, staff):
    """The regression, and the reason it was reported as "clicking does nothing".

    Wrapping the button in a form makes the *form* daisyUI's menu item: it takes
    the row's padding and lays the button out in a content-sized grid column, so
    only the width of the words is clickable — measured at 13% of the row. The
    button has to be the menu item, with the form joined to it out of line by
    the HTML ``form`` attribute.
    """
    client.force_login(staff)
    body = client.get(reverse('core:dashboard')).content.decode()

    assert 'form="logout-form"' in body
    assert 'id="logout-form"' in body

    menu = re.search(r'<ul tabindex="0" class="dropdown-content.*?</ul>', body, re.S)
    assert menu, 'the user menu is not where this test thought it was'
    assert '<form' not in menu.group(0), (
        'The logout form is back inside the menu list. daisyUI then styles the '
        'form as the row and shrinks the button to its text, leaving most of the '
        'row dead. Keep the form outside and use the button form attribute.'
    )


def test_logout_survives_a_stale_csrf_token(client, staff):
    """The regression from the clinic: signing out had to keep working.

    This test used to assert the opposite — that a tokenless POST is refused.
    That was correct in the abstract and wrong in the room: signing in rotates
    the token, so on a shared desk the second person to log in left the first
    with a Log out button that answered "CSRF verification failed", and the
    remedy for that is to sign out. The reasoning for accepting a forged logout
    is on ``ClinicoreLogoutView``.
    """
    unsafe = client.__class__(enforce_csrf_checks=True)
    unsafe.force_login(staff)

    response = unsafe.post(reverse('accounts:logout'))

    assert response.status_code == 302
    assert not unsafe.get(reverse('accounts:login')).wsgi_request.user.is_authenticated


def test_logout_is_still_post_only(client, staff):
    """The exemption is from the token, not from the method.

    POST-only is what stops a link, an image, or a prefetch signing somebody
    out, which is the reason Django requires it — and that reason survives.
    """
    client.force_login(staff)
    assert client.get(reverse('accounts:logout')).status_code == 405
    assert client.get(reverse('core:dashboard')).wsgi_request.user.is_authenticated


def test_a_stale_token_elsewhere_gets_a_readable_page(client, staff, settings):
    """Django's debug 403 is a wall of technical text; reception gets a sentence.

    Asserted against a real CSRF failure rather than by calling the view, so a
    mis-wired ``CSRF_FAILURE_VIEW`` fails here.
    """
    unsafe = client.__class__(enforce_csrf_checks=True)
    unsafe.force_login(staff)

    response = unsafe.post(reverse('patients:create'), {'full_name': 'Rahima Begum'})

    assert response.status_code == 403
    body = response.content.decode()
    assert 'Your session expired' in body
    assert reverse('accounts:login') in body
    # The words a receptionist cannot act on.
    assert 'CSRF' not in body
    assert 'Forbidden' not in body
