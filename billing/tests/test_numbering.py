"""Invoice numbering: contiguous, unique, and under concurrency.

A gap in an invoice run reads as a deleted transaction, which is a trust
problem rather than a cosmetic one, so this is tested with real threads and not
only by calling the allocator in a loop.
"""

import threading
from decimal import Decimal

import pytest
from django.db import connection, transaction

from billing.models import Invoice, InvoiceItem, LineType
from billing.services import next_invoice_number
from core.context import organization_context
from core.models import DocumentSequence
from core.services import current_period
from patients.models import Patient

pytestmark = pytest.mark.django_db

WORKERS = 8


def _issue_invoice(organization, patient) -> str:
    """One invoice, numbered inside the transaction that writes it."""
    with organization_context(organization), transaction.atomic():
        invoice = Invoice.objects.create(
            organization=organization,
            patient=patient,
            currency=organization.currency,
            number=next_invoice_number(organization),
        )
        InvoiceItem.objects.create(
            organization=organization,
            invoice=invoice,
            line_type=LineType.OTHER,
            name_snapshot='Consultation fee',
            quantity=Decimal('1'),
            unit_price=Decimal('500.00'),
        )
        return invoice.number


def test_numbers_are_sequential_and_carry_the_period(organization, patient):
    numbers = [_issue_invoice(organization, patient) for _ in range(3)]
    period = current_period()
    assert numbers == [
        f'INV-{period}-0001',
        f'INV-{period}-0002',
        f'INV-{period}-0003',
    ]


def test_each_organization_numbers_from_one(organization, other_organization, patient):
    with organization_context(other_organization):
        theirs = Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Kamal Hossain'
        )
    mine = _issue_invoice(organization, patient)
    yours = _issue_invoice(other_organization, theirs)
    assert mine == yours  # same string, different tenants, no collision
    assert _issue_invoice(organization, patient).endswith('0002')


def test_a_rolled_back_invoice_returns_its_number(organization, patient):
    """Gap-free means gap-free: an abandoned save must not burn a number."""
    first = _issue_invoice(organization, patient)

    with (
        pytest.raises(RuntimeError),
        organization_context(organization),
        transaction.atomic(),
    ):
        Invoice.objects.create(
            organization=organization,
            patient=patient,
            currency=organization.currency,
            number=next_invoice_number(organization),
        )
        raise RuntimeError('something went wrong mid-save')

    second = _issue_invoice(organization, patient)
    period = current_period()
    assert (first, second) == (f'INV-{period}-0001', f'INV-{period}-0002')


@pytest.mark.django_db(transaction=True)
def test_concurrent_creation_produces_no_gaps_and_no_duplicates(organization, patient):
    """The guarantee is a row lock, so it is only meaningful on a real one.

    SQLite has no ``SELECT … FOR UPDATE`` and serializes writers at the file
    level instead, so running this there would test the wrong thing. CI runs
    against Postgres (SPEC §9); locally, use the compose database.
    """
    if connection.vendor != 'postgresql':
        pytest.skip('Row-level locking is a Postgres guarantee; SQLite has none.')

    numbers: list[str] = []
    failures: list[BaseException] = []
    start = threading.Barrier(WORKERS)

    def worker():
        try:
            # Every thread lines up first, so the allocations actually overlap
            # rather than politely following one another.
            start.wait(timeout=10)
            numbers.append(_issue_invoice(organization, patient))
        except BaseException as error:  # reported after the join below
            failures.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not failures, f'concurrent allocation raised: {failures}'
    period = current_period()
    assert sorted(numbers) == [f'INV-{period}-{n:04d}' for n in range(1, WORKERS + 1)]
    assert len(set(numbers)) == WORKERS

    with organization_context(organization):
        assert Invoice.objects.count() == WORKERS
        sequence = DocumentSequence.objects.get(kind='INVOICE', period=period)
        assert sequence.last_number == WORKERS
