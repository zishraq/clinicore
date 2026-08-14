"""Telling a typed phone number from a typed name.

One rule in one place. The picker's search box takes both — "Rahima" and
"01712345678" are equally normal things to type — and whichever was typed has
to seed the right field when the receptionist registers whoever she failed to
find. Getting it wrong writes a phone number into ``full_name``, which is how a
patient ends up in the record as "01712345678".
"""

import re

__all__ = ['MIN_PHONE_DIGITS', 'dial_string', 'looks_like_phone']

#: Punctuation people put *inside* a number, none of which is dialled. Stripped
#: for both the shape test and the ``tel:`` href, so the two cannot disagree
#: about what counts as a separator.
_SEPARATORS = re.compile(r'[\s\-().]')

#: Fewer digits than this is more likely a house number, an age, or a dose than
#: a phone number, and guessing wrong costs the receptionist a retype.
MIN_PHONE_DIGITS = 6


def dial_string(value: str) -> str:
    """``value`` with its visual separators removed, ready for ``tel:``.

    A leading ``+`` survives: it is part of dialling an international number,
    not decoration.
    """
    return _SEPARATORS.sub('', value or '')


def looks_like_phone(value: str) -> bool:
    """True when the typed text is a number rather than somebody's name.

    Anything holding a letter is a name, so this stays quiet on "Ward 7" and on
    every ordinary name. ``isdecimal`` rather than ``isdigit`` because the
    latter also accepts superscripts, which are not a phone number in any
    country.
    """
    digits = dial_string(value).replace('+', '')
    return len(digits) >= MIN_PHONE_DIGITS and digits.isdecimal()
