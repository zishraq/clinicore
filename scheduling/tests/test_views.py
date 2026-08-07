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


def test_staff_reach_no_clinical_or_billing_data_from_the_day_list(
    client, staff, organization, patient, branch
):
    """STAFF's screen stays clinical-free, including its arrived rows.

    The boundary is on the role, not on the screen: the day list now offers the
    doctor a way into the visit form, and this is the half that must not. An
    offer STAFF cannot follow is a 403 they were invited to walk into.
    """
    with organization_context(organization):
        services.walk_in(organization, actor=staff, patient=patient, branch=branch)

    client.force_login(staff)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert 'Rahima Begum' in body, 'the arrived row this is asserted against is missing'
    assert '/clinical/' not in body
    assert '/billing/' not in body
    assert '/stock/' not in body


def test_the_polled_fragment_is_gated_the_same_way_the_page_is(
    client, staff, organization, patient, branch
):
    """The fragment is a second render of the same rows, through a second view.

    Asserted separately because the failure mode is invisible on load: if
    ``membership`` reached the page's context but not ``day_rows``', the gate
    would be silently false for everyone and the doctor's "start visit" link
    would disappear five seconds after he opened the screen — or, the other way
    round, appear for STAFF on the first poll. Found while browser-checking the
    swap, where the fragment renders identically for a practitioner.
    """
    with organization_context(organization):
        services.walk_in(organization, actor=staff, patient=patient, branch=branch)

    client.force_login(staff)
    fragment = client.get(reverse('scheduling:day_rows')).content.decode()

    assert 'Rahima Begum' in fragment, 'the arrived row is missing from the fragment'
    assert '/clinical/' not in fragment
    assert '/billing/' not in fragment


def test_the_polled_fragment_keeps_the_practitioners_link_and_badges(
    client, practitioner, organization, seen_with_bill, patient, branch
):
    """The other half of the same coupling, from the role that should see them."""
    with organization_context(organization):
        services.walk_in(
            organization, actor=practitioner, patient=patient, branch=branch
        )

    client.force_login(practitioner)
    fragment = client.get(reverse('scheduling:day_rows')).content.decode()

    assert f'{reverse("clinical:encounter_create")}?appointment=' in fragment
    assert 'Unpaid' in fragment


def test_a_practitioner_can_start_the_visit_from_an_arrived_row(
    client, practitioner, organization, patient, branch
):
    """The other half. He learns they arrived here, so he acts on it here."""
    with organization_context(organization):
        arrived = services.walk_in(
            organization, actor=practitioner, patient=patient, branch=branch
        )

    client.force_login(practitioner)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert f'{reverse("clinical:encounter_create")}?appointment={arrived.pk}' in body


def test_only_arrived_rows_offer_to_start_the_visit(
    client, practitioner, organization, booking
):
    """A booked patient is not in the building yet; ARRIVED is the only source
    state ``transition(to=SEEN)`` accepts, so offering it earlier would be a
    button that refuses."""
    client.force_login(practitioner)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert f'?appointment={booking.pk}' not in body


@pytest.fixture
def seen_with_bill(organization, patient, branch, practitioner):
    """A visit written from the day list, and the bill it was charged on.

    Built through the models rather than the billing form: what is under test is
    the read through appointment → encounter → invoice, not form binding.
    """
    from decimal import Decimal

    from billing.models import Invoice, InvoiceItem, LineType
    from billing.services import next_invoice_number
    from clinical.models import Encounter, EncounterStatus

    with organization_context(organization):
        appointment = services.walk_in(
            organization, actor=practitioner, patient=patient, branch=branch
        )
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            status=EncounterStatus.FINALIZED,
            finalized_at=timezone.now(),
        )
        services.transition(
            appointment,
            to=AppointmentStatus.SEEN,
            actor=practitioner,
            encounter=encounter,
        )
        invoice = Invoice.objects.create(
            organization=organization,
            created_by=practitioner,
            patient=patient,
            encounter=encounter,
            branch=branch,
            currency=organization.currency,
            number=next_invoice_number(organization),
        )
        InvoiceItem.objects.create(
            organization=organization,
            invoice=invoice,
            line_type=LineType.CONSULTATION,
            name_snapshot='Consultation fee',
            quantity=Decimal('1'),
            unit_price=Decimal('500.00'),
            sort_order=0,
        )
    return appointment, invoice


def test_the_row_shows_what_the_visit_was_billed(client, practitioner, seen_with_bill):
    client.force_login(practitioner)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert 'Unpaid' in body


def test_the_payment_state_is_read_through_and_not_stored(
    client, practitioner, organization, seen_with_bill
):
    """Paying at the desk changes the badge with nothing written to the row.

    The whole reason ADR 0010 kept payment off the appointment: a copy would be
    right when written and wrong the moment a payment lands.
    """
    appointment, invoice = seen_with_bill
    # From the database, not from the fixture's copy: ``transition`` writes
    # through a locked re-read, so the instance it was called with is stale.
    appointment.refresh_from_db()
    updated_before = appointment.updated_at

    from decimal import Decimal

    from billing.services import record_payment

    with organization_context(organization):
        record_payment(
            organization,
            invoice=invoice,
            actor=practitioner,
            amount=Decimal('500.00'),
            method='CASH',
        )

    client.force_login(practitioner)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert 'Paid' in body
    appointment.refresh_from_db()
    assert appointment.updated_at == updated_before


def test_staff_are_shown_no_payment_state_at_all(client, staff, seen_with_bill):
    """SPEC §6.1 as amended: every billing surface is PRACTITIONER/OWNER, even
    on the screen STAFF otherwise owns."""
    client.force_login(staff)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert 'Rahima Begum' in body, 'the seen row this is asserted against is missing'
    assert 'Unpaid' not in body
    assert 'Paid' not in body


def test_a_visit_with_no_bill_says_so_rather_than_looking_paid(
    client, practitioner, organization, patient, branch
):
    """Blank would read as "nothing owing", which is the opposite of the truth."""
    from clinical.models import Encounter

    with organization_context(organization):
        appointment = services.walk_in(
            organization, actor=practitioner, patient=patient, branch=branch
        )
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
        )
        services.transition(
            appointment,
            to=AppointmentStatus.SEEN,
            actor=practitioner,
            encounter=encounter,
        )

    client.force_login(practitioner)
    body = client.get(reverse('scheduling:day')).content.decode()

    assert 'No bill' in body


def test_the_bills_are_one_query_for_the_whole_day(
    practitioner,
    organization,
    branch,
    seen_with_bill,
    django_assert_num_queries,
):
    """The lookup annotates a day, it does not walk it.

    Same call as the invoice list's: totals arrive as annotations so the page
    cost does not grow with the number of patients seen.
    """
    from clinical.models import Encounter
    from patients.models import Patient

    with organization_context(organization):
        for index in range(4):
            extra = Patient.objects.create(
                organization=organization,
                code=f'P-01{index}',
                full_name=f'Patient {index}',
            )
            appointment = services.walk_in(
                organization, actor=practitioner, patient=extra, branch=branch
            )
            encounter = Encounter.objects.create(
                organization=organization,
                patient=extra,
                practitioner=practitioner,
                branch=branch,
                occurred_at=timezone.now(),
            )
            services.transition(
                appointment,
                to=AppointmentStatus.SEEN,
                actor=practitioner,
                encounter=encounter,
            )
        closed = services.day_list(organization, on_date=timezone.localdate())['closed']
        with django_assert_num_queries(2):
            rows = services.with_bills(organization, closed)
            assert [row.bill for row in rows].count(None) == 4


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
