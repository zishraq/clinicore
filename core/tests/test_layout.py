"""A canary for the one layout rule that has already broken once.

This cannot prove the page looks right — only a browser can, and the project
rule is to check there. What it does catch is the specific regression: someone
tidying ``sm:pb-24`` out of base.html as redundant, which it is not.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_the_layout_clears_the_fixed_bottom_nav(client, practitioner):
    """Tailwind emits responsive variants after base utilities.

    So a bare ``pb-24`` loses to ``sm:p-6`` from 640px up, the bottom padding
    collapses to 24px, and the fixed 64px nav covers the foot of any page long
    enough to scroll — including every form's submit button.
    """
    client.force_login(practitioner)
    body = client.get(reverse('core:dashboard')).content.decode()
    assert 'pb-24 sm:pb-24 lg:pb-6' in body
