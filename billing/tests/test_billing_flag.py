"""The per-organization billing capability.

``Organization.billing_enabled`` is ``advice_enabled`` (A3) one size larger: it
hides a whole app rather than half a form. The rule is the same and it is what
most of this file is about — **hiding a feature must never hide data**. A clinic
that is not ready to put money in the system turns it off, works for a month,
turns it back on, and finds every bill, payment and balance exactly as it was.
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from billing import services
from billing.models import Invoice, LineType, PaymentMethod, PaymentStatus
from clinical.models import Encounter, EncounterStatus
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


#: Every named route in the billing app, with how to build its arguments from
#: the seeded invoice. Written out rather than discovered, because a route that
#: silently stopped being checked is the failure this file guards against.
def _billing_urls(invoice) -> list[str]:
    return [
        reverse('billing:invoice_list'),
        reverse('billing:invoice_create'),
        reverse('billing:line_row'),
        reverse('billing:invoice_detail', args=[invoice.pk]),
        reverse('billing:invoice_update', args=[invoice.pk]),
        reverse('billing:invoice_void', args=[invoice.pk]),
        reverse('billing:receipt_print', args=[invoice.pk]),
        reverse('billing:payment_create', args=[invoice.pk]),
        reverse('billing:payment_void', args=[invoice.pk, invoice.payment_pk]),
    ]


@pytest.fixture
def billed_visit(organization, branch, practitioner):
    """A visit, a bill against it, and a part payment — recorded while billing
    was on, which is the state the switch has to be able to restore."""
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
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
        invoice = Invoice.objects.create(
            organization=organization,
            patient=patient,
            encounter=encounter,
            branch=branch,
            number=services.next_invoice_number(organization),
            currency='BDT',
            issued_at=timezone.now(),
        )
        invoice.items.create(
            organization=organization,
            line_type=LineType.CONSULTATION,
            name_snapshot='Consultation',
            quantity=Decimal('1'),
            unit_price=Decimal('500.00'),
        )
        payment = services.record_payment(
            organization,
            invoice=invoice,
            actor=practitioner,
            amount=Decimal('200.00'),
            method=PaymentMethod.CASH,
            note='',
        )
        billed = Invoice.objects.with_totals().get(pk=invoice.pk)
        # Carried on the object so the tests can build every billing URL
        # without reopening an organization context to reach the payment.
        billed.payment_pk = payment.pk
        billed.encounter_pk = encounter.pk
        billed.patient_pk = patient.pk
        billed.branch_pk = branch.pk
        billed.practitioner_id = practitioner.pk
        return billed


@pytest.fixture
def billing_off(organization):
    organization.billing_enabled = False
    organization.save(update_fields=['billing_enabled', 'updated_at'])
    return organization


def test_the_default_is_on(organization):
    """Existing behaviour is unchanged for anyone who never touches the switch."""
    assert organization.billing_enabled is True


# --- the routes are gone, for everybody ------------------------------------


@pytest.mark.parametrize('role', ['owner', 'practitioner', 'staff', 'developer'])
def test_every_billing_url_404s_for_every_role(
    client, request, organization, billing_off, billed_visit, role
):
    """404 rather than 403, and the same answer for the roles that would
    otherwise be allowed as for the one that would not.

    403 says the thing exists and you may not have it, which invites somebody to
    go and ask for access to a feature their own clinic switched off. 404 says
    there is nothing here, which is true.
    """
    client.force_login(request.getfixturevalue(role))
    for url in _billing_urls(billed_visit):
        assert client.get(url).status_code == 404, url


def test_the_capability_check_runs_before_the_role_check(
    client, staff, billing_off, billed_visit
):
    """STAFF is refused by ``clinical_access_required`` when billing is on, so
    the ordering is what decides whether they learn the feature exists."""
    client.force_login(staff)
    assert client.get(reverse('billing:invoice_list')).status_code == 404


def test_a_post_is_refused_too_and_writes_nothing(
    client, practitioner, organization, billing_off, billed_visit
):
    """A hidden form is not a closed door; the POST endpoints have to refuse."""
    client.force_login(practitioner)
    response = client.post(
        reverse('billing:payment_create', args=[billed_visit.pk]),
        {'amount': '100.00', 'method': PaymentMethod.CASH, 'note': ''},
    )
    assert response.status_code == 404
    with organization_context(organization):
        assert billed_visit.payments.count() == 1


def test_staff_still_gets_403_rather_than_404_when_billing_is_on(
    client, staff, billed_visit
):
    """The role boundary is untouched: the capability gate is a second question,
    not a replacement for the first."""
    client.force_login(staff)
    assert client.get(reverse('billing:invoice_list')).status_code == 403


# --- nothing that mentions billing is rendered -----------------------------


def _page(client, url) -> str:
    return client.get(url).content.decode()


def test_the_nav_entry_is_absent(client, practitioner, billing_off):
    body = _page(client_logged(client, practitioner), reverse('patients:list'))
    assert reverse('billing:invoice_list') not in body


def client_logged(client, user):
    client.force_login(user)
    return client


def test_the_visit_page_offers_no_billing_action(
    client, practitioner, organization, billing_off, billed_visit
):
    body = _page(
        client_logged(client, practitioner),
        reverse('clinical:encounter_detail', args=[billed_visit.encounter_pk]),
    )
    assert 'Create bill' not in body
    assert reverse('billing:invoice_create') not in body
    # The pill naming an existing bill is gone too — that one carries a balance.
    assert billed_visit.number not in body


def test_the_patient_page_has_no_bills_section_and_no_outstanding_figure(
    client, practitioner, organization, billing_off, billed_visit
):
    """The Outstanding total is the sharp one: a number nobody asked for,
    rendered on a page that is otherwise clean."""
    response = client_logged(client, practitioner).get(
        reverse('patients:detail', args=[billed_visit.patient_pk])
    )
    body = response.content.decode()
    assert 'Outstanding' not in body
    assert billed_visit.number not in body
    assert reverse('billing:invoice_create') not in body
    # Skipped in the view, not merely hidden in the template.
    assert response.context['outstanding'] is None
    assert response.context['invoices'] == []


def test_the_day_list_has_no_payment_column(
    client, practitioner, organization, billing_off, billed_visit
):
    from scheduling.models import Appointment, AppointmentSource

    with organization_context(organization):
        now = timezone.now()
        appointment = Appointment.objects.create(
            organization=organization,
            patient_id=billed_visit.patient_pk,
            branch_id=billed_visit.branch_pk,
            practitioner_id=billed_visit.practitioner_id,
            scheduled_date=timezone.localdate(),
            source=AppointmentSource.WALK_IN,
            arrived_at=now,
            seen_at=now,
        )
        encounter = Encounter.objects.get(pk=billed_visit.encounter_pk)
        encounter.appointment = appointment
        encounter.save(update_fields=['appointment'])

    response = client_logged(client, practitioner).get(reverse('scheduling:day'))
    body = response.content.decode()
    assert 'No bill' not in body
    # Hidden by not being looked up: with the switch off nothing attaches a bill
    # to the row, so a template that forgot its check would have nothing to leak.
    assert all(getattr(row, 'bill', None) is None for row in response.context['rows'])


# --- and the data is untouched ---------------------------------------------


def test_turning_it_back_on_restores_the_bill_its_payments_and_its_balance(
    client, practitioner, organization, billed_visit
):
    """The point of the whole increment.

    A switch that loses a clinic's money is not a switch, it is a delete with a
    friendly label.
    """
    with organization_context(organization):
        before = Invoice.objects.with_totals().get(pk=billed_visit.pk)
    assert before.amount_due == Decimal('500.00')
    assert before.amount_paid == Decimal('200.00')
    assert before.balance == Decimal('300.00')
    assert before.payment_status == PaymentStatus.PARTIALLY_PAID

    organization.billing_enabled = False
    organization.save(update_fields=['billing_enabled', 'updated_at'])
    client.force_login(practitioner)
    assert client.get(reverse('billing:invoice_list')).status_code == 404
    # The rows never went anywhere; only the routes did.
    with organization_context(organization):
        assert Invoice.objects.count() == 1

    organization.billing_enabled = True
    organization.save(update_fields=['billing_enabled', 'updated_at'])

    with organization_context(organization):
        after = Invoice.objects.with_totals().get(pk=billed_visit.pk)
        assert after.payments.count() == 1
    assert after.number == before.number
    assert after.amount_due == before.amount_due
    assert after.amount_paid == before.amount_paid
    assert after.balance == before.balance
    assert after.payment_status == before.payment_status

    body = _page(client, reverse('billing:invoice_detail', args=[after.pk]))
    assert after.number in body
    assert '300.00' in body


# --- the switch itself ------------------------------------------------------


def test_an_administrator_can_turn_it_off_and_on(client, owner, organization):
    url = reverse('organizations:feature_settings')
    client.force_login(owner)

    # An unticked checkbox posts nothing at all, which is how the switch is
    # turned off — and the reason the settings view binds on request.method.
    assert client.post(url, {'advice_enabled': 'on'}).status_code == 302
    organization.refresh_from_db()
    assert organization.billing_enabled is False

    assert client.post(url, {'billing_enabled': 'on'}).status_code == 302
    organization.refresh_from_db()
    assert organization.billing_enabled is True


def test_the_switch_says_that_nothing_is_deleted(client, owner):
    """The switch looks destructive and is not. A clinic will not turn it off
    unless the screen says so."""
    client.force_login(owner)
    body = _page(client, reverse('organizations:feature_settings'))
    assert 'Nothing is deleted' in body


def test_the_billing_settings_screen_goes_with_it(client, owner, billing_off):
    """Currency and the consultation fee are read only by billing surfaces, so
    with the switch off that screen configures nothing that renders."""
    client.force_login(owner)
    assert client.get(reverse('organizations:billing_settings')).status_code == 404
    body = _page(client, reverse('organizations:feature_settings'))
    assert reverse('organizations:billing_settings') not in body


def test_staff_cannot_reach_the_switch(client, staff):
    client.force_login(staff)
    assert client.get(reverse('organizations:feature_settings')).status_code == 403


def test_the_switch_is_per_organization(organization, other_organization):
    """One clinic turning billing off must not touch the one next door."""
    organization.billing_enabled = False
    organization.save(update_fields=['billing_enabled', 'updated_at'])
    other_organization.refresh_from_db()
    assert other_organization.billing_enabled is True


# --- the loaders ------------------------------------------------------------


def test_bootstrap_clinic_can_start_a_clinic_with_billing_off():
    from io import StringIO

    from django.core.management import call_command

    from organizations.models import Organization

    call_command(
        'bootstrap_clinic',
        '--name=Global Homeopathy Clinic',
        '--timezone=Asia/Dhaka',
        '--branch=Mirpur Chamber',
        '--admin-phone=01700000000',
        '--admin-name=Dr Rafiqul Islam',
        '--no-billing',
        stdout=StringIO(),
    )
    assert (
        Organization.objects.get(slug='global-homeopathy-clinic').billing_enabled
        is False
    )


def test_bootstrap_clinic_leaves_billing_on_by_default():
    from io import StringIO

    from django.core.management import call_command

    from organizations.models import Organization

    call_command(
        'bootstrap_clinic',
        '--name=Karim Homeo Hall',
        '--timezone=Asia/Dhaka',
        '--branch=Main Chamber',
        '--admin-phone=01712345678',
        '--admin-name=Dr Ayesha Karim',
        stdout=StringIO(),
    )
    assert Organization.objects.get(slug='karim-homeo-hall').billing_enabled is True


def test_the_demo_keeps_billing_on():
    """The demo exists to show the whole product."""
    from io import StringIO

    from django.core.management import call_command
    from django.test import override_settings

    from organizations.models import Organization

    with override_settings(DEBUG=True):
        call_command('bootstrap_demo', stdout=StringIO())
    assert Organization.objects.get(slug='demo-clinic').billing_enabled is True
