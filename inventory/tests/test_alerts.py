"""The three things a clinic has to be told about its shelf (SPEC §6.5).

Below reorder level, expiring soon, already expired. All three are derived the
same way everything else in this app is — counted off the ledger, never read
from a column (docs/adr/0009-ledger-based-stock.md) — so the interesting cases
are about what *counts* as cover, not about arithmetic.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
from core.context import organization_context
from inventory import services
from organizations.models import Branch

pytestmark = pytest.mark.django_db


def _days(offset: int):
    return timezone.localdate() + timedelta(days=offset)


@pytest.fixture
def make_product(organization):
    def _make(name, *, reorder_level=Decimal('0'), tracked=True, active=True):
        with organization_context(organization):
            return Product.objects.create(
                organization=organization,
                name=name,
                unit='Tablet',
                sale_price=Decimal('5.00'),
                is_stock_tracked=tracked,
                is_sellable=True,
                is_active=active,
                reorder_level=reorder_level,
            )

    return _make


def _alerts(organization, **kwargs):
    with organization_context(organization):
        alerts = services.stock_alerts(organization, **kwargs)
        return {
            'below_reorder': list(alerts['below_reorder']),
            'expiring': list(alerts['expiring']),
            'expired': list(alerts['expired']),
            'within_days': alerts['within_days'],
        }


def test_a_product_at_or_under_its_reorder_level_is_flagged(
    organization, branch, practitioner, make_product, receive
):
    low = make_product('Ranitidine 150mg', reorder_level=Decimal('10'))
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[{'product': low, 'quantity': Decimal('10')}],
    )

    alerts = _alerts(organization)
    # At the level, not merely under it: ten left with ten as the floor is
    # already the moment to reorder.
    assert [product.name for product in alerts['below_reorder']] == [low.name]


def test_a_reorder_level_of_zero_means_no_alert_rather_than_alert_at_nothing(
    organization, branch, practitioner, make_product, receive
):
    """The default for a clinic that has not thought about levels yet."""
    unset = make_product('Vitamin C', reorder_level=Decimal('0'))
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[{'product': unset, 'quantity': Decimal('0.01')}],
    )

    assert _alerts(organization)['below_reorder'] == []


def test_expired_stock_is_not_cover_for_a_product_running_out(
    organization, branch, practitioner, make_product, receive
):
    """The reorder alert counts usable stock only.

    A shelf holding forty expired boxes and two good ones needs reordering; the
    stock *list* still shows all forty-two, because that is what is physically
    there.
    """
    product = make_product('Cetirizine 10mg', reorder_level=Decimal('5'))
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('40'),
                'lot_number': 'OLD',
                'expiry_date': _days(-2),
            },
            {
                'product': product,
                'quantity': Decimal('2'),
                'lot_number': 'GOOD',
                'expiry_date': _days(200),
            },
        ],
    )

    alerts = _alerts(organization)
    assert [item.name for item in alerts['below_reorder']] == [product.name]
    with organization_context(organization):
        assert services.on_hand(organization, product=product) == Decimal('42.00')


def test_untracked_and_inactive_products_are_never_flagged(
    organization, branch, practitioner, make_product, receive
):
    make_product('Service fee', reorder_level=Decimal('10'), tracked=False)
    retired = make_product('Old syrup', reorder_level=Decimal('10'), active=False)
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[{'product': retired, 'quantity': Decimal('1')}],
    )

    assert _alerts(organization)['below_reorder'] == []


def test_batches_are_split_between_expiring_soon_and_already_expired(
    organization, branch, practitioner, make_product, receive
):
    product = make_product('Amoxicillin 250mg')
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'GONE',
                'expiry_date': _days(-1),
            },
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'SOON',
                'expiry_date': _days(10),
            },
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'FINE',
                'expiry_date': _days(400),
            },
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'UNDATED',
            },
        ],
    )

    alerts = _alerts(organization)
    assert [batch.lot_number for batch in alerts['expiring']] == ['SOON']
    assert [batch.lot_number for batch in alerts['expired']] == ['GONE']


def test_the_expiry_horizon_is_adjustable(
    organization, branch, practitioner, make_product, receive
):
    product = make_product('Amoxicillin 250mg')
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'FAR',
                'expiry_date': _days(90),
            }
        ],
    )

    assert _alerts(organization)['expiring'] == []
    assert [
        b.lot_number for b in _alerts(organization, within_days=120)['expiring']
    ] == ['FAR']


def test_an_emptied_batch_stops_being_an_alert(
    organization, branch, practitioner, make_product, receive
):
    """History, not a job for someone: nothing is left to throw away."""
    product = make_product('Amoxicillin 250mg')
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'GONE',
                'expiry_date': _days(-1),
            }
        ],
    )
    assert len(_alerts(organization)['expired']) == 1

    with organization_context(organization):
        batch = services.batches_for(organization, product=product).first()
        services.consume_from_batch(
            organization,
            batch=batch,
            quantity=Decimal('5'),
            movement_type='WASTAGE',
            actor=practitioner,
            reason='Past expiry, disposed of',
        )

    assert _alerts(organization)['expired'] == []


def test_alerts_can_be_narrowed_to_one_branch(
    organization, branch, practitioner, make_product, receive
):
    with organization_context(organization):
        other = Branch.objects.create(
            organization=organization, name='Second Chamber', code='TWO'
        )
    product = make_product('Amoxicillin 250mg')
    receive(
        organization,
        branch=other,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('5'),
                'lot_number': 'ELSEWHERE',
                'expiry_date': _days(-1),
            }
        ],
    )

    assert _alerts(organization, branch=branch)['expired'] == []
    assert len(_alerts(organization, branch=other)['expired']) == 1


def test_the_dashboard_shows_the_alerts_to_a_practitioner(
    client, practitioner, organization, branch, make_product, receive
):
    product = make_product('Amoxicillin 250mg', reorder_level=Decimal('10'))
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('3'),
                'lot_number': 'GONE',
                'expiry_date': _days(-1),
            }
        ],
    )

    client.force_login(practitioner)
    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 200
    alerts = response.context['stock_alerts']
    assert alerts['below_reorder']['total'] == 1
    assert alerts['expired']['total'] == 1
    assert [batch.lot_number for batch in alerts['expired']['rows']] == ['GONE']
    assert b'Expired, still on the shelf' in response.content


def test_a_healthy_shelf_renders_no_alert_section(
    client, practitioner, organization, branch, make_product, receive
):
    """An alert panel that is usually empty stops being read."""
    product = make_product('Amoxicillin 250mg', reorder_level=Decimal('1'))
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('99'),
                'expiry_date': _days(400),
            }
        ],
    )

    client.force_login(practitioner)
    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 200
    assert b'Expired, still on the shelf' not in response.content
    assert b'Below reorder level' not in response.content


def test_staff_get_a_dashboard_without_stock_alerts(
    client, staff, organization, branch, practitioner, make_product, receive
):
    """Stock is a PRACTITIONER/OWNER surface, like the rest of the app."""
    product = make_product('Amoxicillin 250mg', reorder_level=Decimal('10'))
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('1'),
                'expiry_date': _days(-1),
            }
        ],
    )

    client.force_login(staff)
    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 200
    assert 'stock_alerts' not in response.context
    assert b'Expired, still on the shelf' not in response.content
