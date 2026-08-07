"""Every formset row removes the same way, and says so.

Three screens grew their own remove control: a bare red ✕ on the prescription
row, the same glyph on the bill row, and nothing at all on the goods receipt.
A glyph that small makes the user work out that it is a control; three
different answers make each screen a separate thing to learn.

These are coupling tests rather than pixel tests — they check the row templates
share one partial, which is what keeps them from drifting apart again.
"""

from pathlib import Path

import pytest
from django.conf import settings

ROW_TEMPLATES = (
    'clinical/_item_row.html',
    'billing/_line_row.html',
    'inventory/_receipt_row.html',
)

SHARED_BUTTON = 'partials/_remove_row_button.html'


def _read(name: str) -> str:
    return (Path(settings.BASE_DIR) / 'templates' / name).read_text(encoding='utf-8')


@pytest.mark.parametrize('template', ROW_TEMPLATES)
def test_every_formset_row_uses_the_shared_remove_button(template: str):
    body = _read(template)
    assert SHARED_BUTTON in body, (
        f'{template} does not include {SHARED_BUTTON}. Every formset row uses '
        'the one control so the three screens cannot disagree about what '
        'removing a row looks like.'
    )


@pytest.mark.parametrize('template', ROW_TEMPLATES)
def test_no_row_hand_rolls_its_own_remove_control(template: str):
    """A row growing its own again is what this partial exists to prevent."""
    body = _read(template)
    assert '✕' not in body
    assert 'btn-ghost btn-sm text-[var(--cc-danger)]' not in body


def test_the_shared_button_is_a_filled_square_matching_the_inputs():
    """Icon only, but still filled — the colour is what reads as clickable.

    ``btn-square`` at the default size is 3rem, which is the height of
    ``input input-bordered``; ``self-end`` puts it on the inputs' baseline
    rather than a label's height above it.
    """
    body = _read(SHARED_BUTTON)
    assert '&times;' in body, 'the control is the icon, not the word'
    assert '>Remove<' not in body
    assert 'btn-square' in body, 'it must be square and input-height'
    assert 'self-end' in body, 'it must sit on the row baseline, not float'
    assert 'bg-[var(--cc-danger)]' in body, 'it must be filled, in the danger colour'


def test_the_icon_only_button_still_has_an_accessible_name():
    """Dropping the word must not drop the meaning for a screen reader."""
    assert 'aria-label="Remove"' in _read(SHARED_BUTTON)


@pytest.mark.parametrize('template', ROW_TEMPLATES)
def test_every_row_includes_it_without_parameters(template: str):
    """Identical on all three screens, so it takes nothing that could differ."""
    assert "{% include 'partials/_remove_row_button.html' %}" in _read(template), (
        f'{template} passes arguments to a control that is meant to be identical'
    )


def test_the_shared_button_calls_the_rows_own_remove():
    """Each row provides remove(); the button knows nothing about formsets."""
    assert '@click="remove()"' in _read(SHARED_BUTTON)
