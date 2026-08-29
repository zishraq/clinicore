"""One canonical column, two units on the screen.

The reversal these tests pin: ``docs/adr/0020-the-case-record.md`` §7 stored
Celsius only. Temperature is now stored in **Fahrenheit, always**, and
``Organization.temperature_unit`` decides presentation and nothing else — see
``core/temperature.py`` for why a unit flag that reinterprets stored values is
the bug being designed out.
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from core.temperature import BOUNDS, TemperatureUnit, for_display, symbol, to_canonical


def test_fahrenheit_is_stored_exactly_as_typed():
    """The canonical unit is the one that is never converted."""
    assert to_canonical('98.6', TemperatureUnit.FAHRENHEIT) == Decimal('98.6')


def test_celsius_is_converted_on_the_way_in():
    assert to_canonical('37', TemperatureUnit.CELSIUS) == Decimal('98.6')
    assert to_canonical('40', TemperatureUnit.CELSIUS) == Decimal('104.0')


def test_a_stored_reading_renders_in_the_clinics_unit():
    assert for_display(Decimal('98.6'), TemperatureUnit.FAHRENHEIT) == Decimal('98.6')
    assert for_display(Decimal('98.6'), TemperatureUnit.CELSIUS) == Decimal('37.0')


@pytest.mark.parametrize('entered', ['36.0', '36.5', '37.0', '37.1', '38.9', '42.9'])
def test_a_celsius_reading_survives_the_round_trip(entered):
    """The reason the column can be Fahrenheit without cheating the other clinic.

    0.1 °F is finer than 0.1 °C, so every distinct Celsius reading lands on a
    distinct stored value and comes back as itself. A coarser canonical unit
    would silently round a clinic's own numbers under it.
    """
    stored = to_canonical(entered, TemperatureUnit.CELSIUS)

    assert for_display(stored, TemperatureUnit.CELSIUS) == Decimal(entered)


def test_the_setting_never_changes_what_a_stored_number_means():
    """The whole argument for one canonical column, asserted directly.

    A reading taken while the clinic worked in Fahrenheit still *is* that
    reading after somebody switches the screen to Celsius. Only the rendering
    moves.
    """
    stored = to_canonical('101.2', TemperatureUnit.FAHRENHEIT)

    assert for_display(stored, TemperatureUnit.FAHRENHEIT) == Decimal('101.2')
    assert for_display(stored, TemperatureUnit.CELSIUS) == Decimal('38.4')


@pytest.mark.parametrize(
    ('entered', 'unit'),
    [
        # The mistake this range check exists for, in both directions.
        ('98.6', TemperatureUnit.CELSIUS),
        ('37', TemperatureUnit.FAHRENHEIT),
        ('0', TemperatureUnit.CELSIUS),
        ('200', TemperatureUnit.FAHRENHEIT),
    ],
)
def test_a_reading_in_the_wrong_unit_is_refused(entered, unit):
    with pytest.raises(ValidationError):
        to_canonical(entered, unit)


def test_the_refusal_names_the_unit_that_was_typed():
    """Reporting the bound in a unit nobody entered is worse than no message."""
    with pytest.raises(ValidationError) as refusal:
        to_canonical('98.6', TemperatureUnit.CELSIUS)

    assert '°C' in refusal.value.messages[0]
    assert '32' in refusal.value.messages[0]


@pytest.mark.parametrize('unit', list(TemperatureUnit))
def test_the_ends_of_each_range_are_accepted(unit):
    low, high = BOUNDS[unit]
    assert to_canonical(low, unit) is not None
    assert to_canonical(high, unit) is not None


def test_blank_stays_blank():
    """Not recorded is a legitimate answer everywhere this is used."""
    for unit in TemperatureUnit:
        assert to_canonical('', unit) is None
        assert to_canonical(None, unit) is None
        assert for_display(None, unit) is None


def test_text_is_refused_rather_than_crashing():
    with pytest.raises(ValidationError):
        to_canonical('warm', TemperatureUnit.FAHRENHEIT)


def test_the_symbol_is_what_a_label_shows():
    assert symbol(TemperatureUnit.FAHRENHEIT) == '°F'
    assert symbol(TemperatureUnit.CELSIUS) == '°C'
