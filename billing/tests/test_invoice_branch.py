"""Which shelf a bill comes off.

The branch only matters once a product line moves stock, but it has to be right
by then, and it must never become a dropdown someone re-picks every day.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from billing import services
from billing.models import Invoice
from billing.tests.conftest import invoice_payload
from clinical.models import Encounter
from core.context import organization_context
from organizations.models import Branch

pytestmark = pytest.mark.django_db


def _branch(organization, name, code):
    with organization_context(organization):
        return Branch.objects.create(organization=organization, name=name, code=code)


def test_the_visits_branch_wins(organization, branch, practitioner, encounter):
    other = _branch(organization, 'Second Chamber', 'TWO')
    with organization_context(organization):
        resolved = services.resolve_invoice_branch(
            organization, actor=practitioner, encounter=encounter
        )
    assert resolved == branch != other


def test_a_single_branch_clinic_is_never_asked(organization, branch, practitioner):
    with organization_context(organization):
        resolved = services.resolve_invoice_branch(organization, actor=practitioner)
    assert resolved == branch


def test_a_standalone_bill_falls_back_to_where_the_practitioner_last_worked(
    organization, branch, practitioner, patient
):
    second = _branch(organization, 'Second Chamber', 'TWO')
    with organization_context(organization):
        Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=second,
            occurred_at=timezone.now(),
        )
        resolved = services.resolve_invoice_branch(organization, actor=practitioner)
    assert resolved == second


def test_a_multi_branch_clinic_with_no_signal_asks(organization, branch, practitioner):
    _branch(organization, 'Second Chamber', 'TWO')
    with organization_context(organization):
        # No visit, and this practitioner has never worked anywhere yet.
        assert services.resolve_invoice_branch(organization, actor=practitioner) is None


def test_a_bill_raised_through_the_form_is_stamped_without_being_asked(
    client, organization, branch, practitioner, patient
):
    """One branch, so the field is not even rendered — and it still lands."""
    client.force_login(practitioner)
    response = client.get(reverse('billing:invoice_create'))
    assert 'branch' not in response.context['form'].fields

    client.post(reverse('billing:invoice_create'), invoice_payload(patient))
    with organization_context(organization):
        assert Invoice.objects.get().branch == branch
