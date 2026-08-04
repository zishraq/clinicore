"""The markup contract between the item row and its JavaScript.

static/js/item-autocomplete.js finds every input it writes to by ``data-role``.
Those hooks live in widget attrs, which nothing else references — so if a widget
is ever replaced (the ``formfield_callback`` rebuilds relation widgets, for one)
the attribute disappears silently, the JS gets null, and the autocomplete dies
while every status-code test stays green. That has happened; this is the guard.
"""

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.forms import PrescriptionItemFormSet
from clinical.models import Encounter, Prescription
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db

#: Every hook the component looks up. Keep in step with the module docstring in
#: static/js/item-autocomplete.js.
REQUIRED_ROLES = [
    'item-search',
    'item-type',
    'item-product',
    'item-advice',
    'item-free-text',
    'item-delete',
]


def _roles(html: str) -> set[str]:
    return set(re.findall(r'data-role="([a-z-]+)"', html))


def test_the_rendered_row_carries_every_hook_the_js_needs(organization, rf):
    """Rendered through the real formset, not by inspecting widget attrs."""
    with organization_context(organization):
        formset = PrescriptionItemFormSet(organization=organization)
        html = ''.join(str(form) for form in formset.forms)
    missing = set(REQUIRED_ROLES) - _roles(html)
    assert not missing, (
        f'The item row lost these JS hooks: {sorted(missing)}. '
        f'static/js/item-autocomplete.js writes to them by data-role.'
    )


def test_the_encounter_form_page_carries_every_hook(client, practitioner, organization):
    """End to end: what the browser actually receives."""
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))
    assert response.status_code == 200
    missing = set(REQUIRED_ROLES) - _roles(response.content.decode())
    assert not missing, f'Encounter form is missing JS hooks: {sorted(missing)}'


def test_the_add_row_endpoint_carries_every_hook(client, practitioner):
    """The HTMX-inserted row is built separately and must match."""
    client.force_login(practitioner)
    response = client.get(reverse('clinical:item_row'), {'items-TOTAL_FORMS': '1'})
    assert response.status_code == 200
    body = response.content.decode()
    missing = set(REQUIRED_ROLES) - _roles(body)
    assert not missing, f'Added row is missing JS hooks: {sorted(missing)}'
    assert '__prefix__' not in body


def test_suggestions_read_the_query_from_the_rows_own_input(
    client, practitioner, organization
):
    """htmx sends the box's value under its formset name, not as ?q=.

    Renaming it with hx-vals does not work in htmx 2.0.4, so the view reads the
    formset parameter. If that fallback is lost the autocomplete silently
    returns everything instead of a filtered list.
    """
    from catalog.models import Product

    with organization_context(organization):
        Product.objects.create(organization=organization, name='Calcium + Vitamin D3')
        Product.objects.create(organization=organization, name='Paracetamol 500mg')

    client.force_login(practitioner)
    response = client.get(
        reverse('catalog:suggestions'), {'items-0-display_name': 'vitamin'}
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Calcium + Vitamin D3' in body
    assert 'Paracetamol 500mg' not in body


def test_an_explicit_q_still_wins(client, practitioner, organization):
    from catalog.models import Product

    with organization_context(organization):
        Product.objects.create(organization=organization, name='Calcium + Vitamin D3')

    client.force_login(practitioner)
    response = client.get(
        reverse('catalog:suggestions'),
        {'q': 'vitamin', 'items-0-display_name': 'ignored'},
    )
    assert b'Calcium + Vitamin D3' in response.content


def test_removing_a_middle_row_leaves_a_gap_the_formset_tolerates(
    client, practitioner, organization, branch
):
    """Removing an unsaved row deletes its inputs, so that index posts nothing.

    TOTAL_FORMS deliberately still counts it — lowering the count past a
    surviving row would make Django read the wrong data for that row. The gap
    must simply be ignored, and the rows either side must save.
    """
    from catalog.models import Product
    from clinical.models import ItemType, PrescriptionItem

    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )
        first = Product.objects.create(organization=organization, name='Cetirizine')
        third = Product.objects.create(organization=organization, name='Omeprazole')

    payload = {
        'patient': patient.pk,
        'branch': branch.pk,
        'practitioner': practitioner.pk,
        'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
        'chief_complaint': 'Itching',
        'examination': '',
        'assessment': '',
        'plan': '',
        'general_instructions': '',
        'print_size': 'A5',
        # Three forms declared; index 1 was removed from the DOM and so posts
        # nothing whatsoever.
        'items-TOTAL_FORMS': '3',
        'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        'items-0-display_name': 'Cetirizine',
        'items-0-product': first.pk,
        'items-0-item_type': ItemType.MEDICATION,
        'items-0-dosage': '1 tablet',
        'items-0-frequency': '',
        'items-0-duration': '',
        'items-0-instructions': '',
        'items-0-sort_order': '0',
        'items-2-display_name': 'Omeprazole',
        'items-2-product': third.pk,
        'items-2-item_type': ItemType.MEDICATION,
        'items-2-dosage': '1 capsule',
        'items-2-frequency': '',
        'items-2-duration': '',
        'items-2-instructions': '',
        'items-2-sort_order': '2',
    }

    client.force_login(practitioner)
    response = client.post(reverse('clinical:encounter_create'), payload)
    assert response.status_code == 302, (
        'The gap left by a removed row was treated as a filled-in item: '
        f'{response.context["item_formset"].errors if response.context else ""}'
    )

    with organization_context(organization):
        saved = list(
            PrescriptionItem.objects.order_by('sort_order').values_list(
                'name_snapshot', flat=True
            )
        )
        assert saved == ['Cetirizine', 'Omeprazole']


def test_a_blank_extra_row_is_ignored(client, practitioner, organization, branch):
    """The single empty row a fresh form renders must not block saving."""
    from clinical.models import PrescriptionItem

    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0002', full_name='Kamal Hossain'
        )

    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        {
            'patient': patient.pk,
            'branch': branch.pk,
            'practitioner': practitioner.pk,
            'occurred_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'chief_complaint': 'Routine review',
            'examination': '',
            'assessment': '',
            'plan': '',
            'general_instructions': '',
            'print_size': 'A5',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-display_name': '',
            'items-0-item_type': 'MEDICATION',
            'items-0-product': '',
            'items-0-advice_template': '',
            'items-0-dosage': '',
            'items-0-frequency': '',
            'items-0-duration': '',
            'items-0-instructions': '',
            'items-0-sort_order': '0',
        },
    )
    assert response.status_code == 302
    with organization_context(organization):
        assert not PrescriptionItem.objects.exists()


def test_deleting_a_saved_row_removes_the_item(
    client, practitioner, organization, branch
):
    """The Remove button ticks DELETE; the formset must honour it on save."""
    from catalog.models import Product
    from clinical.models import ItemType, PrescriptionItem

    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter
        )
        product = Product.objects.create(organization=organization, name='Cetirizine')
        item = PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            item_type=ItemType.MEDICATION,
            product=product,
        )
        item_pk, prescription_pk = item.pk, prescription.pk

    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_update', args=[encounter.pk]),
        {
            'patient': patient.pk,
            'branch': branch.pk,
            'practitioner': practitioner.pk,
            'occurred_at': timezone.localtime(encounter.occurred_at).strftime(
                '%Y-%m-%dT%H:%M'
            ),
            'chief_complaint': '',
            'examination': '',
            'assessment': '',
            'plan': '',
            'general_instructions': '',
            'print_size': 'A5',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item_pk,
            'items-0-prescription': prescription_pk,
            'items-0-display_name': 'Cetirizine',
            'items-0-product': product.pk,
            'items-0-item_type': ItemType.MEDICATION,
            'items-0-dosage': '',
            'items-0-frequency': '',
            'items-0-duration': '',
            'items-0-instructions': '',
            'items-0-sort_order': '0',
            'items-0-DELETE': 'on',
        },
    )
    assert response.status_code == 302
    with organization_context(organization):
        assert not PrescriptionItem.objects.filter(pk=item_pk).exists()
