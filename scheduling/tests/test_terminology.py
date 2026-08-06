"""Every appointment status has a label a clinic can change (SPEC §5).

The statuses are derived rather than stored, but they still reach the UI as
words, so they go through the same map as everything else. Nothing here has a
template yet — the point is that the keys exist before the UI needs them, so no
screen has an excuse to hardcode one.
"""

import pytest
from django.template import Context, Template

from organizations.models import DEFAULT_TERMINOLOGY
from scheduling.models import AppointmentStatus

pytestmark = pytest.mark.django_db


def _render(status, terms=None) -> str:
    template = Template('{% load terminology %}{% status_label status %}')
    return template.render(Context({'status': status, 'terms': terms})).strip()


@pytest.mark.parametrize('status', list(AppointmentStatus))
def test_every_status_has_a_default_label(status):
    key = f'status_{status.lower()}'
    assert key in DEFAULT_TERMINOLOGY, (
        f'{status} has no terminology key. Add {key} to DEFAULT_TERMINOLOGY so a '
        f'clinic can relabel it without a migration.'
    )
    assert _render(status) == DEFAULT_TERMINOLOGY[key]


def test_the_row_itself_has_a_label(organization):
    assert organization.terms['appointment'] == 'Appointment'
    assert organization.terms['appointment_plural'] == 'Appointments'
    assert organization.terms['walk_in'] == 'Walk-in'


def test_a_clinic_can_rename_the_states(organization):
    organization.terminology = {
        'appointment': 'Booking',
        'status_arrived': 'Checked in',
        'status_no_show': 'Missed',
    }
    organization.save(update_fields=['terminology'])

    terms = organization.terms
    assert terms['appointment'] == 'Booking'
    assert _render(AppointmentStatus.ARRIVED, terms) == 'Checked in'
    assert _render(AppointmentStatus.NO_SHOW, terms) == 'Missed'
    # Untouched keys keep their defaults.
    assert _render(AppointmentStatus.SEEN, terms) == 'Seen'
