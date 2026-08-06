"""Reversal semantics for a payment (B9).

Correction is by reversal, never by editing money away, so the interesting
properties are all about the second attempt: a double-click, a stale object in
the caller's hand, a reason that is only whitespace. The arithmetic that a void
stops counting lives in ``test_balances.py``.

``void_payment`` reads the row under ``select_for_update`` for the same reason
``void_invoice`` does. Today two clicks write the same values and the lock only
makes the two functions symmetric — but voiding an invoice already grew a side
effect (returning stock), and this is the function that would grow the next one.
"""

from decimal import Decimal

import pytest

from billing import services
from billing.models import Payment
from core.context import organization_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def payment(organization, patient, practitioner, make_invoice) -> Payment:
    invoice = make_invoice(
        organization,
        patient=patient,
        actor=practitioner,
        lines=[('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))],
    )
    with organization_context(organization):
        return services.record_payment(
            organization,
            invoice=invoice,
            actor=practitioner,
            amount=Decimal('500.00'),
            method='CASH',
        )


def test_voiding_requires_a_reason(organization, practitioner, payment):
    with organization_context(organization), pytest.raises(services.BillingError):
        services.void_payment(payment, actor=practitioner, reason='')

    payment.refresh_from_db()
    assert not payment.is_void


def test_a_whitespace_reason_is_no_reason(organization, practitioner, payment):
    with organization_context(organization), pytest.raises(services.BillingError):
        services.void_payment(payment, actor=practitioner, reason='   \n  ')

    payment.refresh_from_db()
    assert not payment.is_void


def test_the_returned_row_is_the_one_that_was_written(
    organization, practitioner, payment
):
    """The caller gets the locked row back, so it needs no refresh of its own."""
    with organization_context(organization):
        returned = services.void_payment(
            payment, actor=practitioner, reason='Typed twice'
        )
    assert returned.pk == payment.pk
    assert returned.is_void
    assert returned.void_reason == 'Typed twice'
    assert returned.voided_by == practitioner


def test_voiding_twice_does_not_overwrite_the_first_reversal(
    organization, practitioner, owner, payment
):
    """A double-click must not rewrite who voided it, when, or why."""
    with organization_context(organization):
        first = services.void_payment(payment, actor=practitioner, reason='Typed twice')
        second = services.void_payment(
            payment, actor=owner, reason='Something else entirely'
        )

    assert second.pk == first.pk
    assert second.voided_at == first.voided_at
    assert second.voided_by == practitioner
    assert second.void_reason == 'Typed twice'

    payment.refresh_from_db()
    assert payment.void_reason == 'Typed twice'


def test_a_stale_object_does_not_resurrect_the_payment(
    organization, practitioner, payment
):
    """The row is re-read under the lock, so a caller's stale copy cannot win."""
    with organization_context(organization):
        stale = Payment.all_objects.get(pk=payment.pk)
        services.void_payment(payment, actor=practitioner, reason='Typed twice')

        # ``stale`` still believes the payment is live. Voiding through it must
        # find the row already void rather than stamping it again.
        returned = services.void_payment(
            stale, actor=practitioner, reason='Second attempt'
        )

    assert returned.void_reason == 'Typed twice'
    assert (
        Payment.all_objects.filter(pk=payment.pk, voided_at__isnull=False).count() == 1
    )


def test_an_over_long_reason_is_truncated_to_the_column(
    organization, practitioner, payment
):
    with organization_context(organization):
        returned = services.void_payment(payment, actor=practitioner, reason='x' * 400)
    assert len(returned.void_reason) == 300
