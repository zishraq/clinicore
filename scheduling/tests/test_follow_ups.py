"""One intention, one writer.

``Encounter.follow_up_date`` stays the stored field — the follow-ups-due work
list (SPEC §6.3) wants a date it can index, and deriving that across two tables
to build a work queue is worse in every way. What makes it safe is the rule
these tests exist to pin down: **once a follow-up appointment exists, the
appointment is the only thing that writes that date.**

The form half of the rule — the field stops being separately editable once
booked — lands with the UI. This is the service half.
"""

import datetime

import pytest
from django.utils import timezone

from core.context import organization_context
from scheduling import services
from scheduling.models import AppointmentStatus, DayPart

pytestmark = pytest.mark.django_db


@pytest.fixture
def follow_up(organization, staff, patient, branch, encounter):
    """A visit that asked for a return in four weeks, and the booking for it."""
    due = timezone.localdate() + datetime.timedelta(days=28)
    with organization_context(organization):
        encounter.follow_up_date = due
        encounter.save(update_fields=['follow_up_date', 'updated_at'])
        return services.book(
            organization,
            actor=staff,
            patient=patient,
            branch=branch,
            scheduled_date=due,
            origin_encounter=encounter,
        )


def test_a_follow_up_knows_the_visit_that_asked_for_it(
    organization, follow_up, encounter
):
    assert follow_up.origin_encounter_id == encounter.pk
    with organization_context(organization):
        assert list(encounter.follow_up_appointments.all()) == [follow_up]


def test_rescheduling_moves_the_visits_follow_up_date(
    organization, staff, follow_up, encounter
):
    """The appointment is the writer; the field follows it, never the reverse."""
    moved_to = timezone.localdate() + datetime.timedelta(days=35)
    with organization_context(organization):
        services.reschedule(
            follow_up, actor=staff, scheduled_date=moved_to, day_part=DayPart.MORNING
        )
        encounter.refresh_from_db()

    assert encounter.follow_up_date == moved_to
    follow_up.refresh_from_db()
    assert follow_up.scheduled_date == moved_to
    assert follow_up.day_part == DayPart.MORNING


def test_rescheduling_a_plain_booking_touches_no_visit(
    organization, staff, patient, branch, encounter
):
    """Only a row that came from a follow-up writes back."""
    original = timezone.localdate() + datetime.timedelta(days=7)
    with organization_context(organization):
        encounter.follow_up_date = original
        encounter.save(update_fields=['follow_up_date', 'updated_at'])
        booking = services.book(
            organization,
            actor=staff,
            patient=patient,
            branch=branch,
            scheduled_date=timezone.localdate(),
        )
        services.reschedule(
            booking,
            actor=staff,
            scheduled_date=timezone.localdate() + datetime.timedelta(days=1),
        )
        encounter.refresh_from_db()

    assert encounter.follow_up_date == original


def test_rescheduling_refuses_a_time_and_a_day_part(organization, staff, follow_up):
    with organization_context(organization), pytest.raises(services.AppointmentError):
        services.reschedule(
            follow_up,
            actor=staff,
            scheduled_date=timezone.localdate(),
            scheduled_time=datetime.time(9, 0),
            day_part=DayPart.MORNING,
        )


def test_a_closed_appointment_cannot_be_moved(organization, staff, follow_up):
    """Rescheduling a cancelled row would silently rewrite the visit's date."""
    with organization_context(organization):
        services.transition(
            follow_up,
            to=AppointmentStatus.CANCELLED,
            actor=staff,
            reason='Patient moved away',
        )
        with pytest.raises(services.AppointmentError):
            services.reschedule(
                follow_up,
                actor=staff,
                scheduled_date=timezone.localdate() + datetime.timedelta(days=90),
            )


def test_cancelling_a_follow_up_leaves_the_visit_alone(
    organization, staff, follow_up, encounter
):
    """The doctor still wanted them back; only the booking went away."""
    due = follow_up.scheduled_date
    with organization_context(organization):
        services.transition(
            follow_up,
            to=AppointmentStatus.CANCELLED,
            actor=staff,
            reason='Patient rang to cancel',
        )
        encounter.refresh_from_db()

    assert encounter.follow_up_date == due


def test_a_visit_needs_no_appointment_to_have_a_follow_up_date(organization, encounter):
    """The unbooked case has to keep working exactly as it did."""
    due = timezone.localdate() + datetime.timedelta(days=14)
    with organization_context(organization):
        encounter.follow_up_date = due
        encounter.save(update_fields=['follow_up_date', 'updated_at'])
        encounter.refresh_from_db()

        assert encounter.follow_up_date == due
        assert not encounter.follow_up_appointments.exists()
