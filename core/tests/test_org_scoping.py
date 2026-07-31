"""Tenant isolation: a query under organization A must never see organization B.

Parametrized over every concrete ``OrgOwnedModel`` subclass. Adding a model
without a builder entry fails ``test_every_org_owned_model_has_a_builder``, so
the coverage cannot silently rot — that mechanism is the point of this file
(docs/adr/0005-org-scoped-default-manager.md).
"""

import pytest
from django.apps import apps
from django.utils import timezone

from accounts.models import Membership, Role, User
from clinical.models import Encounter, Prescription, PrescriptionItem
from core.context import organization_context
from core.exceptions import ActiveOrganizationRequired
from core.models import OrgOwnedModel
from organizations.models import Branch
from patients.models import Patient, PatientClinicalProfile

pytestmark = pytest.mark.django_db


def _build_branch(organization):
    return Branch.objects.create(
        organization=organization, name='Chamber', code=f'C{organization.pk}'
    )


def _build_patient(organization):
    return Patient.objects.create(
        organization=organization, code=f'P-{organization.pk:04d}', full_name='Test One'
    )


def _build_clinical_profile(organization):
    return PatientClinicalProfile.objects.create(
        organization=organization,
        patient=_build_patient(organization),
        medical_history='none',
    )


def _build_encounter(organization):
    return Encounter.objects.create(
        organization=organization,
        patient=_build_patient(organization),
        practitioner=_practitioner_for(organization),
        branch=_build_branch(organization),
        occurred_at=timezone.now(),
    )


def _build_prescription(organization):
    return Prescription.objects.create(
        organization=organization, encounter=_build_encounter(organization)
    )


def _build_prescription_item(organization):
    return PrescriptionItem.objects.create(
        organization=organization,
        prescription=_build_prescription(organization),
        free_text_name='Test item',
    )


def _practitioner_for(organization):
    user = User.objects.create_user(
        phone=f'0199{organization.pk:07d}', full_name='Dr Test'
    )
    Membership.objects.create(
        user=user, organization=organization, role=Role.PRACTITIONER
    )
    return user


#: One builder per concrete org-owned model, keyed by ``app_label.ModelName``.
BUILDERS = {
    'clinical.Encounter': _build_encounter,
    'clinical.Prescription': _build_prescription,
    'clinical.PrescriptionItem': _build_prescription_item,
    'organizations.Branch': _build_branch,
    'patients.Patient': _build_patient,
    'patients.PatientClinicalProfile': _build_clinical_profile,
}


def _concrete_org_owned_models():
    return [
        model
        for model in apps.get_models()
        if issubclass(model, OrgOwnedModel) and not model._meta.abstract
    ]


def test_every_org_owned_model_has_a_builder():
    missing = {
        model._meta.label
        for model in _concrete_org_owned_models()
        if model._meta.label not in BUILDERS
    }
    assert not missing, (
        f'Org-owned models with no isolation-test builder: {sorted(missing)}. '
        f'Add one to BUILDERS so the tenancy guarantee stays tested.'
    )


@pytest.mark.parametrize('label', sorted(BUILDERS))
def test_queries_cannot_cross_organizations(label, organization, other_organization):
    build = BUILDERS[label]
    model = apps.get_model(label)

    with organization_context(organization):
        mine = build(organization)
    with organization_context(other_organization):
        theirs = build(other_organization)

    with organization_context(organization):
        assert list(model.objects.all()) == [mine]
        assert model.objects.count() == 1
        assert model.objects.filter(pk=theirs.pk).count() == 0
        with pytest.raises(model.DoesNotExist):
            model.objects.get(pk=theirs.pk)

    # And the unfiltered manager still sees both, which is what makes the
    # filtered result above meaningful rather than an empty database.
    assert model.all_objects.count() == 2


@pytest.mark.parametrize('label', sorted(BUILDERS))
def test_querying_without_an_organization_raises(label, organization):
    model = apps.get_model(label)
    with organization_context(organization):
        BUILDERS[label](organization)

    # Loud beats silently empty: see the ADR.
    with pytest.raises(ActiveOrganizationRequired):
        list(model.objects.all())
