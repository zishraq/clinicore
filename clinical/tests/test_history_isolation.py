"""Tenant isolation for the history tables.

simple-history generates ``Historical*`` models with their own manager, which
does **not** inherit ``OrgScopedManager``. ``Encounter.history`` is therefore
unfiltered across tenants — the same class of leak
``core/tests/test_org_scoping.py`` guards against for live rows, but invisible
to it because historical models are not ``OrgOwnedModel`` subclasses.

See docs/adr/0006-encounter-amendments.md.
"""

import pytest
from django.apps import apps
from django.urls import reverse
from django.utils import timezone

from accounts.models import Membership, Role, User
from clinical import services
from clinical.models import Encounter
from core.context import organization_context
from core.models import OrgOwnedModel
from organizations.models import Branch
from patients.models import Patient

pytestmark = pytest.mark.django_db


def _encounter_in(organization, *, complaint: str) -> Encounter:
    """A complete encounter in its own organization, with its practitioner."""
    with organization_context(organization):
        user = User.objects.create_user(
            phone=f'0190000{organization.pk:04d}', full_name='Dr Someone'
        )
        Membership.objects.create(
            user=user, organization=organization, role=Role.PRACTITIONER
        )
        branch = Branch.objects.create(
            organization=organization, name='Chamber', code=f'C{organization.pk}'
        )
        patient = Patient.objects.create(
            organization=organization,
            code=f'P-{organization.pk:04d}',
            full_name='Some Patient',
        )
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=user,
            branch=branch,
            occurred_at=timezone.now(),
            chief_complaint=complaint,
        )
        encounter.actor = user
        return encounter


@pytest.fixture
def mine(organization) -> Encounter:
    return _encounter_in(organization, complaint='Mine')


@pytest.fixture
def theirs(other_organization) -> Encounter:
    return _encounter_in(other_organization, complaint='Theirs')


def test_the_raw_history_manager_is_not_organization_scoped(mine, theirs):
    """Documents the trap this module guards, so a regression here is loud."""
    with organization_context(mine.organization):
        # Live rows are filtered by the org-scoped default manager...
        assert Encounter.objects.count() == 1
        # ...but the history manager knows nothing about organizations.
        assert Encounter.history.count() == 2


def test_revision_queries_are_filtered_by_organization(mine, theirs):
    revisions = services.encounter_revisions(mine.organization, mine)
    assert [row.chief_complaint for row in revisions] == ['Mine']

    # Even handed another tenant's encounter, the org filter wins and the caller
    # learns nothing about it.
    assert not services.encounter_revisions(mine.organization, theirs).exists()
    assert services.revision_timeline(mine.organization, theirs) == []


def test_history_view_cannot_reach_another_organizations_encounter(
    client, mine, theirs
):
    client.force_login(mine.actor)
    response = client.get(reverse('clinical:encounter_history', args=[theirs.pk]))
    assert response.status_code == 404
    assert b'Theirs' not in response.content


def test_every_historical_model_keeps_an_organization_column():
    """Without this column there is no way to filter history by tenant at all."""
    missing = []
    for model in apps.get_models():
        records = getattr(model, 'history', None)
        if records is None or not issubclass(model, OrgOwnedModel):
            continue
        field_names = {field.name for field in records.model._meta.fields}
        if 'organization' not in field_names:
            missing.append(records.model._meta.label)
    assert not missing, (
        f'Historical models with no organization column: {sorted(missing)}. '
        f'History cannot be tenant-filtered without it.'
    )
