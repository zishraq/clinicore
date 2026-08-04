"""The counter workflow, end to end through the views.

Create a bill from a completed visit, take a partial payment, take the rest,
print the receipt — plus what happens when someone tries to edit a bill that
has already been paid against.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import Invoice, InvoiceState, LineType, PaymentStatus
from billing.tests.conftest import invoice_payload
from core.context import organization_context

pytestmark = pytest.mark.django_db


def test_creating_a_bill_from_a_visit_prefills_the_consultation_fee(
    client, practitioner, organization, encounter
):
    organization.default_consultation_fee = Decimal('750.00')
    organization.save(update_fields=['default_consultation_fee', 'updated_at'])

    client.force_login(practitioner)
    response = client.get(
        reverse('billing:invoice_create'), {'encounter': encounter.pk}
    )
    assert response.status_code == 200
    formset = response.context['item_formset']
    first = formset.forms[0].initial
    assert first['display_name'] == organization.terms['consultation_fee']
    assert first['unit_price'] == Decimal('750.00')
    assert first['line_type'] == LineType.CONSULTATION
    # And the visit and its patient come along.
    assert response.context['form'].initial['encounter'] == encounter.pk
    assert response.context['form'].initial['patient'] == encounter.patient_id


def test_the_full_counter_flow(
    client, practitioner, organization, patient, encounter, product
):
    client.force_login(practitioner)

    payload = invoice_payload(
        patient,
        encounter=encounter.pk,
        **{
            'items-TOTAL_FORMS': '2',
            'items-1-display_name': 'ignored, the product name wins',
            'items-1-line_type': LineType.PRODUCT,
            'items-1-product': product.pk,
            'items-1-quantity': '10',
            'items-1-unit_price': '12.00',
            'items-1-discount': '20.00',
            'items-1-sort_order': '1',
        },
    )
    response = client.post(reverse('billing:invoice_create'), payload, follow=True)
    assert response.status_code == 200

    with organization_context(organization):
        invoice = Invoice.objects.with_totals().get()
        assert invoice.number.startswith('INV-')
        assert invoice.currency == organization.currency
        assert invoice.encounter_id == encounter.pk
        assert invoice.created_by == practitioner
        names = [item.name_snapshot for item in invoice.items.all()]
        assert names == ['Consultation fee', 'Paracetamol 500mg']
        # 500 + (10 * 12 - 20) = 600
        assert invoice.amount_due == Decimal('600.00')
        assert invoice.payment_status == PaymentStatus.UNPAID

    # Partial payment at the counter.
    client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '250.00', 'method': 'CASH', 'note': ''},
    )
    with organization_context(organization):
        partly = Invoice.objects.with_totals().get(pk=invoice.pk)
        assert partly.balance == Decimal('350.00')
        assert partly.payment_status == PaymentStatus.PARTIALLY_PAID

    # Overpaying the remainder is refused, and nothing is recorded.
    client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '400.00', 'method': 'CASH', 'note': ''},
    )
    with organization_context(organization):
        assert Invoice.objects.with_totals().get(pk=invoice.pk).balance == Decimal(
            '350.00'
        )

    # The patient comes back and settles it.
    client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '350.00', 'method': 'MOBILE', 'note': 'bKash'},
    )
    with organization_context(organization):
        settled = Invoice.objects.with_totals().get(pk=invoice.pk)
        assert settled.balance == Decimal('0.00')
        assert settled.payment_status == PaymentStatus.PAID

    receipt = client.get(reverse('billing:receipt_print', args=[invoice.pk]))
    body = receipt.content.decode()
    assert receipt.status_code == 200
    assert 'Consultation fee' in body
    assert 'Paracetamol 500mg' in body
    assert '600.00' in body  # total
    assert '350.00' in body  # the second payment


def test_a_bill_with_payments_cannot_be_edited(
    client, practitioner, organization, patient, make_invoice
):
    invoice = make_invoice(organization, patient=patient, actor=practitioner)
    client.force_login(practitioner)
    client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '100.00', 'method': 'CASH', 'note': ''},
    )

    response = client.get(reverse('billing:invoice_update', args=[invoice.pk]))
    assert response.status_code == 302

    response = client.post(
        reverse('billing:invoice_update', args=[invoice.pk]),
        invoice_payload(patient, **{'items-0-unit_price': '9999.00'}),
    )
    assert response.status_code == 302
    with organization_context(organization):
        assert Invoice.objects.with_totals().get(pk=invoice.pk).amount_due == Decimal(
            '500.00'
        )


def test_voiding_a_bill_needs_a_reason_and_records_the_actor(
    client, practitioner, organization, patient, make_invoice
):
    invoice = make_invoice(organization, patient=patient, actor=practitioner)
    client.force_login(practitioner)

    client.post(reverse('billing:invoice_void', args=[invoice.pk]), {'reason': ''})
    invoice.refresh_from_db()
    assert invoice.status == InvoiceState.ISSUED

    client.post(
        reverse('billing:invoice_void', args=[invoice.pk]),
        {'reason': 'Raised against the wrong patient'},
    )
    invoice.refresh_from_db()
    assert invoice.status == InvoiceState.VOID
    assert invoice.voided_by == practitioner
    assert invoice.void_reason == 'Raised against the wrong patient'

    # And a voided bill takes no more money.
    client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '10.00', 'method': 'CASH', 'note': ''},
    )
    with organization_context(organization):
        assert Invoice.objects.with_totals().get(pk=invoice.pk).amount_paid == Decimal(
            '0.00'
        )


def test_a_bill_with_payments_cannot_be_voided_until_they_are(
    client, practitioner, organization, patient, make_invoice
):
    invoice = make_invoice(organization, patient=patient, actor=practitioner)
    client.force_login(practitioner)
    client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '100.00', 'method': 'CASH', 'note': ''},
    )
    client.post(
        reverse('billing:invoice_void', args=[invoice.pk]), {'reason': 'Mistake'}
    )
    invoice.refresh_from_db()
    assert invoice.status == InvoiceState.ISSUED

    with organization_context(organization):
        payment = invoice.payments.get()
    client.post(
        reverse('billing:payment_void', args=[invoice.pk, payment.pk]),
        {'reason': 'Recorded on the wrong bill'},
    )
    client.post(
        reverse('billing:invoice_void', args=[invoice.pk]), {'reason': 'Mistake'}
    )
    invoice.refresh_from_db()
    assert invoice.status == InvoiceState.VOID


def test_a_bill_needs_at_least_one_line(client, practitioner, organization, patient):
    client.force_login(practitioner)
    payload = invoice_payload(
        patient,
        **{
            'items-0-display_name': '',
            'items-0-unit_price': '',
            'items-0-line_type': '',
        },
    )
    response = client.post(reverse('billing:invoice_create'), payload)
    assert response.status_code == 200
    assert response.context['item_formset'].non_form_errors()
    with organization_context(organization):
        assert Invoice.objects.count() == 0


def test_the_add_line_endpoint_renumbers_the_formset_prefix(client, practitioner):
    client.force_login(practitioner)
    response = client.get(reverse('billing:line_row'), {'items-TOTAL_FORMS': '3'})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'items-3-unit_price' in body
    assert '__prefix__' not in body


def test_the_patient_page_shows_what_is_still_owed(
    client, practitioner, organization, patient, make_invoice
):
    make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    client.force_login(practitioner)
    response = client.get(reverse('patients:detail', args=[patient.pk]))
    assert response.status_code == 200
    assert response.context['outstanding'] == Decimal('500.00')
    assert len(response.context['invoices']) == 1
