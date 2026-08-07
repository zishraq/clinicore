"""The next appointment, from the visit form and from the patient's page.

Both doors lead to ``schedule_follow_up``, which is what keeps
``Encounter.follow_up_date`` and the appointment it produced from drifting
apart — the single-writer rule ADR 0010 set out.
"""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.models import Encounter
from clinical.tests.test_encounter_flow import _payload
from core.context import organization_context
from patients.models import Patient
from scheduling import services
from scheduling.models import Appointment, AppointmentStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


def test_a_follow_up_date_becomes_a_row_somebody_will_see(
    client, practitioner, organization, patient, branch
):
    """It used to be a date written onto the visit and never shown again."""
    next_week = timezone.localdate() + datetime.timedelta(days=7)
    client.force_login(practitioner)

    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(
            patient,
            branch,
            practitioner,
            follow_up_date=next_week.strftime('%Y-%m-%d'),
        ),
    )
    assert response.status_code == 302

    with organization_context(organization):
        appointment = Appointment.objects.get()
        encounter = Encounter.objects.get()
    assert appointment.scheduled_date == next_week
    assert appointment.patient_id == patient.pk
    assert appointment.origin_encounter_id == encounter.pk
    assert appointment.status == AppointmentStatus.BOOKED


def test_a_visit_with_no_follow_up_books_nothing(
    client, practitioner, organization, patient, branch
):
    client.force_login(practitioner)
    client.post(
        reverse('clinical:encounter_create'), _payload(patient, branch, practitioner)
    )

    with organization_context(organization):
        assert not Appointment.objects.exists()


def test_moving_the_date_moves_the_row_rather_than_making_a_second(
    organization, practitioner, patient, branch
):
    """The single-writer rule, exercised directly.

    Two rows for one follow-up is the drift this guards against — and so is a
    row that stays put while the visit says a different date.
    """
    first = timezone.localdate() + datetime.timedelta(days=7)
    later = timezone.localdate() + datetime.timedelta(days=14)

    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            follow_up_date=first,
        )
        services.schedule_follow_up(
            organization, actor=practitioner, encounter=encounter, on_date=first
        )
        services.schedule_follow_up(
            organization, actor=practitioner, encounter=encounter, on_date=later
        )

        assert Appointment.objects.count() == 1
        appointment = Appointment.objects.get()
        encounter.refresh_from_db()

    assert appointment.scheduled_date == later
    # reschedule is the writer, so the visit followed the row.
    assert encounter.follow_up_date == later


def test_booking_again_for_the_same_day_is_idempotent(
    organization, practitioner, patient, branch
):
    """Saving a visit twice must not produce two identical follow-ups."""
    on_date = timezone.localdate() + datetime.timedelta(days=7)
    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            follow_up_date=on_date,
        )
        for _ in range(3):
            services.schedule_follow_up(
                organization, actor=practitioner, encounter=encounter, on_date=on_date
            )
        assert Appointment.objects.count() == 1


def test_a_cancelled_follow_up_is_not_reused(
    organization, practitioner, patient, branch
):
    """Cancelling means it is not happening; the next date is a new row.

    Rescheduling the cancelled one would quietly un-cancel it, which is a
    decision somebody made and this has no business reversing.
    """
    first = timezone.localdate() + datetime.timedelta(days=7)
    later = timezone.localdate() + datetime.timedelta(days=14)

    with organization_context(organization):
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            follow_up_date=first,
        )
        booked = services.schedule_follow_up(
            organization, actor=practitioner, encounter=encounter, on_date=first
        )
        services.transition(
            booked,
            to=AppointmentStatus.CANCELLED,
            actor=practitioner,
            reason='Rang to cancel',
        )
        services.schedule_follow_up(
            organization, actor=practitioner, encounter=encounter, on_date=later
        )
        assert Appointment.objects.count() == 2


def test_the_visit_form_calls_it_the_next_appointment(client, practitioner):
    """The label follows the terminology map, not the model's field name."""
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))

    assert response.context['form'].fields['follow_up_date'].label == (
        'Next appointment'
    )
    assert 'Follow up date' not in response.content.decode()


def test_the_patient_page_offers_the_same_action(client, staff, patient):
    """Same modal, same view, same service — not a second booking screen."""
    client.force_login(staff)
    body = client.get(reverse('patients:detail', args=[patient.pk])).content.decode()

    assert f'{reverse("scheduling:create")}?patient={patient.pk}' in body
    assert 'id="appointment-body"' in body
    # The picker inside that modal offers to register a patient, so the dialog
    # it targets has to be on this page too.
    assert 'id="add-patient-body"' in body


def test_booking_from_the_patient_page_returns_there(
    client, staff, organization, patient, branch
):
    """That page has no day list to swap, so the rows would land nowhere."""
    client.force_login(staff)
    response = client.post(
        reverse('scheduling:create'),
        {
            'patient': patient.pk,
            'appointment_branch': branch.pk,
            'appointment_date': timezone.localdate().strftime('%Y-%m-%d'),
            'redirect_to': '1',
        },
    )

    assert response.status_code == 204
    assert response['HX-Redirect'] == reverse('patients:detail', args=[patient.pk])
    with organization_context(organization):
        assert Appointment.objects.count() == 1


def test_the_redirect_target_cannot_be_dictated_by_the_caller(
    client, staff, organization, patient, branch
):
    """It is rebuilt from the patient's pk; an echoed URL is an open redirect."""
    client.force_login(staff)
    response = client.post(
        reverse('scheduling:create'),
        {
            'patient': patient.pk,
            'appointment_branch': branch.pk,
            'appointment_date': timezone.localdate().strftime('%Y-%m-%d'),
            'redirect_to': 'https://example.com/phish',
        },
    )

    assert response['HX-Redirect'] == reverse('patients:detail', args=[patient.pk])
