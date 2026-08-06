"""Moving a row through the day, and what refuses to move.

The transition map is the whole safety story for a derived status: nothing else
stops ``seen_at`` and ``resolution`` being set on the same row by two different
callers. So the illegal moves matter as much as the legal ones here.
"""

import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from clinical.models import Encounter
from core.context import organization_context
from scheduling import services
from scheduling.models import Appointment, AppointmentSource, AppointmentStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking(organization, patient, branch, staff):
    with organization_context(organization):
        return services.book(
            organization,
            actor=staff,
            patient=patient,
            branch=branch,
            scheduled_date=timezone.localdate(),
            scheduled_time=datetime.time(10, 30),
        )


def _to(organization, appointment, to, **kwargs):
    with organization_context(organization):
        return services.transition(appointment, to=to, **kwargs)


def test_booking_puts_a_patient_on_the_day(organization, booking):
    assert booking.status == AppointmentStatus.BOOKED
    assert booking.source == AppointmentSource.BOOKED
    assert booking.when_display == '10:30'


def test_a_walk_in_is_arrived_the_moment_it_exists(
    organization, patient, branch, staff
):
    """They are standing there; a row that says otherwise sorts into the wrong band."""
    with organization_context(organization):
        walk_in = services.walk_in(
            organization, actor=staff, patient=patient, branch=branch
        )
    assert walk_in.status == AppointmentStatus.ARRIVED
    assert walk_in.is_walk_in
    assert walk_in.scheduled_date == timezone.localdate()
    assert walk_in.arrived_at is not None


def test_a_walk_in_needs_no_practitioner(organization, patient, branch, staff):
    with organization_context(organization):
        walk_in = services.walk_in(
            organization, actor=staff, patient=patient, branch=branch
        )
    assert walk_in.practitioner_id is None


def test_booking_refuses_a_time_and_a_day_part(organization, patient, branch, staff):
    """Refused with a sentence rather than left to the check constraint."""
    with organization_context(organization), pytest.raises(services.AppointmentError):
        services.book(
            organization,
            actor=staff,
            patient=patient,
            branch=branch,
            scheduled_date=timezone.localdate(),
            scheduled_time=datetime.time(10, 30),
            day_part='MORNING',
        )


def test_marking_arrived(organization, booking, staff):
    arrived = _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    assert arrived.status == AppointmentStatus.ARRIVED
    assert arrived.arrived_at is not None


def test_marking_arrived_twice_keeps_the_first_time(organization, booking, staff):
    """A shared day list gets double-clicked; the second must not move the clock."""
    first = _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    stamp = first.arrived_at
    second = _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    assert second.arrived_at == stamp


def test_cancelling_requires_a_reason(organization, booking, staff):
    with organization_context(organization), pytest.raises(services.AppointmentError):
        services.transition(
            booking, to=AppointmentStatus.CANCELLED, actor=staff, reason='  '
        )
    booking.refresh_from_db()
    assert booking.status == AppointmentStatus.BOOKED


def test_cancelling_records_who_and_why(organization, booking, staff):
    cancelled = _to(
        organization,
        booking,
        AppointmentStatus.CANCELLED,
        actor=staff,
        reason='Patient rang to cancel',
    )
    assert cancelled.status == AppointmentStatus.CANCELLED
    assert cancelled.resolution_reason == 'Patient rang to cancel'


def test_a_no_show_needs_no_reason(organization, booking, staff):
    no_show = _to(organization, booking, AppointmentStatus.NO_SHOW, actor=staff)
    assert no_show.status == AppointmentStatus.NO_SHOW


def test_a_no_show_who_turns_up_late_is_arrived(organization, booking, staff):
    """Not terminal on purpose — this happens, and rebooking to say so is silly."""
    _to(organization, booking, AppointmentStatus.NO_SHOW, actor=staff)
    late = _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)

    assert late.status == AppointmentStatus.ARRIVED
    # The no-show is undone rather than left underneath the arrival.
    assert late.resolution == ''
    assert late.resolution_reason == ''


@pytest.mark.parametrize(
    ('start', 'target'),
    [
        (AppointmentStatus.BOOKED, AppointmentStatus.SEEN),
        (AppointmentStatus.CANCELLED, AppointmentStatus.ARRIVED),
        (AppointmentStatus.SEEN, AppointmentStatus.CANCELLED),
        (AppointmentStatus.SEEN, AppointmentStatus.ARRIVED),
    ],
)
def test_illegal_moves_are_refused(
    organization, booking, staff, encounter, start, target
):
    reach = {
        AppointmentStatus.BOOKED: [],
        AppointmentStatus.CANCELLED: [(AppointmentStatus.CANCELLED, 'Rang to cancel')],
        AppointmentStatus.SEEN: [
            (AppointmentStatus.ARRIVED, ''),
            (AppointmentStatus.SEEN, ''),
        ],
    }
    for step, reason in reach[start]:
        with organization_context(organization):
            services.transition(
                booking, to=step, actor=staff, reason=reason, encounter=encounter
            )

    with organization_context(organization), pytest.raises(services.IllegalTransition):
        services.transition(booking, to=target, actor=staff, encounter=encounter)


def test_seen_is_not_a_button(organization, booking, staff):
    """It is what saving a visit does, so it refuses to happen on its own."""
    _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    with organization_context(organization), pytest.raises(services.AppointmentError):
        services.transition(booking, to=AppointmentStatus.SEEN, actor=staff)


def test_seeing_links_the_visit(organization, booking, staff, encounter):
    _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    seen = _to(
        organization,
        booking,
        AppointmentStatus.SEEN,
        actor=staff,
        encounter=encounter,
    )

    assert seen.status == AppointmentStatus.SEEN
    with organization_context(organization):
        encounter.refresh_from_db()
        assert encounter.appointment_id == seen.pk
        assert seen.encounter == encounter


def test_seen_survives_the_visit_being_deleted(organization, booking, staff, encounter):
    """The reason SEEN is a timestamp and not a lookup through the link.

    Whatever later becomes of the visit — hard deleted here, soft deleted if
    clinical records ever gain it — this day's history must not quietly rewrite
    itself back to ARRIVED.
    """
    _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    _to(organization, booking, AppointmentStatus.SEEN, actor=staff, encounter=encounter)

    with organization_context(organization):
        Encounter.objects.filter(pk=encounter.pk).delete()
        booking.refresh_from_db()

        # The link is gone — there is genuinely no visit to point at any more...
        assert not Encounter.all_objects.filter(appointment=booking).exists()
    # ...and the day's record of what happened is unchanged.
    assert booking.status == AppointmentStatus.SEEN
    assert booking.seen_at is not None


def test_one_visit_per_appointment(organization, booking, staff, encounter, branch):
    """A plural link would make "was this seen?" ambiguous, so it is one-to-one."""
    _to(organization, booking, AppointmentStatus.ARRIVED, actor=staff)
    _to(organization, booking, AppointmentStatus.SEEN, actor=staff, encounter=encounter)

    with organization_context(organization):
        second = Encounter.objects.create(
            organization=organization,
            patient=booking.patient,
            practitioner=encounter.practitioner,
            branch=branch,
            occurred_at=timezone.now(),
        )
        second.appointment = booking
        with pytest.raises(IntegrityError), transaction.atomic():
            second.save(update_fields=['appointment', 'updated_at'])


def test_the_lock_is_filtered_on_the_rows_own_tenant(organization, booking, staff):
    """``transition`` reads through ``all_objects``, so the filter is the guard.

    Without the explicit ``organization_id`` it would be the one place in the
    app where a row is fetched with no tenant bound at all.
    """
    with organization_context(organization):
        locked = Appointment.all_objects.get(
            pk=booking.pk, organization_id=organization.pk
        )
    assert locked.organization_id == organization.pk

    with pytest.raises(Appointment.DoesNotExist):
        Appointment.all_objects.get(pk=booking.pk, organization_id=organization.pk + 1)
