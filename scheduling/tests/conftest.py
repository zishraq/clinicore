"""Fixtures for the scheduling tests.

Everything that touches an org-scoped model opens an explicit
``organization_context``; the contextvar is never set implicitly outside a
request (docs/adr/0005-org-scoped-default-manager.md).
"""

import pytest
from django.utils import timezone

from clinical.models import Encounter
from core.context import organization_context
from patients.models import Patient


@pytest.fixture
def patient(organization) -> Patient:
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )


@pytest.fixture
def other_patient(organization) -> Patient:
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization, code='P-0002', full_name='Kamal Hossain'
        )


@pytest.fixture
def encounter(organization, patient, practitioner, branch) -> Encounter:
    with organization_context(organization):
        return Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
        )
