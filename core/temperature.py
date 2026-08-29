"""One canonical unit in the column, the clinic's unit on the screen.

Temperature is stored in **Fahrenheit, always, in one column**, and
``Organization.temperature_unit`` decides only what the input is labelled, what
it accepts, and what is rendered back. It never decides what a stored number
means.

That asymmetry is the whole design. A unit flag that reinterprets stored values
would make flipping the setting silently rewrite every reading ever recorded —
a 38 taken in Celsius becoming a 38 read as Fahrenheit, with nothing to
distinguish the two and no migration able to tell them apart afterwards. It is
the same family as the rule that keeps an invoice balance derived rather than
stored (ADR 0008) and stock on hand summed rather than counted (ADR 0009): one
fact, recorded once, and every other view of it computed.

Fahrenheit is canonical rather than Celsius because the clinic this was built
for works in Fahrenheit, and the unit that is never converted is the unit whose
values are exactly what somebody typed.

Amends docs/adr/0020-the-case-record.md, whose original §7 stored Celsius only.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models

__all__ = [
    'BOUNDS',
    'TemperatureUnit',
    'for_display',
    'symbol',
    'to_canonical',
]


class TemperatureUnit(models.TextChoices):
    FAHRENHEIT = 'F', 'Fahrenheit (°F)'
    CELSIUS = 'C', 'Celsius (°C)'


#: Body temperatures a living patient can have, per unit. Narrow on purpose:
#: the mistake worth catching is the one this whole module exists because of —
#: a number typed in the other unit. 98.6 in a Celsius box and 37 in a
#: Fahrenheit box are both refused, and nothing legitimate is.
BOUNDS = {
    TemperatureUnit.FAHRENHEIT: (Decimal('90'), Decimal('110')),
    TemperatureUnit.CELSIUS: (Decimal('32'), Decimal('43')),
}

#: One decimal place, which is the resolution of every clinical thermometer
#: this will ever meet. 0.1 °F is finer than 0.1 °C, so a Celsius value
#: survives the round trip through the canonical column unchanged.
_PLACES = Decimal('0.1')


def symbol(unit: str) -> str:
    """``°F`` or ``°C`` — what the label beside the box says."""
    return '°C' if unit == TemperatureUnit.CELSIUS else '°F'


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_PLACES, rounding=ROUND_HALF_UP)


def to_canonical(value, unit: str) -> Decimal | None:
    """A number entered in ``unit``, range-checked and returned in Fahrenheit.

    Validates against the unit it was *entered* in, then converts — checking a
    converted value would report the bound in a unit nobody typed.

    Raises ``ValidationError`` so a form can let this be the field's own error.
    """
    if value in (None, ''):
        return None
    try:
        entered = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError('Enter a number.') from None

    low, high = BOUNDS.get(unit, BOUNDS[TemperatureUnit.FAHRENHEIT])
    if not low <= entered <= high:
        raise ValidationError(
            f'A body temperature is between {low:g} and {high:g} {symbol(unit)}.'
        )
    if unit == TemperatureUnit.CELSIUS:
        entered = entered * Decimal(9) / Decimal(5) + Decimal(32)
    return _quantize(entered)


def for_display(value, unit: str) -> Decimal | None:
    """A stored Fahrenheit reading, rendered in the unit this clinic works in."""
    if value in (None, ''):
        return None
    stored = Decimal(str(value))
    if unit == TemperatureUnit.CELSIUS:
        stored = (stored - Decimal(32)) * Decimal(5) / Decimal(9)
    return _quantize(stored)
