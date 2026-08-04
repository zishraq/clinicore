"""Fixtures for the inventory tests.

Everything that touches an org-scoped model opens an explicit
``organization_context``; the contextvar is never set implicitly outside a
request (docs/adr/0005-org-scoped-default-manager.md).
"""

from decimal import Decimal

import pytest

from catalog.models import Product
from core.context import organization_context
from inventory import services


@pytest.fixture
def product(organization) -> Product:
    with organization_context(organization):
        return Product.objects.create(
            organization=organization,
            name='Paracetamol 500mg',
            unit='Tablet',
            sale_price=Decimal('2.50'),
            is_stock_tracked=True,
            is_sellable=True,
        )


@pytest.fixture
def receive(db):
    """Book a delivery in and hand back the receipt."""

    def _receive(organization, *, branch, actor, lines, **kwargs):
        with organization_context(organization):
            return services.receive_stock(
                organization, branch=branch, actor=actor, lines=lines, **kwargs
            )

    return _receive


@pytest.fixture
def stocked(organization, branch, practitioner, product, receive):
    """One batch of 20, no lot number, no expiry. The plain starting point."""
    receive(
        organization,
        branch=branch,
        actor=practitioner,
        lines=[
            {
                'product': product,
                'quantity': Decimal('20'),
                'cost_price': Decimal('1.20'),
            }
        ],
    )
    return product
