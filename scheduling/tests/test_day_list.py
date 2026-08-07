"""One day, one chronological list, and the filters that narrow it.

Ordering is most of this file because it is the part a screenshot cannot verify
later: a vague booking and a timed one have to sit on one axis without either
jumping the other, and a walk-in has to take its place among them rather than
sinking to the bottom.

The three bands this replaced are gone. What they encoded — which rows are
still to come, who is in the building, what is finished — is now the status
filter, so the same questions are asked here through ``status=``.
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


def _day(organization, **kwargs) -> list[str]:
    kwargs.setdefault('on_date', timezone.localdate())
    return _names(services.day_list(organization, **kwargs))


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


def test_a_timed_booking_sits_in_its_part_of_day(organization, staff, branch, people):
    """A 16:00 booking belongs after "Morning" and before "Evening"."""
    _, _, morning, afternoon, evening, anytime = people
    _book(organization, staff, evening, branch, day_part=DayPart.EVENING)
    _book(organization, staff, anytime, branch)
    _book(organization, staff, afternoon, branch, scheduled_time=datetime.time(16, 0))
    _book(organization, staff, morning, branch, day_part=DayPart.MORNING)

    with organization_context(organization):
        assert _day(organization) == ['Morning', 'Afternoon', 'Evening', 'Anytime']


def test_two_times_are_ordered_by_the_clock(organization, staff, branch, people):
    early, late = people[0], people[1]
    _book(organization, staff, late, branch, scheduled_time=datetime.time(11, 45))
    _book(organization, staff, early, branch, scheduled_time=datetime.time(9, 15))

    with organization_context(organization):
        assert _day(organization) == ['Early', 'Late']


def test_a_walk_in_sorts_by_when_they_arrived(organization, staff, branch, people):
    """The point of one list: somebody standing there is not "any time".

    A walk-in has no booked time, so the row is sorted — and labelled — by the
    moment they turned up. Read through from ``arrived_at`` rather than copied
    into ``scheduled_time``, so there is still one column holding the fact.
    """
    morning, evening = people[2], people[4]
    _book(organization, staff, evening, branch, scheduled_time=datetime.time(17, 30))
    _book(organization, staff, morning, branch, scheduled_time=datetime.time(9, 0))

    with organization_context(organization):
        walk_in = services.walk_in(
            organization, actor=staff, patient=people[5], branch=branch
        )
        rows = list(services.day_list(organization, on_date=timezone.localdate()))

    arrived_at = timezone.localtime(walk_in.arrived_at).time()
    expected = ['Morning', 'Anytime', 'Evening']
    if arrived_at < datetime.time(9, 0):
        expected = ['Anytime', 'Morning', 'Evening']
    elif arrived_at > datetime.time(17, 30):
        expected = ['Morning', 'Evening', 'Anytime']
    assert _names(rows) == expected
    # And the row says the time it is about rather than "Any time".
    assert walk_in.when_display == arrived_at.strftime('%H:%M')


def test_the_status_filter_answers_what_the_bands_used_to(
    organization, staff, branch, patient, encounter
):
    """Expected, waiting, seen — one row walked through all three."""
    booking = _book(
        organization, staff, patient, branch, scheduled_time=datetime.time(10, 0)
    )
    today = timezone.localdate()

    with organization_context(organization):
        assert _day(organization, status='expected') == ['Rahima Begum']
        assert _day(organization, status='waiting') == []

        services.transition(booking, to=AppointmentStatus.ARRIVED, actor=staff)
        assert _day(organization, status='expected') == []
        assert _day(organization, status='waiting') == ['Rahima Begum']

        services.transition(
            booking, to=AppointmentStatus.SEEN, actor=staff, encounter=encounter
        )
        assert _day(organization, status='waiting') == []
        assert _day(organization, status='seen') == ['Rahima Begum']
        # And it is still on the unfiltered list, which is the whole change:
        # finishing with someone no longer removes them from the day.
        assert _day(organization, on_date=today) == ['Rahima Begum']


def test_a_cancelled_row_stays_on_the_day_and_filters_out(
    organization, staff, branch, patient
):
    booking = _book(organization, staff, patient, branch)
    with organization_context(organization):
        services.transition(
            booking,
            to=AppointmentStatus.CANCELLED,
            actor=staff,
            reason='Rang to cancel',
        )
        assert _day(organization) == ['Rahima Begum']
        assert _day(organization, status='cancelled') == ['Rahima Begum']
        assert _day(organization, status='expected') == []
        assert _day(organization, status='waiting') == []


def test_a_no_show_who_returns_is_waiting_again(organization, staff, branch, patient):
    booking = _book(organization, staff, patient, branch)

    with organization_context(organization):
        services.transition(booking, to=AppointmentStatus.NO_SHOW, actor=staff)
        assert _day(organization, status='no_show') == ['Rahima Begum']

        services.transition(booking, to=AppointmentStatus.ARRIVED, actor=staff)
        assert _day(organization, status='waiting') == ['Rahima Begum']
        assert _day(organization, status='no_show') == []


def test_an_unknown_status_shows_everything(organization, staff, branch, patient):
    """A filter that cannot be honoured must not silently hide the day."""
    _book(organization, staff, patient, branch)
    with organization_context(organization):
        assert _day(organization, status='nonsense') == ['Rahima Begum']


def test_the_search_matches_name_or_phone(organization, staff, branch):
    """How the front desk finds somebody when the day is long."""
    from patients.models import Patient

    with organization_context(organization):
        rahima = Patient.objects.create(
            organization=organization,
            code='P-0001',
            full_name='Rahima Begum',
            phone='01712345678',
        )
        kamal = Patient.objects.create(
            organization=organization, code='P-0002', full_name='Kamal Hossain'
        )
    _book(organization, staff, rahima, branch, scheduled_time=datetime.time(9, 0))
    _book(organization, staff, kamal, branch, scheduled_time=datetime.time(10, 0))

    with organization_context(organization):
        assert _day(organization, search='rahima') == ['Rahima Begum']
        assert _day(organization, search='0171234') == ['Rahima Begum']
        assert _day(organization, search='hossain') == ['Kamal Hossain']
        assert _day(organization, search='  ') == ['Rahima Begum', 'Kamal Hossain']


def test_the_filters_compose(organization, staff, branch, people):
    """Status and search narrow together, not one instead of the other."""
    early, late = people[0], people[1]
    first = _book(
        organization, staff, early, branch, scheduled_time=datetime.time(9, 0)
    )
    _book(organization, staff, late, branch, scheduled_time=datetime.time(10, 0))

    with organization_context(organization):
        services.transition(first, to=AppointmentStatus.ARRIVED, actor=staff)
        assert _day(organization, status='waiting') == ['Early']
        assert _day(organization, status='waiting', search='Late') == []
        assert _day(organization, status='expected', search='Late') == ['Late']


def test_the_day_list_is_one_day(organization, staff, branch, people):
    today = timezone.localdate()
    tomorrow = today + datetime.timedelta(days=1)
    _book(organization, staff, people[0], branch, scheduled_date=today)
    _book(organization, staff, people[1], branch, scheduled_date=tomorrow)

    with organization_context(organization):
        assert _day(organization, on_date=today) == ['Early']
        assert _day(organization, on_date=tomorrow) == ['Late']


def test_the_day_list_can_narrow_to_a_branch(organization, staff, branch, people):
    from organizations.models import Branch

    with organization_context(organization):
        other = Branch.objects.create(
            organization=organization, name='Uttara Chamber', code='UTT'
        )
    _book(organization, staff, people[0], branch)
    _book(organization, staff, people[1], other)

    with organization_context(organization):
        assert _day(organization, branch=branch) == ['Early']


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
        assert _day(organization) == ['Rahima Begum']
