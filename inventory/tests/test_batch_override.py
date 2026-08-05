"""Taking stock off a batch someone named by hand, instead of off FEFO.

Automatic allocation is the rule and this is the exception (SPEC §6.5): the
practitioner is handing over one particular box — a later expiry for a patient
travelling, or the lot the shelf label actually matches. Because a human chose
it, a refusal has to say why rather than quietly substitute another batch,
which is the whole difference between this path and ``allocate_fefo``.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
from core.context import organization_context
from inventory import services
from inventory.models import MovementType, StockBatch
from organizations.models import Branch

pytestmark = pytest.mark.django_db


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


@pytest.fixture
def dated_batches(organization, branch, practitioner, product, receive):
    """Ten expiring yesterday, ten next month, ten next year."""
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'GONE',
                'expiry_date': _days(-1),
            },
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'SOON',
                'expiry_date': _days(30),
            },
            {
                'product': product,
                'quantity': Decimal('10'),
                'lot_number': 'LATER',
                'expiry_date': _days(365),
            },
        ],
    )

    def _lot(lot_number) -> StockBatch:
        with organization_context(organization):
            return StockBatch.objects.for_organization(organization).get(
                lot_number=lot_number
            )

    return _lot


def test_a_named_batch_is_taken_from_even_when_an_earlier_one_exists(
    organization, practitioner, product, branch, dated_batches
):
    """The point of the override: FEFO would have taken SOON, not LATER."""
    later = dated_batches('LATER')
    with organization_context(organization):
        movements = services.consume_from_batch(
            organization,
            batch=later,
            quantity=Decimal('4'),
            movement_type=MovementType.SALE,
            actor=practitioner,
        )

        assert len(movements) == 1
        assert movements[0].batch_id == later.pk
        assert movements[0].quantity == Decimal('-4.00')
        assert dated_batches('LATER').on_hand == Decimal('6.00')
        # And the batch FEFO would have chosen is untouched.
        assert dated_batches('SOON').on_hand == Decimal('10.00')


def test_selling_off_an_expired_batch_is_refused_with_the_lot_in_the_message(
    organization, practitioner, product, branch, dated_batches
):
    """Refused, not skipped. Someone named this lot and is owed the reason."""
    expired = dated_batches('GONE')
    with organization_context(organization):
        with pytest.raises(services.BatchExpired) as caught:
            services.consume_from_batch(
                organization,
                batch=expired,
                quantity=Decimal('1'),
                movement_type=MovementType.SALE,
                actor=practitioner,
            )

        message = str(caught.value)
        assert 'lot GONE' in message
        assert product.name in message
        # Nothing left the shelf on the way to the refusal.
        assert dated_batches('GONE').on_hand == Decimal('10.00')


def test_expired_stock_can_still_be_written_off(
    organization, practitioner, product, branch, dated_batches
):
    """The one way stock leaves a past-date batch: it is thrown away.

    Blocking wastage would strand expired stock on the shelf forever, which is
    the opposite of what the expiry alert is asking the clinic to do about it.
    """
    expired = dated_batches('GONE')
    with organization_context(organization):
        movements = services.consume_from_batch(
            organization,
            batch=expired,
            quantity=Decimal('10'),
            movement_type=MovementType.WASTAGE,
            actor=practitioner,
            reason='Past expiry, disposed of',
        )

        assert len(movements) == 1
        assert movements[0].movement_type == MovementType.WASTAGE
        assert dated_batches('GONE').on_hand == Decimal('0.00')


def test_a_named_batch_is_not_topped_up_from_its_neighbours(
    organization, practitioner, product, branch, dated_batches
):
    """Twenty usable in the branch, but only ten on the lot that was named."""
    with organization_context(organization):
        assert services.on_hand(
            organization, product=product, branch=branch
        ) == Decimal('30.00')
        with pytest.raises(services.InsufficientStock, match='lot LATER'):
            services.consume_from_batch(
                organization,
                batch=dated_batches('LATER'),
                quantity=Decimal('11'),
                movement_type=MovementType.SALE,
                actor=practitioner,
            )
        assert dated_batches('LATER').on_hand == Decimal('10.00')


def test_an_inbound_movement_type_is_rejected(
    organization, practitioner, product, branch, dated_batches
):
    """The signature takes a positive magnitude and negates it, so the type
    has to be one that actually takes stock out."""
    with (
        organization_context(organization),
        pytest.raises(services.InventoryError, match='does not take stock out'),
    ):
        services.consume_from_batch(
            organization,
            batch=dated_batches('LATER'),
            quantity=Decimal('1'),
            movement_type=MovementType.PURCHASE,
            actor=practitioner,
        )


def test_zero_and_negative_quantities_are_rejected(
    organization, practitioner, product, branch, dated_batches
):
    with organization_context(organization):
        for quantity in (Decimal('0'), Decimal('-5')):
            with pytest.raises(services.InventoryError, match='greater than zero'):
                services.consume_from_batch(
                    organization,
                    batch=dated_batches('LATER'),
                    quantity=quantity,
                    movement_type=MovementType.SALE,
                    actor=practitioner,
                )


def test_the_options_list_shows_expired_lots_rather_than_hiding_them(
    organization, practitioner, product, branch, dated_batches
):
    """A box that is physically on the shelf has to appear in the list.

    Hiding it would leave the practitioner hunting for a lot they can see in
    their hand; the refusal on submit is what tells them it cannot go out.
    """
    with organization_context(organization):
        offered = list(
            services.sellable_batches(organization, product=product, branch=branch)
        )

    assert [batch.lot_number for batch in offered] == ['GONE', 'SOON', 'LATER']


def test_emptied_batches_drop_out_of_the_options_list(
    organization, practitioner, product, branch, dated_batches
):
    """An empty past-date batch is history, not a choice at the counter."""
    with organization_context(organization):
        services.consume_from_batch(
            organization,
            batch=dated_batches('GONE'),
            quantity=Decimal('10'),
            movement_type=MovementType.WASTAGE,
            actor=practitioner,
            reason='Past expiry, disposed of',
        )
        offered = list(
            services.sellable_batches(organization, product=product, branch=branch)
        )

    assert [batch.lot_number for batch in offered] == ['SOON', 'LATER']


def _options(client, product=None, **extra):
    """Call the htmx endpoint the way the bill line does, under a formset name."""
    params = {'items-3-product': str(product.pk) if product else '', **extra}
    return client.get(reverse('inventory:batch_options'), params)


def test_staff_are_refused_the_batch_options_endpoint(client, staff):
    """It reads the shelf, so it sits behind the same door as the rest of it."""
    client.force_login(staff)
    assert _options(client).status_code == 403


def test_the_endpoint_returns_one_option_per_batch_with_the_expired_one_marked(
    client, practitioner, organization, product, branch, dated_batches
):
    client.force_login(practitioner)

    response = _options(client, product)

    assert response.status_code == 200
    batches = list(response.context['batches'])
    assert [batch.lot_number for batch in batches] == ['GONE', 'SOON', 'LATER']
    # The placeholder plus the three lots; the row counts these to decide
    # whether to show itself at all (static/js/invoice-line.js).
    assert response.content.count(b'<option') == 4
    assert b'EXPIRED' in response.content


def test_a_row_with_no_product_yet_gets_only_the_placeholder(
    client, practitioner, organization, branch, dated_batches
):
    """Every row asks on load, before anyone has chosen anything."""
    client.force_login(practitioner)

    response = _options(client)

    assert response.status_code == 200
    assert list(response.context['batches']) == []
    assert response.content.count(b'<option') == 1


def test_an_untracked_product_offers_nothing(
    client, practitioner, organization, branch, dated_batches
):
    """A consultation fee has no shelf, so the row stays out of the way."""
    with organization_context(organization):
        untracked = Product.objects.create(
            organization=organization,
            name='Dressing charge',
            sale_price=Decimal('150.00'),
            is_stock_tracked=False,
            is_sellable=True,
        )
    client.force_login(practitioner)

    response = _options(client, untracked)

    assert list(response.context['batches']) == []


def test_the_current_choice_comes_back_selected(
    client, practitioner, organization, product, branch, dated_batches
):
    """Re-rendering a saved line must not silently drop its lot."""
    later = dated_batches('LATER')
    client.force_login(practitioner)

    response = _options(client, product, selected=str(later.pk))

    assert response.context['selected'] == str(later.pk)
    assert f'value="{later.pk}" selected'.encode() in response.content


@pytest.fixture
def other_branch(organization):
    with organization_context(organization):
        return Branch.objects.create(
            organization=organization, name='Uttara Chamber', code='UTT'
        )


@pytest.fixture
def two_shelves(organization, branch, other_branch, practitioner, product, receive):
    """The same product on two shelves, one lot each, told apart by lot number."""
    for target, lot in ((branch, 'MAIN01'), (other_branch, 'UTT01')):
        receive(
            organization,
            branch=target,
            actor=practitioner,
            lines=[
                {
                    'product': product,
                    'quantity': Decimal('10'),
                    'lot_number': lot,
                    'expiry_date': _days(200),
                }
            ],
        )


def test_batch_options_are_scoped_to_the_branch_the_bill_is_for(
    client, practitioner, organization, product, branch, other_branch, two_shelves
):
    """The shelf is per branch, so the lots offered on a line must be too.

    The bill's branch select rides along on the request
    (``hx-include="[name='branch']"`` in templates/billing/_line_row.html); this
    is the server half of that contract.
    """
    client.force_login(practitioner)

    at_main = _options(client, product, branch=str(branch.pk))
    at_other = _options(client, product, branch=str(other_branch.pk))

    assert [b.lot_number for b in at_main.context['batches']] == ['MAIN01']
    assert [b.lot_number for b in at_other.context['batches']] == ['UTT01']


def test_with_two_branches_and_no_choice_yet_nothing_is_offered(
    client, practitioner, organization, product, branch, other_branch, two_shelves
):
    """Guessing a shelf would be worse than waiting for one to be picked."""
    client.force_login(practitioner)

    response = _options(client, product)

    assert list(response.context['batches']) == []
