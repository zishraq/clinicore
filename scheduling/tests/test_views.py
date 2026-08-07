"""The day list through the views, driven as the receptionist.

STAFF is the primary user of this screen rather than a role it was adapted for,
so almost every test here signs in as STAFF. What matters is that the whole
working path — add a walk-in, mark arrived, no-show, cancel with a reason —
completes with no clinical access anywhere near it.
"""

import datetime
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from core.context import organization_context
from scheduling import services
from scheduling.models import Appointment, AppointmentStatus

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


def test_staff_can_open_the_day_list(client, staff, booking):
    client.force_login(staff)
    response = client.get(reverse('scheduling:day'))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'Rahima Begum' in body
    assert 'Waiting' in body
    assert 'Expected' in body


def test_the_day_list_is_in_both_navs_for_staff(client, staff):
    """Their first screen. Before this they had Dashboard and Patients."""
    client.force_login(staff)
    body = client.get(reverse('core:dashboard')).content.decode()
    # Sidebar and bottom nav both, and neither behind can_view_clinical.
    assert body.count(reverse('scheduling:day')) >= 2


def test_staff_can_add_a_walk_in(client, staff, organization, patient, branch):
    client.force_login(staff)
    assert client.get(reverse('scheduling:walk_in')).status_code == 200

    response = client.post(
        reverse('scheduling:walk_in'),
        {'patient': patient.pk, 'walk_in_branch': branch.pk, 'note': 'Chest pain'},
    )
    assert response.status_code == 200
    with organization_context(organization):
        created = Appointment.objects.get()
        assert created.status == AppointmentStatus.ARRIVED
        assert created.is_walk_in
        assert created.note == 'Chest pain'
    # The response is the rebuilt day, so the row appears without a reload.
    assert 'Rahima Begum' in response.content.decode()


def test_the_registration_offer_has_somewhere_to_open(client, staff):
    """The walk-in's "add a new patient" offer must have its dialog on the page.

    base.html's modals block is empty, so a page that renders the picker owes it
    templates/patients/_add_patient_modal.html. Without it htmx raises a
    targetError and the offer silently does nothing — which is how the walk-in
    modal shipped, past a green suite, because every other test here asserts a
    status code and this failure is entirely in the browser.

    Asserted as a coupling rather than a literal id: the offer's own hx-target
    is read out of the fragment, so renaming it fails here instead of in a
    clinic.
    """
    client.force_login(staff)
    offer = client.get(
        reverse('patients:suggestions'), {'q': 'Nobody Registered Yet'}
    ).content.decode()
    target = re.search(r'data-add-patient.*?hx-target="#([\w-]+)"', offer, re.S)
    assert target, 'the picker no longer offers to register a patient'

    body = client.get(reverse('scheduling:day')).content.decode()
    assert f'id="{target.group(1)}"' in body


def test_a_walk_in_without_a_patient_is_refused_into_its_own_modal(
    client, staff, organization, branch
):
    """The error must not swap over the day list, which is the button's target."""
    client.force_login(staff)
    response = client.post(reverse('scheduling:walk_in'), {'walk_in_branch': branch.pk})
    assert response.status_code == 200
    assert response['HX-Retarget'] == '#walk-in-body'
    assert 'before saving' in response.content.decode()
    with organization_context(organization):
        assert not Appointment.objects.exists()


def test_staff_can_mark_arrived(client, staff, organization, booking):
    client.force_login(staff)
    response = client.post(reverse('scheduling:mark_arrived', args=[booking.pk]))
    assert response.status_code == 200

    booking.refresh_from_db()
    assert booking.status == AppointmentStatus.ARRIVED
    # Rebuilt bands come straight back, so the row moves without a reload.
    assert 'waiting' in response.content.decode()


def test_marking_arrived_is_a_post_only(client, staff, booking):
    client.force_login(staff)
    assert (
        client.get(reverse('scheduling:mark_arrived', args=[booking.pk])).status_code
        == 405
    )


def test_staff_can_mark_a_no_show(client, staff, booking):
    client.force_login(staff)
    client.post(reverse('scheduling:no_show', args=[booking.pk]))
    booking.refresh_from_db()
    assert booking.status == AppointmentStatus.NO_SHOW


def test_staff_can_cancel_with_a_reason(client, staff, booking):
    client.force_login(staff)
    assert (
        client.get(reverse('scheduling:cancel', args=[booking.pk])).status_code == 200
    )

    response = client.post(
        reverse('scheduling:cancel', args=[booking.pk]),
        {'reason': 'Rang to cancel'},
    )
    assert response.status_code == 200
    assert 'HX-Retarget' not in response

    booking.refresh_from_db()
    assert booking.status == AppointmentStatus.CANCELLED
    assert booking.resolution_reason == 'Rang to cancel'


def test_a_reasonless_cancellation_goes_back_to_the_modal(client, staff, booking):
    """Otherwise the refusal would replace the whole day list with a form."""
    client.force_login(staff)
    response = client.post(
        reverse('scheduling:cancel', args=[booking.pk]), {'reason': '   '}
    )
    assert response.status_code == 200
    assert response['HX-Retarget'] == '#cancel-body'
    assert 'requires a reason' in response.content.decode()

    booking.refresh_from_db()
    assert booking.status == AppointmentStatus.BOOKED


def test_actions_keep_the_day_being_looked_at(
    client, staff, organization, patient, branch
):
    """A POST has no query string, so the filters ride along in the body."""
    tomorrow = timezone.localdate() + datetime.timedelta(days=1)
    with organization_context(organization):
        future = services.book(
            organization,
            actor=staff,
            patient=patient,
            branch=branch,
            scheduled_date=tomorrow,
        )

    client.force_login(staff)
    response = client.post(
        reverse('scheduling:mark_arrived', args=[future.pk]),
        {'date': tomorrow.strftime('%Y-%m-%d'), 'branch': ''},
    )
    # Rebuilt for tomorrow, so the row the user just actioned is still on screen.
    assert 'Rahima Begum' in response.content.decode()


def test_the_polled_fragment_holds_no_typed_input(client, staff, booking):
    """The whole reason the modals live in base.html's modals block.

    A five-second swap over a half-written cancellation reason is a bug that
    only shows up under time pressure and gets reported as "it clears what I
    type", so this is asserted rather than remembered.
    """
    client.force_login(staff)
    fragment = client.get(reverse('scheduling:day_rows')).content.decode()

    assert '<input' not in fragment
    assert '<textarea' not in fragment
    assert '<select' not in fragment


def test_the_page_polls_only_while_it_is_being_looked_at(client, staff):
    client.force_login(staff)
    body = client.get(reverse('scheduling:day')).content.decode()
    assert "every 5s [document.visibilityState === 'visible']" in body
    assert reverse('scheduling:day_rows') in body


def test_the_day_list_reaches_no_clinical_or_billing_data(client, staff, booking):
    """STAFF's screen. Nothing on it should tempt a link they cannot follow."""
    client.force_login(staff)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert '/clinical/' not in body
    assert '/billing/' not in body
    assert '/stock/' not in body


def test_another_tenants_appointment_is_a_404(
    client, staff, other_organization, branch
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
        appointment = services.book(
            other_organization,
            actor=staff,
            patient=theirs,
            branch=their_branch,
            scheduled_date=timezone.localdate(),
        )

    client.force_login(staff)
    assert (
        client.post(
            reverse('scheduling:mark_arrived', args=[appointment.pk])
        ).status_code
        == 404
    )


def test_a_bad_date_falls_back_to_today(client, staff):
    client.force_login(staff)
    response = client.get(reverse('scheduling:day'), {'date': 'not-a-date'})
    assert response.status_code == 200
    assert response.context['on_date'] == timezone.localdate()
