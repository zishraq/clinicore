"""What an appointment row may and may not hold.

The derived status is the subject of most of this file. It exists so that
``arrived_at``, ``seen_at`` and ``resolution`` cannot disagree with a fourth
field claiming to summarise them — so the tests worth having are the ones that
would catch a summary drifting from what actually happened.
"""

import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.context import organization_context
from scheduling.models import (
    Appointment,
    AppointmentSource,
    AppointmentStatus,
    DayPart,
    Resolution,
)

pytestmark = pytest.mark.django_db


def _appointment(organization, patient, branch, **overrides) -> Appointment:
    fields = {
        'organization': organization,
        'patient': patient,
        'branch': branch,
        'scheduled_date': timezone.localdate(),
    }
    fields.update(overrides)
    return Appointment.objects.create(**fields)


def test_a_new_booking_is_booked(organization, patient, branch):
    with organization_context(organization):
        appointment = _appointment(organization, patient, branch)
        assert appointment.status == AppointmentStatus.BOOKED
        assert not appointment.is_closed


def test_arrival_is_read_from_the_timestamp(organization, patient, branch):
    with organization_context(organization):
        appointment = _appointment(organization, patient, branch)
        appointment.arrived_at = timezone.now()
        assert appointment.status == AppointmentStatus.ARRIVED


def test_seen_is_read_from_the_timestamp(organization, patient, branch):
    with organization_context(organization):
        now = timezone.now()
        appointment = _appointment(
            organization, patient, branch, arrived_at=now, seen_at=now
        )
        assert appointment.status == AppointmentStatus.SEEN
        assert appointment.is_closed


@pytest.mark.parametrize(
    'resolution',
    [Resolution.NO_SHOW, Resolution.CANCELLED],
)
def test_a_resolution_is_its_own_status(organization, patient, branch, resolution):
    with organization_context(organization):
        appointment = _appointment(organization, patient, branch, resolution=resolution)
        assert appointment.status == resolution
        assert appointment.is_closed


def test_the_database_refuses_a_time_and_a_day_part_together(
    organization, patient, branch
):
    """A time already says which part of the day it is."""
    with (
        organization_context(organization),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        _appointment(
            organization,
            patient,
            branch,
            scheduled_time=datetime.time(10, 30),
            day_part=DayPart.MORNING,
        )


def test_a_walk_in_must_have_arrived(organization, patient, branch):
    with (
        organization_context(organization),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        _appointment(organization, patient, branch, source=AppointmentSource.WALK_IN)


def test_nobody_is_seen_who_never_arrived(organization, patient, branch):
    with (
        organization_context(organization),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        _appointment(organization, patient, branch, seen_at=timezone.now())


def test_seen_and_resolved_cannot_both_hold(organization, patient, branch):
    """Otherwise the derived status depends on which branch is tested first."""
    now = timezone.now()
    with (
        organization_context(organization),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        _appointment(
            organization,
            patient,
            branch,
            arrived_at=now,
            seen_at=now,
            resolution=Resolution.CANCELLED,
        )


def test_a_reason_needs_something_to_explain(organization, patient, branch):
    with (
        organization_context(organization),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        _appointment(organization, patient, branch, resolution_reason='Left early')


def test_when_display_never_invents_a_time(organization, patient, branch):
    with organization_context(organization):
        vague = _appointment(organization, patient, branch, day_part=DayPart.MORNING)
        firm = _appointment(
            organization, patient, branch, scheduled_time=datetime.time(10, 30)
        )
        unknown = _appointment(organization, patient, branch)

        assert vague.when_display == 'Morning'
        assert firm.when_display == '10:30'
        assert unknown.when_display == 'Any time'
        # The vague one is still vague in the database, not rounded to a time.
        assert vague.scheduled_time is None
