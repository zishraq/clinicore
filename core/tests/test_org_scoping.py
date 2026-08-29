"""Tenant isolation: a query under organization A must never see organization B.

Parametrized over every concrete ``OrgOwnedModel`` subclass. Adding a model
without a builder entry fails ``test_every_org_owned_model_has_a_builder``, so
the coverage cannot silently rot — that mechanism is the point of this file
(docs/adr/0005-org-scoped-default-manager.md).
"""

from decimal import Decimal

import pytest
from django.apps import apps
from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.models import Membership, Role, User
from billing.models import Invoice, InvoiceItem, LineType, Payment
from billing.services import next_invoice_number
from catalog.models import AdviceTemplate, Product
from clinical.models import (
    Encounter,
    EncounterPhoto,
    Prescription,
    PrescriptionItem,
)
from core.context import organization_context
from core.exceptions import ActiveOrganizationRequired
from core.models import DocumentSequence, OrgOwnedModel
from inventory.models import (
    GoodsReceipt,
    GoodsReceiptItem,
    MovementType,
    StockBatch,
    StockMovement,
)
from organizations.models import Branch
from patients.models import (
    CaseAnalysisEntry,
    CaseComplaint,
    CaseInvestigation,
    CaseModality,
    CaseRecord,
    Patient,
)
from scheduling.models import Appointment

pytestmark = pytest.mark.django_db

#: Smallest thing that is unambiguously an image file, for builders that only
#: need a FileField to be non-empty.
_ONE_PIXEL_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!'
    b'\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00'
    b'\x00\x02\x02D\x01\x00;'
)


def _build_branch(organization):
    return Branch.objects.create(
        organization=organization, name='Chamber', code=f'C{organization.pk}'
    )


def _build_patient(organization):
    return Patient.objects.create(
        organization=organization, code=f'P-{organization.pk:04d}', full_name='Test One'
    )


def _build_case_record(organization):
    return CaseRecord.objects.create(
        organization=organization,
        patient=_build_patient(organization),
        assessment_provisional='none',
    )


def _build_case_complaint(organization):
    return CaseComplaint.objects.create(
        organization=organization,
        case_record=_build_case_record(organization),
        complaint='Headache',
    )


def _build_case_modality(organization):
    return CaseModality.objects.create(
        organization=organization,
        case_record=_build_case_record(organization),
        factor='Time',
    )


def _build_case_investigation(organization):
    return CaseInvestigation.objects.create(
        organization=organization,
        case_record=_build_case_record(organization),
        name='CBC',
    )


def _build_case_analysis_entry(organization):
    return CaseAnalysisEntry.objects.create(
        organization=organization,
        case_record=_build_case_record(organization),
        finding='Head, pain',
    )


def _build_product(organization):
    return Product.objects.create(organization=organization, name='Paracetamol 500mg')


def _build_advice_template(organization):
    return AdviceTemplate.objects.create(
        organization=organization, text='Drink more water.'
    )


def _build_encounter(organization):
    return Encounter.objects.create(
        organization=organization,
        patient=_build_patient(organization),
        practitioner=_practitioner_for(organization),
        branch=_build_branch(organization),
        occurred_at=timezone.now(),
    )


def _build_appointment(organization):
    return Appointment.objects.create(
        organization=organization,
        patient=_build_patient(organization),
        branch=_build_branch(organization),
        scheduled_date=timezone.localdate(),
    )


def _build_encounter_photo(organization):
    photo = EncounterPhoto(
        organization=organization, encounter=_build_encounter(organization)
    )
    # A one-pixel GIF, so this builder needs neither Pillow nor a fixture file:
    # what is being tested is the row's scoping, not the image pipeline
    # (clinical/tests/test_photos.py covers that).
    photo.image.save('probe.jpg', ContentFile(_ONE_PIXEL_GIF), save=False)
    photo.save()
    return photo


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


def _build_document_sequence(organization):
    return DocumentSequence.objects.create(
        organization=organization, kind='INVOICE', period='2026'
    )


def _build_invoice(organization):
    return Invoice.objects.create(
        organization=organization,
        patient=_build_patient(organization),
        currency=organization.currency,
        number=next_invoice_number(organization),
    )


def _build_invoice_item(organization):
    return InvoiceItem.objects.create(
        organization=organization,
        invoice=_build_invoice(organization),
        line_type=LineType.OTHER,
        name_snapshot='Consultation fee',
        quantity=1,
        unit_price=Decimal('500.00'),
    )


def _build_payment(organization):
    return Payment.objects.create(
        organization=organization,
        invoice=_build_invoice(organization),
        amount=Decimal('100.00'),
        received_by=_practitioner_for(organization),
    )


def _build_stock_batch(organization):
    return StockBatch.objects.create(
        organization=organization,
        product=_build_product(organization),
        branch=_build_branch(organization),
        lot_number=f'LOT-{organization.pk}',
    )


def _build_stock_movement(organization):
    return StockMovement.objects.create(
        organization=organization,
        batch=_build_stock_batch(organization),
        movement_type=MovementType.PURCHASE,
        quantity=Decimal('10.00'),
    )


def _build_goods_receipt(organization):
    return GoodsReceipt.objects.create(
        organization=organization,
        branch=_build_branch(organization),
        number=f'GRN-2026-{organization.pk:04d}',
    )


def _build_goods_receipt_item(organization):
    # One branch, shared: the receipt and the batch it feeds are by definition
    # at the same place, and Branch.code is unique per organization.
    branch = _build_branch(organization)
    product = _build_product(organization)
    return GoodsReceiptItem.objects.create(
        organization=organization,
        receipt=GoodsReceipt.objects.create(
            organization=organization,
            branch=branch,
            number=f'GRN-2026-{organization.pk:04d}',
        ),
        product=product,
        batch=StockBatch.objects.create(
            organization=organization,
            product=product,
            branch=branch,
            lot_number=f'LOT-{organization.pk}',
        ),
        quantity=Decimal('10.00'),
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
    'billing.Invoice': _build_invoice,
    'billing.InvoiceItem': _build_invoice_item,
    'billing.Payment': _build_payment,
    'catalog.AdviceTemplate': _build_advice_template,
    'catalog.Product': _build_product,
    'clinical.Encounter': _build_encounter,
    'clinical.EncounterPhoto': _build_encounter_photo,
    'clinical.Prescription': _build_prescription,
    'clinical.PrescriptionItem': _build_prescription_item,
    'core.DocumentSequence': _build_document_sequence,
    'inventory.GoodsReceipt': _build_goods_receipt,
    'inventory.GoodsReceiptItem': _build_goods_receipt_item,
    'inventory.StockBatch': _build_stock_batch,
    'inventory.StockMovement': _build_stock_movement,
    'organizations.Branch': _build_branch,
    'patients.Patient': _build_patient,
    'patients.CaseAnalysisEntry': _build_case_analysis_entry,
    'patients.CaseComplaint': _build_case_complaint,
    'patients.CaseInvestigation': _build_case_investigation,
    'patients.CaseModality': _build_case_modality,
    'patients.CaseRecord': _build_case_record,
    'scheduling.Appointment': _build_appointment,
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
