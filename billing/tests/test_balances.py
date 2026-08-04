"""Balance arithmetic: the one thing nobody forgives a clinic system for.

Partial payments and instalments are the normal case here, not an edge case, so
they are what these tests are mostly about.
"""

from decimal import Decimal

import pytest

from billing import services
from billing.models import Invoice, PaymentStatus
from core.context import organization_context

pytestmark = pytest.mark.django_db


def _record(organization, invoice, actor, amount, method='CASH'):
    with organization_context(organization):
        return services.record_payment(
            organization,
            invoice=invoice,
            actor=actor,
            amount=Decimal(amount),
            method=method,
        )


def test_a_new_invoice_is_unpaid_and_owes_its_total(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[
            ('Consultation fee', 1, Decimal('500.00'), Decimal('0.00')),
            ('Paracetamol 500mg', 10, Decimal('2.50'), Decimal('5.00')),
        ],
    )
    with organization_context(organization):
        invoice = Invoice.objects.with_totals().get(pk=invoice.pk)
        # 500 + (10 * 2.50 - 5) = 520
        assert invoice.amount_due == Decimal('520.00')
        assert invoice.amount_paid == Decimal('0.00')
        assert invoice.balance == Decimal('520.00')
        assert invoice.payment_status == PaymentStatus.UNPAID


def test_partial_payments_accumulate_and_close_the_invoice(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )

    _record(organization, invoice, practitioner, '200.00')
    with organization_context(organization):
        partly = Invoice.objects.with_totals().get(pk=invoice.pk)
        assert partly.amount_paid == Decimal('200.00')
        assert partly.balance == Decimal('300.00')
        assert partly.payment_status == PaymentStatus.PARTIALLY_PAID

    # Instalments: three more visits to the counter, one of them tiny.
    _record(organization, invoice, practitioner, '150.00')
    _record(organization, invoice, practitioner, '149.50', method='MOBILE')
    _record(organization, invoice, practitioner, '0.50')

    with organization_context(organization):
        settled = Invoice.objects.with_totals().get(pk=invoice.pk)
        assert settled.amount_paid == Decimal('500.00')
        assert settled.balance == Decimal('0.00')
        assert settled.payment_status == PaymentStatus.PAID
        assert settled.payments.count() == 4


def test_overpayment_is_rejected_with_a_usable_message(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    _record(organization, invoice, practitioner, '450.00')

    with pytest.raises(services.Overpayment) as caught:
        _record(organization, invoice, practitioner, '100.00')
    # The message has to name the number the practitioner should type instead.
    assert '50.00' in str(caught.value)

    with organization_context(organization):
        assert Invoice.objects.with_totals().get(pk=invoice.pk).amount_paid == Decimal(
            '450.00'
        )


def test_a_payment_of_exactly_the_balance_is_allowed(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    _record(organization, invoice, practitioner, '500.00')
    with organization_context(organization):
        assert Invoice.objects.with_totals().get(pk=invoice.pk).balance == Decimal(
            '0.00'
        )


def test_zero_and_negative_payments_are_refused(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(organization, patient=patient, actor=practitioner)
    for amount in ('0.00', '-10.00'):
        with pytest.raises(services.BillingError):
            _record(organization, invoice, practitioner, amount)


def test_line_totals_round_half_up_at_two_places(
    organization, patient, practitioner, make_invoice
):
    """Rounding happens per line, and the invoice total is the sum of those."""
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[
            ('Syrup', 3, Decimal('0.335'), Decimal('0.00')),
            ('Dressing', 1, Decimal('0.125'), Decimal('0.00')),
        ],
    )
    with organization_context(organization):
        items = list(invoice.items.all())
        assert items[0].line_total == Decimal('1.01')  # 1.005 → 1.01
        assert items[1].line_total == Decimal('0.13')  # 0.125 → 0.13
        assert Invoice.objects.with_totals().get(pk=invoice.pk).amount_due == Decimal(
            '1.14'
        )


def test_balance_ignores_voided_payments(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    payment = _record(organization, invoice, practitioner, '500.00')

    with organization_context(organization):
        services.void_payment(payment, actor=practitioner, reason='Typed twice')
        reopened = Invoice.objects.with_totals().get(pk=invoice.pk)
        assert reopened.amount_paid == Decimal('0.00')
        assert reopened.balance == Decimal('500.00')
        assert reopened.payment_status == PaymentStatus.UNPAID
        # The record itself survives, with who did it and why.
        payment.refresh_from_db()
        assert payment.is_void
        assert payment.void_reason == 'Typed twice'
        assert payment.voided_by == practitioner


def test_outstanding_balance_sums_a_patients_open_invoices(
    organization, patient, practitioner, make_invoice
):
    first = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Dressing', 1, Decimal('120.00'), Decimal('0.00'))],
    )
    _record(organization, first, practitioner, '200.00')

    with organization_context(organization):
        assert services.outstanding_balance(organization, patient) == Decimal('420.00')


def test_a_voided_invoice_stops_counting_towards_what_is_owed(
    organization, patient, practitioner, make_invoice
):
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    with organization_context(organization):
        services.void_invoice(
            invoice, actor=practitioner, reason='Billed the wrong patient'
        )
        assert services.outstanding_balance(organization, patient) == Decimal('0.00')
        assert (
            Invoice.objects.with_totals().get(pk=invoice.pk).payment_status
            == PaymentStatus.VOID
        )


def test_payment_status_filters_select_the_right_invoices(
    organization, patient, practitioner, make_invoice
):
    unpaid = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('A', 1, Decimal('100.00'), Decimal('0.00'))],
    )
    part = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('B', 1, Decimal('100.00'), Decimal('0.00'))],
    )
    paid = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('C', 1, Decimal('100.00'), Decimal('0.00'))],
    )
    _record(organization, part, practitioner, '40.00')
    _record(organization, paid, practitioner, '100.00')

    with organization_context(organization):
        selected = {
            status: {
                invoice.pk
                for invoice in services.filter_invoices(organization, status=status)
            }
            for status in (
                PaymentStatus.UNPAID,
                PaymentStatus.PARTIALLY_PAID,
                PaymentStatus.PAID,
            )
        }
    assert selected[PaymentStatus.UNPAID] == {unpaid.pk}
    assert selected[PaymentStatus.PARTIALLY_PAID] == {part.pk}
    assert selected[PaymentStatus.PAID] == {paid.pk}
