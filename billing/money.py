"""Where money rounding happens, so it happens in exactly one place.

Every amount is a ``Decimal`` quantized to two places with ``ROUND_HALF_UP``
(SPEC §4 — never float). The rounding points are deliberate and there are only
two of them:

1. ``InvoiceItem.line_total`` is rounded when the line is saved.
2. ``Payment.amount`` is rounded when the payment is recorded.

Everything downstream — the amount due, the amount paid, the balance — is a sum
of already-rounded columns, so it needs no rounding of its own and cannot drift
away from what the receipt shows. Display never rounds.
"""

from decimal import ROUND_HALF_UP, Decimal

__all__ = ['ZERO', 'to_money']

#: Two decimal places. Currencies with other minor units are a later problem —
#: they need per-currency exponents, not a different rounding rule here.
_EXPONENT = Decimal('0.01')

ZERO = Decimal('0.00')


def to_money(value) -> Decimal:
    """Quantize ``value`` to the money scale, half-up."""
    if value is None:
        return ZERO
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_EXPONENT, rounding=ROUND_HALF_UP)
