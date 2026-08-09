"""A canary for the one layout rule that has already broken once.

This cannot prove the page looks right — only a browser can, and the project
rule is to check there. What it does catch is the specific regression: someone
tidying ``sm:pb-24`` out of base.html as redundant, which it is not.
"""

import pytest
from django.conf import settings
from django.urls import reverse

from core.context import organization_context

pytestmark = pytest.mark.django_db

#: The two tiers a list row can opt into; anything unannotated keeps the
#: labelled full-width row. See the table-cards block in static/css/app.css.
CARD_TIERS = ('title', 'meta')


def test_the_layout_clears_the_fixed_bottom_nav(client, practitioner):
    """Tailwind emits responsive variants after base utilities.

    So a bare ``pb-24`` loses to ``sm:p-6`` from 640px up, the bottom padding
    collapses to 24px, and the fixed 64px nav covers the foot of any page long
    enough to scroll — including every form's submit button.
    """
    client.force_login(practitioner)
    body = client.get(reverse('core:dashboard')).content.decode()
    assert 'pb-24 sm:pb-24 lg:pb-6' in body


@pytest.mark.parametrize('tier', CARD_TIERS)
def test_the_card_annotations_have_css_behind_them(tier):
    """The markup and the stylesheet are one feature in two files.

    A `data-card` attribute with no rule to match it is not an error anywhere:
    the cell simply renders as an ordinary labelled row and the list quietly
    goes back to being twice as tall. Nothing else in the suite would notice.
    """
    css = (settings.BASE_DIR / 'static' / 'css' / 'app.css').read_text()
    assert f"[data-card='{tier}']" in css


def test_the_patient_list_names_a_card_title(client, practitioner, organization):
    """The other half of that coupling, from the markup side.

    Needs a real row: with none, the list renders the empty state and would
    pass this by never reaching the table at all.
    """
    from patients.models import Patient

    with organization_context(organization):
        Patient.objects.create(
            organization=organization, code='P-0001', full_name='Card Title Patient'
        )

    client.force_login(practitioner)
    body = client.get(reverse('patients:list')).content.decode()

    assert 'Card Title Patient' in body, 'the table did not render'
    assert 'data-card="title"' in body
    assert 'data-card="meta"' in body
