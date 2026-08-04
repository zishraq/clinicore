"""Role and tenant boundaries on billing.

Enforced before the view runs, so a direct URL is a 403 or a 404 rather than a
hidden template block (SPEC §6.1). In this workflow the practitioner raises the
bill and takes the money, so STAFF is out of billing entirely.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from billing.models import Invoice, Payment
from billing.tests.conftest import invoice_payload
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def their_patient(other_organization) -> Patient:
    with organization_context(other_organization):
        return Patient.objects.create(
            organization=other_organization, code='P-0001', full_name='Kamal Hossain'
        )


def test_staff_cannot_reach_any_billing_page(client, staff, patient):
    client.force_login(staff)
    for url in (
        reverse('billing:invoice_list'),
        reverse('billing:invoice_create'),
    ):
        assert client.get(url).status_code == 403


def test_staff_cannot_create_an_invoice(client, staff, patient, organization):
    client.force_login(staff)
    response = client.post(reverse('billing:invoice_create'), invoice_payload(patient))
    assert response.status_code == 403
    with organization_context(organization):
        assert Invoice.objects.count() == 0


def test_staff_cannot_record_a_payment(
    client, staff, practitioner, patient, organization, make_invoice
):
    invoice = make_invoice(organization, patient=patient, actor=practitioner)
    client.force_login(staff)
    response = client.post(
        reverse('billing:payment_create', args=[invoice.pk]),
        {'amount': '100.00', 'method': 'CASH', 'note': ''},
    )
    assert response.status_code == 403
    with organization_context(organization):
        assert Payment.objects.count() == 0


def test_another_tenants_invoice_is_a_404(
    client, practitioner, other_organization, their_patient, make_invoice, make_member
):
    theirs = make_invoice(
        other_organization,
        patient=their_patient,
        actor=make_member(other_organization, role='PRACTITIONER', phone='01799000001'),
    )
    client.force_login(practitioner)
    assert (
        client.get(reverse('billing:invoice_detail', args=[theirs.pk])).status_code
        == 404
    )
    assert (
        client.get(reverse('billing:receipt_print', args=[theirs.pk])).status_code
        == 404
    )


def test_a_payment_cannot_be_posted_onto_another_tenants_invoice(
    client,
    practitioner,
    organization,
    other_organization,
    their_patient,
    make_invoice,
    make_member,
):
    theirs = make_invoice(
        other_organization,
        patient=their_patient,
        actor=make_member(other_organization, role='PRACTITIONER', phone='01799000002'),
    )
    client.force_login(practitioner)
    response = client.post(
        reverse('billing:payment_create', args=[theirs.pk]),
        {'amount': '100.00', 'method': 'CASH', 'note': ''},
    )
    assert response.status_code == 404
    with organization_context(other_organization):
        assert Payment.objects.count() == 0


def test_an_invoice_cannot_be_raised_against_another_tenants_patient(
    client, practitioner, organization, their_patient
):
    client.force_login(practitioner)
    response = client.post(
        reverse('billing:invoice_create'), invoice_payload(their_patient)
    )
    assert response.status_code == 200  # redisplayed with an error, not saved
    assert 'patient' in response.context['form'].errors
    with organization_context(organization):
        assert Invoice.objects.count() == 0


def test_billing_settings_are_owner_only(client, practitioner, owner, organization):
    url = reverse('organizations:billing_settings')
    client.force_login(practitioner)
    assert client.get(url).status_code == 403

    client.force_login(owner)
    assert client.get(url).status_code == 200
    saved = client.post(url, {'currency': 'BDT', 'default_consultation_fee': '600.00'})
    assert saved.status_code == 302
    organization.refresh_from_db()
    assert organization.default_consultation_fee == Decimal('600.00')
