"""Every date field is drawn by the application, and still posts ISO.

A native ``type="date"`` renders its text in the *device's* locale, so the same
field reads d/m/Y in the clinic and m/d/Y on a laptop from elsewhere, and the
page cannot influence it. All seven were replaced by flatpickr behind
``data-datepicker`` — see docs/adr/0016-one-date-picker-the-app-controls.md.

Two things have to hold and neither is visible from a status code:

* the native control does not come back, anywhere;
* the value crossing the wire is still ``Y-m-d``, because four of the seven
  consumers are ISO-only with no fallback (``parse_date`` returns ``None``,
  ``strptime`` raises).

The second is the one worth guarding hardest. Swapping the widget is a display
change right up until it is a silent data change.
"""

import datetime
import re
from pathlib import Path

import pytest
from django.conf import settings

from clinical.forms import EncounterForm
from core.context import organization_context
from inventory.forms import receipt_item_formset_class
from patients.forms import PatientForm

#: An ``<input>`` tag carrying the native type. Deliberately tag-scoped: the
#: templates discuss ``type="date"`` in prose, and prose is not a regression.
NATIVE_DATE_INPUT = re.compile(r'<input\b[^>]*type=["\']date["\']', re.IGNORECASE)

TEMPLATE_ROOTS = [Path(directory) for directory in settings.TEMPLATES[0]['DIRS']] + [
    path for path in Path(settings.BASE_DIR).glob('*/templates') if path.is_dir()
]


def _template_files() -> list[Path]:
    found: list[Path] = []
    for root in TEMPLATE_ROOTS:
        found.extend(sorted(root.rglob('*.html')))
    return found


def test_no_template_renders_a_native_date_input():
    """The OS-locale control is gone from the templates and stays gone."""
    offenders = [
        f'{path.relative_to(settings.BASE_DIR)}:{number}'
        for path in _template_files()
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if NATIVE_DATE_INPUT.search(line)
    ]
    assert offenders == [], (
        'A native date input renders in the device locale, which the page '
        'cannot override. Use data-datepicker: ' + ', '.join(offenders)
    )


def test_the_shared_picker_is_loaded_for_every_page():
    """Not from a per-page scripts block — two fields live in shared modals."""
    base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text()
    assert 'js/date-picker.js' in base
    assert 'flatpickr' in base


@pytest.mark.django_db
def test_the_three_django_widgets_are_handed_to_the_picker(organization):
    """``core.forms.date_widget``, not ``DateInput(type='date')``."""
    with organization_context(organization):
        widgets = {
            'date_of_birth': PatientForm(organization=organization)['date_of_birth'],
            'follow_up_date': EncounterForm(organization=organization)[
                'follow_up_date'
            ],
            'expiry_date': receipt_item_formset_class()(prefix='items').forms[0][
                'expiry_date'
            ],
        }
    for name, bound in widgets.items():
        rendered = str(bound)
        assert 'data-datepicker' in rendered, name
        assert not NATIVE_DATE_INPUT.search(rendered), name


@pytest.mark.django_db
def test_a_stored_date_renders_back_as_iso_not_as_the_display_format(organization):
    """What flatpickr reads on load. d/m/Y in the value would be a wrong date."""
    form = PatientForm(
        organization=organization,
        initial={'date_of_birth': datetime.date(1979, 4, 2)},
    )
    rendered = str(form['date_of_birth'])
    assert 'value="1979-04-02"' in rendered
    assert '02/04/1979' not in rendered


@pytest.mark.django_db
def test_iso_still_parses_on_the_way_in(organization):
    """The posted value is unchanged, so the three fields still bind."""
    with organization_context(organization):
        patient = PatientForm(
            data={
                'full_name': 'Rahima Begum',
                'phone': '01812345678',
                'sex': 'F',
                'date_of_birth': '1979-04-02',
                'address': '',
                'registered_branch': '',
            },
            organization=organization,
        )
        assert patient.is_valid(), patient.errors
    assert patient.cleaned_data['date_of_birth'] == datetime.date(1979, 4, 2)
