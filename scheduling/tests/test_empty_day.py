"""An empty day must say where the bookings are, not just that there are none.

"Nothing on this day yet" and a page that failed to load look identical, and the
clinic read the first as the second. The empty state now names the day it is
showing and, when other days have rows, links the nearest one.
"""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from core.context import organization_context
from scheduling import services

pytestmark = pytest.mark.django_db


def _book(organization, patient, branch, actor, *, on_date):
    with organization_context(organization):
        return services.book(
            organization,
            actor=actor,
            patient=patient,
            branch=branch,
            scheduled_date=on_date,
            scheduled_time=datetime.time(10, 30),
        )


def _day(client, on_date):
    return client.get(
        reverse('scheduling:day'), {'date': on_date.strftime('%Y-%m-%d')}
    ).content.decode()


def test_an_empty_day_names_the_day_it_is_showing(client, staff, organization):
    """The day being viewed is the fact that stops this reading as data loss."""
    client.force_login(staff)
    on_date = timezone.localdate() + datetime.timedelta(days=3)

    body = _day(client, on_date)

    assert on_date.strftime('%d %B %Y') in body


def test_an_empty_day_links_the_nearest_day_that_has_rows(
    client, staff, organization, patient, branch
):
    today = timezone.localdate()
    _book(organization, patient, branch, staff, on_date=today - datetime.timedelta(4))
    _book(organization, patient, branch, staff, on_date=today + datetime.timedelta(6))

    client.force_login(staff)
    body = _day(client, today)

    # Both directions, because looking backwards is the same question.
    assert (today - datetime.timedelta(4)).strftime('%Y-%m-%d') in body
    assert (today + datetime.timedelta(6)).strftime('%Y-%m-%d') in body
    assert 'The nearest day with any' in body


def test_the_nearest_day_is_the_nearest_one_not_merely_any(
    client, staff, organization, patient, branch
):
    today = timezone.localdate()
    near = today + datetime.timedelta(2)
    far = today + datetime.timedelta(30)
    _book(organization, patient, branch, staff, on_date=far)
    _book(organization, patient, branch, staff, on_date=near)

    client.force_login(staff)
    body = _day(client, today)

    assert near.strftime('%Y-%m-%d') in body
    assert far.strftime('%Y-%m-%d') not in body


def test_a_clinic_with_no_bookings_at_all_is_told_so(client, staff, organization):
    """Different sentence, because here the data really is empty."""
    client.force_login(staff)
    body = _day(client, timezone.localdate())

    assert 'have been booked on any day yet' in body
    assert 'The nearest day with any' not in body


def test_a_filtered_empty_day_blames_the_filter_not_the_calendar(
    client, staff, organization, patient, branch
):
    """With a filter on, "nothing here" already has an obvious cause.

    Pointing at another date would answer a question the receptionist did not
    ask, and would hide the one thing she can act on — the filter.
    """
    today = timezone.localdate()
    _book(organization, patient, branch, staff, on_date=today + datetime.timedelta(6))

    client.force_login(staff)
    body = client.get(
        reverse('scheduling:day'), {'date': today.strftime('%Y-%m-%d'), 'q': 'nobody'}
    ).content.decode()

    assert 'matches that' in body
    assert 'Clear the filters' in body
    assert 'The nearest day with any' not in body


def test_the_nearest_lookup_respects_the_branch_filter(
    client, staff, organization, patient, branch
):
    """A day empty *at this chamber* must not point at another chamber's day."""
    from organizations.models import Branch

    with organization_context(organization):
        other = Branch.objects.create(
            organization=organization, name='Second Chamber', code='TWO'
        )
    today = timezone.localdate()
    _book(organization, patient, other, staff, on_date=today + datetime.timedelta(5))

    with organization_context(organization):
        nearest = services.nearest_booked_days(
            organization, on_date=today, branch=branch
        )
        assert nearest == {'previous': None, 'next': None}

        nearest_other = services.nearest_booked_days(
            organization, on_date=today, branch=other
        )
        assert nearest_other['next'] == today + datetime.timedelta(5)


def test_the_polled_fragment_carries_the_empty_state_too(
    client, staff, organization, patient, branch
):
    """``_rows.html`` renders through the page *and* through ``day_rows``.

    A context key reaching one and not the other would make the empty state
    change five seconds after load — the trap this app has hit before.
    """
    today = timezone.localdate()
    _book(organization, patient, branch, staff, on_date=today + datetime.timedelta(6))

    client.force_login(staff)
    body = client.get(
        reverse('scheduling:day_rows'), {'date': today.strftime('%Y-%m-%d')}
    ).content.decode()

    assert 'The nearest day with any' in body
    assert (today + datetime.timedelta(6)).strftime('%Y-%m-%d') in body


def test_a_row_offers_a_tel_link_only_when_there_is_a_number(
    client, staff, organization, patient, other_patient, branch
):
    """They confirm the day's bookings by phone, usually from a mobile."""
    with organization_context(organization):
        patient.phone = '017 1234-5678'
        patient.save(update_fields=['phone'])

    today = timezone.localdate()
    _book(organization, patient, branch, staff, on_date=today)
    _book(organization, other_patient, branch, staff, on_date=today)

    client.force_login(staff)
    body = _day(client, today)

    # Separators stripped for dialling, and the typed form still displayed.
    assert 'href="tel:01712345678"' in body
    assert '017 1234-5678' in body
    # other_patient has no number, so exactly one link exists.
    assert body.count('href="tel:') == 1
