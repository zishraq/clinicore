"""Booking, walking in, and moving a day-list row between states.

Every status change goes through ``transition``. The rules live here rather
than in ``Appointment.save`` because that is where this codebase keeps them —
``finalize_encounter`` and ``void_invoice`` are the same shape — and because a
transition needs the actor and a reason, which a model save does not have.

Transitions are idempotent and take a row lock before reading. A day list is
shared by the whole front desk, so two people marking the same patient arrived
is the ordinary case, not a race worth losing data to.
"""

from django.db import transaction
from django.utils import timezone

from scheduling.models import (
    Appointment,
    AppointmentSource,
    AppointmentStatus,
    Resolution,
)

__all__ = [
    'AppointmentError',
    'IllegalTransition',
    'book',
    'day_list',
    'reschedule',
    'schedule_follow_up',
    'transition',
    'walk_in',
    'with_bills',
]


class AppointmentError(ValueError):
    """Base for the refusals a caller is expected to show to a user."""


class IllegalTransition(AppointmentError):
    """Raised when a status change is not one the day list allows."""


#: What may follow what. NO_SHOW is deliberately not terminal: patients turn up
#: an hour late and the receptionist should not have to rebook them to say so.
#: SEEN is, because a visit has been written against it.
ALLOWED = {
    AppointmentStatus.BOOKED: frozenset(
        {
            AppointmentStatus.ARRIVED,
            AppointmentStatus.NO_SHOW,
            AppointmentStatus.CANCELLED,
        }
    ),
    AppointmentStatus.ARRIVED: frozenset(
        {AppointmentStatus.SEEN, AppointmentStatus.CANCELLED}
    ),
    AppointmentStatus.NO_SHOW: frozenset({AppointmentStatus.ARRIVED}),
    AppointmentStatus.SEEN: frozenset(),
    AppointmentStatus.CANCELLED: frozenset(),
}


def _locked(appointment: Appointment) -> Appointment:
    """Re-read the row under a lock, as ``void_payment`` does."""
    return Appointment.all_objects.select_for_update().get(
        pk=appointment.pk, organization_id=appointment.organization_id
    )


@transaction.atomic
def book(
    organization,
    *,
    actor,
    patient,
    branch,
    scheduled_date,
    practitioner=None,
    scheduled_time=None,
    day_part: str = '',
    note: str = '',
    origin_encounter=None,
) -> Appointment:
    """Put a patient on a future day list.

    ``scheduled_time`` and ``day_part`` are mutually exclusive; a check
    constraint enforces it, and this refuses first so the caller gets a sentence
    rather than an IntegrityError.
    """
    if scheduled_time is not None and day_part:
        raise AppointmentError(
            'Record a time or a part of the day, not both — a time already says '
            'which part of the day it is.'
        )
    return Appointment.objects.create(
        organization=organization,
        created_by=actor,
        patient=patient,
        branch=branch,
        practitioner=practitioner,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        day_part=day_part,
        source=AppointmentSource.BOOKED,
        note=note,
        origin_encounter=origin_encounter,
    )


@transaction.atomic
def walk_in(
    organization, *, actor, patient, branch, practitioner=None, note: str = ''
) -> Appointment:
    """Record someone who is already at the desk.

    Created ARRIVED rather than booked-then-arrived: they are standing there,
    and a row that claims otherwise for a moment is a row the day list can show
    in the wrong band. ``scheduled_date`` is today because that is when this is
    happening, which keeps walk-ins and bookings on one list (SPEC §6.3).
    """
    now = timezone.now()
    return Appointment.objects.create(
        organization=organization,
        created_by=actor,
        patient=patient,
        branch=branch,
        practitioner=practitioner,
        scheduled_date=timezone.localdate(),
        source=AppointmentSource.WALK_IN,
        arrived_at=now,
        note=note,
    )


@transaction.atomic
def transition(
    appointment: Appointment,
    *,
    to: str,
    actor,
    reason: str = '',
    encounter=None,
) -> Appointment:
    """Move a row to ``to``, or refuse and say why.

    Idempotent: asking for the state a row is already in returns it untouched,
    so a double-click cannot overwrite the first click's timestamp or reason.
    """
    locked = _locked(appointment)
    current = locked.status
    if current == to:
        return locked
    if to not in ALLOWED[current]:
        raise IllegalTransition(f'{current} cannot become {to}.')

    if to == AppointmentStatus.SEEN:
        return _mark_seen(locked, encounter=encounter)
    if to == AppointmentStatus.ARRIVED:
        return _mark_arrived(locked)
    return _resolve(locked, resolution=to, actor=actor, reason=reason)


def _mark_arrived(locked: Appointment) -> Appointment:
    # Also the way back from NO_SHOW, which is why the resolution is cleared.
    locked.arrived_at = locked.arrived_at or timezone.now()
    locked.resolution = ''
    locked.resolution_reason = ''
    locked.save(
        update_fields=['arrived_at', 'resolution', 'resolution_reason', 'updated_at']
    )
    return locked


def _mark_seen(locked: Appointment, *, encounter) -> Appointment:
    """Consume the row. Called when a visit is saved against it, not by hand."""
    if encounter is None:
        raise AppointmentError(
            'An appointment is marked seen by writing the visit, not on its own.'
        )
    locked.seen_at = timezone.now()
    locked.save(update_fields=['seen_at', 'updated_at'])
    # The link lives on the encounter, so it is written from that side.
    encounter.appointment = locked
    encounter.save(update_fields=['appointment', 'updated_at'])
    return locked


def _resolve(locked: Appointment, *, resolution: str, actor, reason: str):
    reason = (reason or '').strip()
    if resolution == Resolution.CANCELLED and not reason:
        raise AppointmentError('Cancelling an appointment requires a reason.')
    locked.resolution = resolution
    locked.resolution_reason = reason[:300]
    locked.save(update_fields=['resolution', 'resolution_reason', 'updated_at'])
    return locked


@transaction.atomic
def reschedule(
    appointment: Appointment,
    *,
    actor,
    scheduled_date,
    scheduled_time=None,
    day_part: str = '',
) -> Appointment:
    """Move a booking, keeping the visit that asked for it in step.

    When the row came from a visit's follow-up date, this is the **only** writer
    of that date. Two places recording one intention is what this avoids: the
    field stays queryable for the follow-ups-due list (SPEC §6.3), and the
    appointment is what decides its value once one exists.
    """
    if scheduled_time is not None and day_part:
        raise AppointmentError(
            'Record a time or a part of the day, not both — a time already says '
            'which part of the day it is.'
        )
    locked = _locked(appointment)
    if locked.is_closed:
        raise AppointmentError(
            f'A {locked.status.lower()} appointment cannot be moved.'
        )

    locked.scheduled_date = scheduled_date
    locked.scheduled_time = scheduled_time
    locked.day_part = day_part
    locked.save(
        update_fields=['scheduled_date', 'scheduled_time', 'day_part', 'updated_at']
    )

    if locked.origin_encounter_id:
        origin = locked.origin_encounter
        origin.follow_up_date = scheduled_date
        origin.save(update_fields=['follow_up_date', 'updated_at'])
    return locked


@transaction.atomic
def schedule_follow_up(organization, *, actor, encounter, on_date):
    """Put the visit's next appointment on a day list, or move the one there.

    The visit form asks "next appointment?" and used to answer it by writing a
    date onto the encounter and nothing else — a date nobody was ever shown
    again. This is the other half: the date becomes a row the front desk can
    see.

    Routed through ``reschedule`` when a row already exists, which is what keeps
    ``Encounter.follow_up_date`` and the appointment from drifting apart. That
    single-writer rule is the one ADR 0010 set out, and this is the only other
    door into it.
    """
    if on_date is None:
        return None
    existing = (
        Appointment.objects.for_organization(organization)
        .filter(origin_encounter=encounter)
        .exclude(seen_at__isnull=False)
        .filter(resolution='')
        .first()
    )
    if existing is not None:
        if existing.scheduled_date == on_date:
            return existing
        return reschedule(existing, actor=actor, scheduled_date=on_date)
    return book(
        organization,
        actor=actor,
        patient=encounter.patient,
        branch=encounter.branch,
        practitioner=encounter.practitioner,
        scheduled_date=on_date,
        origin_encounter=encounter,
    )


def day_list(
    organization,
    *,
    on_date,
    branch=None,
    practitioner=None,
    status: str = '',
    search: str = '',
):
    """One day, one chronological list, optionally narrowed.

    Was three bands — Waiting, Expected, Done — and is now a single list with a
    status filter. Sections taught the receptionist a layout before she could
    read the day; one list in time order is the thing she already understands,
    and the filter answers the question the sections were guessing at.
    """
    rows = Appointment.objects.for_organization(organization).for_day(
        on_date, branch=branch, practitioner=practitioner
    )
    # ``encounter`` is the reverse of a nullable one-to-one, so this is a left
    # join that costs the unseen rows nothing and saves a query per row once
    # bills are being shown.
    rows = rows.select_related('patient', 'practitioner', 'branch', 'encounter')
    return rows.with_status(status).matching(search).chronological()


def with_bills(organization, rows) -> list:
    """Attach each row's bill, read appointment → encounter → invoice.

    Display only. Nothing is copied onto the appointment: payment state is
    derived from the invoice's own payments (ADR 0008), so reading it through
    the link is the only way it cannot go stale — which is exactly why ADR 0010
    kept it off this model.

    One query for the whole day rather than one per row, and evaluating the
    queryset here is the point: the caller gets a list it can attach to.
    """
    # Deferred, like clinical.encounter_detail's: billing depends on clinical,
    # and scheduling needs only to read what the visit it produced was billed.
    from billing.models import Invoice, InvoiceState

    rows = list(rows)
    encounter_ids = [
        encounter.pk
        for encounter in (getattr(row, 'encounter', None) for row in rows)
        if encounter is not None
    ]
    bills: dict = {}
    if encounter_ids:
        invoices = (
            Invoice.objects.for_organization(organization)
            .filter(encounter_id__in=encounter_ids)
            .exclude(status=InvoiceState.VOID)
            .with_totals()
        )
        # Meta orders newest first, so the first seen for an encounter is the
        # one ``billing.services.invoice_for_encounter`` would have returned.
        for invoice in invoices:
            bills.setdefault(invoice.encounter_id, invoice)

    for row in rows:
        encounter = getattr(row, 'encounter', None)
        row.bill = bills.get(encounter.pk) if encounter is not None else None
    return rows
