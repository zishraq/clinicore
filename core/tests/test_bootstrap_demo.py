"""The demo loader, mostly for its teardown.

``--reset`` deletes a whole organization in an order the PROTECT constraints
dictate, and that order is easy to get subtly wrong: it only breaks once some
row that is normally absent turns up. A bill line carrying a batch override is
exactly such a row — it PROTECTs the lot it named, and it survives its own
stock movements — so the second build is what fails, not the first.
"""

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from billing.models import Invoice, InvoiceItem
from catalog.models import Product
from core.context import organization_context, organization_timezone
from inventory.models import StockBatch, StockMovement
from organizations.models import Branch, Organization
from patients.models import Patient
from scheduling.models import Appointment

pytestmark = pytest.mark.django_db

DEMO_SLUG = 'demo-clinic'


def _build(*args):
    call_command('bootstrap_demo', *args, stdout=StringIO())
    return Organization.objects.get(slug=DEMO_SLUG)


def test_the_demo_builds_a_shelf_with_all_three_alert_states():
    """A demo whose stock is all healthy never shows the dashboard alerts."""
    from inventory.services import stock_alerts

    organization = _build()

    with organization_context(organization):
        assert StockBatch.objects.exists()
        # Issuing the demo bills is what took the stock off, so the ledger has
        # sales on it and not only the opening purchases.
        assert StockMovement.objects.filter(movement_type='SALE').exists()
        alerts = stock_alerts(organization)
        assert alerts['below_reorder'].exists()
        assert alerts['expiring'].exists()
        assert alerts['expired'].exists()


def test_rebuilding_tears_down_a_bill_line_that_named_a_batch():
    """The regression: batches cannot go before the invoice lines pointing at them.

    Nothing in the generated data uses the override — the demo bills all take
    stock FEFO — so this has to be staged by hand to be caught at all.
    """
    organization = _build()

    with organization_context(organization):
        batch = StockBatch.objects.first()
        item = InvoiceItem.objects.filter(product__isnull=False).first()
        item.batch = batch
        item.save(update_fields=['batch', 'updated_at'])

    _build('--reset')

    # The old organization is gone, override and all, and a fresh one stands.
    assert Organization.objects.filter(slug=DEMO_SLUG).count() == 1
    with organization_context(Organization.objects.get(slug=DEMO_SLUG)):
        assert Invoice.objects.exists()
        assert StockBatch.objects.exists()


def test_the_demo_opens_the_day_list_on_every_state():
    """An empty first screen demonstrates nothing, and neither does a uniform one.

    The day view is what a receptionist is shown first, so the loader has to put
    all five states on today — including the two that are decisions rather than
    timestamps.
    """
    from scheduling.models import AppointmentStatus

    organization = _build()

    # On the clinic's calendar, not the server's. The demo org is Asia/Dhaka, so
    # for the six hours after midnight there the two disagree — and the loader
    # files "today" under the clinic's. Reading it in UTC here made this fail at
    # 06:0x Dhaka, which is exactly the bug ADR 0011 was about, in a test.
    with organization_context(organization), organization_timezone(organization):
        today = Appointment.objects.filter(scheduled_date=timezone.localdate())
        assert {row.status for row in today} == set(AppointmentStatus.values)
        # Both ways a row reaches the list, so the "Walk-in" label is visible.
        assert today.filter(source='WALK_IN').exists()
        assert today.filter(source='BOOKED').exists()
        # A time and a day part are mutually exclusive, and the demo shows both
        # kinds of answer rather than inventing precision (ADR 0010).
        assert today.filter(scheduled_time__isnull=False).exists()
        assert today.exclude(day_part='').exists()


def test_the_seen_rows_show_both_answers_the_payment_column_can_give():
    """One visit billed and one not, so the column shows a state and its absence."""
    from scheduling.services import with_bills

    organization = _build()

    with organization_context(organization), organization_timezone(organization):
        seen = Appointment.objects.filter(
            scheduled_date=timezone.localdate(), seen_at__isnull=False
        ).select_related('encounter')
        bills = [row.bill for row in with_bills(organization, seen)]

    assert any(bill is not None for bill in bills)
    assert any(bill is None for bill in bills)


def test_rebuilding_tears_down_an_appointment():
    """Same shape as the batch override, one model later.

    ``Appointment`` PROTECTs the patient and the branch it names. The loader now
    books its own, so the ordinary rebuild exercises this — the staged row is
    kept because it is the one with no encounter, invoice or movement attached,
    and so fails on the FK order alone rather than on something upstream.
    """
    organization = _build()

    with organization_context(organization):
        staged = Appointment.objects.create(
            organization=organization,
            patient=Patient.objects.first(),
            branch=Branch.objects.first(),
            scheduled_date=timezone.localdate(),
        )

    _build('--reset')

    assert Organization.objects.filter(slug=DEMO_SLUG).count() == 1
    # The staged row by pk, not "no appointments at all": the rebuild books its
    # own, and a teardown that left this one behind would have raised on the
    # patient it PROTECTs long before this line.
    assert not Appointment.all_objects.filter(pk=staged.pk).exists()
    with organization_context(Organization.objects.get(slug=DEMO_SLUG)):
        assert Patient.objects.exists()


def test_a_second_build_without_reset_leaves_the_first_alone():
    organization = _build()
    with organization_context(organization):
        before = Product.objects.count()

    _build()

    assert Organization.objects.filter(slug=DEMO_SLUG).count() == 1
    with organization_context(organization):
        assert Product.objects.count() == before


def test_every_tracked_product_priced_and_levelled():
    """Zero is a real answer for a reorder level; a missing price is not."""
    organization = _build()

    with organization_context(organization):
        tracked = Product.objects.filter(is_stock_tracked=True)
        assert tracked.exists()
        assert not tracked.filter(sale_price__lte=Decimal('0')).exists()
        assert not tracked.filter(reorder_level__lt=Decimal('0')).exists()
