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
from core.context import organization_context
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


def test_rebuilding_tears_down_an_appointment():
    """Same shape as the batch override, one model later.

    ``Appointment`` PROTECTs the patient and the branch it names, and nothing
    the loader generates is an appointment yet — so without a staged row the
    teardown order looks correct right up until the first demo that books one.
    """
    organization = _build()

    with organization_context(organization):
        Appointment.objects.create(
            organization=organization,
            patient=Patient.objects.first(),
            branch=Branch.objects.first(),
            scheduled_date=timezone.localdate(),
        )

    _build('--reset')

    assert Organization.objects.filter(slug=DEMO_SLUG).count() == 1
    with organization_context(Organization.objects.get(slug=DEMO_SLUG)):
        assert not Appointment.objects.exists()
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
