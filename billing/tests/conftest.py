"""Fixtures for the billing tests.

Everything that touches an org-scoped model opens an explicit
``organization_context``; the contextvar is never set implicitly outside a
request (docs/adr/0005-org-scoped-default-manager.md).
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from billing.models import Invoice, InvoiceItem, LineType
from billing.services import next_invoice_number
from catalog.models import Product
from clinical.models import Encounter, EncounterStatus
from core.context import organization_context
from patients.models import Patient


@pytest.fixture
def patient(organization) -> Patient:
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


@pytest.fixture
def product(organization) -> Product:
    with organization_context(organization):
        return Product.objects.create(
            organization=organization,
            name='Paracetamol 500mg',
            sale_price=Decimal('12.00'),
        )


@pytest.fixture
def encounter(organization, patient, practitioner, branch) -> Encounter:
    with organization_context(organization):
        return Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            status=EncounterStatus.FINALIZED,
            finalized_at=timezone.now(),
        )


@pytest.fixture
def make_invoice(db):
    """Build an invoice with lines, straight through the models.

    Deliberately not through the form: the arithmetic tests are about the
    ledger, not about form binding.
    """

    def _make(organization, *, patient, actor=None, lines=None, encounter=None):
        lines = lines or [('Consultation fee', 1, Decimal('500.00'), Decimal('0.00'))]
        with organization_context(organization):
            invoice = Invoice.objects.create(
                organization=organization,
                created_by=actor,
                patient=patient,
                encounter=encounter,
                currency=organization.currency,
                number=next_invoice_number(organization),
            )
            for index, (name, quantity, price, discount) in enumerate(lines):
                InvoiceItem.objects.create(
                    organization=organization,
                    invoice=invoice,
                    line_type=LineType.OTHER,
                    name_snapshot=name,
                    quantity=Decimal(str(quantity)),
                    unit_price=price,
                    discount=discount,
                    sort_order=index,
                )
        return invoice

    return _make


def invoice_payload(patient, **overrides) -> dict:
    """A valid POST for the invoice form plus its one-line formset."""
    payload = {
        'patient': patient.pk,
        'encounter': '',
        'notes': '',
        'items-TOTAL_FORMS': '1',
        'items-INITIAL_FORMS': '0',
        'items-MIN_NUM_FORMS': '0',
        'items-MAX_NUM_FORMS': '1000',
        'items-0-display_name': 'Consultation fee',
        'items-0-line_type': LineType.CONSULTATION,
        'items-0-product': '',
        'items-0-quantity': '1',
        'items-0-unit_price': '500.00',
        'items-0-discount': '0',
        'items-0-sort_order': '0',
    }
    payload.update(overrides)
    return payload
