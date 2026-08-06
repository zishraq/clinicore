"""The three bands of a day, and the order the front desk reads them in.

Ordering is the whole content of this file because it is the part a screenshot
cannot verify later: waiting is a fairness question ("who has been here
longest"), and expected has to put a vague booking and a timed one on one axis
without either jumping the other.
"""

import datetime

import pytest
from django.utils import timezone

from core.context import organization_context
from scheduling import services
from scheduling.models import AppointmentStatus, DayPart

pytestmark = pytest.mark.django_db


def _book(organization, staff, patient, branch, **kwargs):
    with organization_context(organization):
        return services.book(
            organization,
            actor=staff,
            patient=patient,
            branch=branch,
            scheduled_date=kwargs.pop('scheduled_date', timezone.localdate()),
            **kwargs,
        )


def _names(rows) -> list[str]:
    return [row.patient.full_name for row in rows]


@pytest.fixture
def people(organization):
    """Six patients, so ordering assertions read as names rather than indexes."""
    from patients.models import Patient

    with organization_context(organization):
        return [
            Patient.objects.create(
                organization=organization, code=f'P-{index:04d}', full_name=name
            )
            for index, name in enumerate(
                ['Early', 'Late', 'Morning', 'Afternoon', 'Evening', 'Anytime'], start=1
            )
        ]


def test_expected_puts_a_timed_booking_in_its_part_of_day(
    organization, staff, branch, people
):
    """A 16:00 booking belongs after "Morning" and before "Evening"."""
    _, _, morning, afternoon, evening, anytime = people
    _book(organization, staff, evening, branch, day_part=DayPart.EVENING)
    _book(organization, staff, anytime, branch)
    _book(organization, staff, afternoon, branch, scheduled_time=datetime.time(16, 0))
    _book(organization, staff, morning, branch, day_part=DayPart.MORNING)

    with organization_context(organization):
        day = services.day_list(organization, on_date=timezone.localdate())
        assert _names(day['expected']) == ['Morning', 'Afternoon', 'Evening', 'Anytime']


def test_expected_orders_two_times_by_the_clock(organization, staff, branch, people):
    early, late = people[0], people[1]
    _book(organization, staff, late, branch, scheduled_time=datetime.time(11, 45))
    _book(organization, staff, early, branch, scheduled_time=datetime.time(9, 15))

    with organization_context(organization):
        day = services.day_list(organization, on_date=timezone.localdate())
        assert _names(day['expected']) == ['Early', 'Late']


def test_waiting_is_longest_wait_first(organization, staff, branch, people):
    """The question the receptionist is actually answering."""
    early, late = people[0], people[1]
    first = _book(organization, staff, early, branch)
    second = _book(organization, staff, late, branch)

    with organization_context(organization):
        services.transition(first, to=AppointmentStatus.ARRIVED, actor=staff)
        services.transition(second, to=AppointmentStatus.ARRIVED, actor=staff)
        day = services.day_list(organization, on_date=timezone.localdate())
        assert _names(day['waiting']) == ['Early', 'Late']


def test_a_row_moves_between_bands_as_it_progresses(
    organization, staff, branch, patient, encounter
):
    booking = _book(
        organization, staff, patient, branch, scheduled_time=datetime.time(10, 0)
    )
    today = timezone.localdate()

    with organization_context(organization):
        day = services.day_list(organization, on_date=today)
        assert _names(day['expected']) == ['Rahima Begum']
        assert list(day['waiting']) == []

        services.transition(booking, to=AppointmentStatus.ARRIVED, actor=staff)
        day = services.day_list(organization, on_date=today)
        assert list(day['expected']) == []
        assert _names(day['waiting']) == ['Rahima Begum']

        services.transition(
            booking, to=AppointmentStatus.SEEN, actor=staff, encounter=encounter
        )
        day = services.day_list(organization, on_date=today)
        assert list(day['waiting']) == []
        assert _names(day['closed']) == ['Rahima Begum']


def test_a_cancelled_row_leaves_the_working_bands(organization, staff, branch, patient):
    booking = _book(organization, staff, patient, branch)
    with organization_context(organization):
        services.transition(
            booking,
            to=AppointmentStatus.CANCELLED,
            actor=staff,
            reason='Rang to cancel',
        )
        day = services.day_list(organization, on_date=timezone.localdate())
        assert list(day['expected']) == []
        assert list(day['waiting']) == []
        assert _names(day['closed']) == ['Rahima Begum']


def test_a_no_show_who_returns_rejoins_the_waiting_band(
    organization, staff, branch, patient
):
    booking = _book(organization, staff, patient, branch)
    today = timezone.localdate()

    with organization_context(organization):
        services.transition(booking, to=AppointmentStatus.NO_SHOW, actor=staff)
        assert _names(services.day_list(organization, on_date=today)['closed']) == [
            'Rahima Begum'
        ]

        services.transition(booking, to=AppointmentStatus.ARRIVED, actor=staff)
        day = services.day_list(organization, on_date=today)
        assert _names(day['waiting']) == ['Rahima Begum']
        assert list(day['closed']) == []


def test_the_day_list_is_one_day(organization, staff, branch, people):
    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)
    _book(organization, staff, people[0], branch, scheduled_date=today)
    _book(organization, staff, people[1], branch, scheduled_date=tomorrow)

    with organization_context(organization):
        assert _names(services.day_list(organization, on_date=today)['expected']) == [
            'Early'
        ]
        assert _names(
            services.day_list(organization, on_date=tomorrow)['expected']
        ) == ['Late']


def test_the_day_list_can_narrow_to_a_branch(organization, staff, branch, people):
    from organizations.models import Branch

    with organization_context(organization):
        other = Branch.objects.create(
            organization=organization, name='Uttara Chamber', code='UTT'
        )
    _book(organization, staff, people[0], branch)
    _book(organization, staff, people[1], other)

    with organization_context(organization):
        day = services.day_list(
            organization, on_date=timezone.localdate(), branch=branch
        )
        assert _names(day['expected']) == ['Early']


def test_the_day_list_does_not_cross_tenants(
    organization, other_organization, staff, branch, patient
):
    from patients.models import Patient

    with organization_context(other_organization):
        theirs = Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Someone Else'
        )
        from organizations.models import Branch

        their_branch = Branch.objects.create(
            organization=other_organization, name='Theirs', code='THR'
        )
    _book(organization, staff, patient, branch)
    _book(other_organization, staff, theirs, their_branch)

    with organization_context(organization):
        day = services.day_list(organization, on_date=timezone.localdate())
        assert _names(day['expected']) == ['Rahima Begum']
