"""Tenant isolation for the case record's five history tables.

``simple_history`` generates ``Historical*`` models with their own manager,
which does **not** inherit ``OrgScopedManager``. ``CaseRecord.history`` is
therefore unfiltered across tenants — the same class of leak
``core/tests/test_org_scoping.py`` guards against for live rows, and invisible
to it because historical models are not ``OrgOwnedModel`` subclasses.

Five models here rather than one, because the case record is five tables and a
filter remembered on four of them is a leak on the fifth.

See docs/adr/0006-encounter-amendments.md and clinical/tests/test_history_isolation.py.
"""

import pytest
from django.urls import reverse

from core.context import organization_context
from patients import services
from patients.models import (
    CaseAnalysisEntry,
    CaseComplaint,
    CaseInvestigation,
    CaseModality,
    CaseRecord,
    Patient,
)

pytestmark = pytest.mark.django_db

#: Each child, with the column that carries its distinguishing text.
CHILDREN = (
    (CaseComplaint, 'complaints', 'complaint'),
    (CaseModality, 'modalities', 'better'),
    (CaseInvestigation, 'investigations', 'name'),
    (CaseAnalysisEntry, 'analysis_entries', 'finding'),
)


def _record_in(organization, *, marker: str) -> CaseRecord:
    """A whole case record in its own organization, children and all."""
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization,
            code=f'P-{organization.pk:04d}',
            full_name='Some Patient',
        )
        record = CaseRecord.objects.create(
            organization=organization,
            patient=patient,
            assessment_provisional=marker,
        )
        for model, _related, column in CHILDREN:
            # ``factor`` is not optional on the fixed grid, so §9's row needs one.
            extra = {'factor': marker} if model is CaseModality else {}
            model.objects.create(
                organization=organization,
                case_record=record,
                **extra,
                **{column: marker},
            )
        return record


@pytest.fixture
def mine(organization) -> CaseRecord:
    return _record_in(organization, marker='Mine')


@pytest.fixture
def theirs(other_organization) -> CaseRecord:
    return _record_in(other_organization, marker='Theirs')


def test_the_raw_history_manager_is_not_organization_scoped(mine, theirs):
    """Documents the trap this module guards, so a regression here is loud."""
    with organization_context(mine.organization):
        assert CaseRecord.objects.count() == 1
        assert CaseRecord.history.count() == 2


def test_revision_queries_are_filtered_by_organization(mine, theirs):
    revisions = services.case_record_revisions(mine.organization, mine)

    assert [row.assessment_provisional for row in revisions] == ['Mine']
    # Even handed another tenant's record, the org filter wins and the caller
    # learns nothing about it.
    assert not services.case_record_revisions(mine.organization, theirs).exists()


@pytest.mark.parametrize(('model', 'related', 'column'), CHILDREN)
def test_each_child_history_can_be_filtered_by_tenant(
    mine, theirs, model, related, column
):
    """A filter remembered on four tables and forgotten on the fifth is a leak."""
    assert model.history.count() == 2

    scoped = model.history.filter(organization=mine.organization)

    assert [getattr(row, column) for row in scoped] == ['Mine']


def test_the_case_record_page_cannot_reach_another_organizations_patient(
    client, mine, theirs, make_member
):
    user = make_member(mine.organization, role='PRACTITIONER', phone='01700009999')
    client.force_login(user)

    response = client.get(reverse('patients:case_record', args=[theirs.patient_id]))

    assert response.status_code == 404
    assert b'Theirs' not in response.content
