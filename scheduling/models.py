"""The day list: one row per patient expected or present, booked or walked in.

Two rules shape this module, both from docs/adr/0010-appointments-as-one-day-list.md:

**Status is never a column.** ``arrived_at``, ``seen_at`` and ``resolution``
have to exist regardless — the first two are what a waiting time and a
consultation time are read from, the third is a decision nothing can infer — so
a ``status`` field would be a fourth value restating three, free to disagree
with them. It is derived, the same way an invoice balance and a batch's on-hand
are.

**Nothing invents precision it was not given.** "Tuesday morning" is a real
answer from this clinic, and storing it as 09:00 would make it indistinguishable
from a booking someone actually committed to. A time and a day part are
therefore mutually exclusive, by check constraint.
"""

from datetime import time

from django.conf import settings
from django.db import models
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce, TruncTime
from django.utils import timezone

from core.managers import OrgScopedManager, OrgScopedQuerySet
from core.models import OrgOwnedModel

__all__ = [
    'Appointment',
    'AppointmentSource',
    'AppointmentStatus',
    'DayPart',
    'Resolution',
]

#: Where a timed appointment falls when the day list sorts it beside a vague
#: one. Clinic-shaped rather than astronomical: afternoon starts after midday,
#: evening after five.
MORNING_ENDS = time(12, 0)
AFTERNOON_ENDS = time(17, 0)


class AppointmentSource(models.TextChoices):
    """How the row came to exist. A walk-in is not a second kind of thing."""

    BOOKED = 'BOOKED', 'Booked'
    WALK_IN = 'WALK_IN', 'Walk-in'


class DayPart(models.TextChoices):
    """Used when the time genuinely is not known. See the module docstring."""

    MORNING = 'MORNING', 'Morning'
    AFTERNOON = 'AFTERNOON', 'Afternoon'
    EVENING = 'EVENING', 'Evening'


class Resolution(models.TextChoices):
    """The only part of the status that cannot be inferred from a timestamp."""

    NO_SHOW = 'NO_SHOW', 'No show'
    CANCELLED = 'CANCELLED', 'Cancelled'


class AppointmentStatus(models.TextChoices):
    """Derived, never stored. Rendered through the terminology map.

    The two resolution values deliberately share their stored strings with
    ``Resolution`` so that ``status`` always returns a member of this set.
    """

    BOOKED = 'BOOKED', 'Booked'
    ARRIVED = 'ARRIVED', 'Arrived'
    SEEN = 'SEEN', 'Seen'
    NO_SHOW = 'NO_SHOW', 'No show'
    CANCELLED = 'CANCELLED', 'Cancelled'


#: Statuses that are finished with: they leave the working part of the day list.
CLOSED_STATUSES = frozenset(
    {AppointmentStatus.SEEN, AppointmentStatus.NO_SHOW, AppointmentStatus.CANCELLED}
)


def _sort_time():
    """The time a row is *about*: what was agreed, or when they walked in.

    A walk-in has no agreed time, but it does have a real one — the moment they
    turned up — and on a single chronological list it has to take its place
    among the bookings rather than sink to the bottom. Read through from
    ``arrived_at`` rather than copied into ``scheduled_time``: two columns
    holding one fact is exactly what this model avoids elsewhere.
    """
    return Coalesce('scheduled_time', TruncTime('arrived_at'))


def _day_part_rank():
    """Sort key placing timed and vague appointments on one axis.

    A 16:00 booking belongs after "Morning" and before "Evening", so a time is
    ranked by the part of day it falls in rather than sorted into a separate
    block ahead of everything vague. Anything with neither goes last — it is the
    least specific thing on the list.

    Reads the ``sort_time`` annotation, so it must be chained after it.
    """
    return Case(
        When(day_part=DayPart.MORNING, then=Value(0)),
        When(day_part=DayPart.AFTERNOON, then=Value(1)),
        When(day_part=DayPart.EVENING, then=Value(2)),
        When(sort_time__lt=MORNING_ENDS, then=Value(0)),
        When(sort_time__lt=AFTERNOON_ENDS, then=Value(1)),
        When(sort_time__isnull=False, then=Value(2)),
        default=Value(3),
        output_field=IntegerField(),
    )


class AppointmentQuerySet(OrgScopedQuerySet):
    def for_day(self, on_date, *, branch=None, practitioner=None):
        queryset = self.filter(scheduled_date=on_date)
        if branch is not None:
            queryset = queryset.filter(branch=branch)
        if practitioner is not None:
            queryset = queryset.filter(practitioner=practitioner)
        return queryset

    def chronological(self):
        """The one order the day list uses, whatever the row's status.

        The screen is a single list now, so every row sorts on one axis: the
        part of the day it falls in, then the time it is about. Annotations are
        guarded so this composes with the status filters, which is the same
        shape as ``InvoiceQuerySet.with_totals``.
        """
        queryset = self
        if 'sort_time' not in queryset.query.annotations:
            queryset = queryset.annotate(sort_time=_sort_time())
        if 'day_rank' not in queryset.query.annotations:
            queryset = queryset.annotate(day_rank=_day_part_rank())
        return queryset.order_by(
            'day_rank', F('sort_time').asc(nulls_last=True), 'created_at'
        )

    def waiting(self):
        """Arrived, not yet seen, not resolved — who is in the building."""
        return self.filter(
            arrived_at__isnull=False, seen_at__isnull=True, resolution=''
        )

    def expected(self):
        """Still to arrive."""
        return self.filter(arrived_at__isnull=True, resolution='')

    def seen(self):
        return self.filter(seen_at__isnull=False)

    def closed(self):
        """Seen, no-showed or cancelled — finished with, either way."""
        return self.filter(Q(seen_at__isnull=False) | ~Q(resolution=''))

    def with_status(self, key: str):
        """Narrow to one of the five states, or return everything.

        Keyed by the strings the URL carries, so the dropdown, the view and the
        queryset all name the states the same way and an unknown value means
        "All" rather than an error.
        """
        if key == 'expected':
            return self.expected()
        if key == 'waiting':
            return self.waiting()
        if key == 'seen':
            return self.seen()
        if key == 'cancelled':
            return self.filter(resolution=Resolution.CANCELLED)
        if key == 'no_show':
            return self.filter(resolution=Resolution.NO_SHOW)
        return self

    def matching(self, term: str):
        """Name or phone, which is how the front desk finds somebody."""
        term = (term or '').strip()
        if not term:
            return self
        return self.filter(
            Q(patient__full_name__icontains=term) | Q(patient__phone__icontains=term)
        )


class AppointmentManager(OrgScopedManager.from_queryset(AppointmentQuerySet)):
    """Organization-scoped, like every other business model."""


class Appointment(OrgOwnedModel):
    patient = models.ForeignKey(
        'patients.Patient', on_delete=models.PROTECT, related_name='appointments'
    )
    branch = models.ForeignKey(
        'organizations.Branch', on_delete=models.PROTECT, related_name='appointments'
    )
    # Nullable: a walk-in can be standing at the desk before anyone is free.
    practitioner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='appointments',
    )
    scheduled_date = models.DateField(db_index=True)
    # Exactly one of these, or neither — see the module docstring.
    scheduled_time = models.TimeField(null=True, blank=True)
    day_part = models.CharField(max_length=10, choices=DayPart.choices, blank=True)
    source = models.CharField(
        max_length=8,
        choices=AppointmentSource.choices,
        default=AppointmentSource.BOOKED,
    )
    arrived_at = models.DateTimeField(null=True, blank=True)
    #: When the visit was written against this row. Local to the appointment on
    #: purpose: deriving SEEN from the encounter link would let whatever later
    #: happens to that record silently rewrite this day's history.
    seen_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=10, choices=Resolution.choices, blank=True)
    resolution_reason = models.CharField(max_length=300, blank=True)
    #: The visit that asked for this follow-up. Its ``follow_up_date`` is kept in
    #: step by ``scheduling.services.reschedule`` and by nothing else.
    origin_encounter = models.ForeignKey(
        'clinical.Encounter',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='follow_up_appointments',
    )
    #: Why they are coming, in the receptionist's words. Not clinical narrative:
    #: STAFF writes and reads this, and the encounter's notes stay theirs alone.
    note = models.CharField(max_length=300, blank=True)

    # Declaration order is load-bearing: objects must be first so it becomes
    # _default_manager (docs/adr/0005-org-scoped-default-manager.md).
    objects = AppointmentManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-scheduled_date', 'scheduled_time']
        base_manager_name = 'all_objects'
        constraints = [
            # Invented precision is indistinguishable from the real thing.
            models.CheckConstraint(
                condition=~(
                    models.Q(scheduled_time__isnull=False) & ~models.Q(day_part='')
                ),
                name='appointment_time_xor_day_part',
            ),
            # A walk-in is recorded because somebody walked in.
            models.CheckConstraint(
                condition=~(
                    models.Q(source=AppointmentSource.WALK_IN)
                    & models.Q(arrived_at__isnull=True)
                ),
                name='appointment_walk_in_has_arrived',
            ),
            # Nobody is seen who never arrived.
            models.CheckConstraint(
                condition=~(
                    models.Q(seen_at__isnull=False) & models.Q(arrived_at__isnull=True)
                ),
                name='appointment_seen_after_arrival',
            ),
            # Seen and resolved are different endings; holding both would make
            # the derived status depend on which branch is tested first.
            models.CheckConstraint(
                condition=~(models.Q(seen_at__isnull=False) & ~models.Q(resolution='')),
                name='appointment_seen_xor_resolved',
            ),
            # A reason with nothing to explain is a leftover from an undo.
            models.CheckConstraint(
                condition=~(models.Q(resolution='') & ~models.Q(resolution_reason='')),
                name='appointment_reason_needs_resolution',
            ),
        ]
        indexes = [
            models.Index(fields=['organization', 'scheduled_date']),
            models.Index(fields=['organization', 'branch', 'scheduled_date']),
        ]

    def __str__(self) -> str:
        return f'{self.patient.full_name} — {self.scheduled_date:%d %b %Y}'

    @property
    def status(self) -> str:
        """The five statuses, computed. Never stored — see the module docstring."""
        if self.resolution:
            return self.resolution
        if self.seen_at:
            return AppointmentStatus.SEEN
        if self.arrived_at:
            return AppointmentStatus.ARRIVED
        return AppointmentStatus.BOOKED

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def is_walk_in(self) -> bool:
        return self.source == AppointmentSource.WALK_IN

    @property
    def when_display(self) -> str:
        """What was actually agreed, at the precision it was agreed to.

        Falls through to the arrival time for someone who was never booked:
        that is a real time rather than an invented one, and on one
        chronological list "Any time" against a row that is standing in front
        of you reads as missing information.
        """
        if self.scheduled_time:
            return self.scheduled_time.strftime('%H:%M')
        if self.day_part:
            return self.get_day_part_display()
        if self.arrived_at:
            return timezone.localtime(self.arrived_at).strftime('%H:%M')
        return 'Any time'
