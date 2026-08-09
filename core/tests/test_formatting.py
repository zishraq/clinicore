"""The `qty` filter, including the money boundary it must not cross."""

from decimal import Decimal

import pytest
from django.template import Context, Template

from core.templatetags.formatting import qty


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (Decimal('12.00'), '12'),
        (Decimal('2.50'), '2.5'),
        (Decimal('0.25'), '0.25'),
        (Decimal('0.00'), '0'),
        # Stock movements are signed: a sale is negative.
        (Decimal('-3.00'), '-3'),
        (Decimal('-0.50'), '-0.5'),
        # `normalize()` on its own renders this as 1E+2.
        (Decimal('100.00'), '100'),
        (Decimal('1000.00'), '1000'),
        (12, '12'),
        ('7.10', '7.1'),
    ],
)
def test_qty_trims_trailing_zeros(value, expected):
    assert qty(value) == expected


@pytest.mark.parametrize('value', [None, ''])
def test_qty_renders_nothing_for_no_value(value):
    assert qty(value) == ''


def test_qty_hands_back_a_non_number_untouched():
    """A template bug should look like one, not like an empty cell."""
    assert qty('—') == '—'


def test_qty_never_uses_float():
    """SPEC §4: money and quantities are Decimal end to end.

    A float round-trip is invisible until it is not — 0.1 + 0.2 arriving as
    0.30000000000000004 in a stock count is the kind of thing that gets blamed
    on the counter rather than the code.
    """
    assert qty(Decimal('0.1')) == '0.1'
    assert qty(Decimal('4.35')) == '4.35'


def test_the_filter_is_loadable_and_renders():
    template = Template('{% load formatting %}{{ v|qty }}')
    assert template.render(Context({'v': Decimal('5.00')})) == '5'
