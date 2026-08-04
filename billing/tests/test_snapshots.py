"""A receipt must not change when the catalog does.

Same guarantee as ``PrescriptionItem.name_snapshot``, extended to the price: a
line keeps the name and the unit price it was billed at, forever.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import Invoice, InvoiceItem, LineType
from billing.services import next_invoice_number
from core.context import organization_context

pytestmark = pytest.mark.django_db


def test_renaming_and_repricing_a_product_leaves_billed_lines_alone(
    organization, patient, practitioner, product
):
    with organization_context(organization):
        invoice = Invoice.objects.create(
            organization=organization,
            patient=patient,
            created_by=practitioner,
            currency=organization.currency,
            number=next_invoice_number(organization),
        )
        item = InvoiceItem.objects.create(
            organization=organization,
            invoice=invoice,
            line_type=LineType.PRODUCT,
            product=product,
            name_snapshot=product.name,
            quantity=Decimal('2'),
            unit_price=product.sale_price,
        )
        assert item.line_total == Decimal('24.00')

        # Next month: the product is renamed and the price goes up.
        product.name = 'Paracetamol 665mg'
        product.sale_price = Decimal('30.00')
        product.save(update_fields=['name', 'sale_price', 'updated_at'])

        item.refresh_from_db()
        assert item.name_snapshot == 'Paracetamol 500mg'
        assert item.unit_price == Decimal('12.00')
        assert item.line_total == Decimal('24.00')
        assert Invoice.objects.with_totals().get(pk=invoice.pk).amount_due == Decimal(
            '24.00'
        )
        # The link to the catalog row survives — it is the price that does not.
        assert item.product_id == product.pk


def test_the_printed_receipt_renders_the_snapshot_not_the_catalog(
    client, organization, patient, practitioner, product
):
    with organization_context(organization):
        invoice = Invoice.objects.create(
            organization=organization,
            patient=patient,
            created_by=practitioner,
            currency=organization.currency,
            number=next_invoice_number(organization),
        )
        InvoiceItem.objects.create(
            organization=organization,
            invoice=invoice,
            line_type=LineType.PRODUCT,
            product=product,
            name_snapshot=product.name,
            quantity=Decimal('1'),
            unit_price=Decimal('12.00'),
        )
        product.name = 'Renamed after the fact'
        product.save(update_fields=['name', 'updated_at'])

    client.force_login(practitioner)
    response = client.get(reverse('billing:receipt_print', args=[invoice.pk]))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'Paracetamol 500mg' in body
    assert 'Renamed after the fact' not in body


def test_a_saved_line_freezes_the_catalog_name_at_save_time(
    organization, patient, practitioner, product
):
    """The snapshot is written by the model, not left to the caller."""
    with organization_context(organization):
        invoice = Invoice.objects.create(
            organization=organization,
            patient=patient,
            currency=organization.currency,
            number=next_invoice_number(organization),
        )
        item = InvoiceItem.objects.create(
            organization=organization,
            invoice=invoice,
            line_type=LineType.PRODUCT,
            product=product,
            quantity=Decimal('1'),
            unit_price=product.sale_price,
        )
        assert item.name_snapshot == 'Paracetamol 500mg'
