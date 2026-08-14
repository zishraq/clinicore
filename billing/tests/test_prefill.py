"""What a bill raised from a visit opens with (A5).

The consultation fee, plus whatever was prescribed that the clinic can actually
sell today. The rules are all about what must *not* appear: advice, things it
does not stock, things it stocks but does not sell, and lots that have expired.

The prefill is a convenience copy and never a link — editing the bill must not
reach back into the prescription. The last test is the one that guards that.
"""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from billing import services
from billing.models import LineType
from catalog.models import AdviceTemplate, Product
from clinical.models import Encounter, EncounterStatus, ItemType, Prescription
from clinical.models import PrescriptionItem as Item
from core.context import organization_context
from inventory import services as inventory

pytestmark = pytest.mark.django_db


def _product(organization, name, **flags) -> Product:
    defaults = {
        'sale_price': Decimal('12.00'),
        'is_sellable': True,
        'is_stock_tracked': True,
    }
    return Product.objects.create(
        organization=organization, name=name, **{**defaults, **flags}
    )


@pytest.fixture
def visit(organization, branch, practitioner, patient):
    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            status=EncounterStatus.FINALIZED,
            finalized_at=timezone.now(),
        )
        Prescription.objects.create(organization=organization, encounter=encounter)
        return encounter


def _prescribe(organization, visit, product=None, **kwargs):
    with organization_context(organization):
        return Item.objects.create(
            organization=organization,
            prescription=visit.prescription,
            product=product,
            **kwargs,
        )


def _stock(organization, branch, actor, product, quantity='20', expiry=None):
    with organization_context(organization):
        inventory.receive_stock(
            organization,
            branch=branch,
            actor=actor,
            lines=[
                {
                    'product': product,
                    'quantity': Decimal(quantity),
                    'cost_price': Decimal('5.00'),
                    'expiry_date': expiry,
                }
            ],
        )


def _names(lines) -> list[str]:
    return [line['display_name'] for line in lines]


def test_a_stocked_sellable_medicine_is_prefilled(
    organization, branch, practitioner, visit
):
    with organization_context(organization):
        product = _product(organization, 'Paracetamol 500mg')
    _prescribe(organization, visit, product, dosage='1 tablet')
    _stock(organization, branch, practitioner, product)

    with organization_context(organization):
        lines = services.prescribed_product_lines(organization, visit, branch=branch)

    assert _names(lines) == ['Paracetamol 500mg']
    line = lines[0]
    assert line['line_type'] == LineType.PRODUCT
    assert line['product'] == product.pk
    # No quantity is invented: a prescription carries none (ADR 0009).
    assert line['quantity'] == 1
    assert line['unit_price'] == Decimal('12.00')


def test_advice_is_never_a_charge(organization, branch, practitioner, visit):
    with organization_context(organization):
        advice = AdviceTemplate.objects.create(
            organization=organization, text='Walk 30 minutes daily.'
        )
    _prescribe(organization, visit, item_type=ItemType.ADVICE, advice_template=advice)

    with organization_context(organization):
        assert (
            services.prescribed_product_lines(organization, visit, branch=branch) == []
        )


def test_a_recommendation_the_clinic_does_not_sell_is_not_prefilled(
    organization, branch, practitioner, visit
):
    """Recommended but bought elsewhere. ``is_sellable`` is what says so.

    This used to assert that an *untracked* product was not prefilled, which
    conflated two flags: not stocking something is not the same as not selling
    it. Under that rule every quick-added medicine — untracked, because nobody
    receipts stock mid-consultation — was silently missing from the bill.
    """
    with organization_context(organization):
        product = _product(
            organization,
            'Vitamin D drops',
            is_sellable=False,
            is_stock_tracked=False,
        )
    _prescribe(organization, visit, product, dosage='5 drops')

    with organization_context(organization):
        assert (
            services.prescribed_product_lines(organization, visit, branch=branch) == []
        )


def test_an_untracked_but_sellable_product_is_prefilled(
    organization, branch, practitioner, visit
):
    """The quick-add case, and the reason the rule changed.

    No ledger exists for an untracked product, so there is no stock level that
    could refuse it — the only question is whether the clinic sells the thing.
    """
    with organization_context(organization):
        product = _product(
            organization,
            'Paracetamol 500mg',
            is_sellable=True,
            is_stock_tracked=False,
        )
    _prescribe(organization, visit, product, dosage='1 tablet')

    with organization_context(organization):
        lines = services.prescribed_product_lines(organization, visit, branch=branch)

    assert _names(lines) == ['Paracetamol 500mg']


def test_a_quick_added_medicine_reaches_the_bill(
    organization, branch, practitioner, visit
):
    """End to end on the defaults, which is where the bug actually lived.

    Built through ``quick_add_product`` rather than by setting flags by hand:
    the whole failure was that the defaults it leaves behind were wrong, so a
    test that spells the flags out would have kept passing throughout.
    """
    from catalog.services import quick_add_product

    with organization_context(organization):
        product = quick_add_product(
            organization, actor=practitioner, name='Cetirizine 10mg'
        )
        assert product.is_sellable and not product.is_stock_tracked
    _prescribe(organization, visit, product, dosage='1 tablet')

    with organization_context(organization):
        lines = services.prescribed_product_lines(organization, visit, branch=branch)

    assert _names(lines) == ['Cetirizine 10mg']
    # Priced on the bill, not in the consultation — see docs/MVP-NOTES.md.
    assert lines[0]['unit_price'] == Decimal('0.00')


def test_a_stocked_but_unsellable_product_is_not_prefilled(
    organization, branch, practitioner, visit
):
    with organization_context(organization):
        product = _product(organization, 'Surgical spirit', is_sellable=False)
    _prescribe(organization, visit, product, dosage='Apply')
    _stock(organization, branch, practitioner, product)

    with organization_context(organization):
        assert (
            services.prescribed_product_lines(organization, visit, branch=branch) == []
        )


def test_a_product_with_no_stock_is_not_prefilled(
    organization, branch, practitioner, visit
):
    with organization_context(organization):
        product = _product(organization, 'Amoxicillin 500mg')
    _prescribe(organization, visit, product, dosage='1 capsule')

    with organization_context(organization):
        assert (
            services.prescribed_product_lines(organization, visit, branch=branch) == []
        )


def test_only_expired_stock_does_not_count_as_in_stock(
    organization, branch, practitioner, visit
):
    """Expired lots cannot be sold, so they are not cover for a prefilled line."""
    with organization_context(organization):
        product = _product(organization, 'Cetirizine 10mg')
    _prescribe(organization, visit, product, dosage='1 tablet')
    yesterday = timezone.localdate() - datetime.timedelta(days=1)
    _stock(organization, branch, practitioner, product, expiry=yesterday)

    with organization_context(organization):
        # Physically on the premises...
        assert inventory.on_hand(organization, product=product) == Decimal('20.00')
        # ...but not sellable, so not offered.
        assert (
            services.prescribed_product_lines(organization, visit, branch=branch) == []
        )


def test_reorder_level_does_not_suppress_the_line(
    organization, branch, practitioner, visit
):
    """A purchasing signal, not a sellability one: sell the last two boxes."""
    with organization_context(organization):
        product = _product(
            organization, 'Omeprazole 20mg', reorder_level=Decimal('50.00')
        )
    _prescribe(organization, visit, product, dosage='1 capsule')
    _stock(organization, branch, practitioner, product, quantity='2')

    with organization_context(organization):
        lines = services.prescribed_product_lines(organization, visit, branch=branch)
    assert _names(lines) == ['Omeprazole 20mg']


def test_the_same_medicine_twice_is_one_charge(
    organization, branch, practitioner, visit
):
    with organization_context(organization):
        product = _product(organization, 'Paracetamol 500mg')
    _prescribe(organization, visit, product, dosage='1 tablet', sort_order=0)
    _prescribe(organization, visit, product, dosage='2 tablets', sort_order=1)
    _stock(organization, branch, practitioner, product)

    with organization_context(organization):
        lines = services.prescribed_product_lines(organization, visit, branch=branch)
    assert _names(lines) == ['Paracetamol 500mg']


def test_stock_at_another_branch_does_not_count(
    organization, branch, practitioner, visit
):
    """A line comes off a particular shelf, so the branch has to be right."""
    from organizations.models import Branch

    with organization_context(organization):
        other = Branch.objects.create(
            organization=organization, name='Uttara Chamber', code='UTT'
        )
        product = _product(organization, 'Paracetamol 500mg')
    _prescribe(organization, visit, product, dosage='1 tablet')
    _stock(organization, other, practitioner, product)

    with organization_context(organization):
        assert (
            services.prescribed_product_lines(organization, visit, branch=branch) == []
        )
        assert _names(
            services.prescribed_product_lines(organization, visit, branch=other)
        ) == ['Paracetamol 500mg']


def test_the_form_opens_with_the_fee_and_the_medicine(
    client, practitioner, organization, branch, visit
):
    """End to end through the view: consultation fee first, then the products."""
    with organization_context(organization):
        product = _product(organization, 'Paracetamol 500mg')
    _prescribe(organization, visit, product, dosage='1 tablet')
    _stock(organization, branch, practitioner, product)

    client.force_login(practitioner)
    response = client.get(reverse('billing:invoice_create'), {'encounter': visit.pk})
    assert response.status_code == 200
    initial = [
        form.initial for form in response.context['item_formset'].forms if form.initial
    ]
    assert [entry['line_type'] for entry in initial] == [
        LineType.CONSULTATION,
        LineType.PRODUCT,
    ]
    assert initial[1]['display_name'] == 'Paracetamol 500mg'


def test_editing_the_bill_leaves_the_prescription_alone(
    client, practitioner, organization, branch, visit
):
    """A convenience copy, not a link — the whole point of the rule."""
    with organization_context(organization):
        product = _product(organization, 'Paracetamol 500mg')
    item = _prescribe(organization, visit, product, dosage='1 tablet')
    _stock(organization, branch, practitioner, product)

    client.force_login(practitioner)
    response = client.post(
        reverse('billing:invoice_create'),
        {
            'patient': visit.patient_id,
            'encounter': visit.pk,
            'branch': branch.pk,
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            # Repriced and requantified away from what was prefilled.
            'items-0-display_name': 'Paracetamol 500mg',
            'items-0-line_type': LineType.PRODUCT,
            'items-0-product': product.pk,
            'items-0-quantity': '6',
            'items-0-unit_price': '9.50',
            'items-0-discount': '0',
            'items-0-sort_order': '0',
        },
    )
    assert response.status_code == 302

    with organization_context(organization):
        item.refresh_from_db()
        assert item.dosage == '1 tablet'
        assert item.name_snapshot == 'Paracetamol 500mg'
        assert visit.prescription.items.count() == 1
