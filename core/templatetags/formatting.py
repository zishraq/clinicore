"""Display formatting that is a house rule rather than a Django default.

**Quantities and money are formatted differently, on purpose. Do not unify
them.** The two look like the same thing — both are two-place Decimals in the
database (`_QUANTITY` and `_AMOUNT` in inventory/models.py are literally the
same dict) — but they answer different questions and read differently to the
person holding the box:

- **A quantity is a count.** Twelve boxes is ``12``, not ``12.00``. The trailing
  zeros are noise that makes a shelf count look like a price, and a column of
  them is harder to scan for the number that is wrong. The scale exists because
  quantities *can* be fractional — half a bottle, 2.5 ml — so ``2.5`` keeps its
  decimal and ``12`` does not grow one.
- **Money keeps both places, always.** ``89.5`` is not a price; a receipt that
  prints one looks wrong to anybody who has seen a receipt, and a bill that
  renders ``100`` next to ``99.50`` is harder to add up by eye. Amounts are
  rounded to exactly two places in ``billing/money.py`` and rendered as stored.

So: ``{{ item.quantity|qty }}`` and ``{{ invoice.balance }}``. If a future
change makes money pass through here, it is a bug, not a tidy-up.
"""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

__all__ = ['qty']


@register.filter
def qty(value):
    """Render a quantity without trailing zeros: 12.00 → 12, 2.50 → 2.5.

    Money must not use this — see the module docstring for why.
    """
    if value is None or value == '':
        return ''
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        # Not a number: hand it back untouched rather than swallow it into an
        # empty cell. A template bug should look like one.
        return value

    # `normalize()` alone turns 100 into 1E+2, so integral values are quantized
    # back to a plain integer scale and the rest keep only the digits they need.
    normalized = number.normalize()
    if normalized == normalized.to_integral_value():
        normalized = normalized.quantize(Decimal(1))
    return f'{normalized:f}'
