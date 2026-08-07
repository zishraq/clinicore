"""Writing the visit from the day list, and consuming the row by saving it.

The doctor learns someone has arrived on the day list, so that is where the
visit starts. What matters here is the join: the form arrives already knowing
who and where, and the appointment is marked seen by the save rather than by a
button (ADR 0010).
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from clinical.models import Encounter
from clinical.tests.test_encounter_flow import _payload
from core.context import organization_context
from patients.models import Patient
from scheduling import services as scheduling_services
from scheduling.models import AppointmentStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient(organization):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


@pytest.fixture
def arrived(organization, patient, branch, practitioner):
    """Someone at the desk, already marked in by reception."""
    with organization_context(organization):
        return scheduling_services.walk_in(
            organization,
            actor=practitioner,
            patient=patient,
            branch=branch,
            practitioner=practitioner,
            note='Chest pain',
        )


def _messages(response) -> list:
    return [str(message) for message in get_messages(response.wsgi_request)]


def test_the_form_arrives_prefilled_from_the_row(
    client, practitioner, arrived, patient, branch
):
    """The receptionist already answered who, where and with whom."""
    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:encounter_create'), {'appointment': arrived.pk}
    )

    assert response.status_code == 200
    initial = response.context['form'].initial
    assert initial['patient'] == patient.pk
    assert initial['branch'] == branch.pk
    assert initial['practitioner'] == practitioner.pk
    # The picker's search box shows a name rather than an empty field over a
    # hidden pk, which is the bug _selected_patient exists to prevent.
    assert response.context['selected_patient'] == patient


def test_the_row_travels_through_the_post_as_a_hidden_field(
    client, practitioner, arrived
):
    """A query string does not survive the POST, so the form has to carry it."""
    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:encounter_create'), {'appointment': arrived.pk}
    ).content.decode()

    assert f'name="appointment" value="{arrived.pk}"' in body


def test_saving_marks_the_appointment_seen_and_links_the_visit(
    client, practitioner, patient, branch, organization, arrived
):
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, appointment=arrived.pk),
    )
    assert response.status_code == 302

    arrived.refresh_from_db()
    assert arrived.status == AppointmentStatus.SEEN
    assert arrived.seen_at is not None

    with organization_context(organization):
        encounter = Encounter.objects.get()
    assert encounter.appointment_id == arrived.pk


def test_a_visit_written_without_an_appointment_is_unchanged(
    client, practitioner, patient, branch, organization
):
    """A5's guarantee, restated: the day list is a way in, not the only one."""
    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'), _payload(patient, branch, practitioner)
    )
    assert response.status_code == 302

    with organization_context(organization):
        encounter = Encounter.objects.get()
    assert encounter.appointment_id is None


def test_a_row_cancelled_mid_consultation_still_saves_the_visit(
    client, practitioner, patient, branch, organization, arrived
):
    """The refusal must cost the doctor the row, never the note.

    Reception cancels while the consultation is being written. The visit is
    complete and valid with no appointment at all, so it is saved and the
    failure to consume the row is reported instead of raised.
    """
    with organization_context(organization):
        scheduling_services.transition(
            arrived,
            to=AppointmentStatus.CANCELLED,
            actor=practitioner,
            reason='Left before being seen',
        )

    client.force_login(practitioner)
    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, appointment=arrived.pk),
    )
    assert response.status_code == 302

    with organization_context(organization):
        encounter = Encounter.objects.get()
    assert encounter.appointment_id is None

    arrived.refresh_from_db()
    assert arrived.status == AppointmentStatus.CANCELLED
    assert any('no longer waiting' in message for message in _messages(response))


def test_a_second_visit_cannot_consume_a_row_already_seen(
    client, practitioner, patient, branch, organization, arrived
):
    """Two tabs, two saves. The second visit is real; the row stays the first's.

    ``Encounter.appointment`` is one-to-one precisely so "was this seen?" has
    one answer, and ``transition`` being idempotent is what keeps the second
    save from trying to move the link.
    """
    client.force_login(practitioner)
    payload = _payload(patient, branch, practitioner, appointment=arrived.pk)
    first = client.post(reverse('clinical:encounter_create'), payload)
    second = client.post(reverse('clinical:encounter_create'), payload)

    assert first.status_code == 302
    assert second.status_code == 302

    with organization_context(organization):
        linked = Encounter.objects.filter(appointment=arrived).count()
        assert Encounter.objects.count() == 2
    assert linked == 1


def test_another_tenants_row_prefills_nothing(
    client, practitioner, other_organization, branch
):
    """Scoping is ambient, so a foreign pk simply finds no row."""
    with organization_context(other_organization):
        from organizations.models import Branch

        theirs = Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Someone Else'
        )
        their_branch = Branch.objects.create(
            organization=other_organization, name='Theirs', code='THR'
        )
        foreign = scheduling_services.walk_in(
            other_organization,
            actor=practitioner,
            patient=theirs,
            branch=their_branch,
        )

    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:encounter_create'), {'appointment': foreign.pk}
    )

    assert response.status_code == 200
    assert response.context['appointment'] is None
    # Not "prefilled with someone else" — prefilled with nobody, because the
    # lookup found nothing to read a patient off.
    assert 'patient' not in response.context['form'].initial
    assert f'name="appointment" value="{foreign.pk}"' not in response.content.decode()


def test_a_visit_prefilled_from_a_row_still_defaults_the_time(
    client, practitioner, arrived
):
    """The appointment answers who and where; the clock still answers when."""
    client.force_login(practitioner)
    response = client.get(
        reverse('clinical:encounter_create'), {'appointment': arrived.pk}
    )

    occurred = response.context['form'].initial['occurred_at']
    assert occurred.date() == timezone.localdate()
