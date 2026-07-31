"""Encounter and prescription operations."""

from django.db import transaction
from django.utils import timezone

from clinical.models import Encounter, EncounterStatus, Prescription

__all__ = ['finalize_encounter', 'prescription_for', 'save_encounter']


@transaction.atomic
def save_encounter(organization, *, actor, form, prescription_form, item_formset):
    """Persist an encounter with its prescription and items in one transaction."""
    encounter = form.save(commit=False)
    encounter.organization = organization
    encounter.created_by = encounter.created_by or actor
    encounter.save()

    prescription = prescription_form.save(commit=False)
    prescription.encounter = encounter
    prescription.organization = organization
    prescription.created_by = prescription.created_by or actor
    prescription.save()

    items = item_formset.save(commit=False)
    for index, item in enumerate(items):
        item.prescription = prescription
        item.organization = organization
        item.created_by = item.created_by or actor
        item.sort_order = item.sort_order or index
        item.save()
    for deleted in item_formset.deleted_objects:
        deleted.delete()
    return encounter


def prescription_for(encounter: Encounter) -> Prescription:
    """Every encounter gets a prescription row, even an empty one, so the print
    view and the edit form never have to special-case its absence."""
    prescription = getattr(encounter, 'prescription', None)
    if prescription is None:
        prescription = Prescription.objects.create(
            encounter=encounter, organization_id=encounter.organization_id
        )
    return prescription


@transaction.atomic
def finalize_encounter(encounter: Encounter) -> Encounter:
    """Lock the record. Corrections after this need the history layer (SPEC §6.4)."""
    encounter.status = EncounterStatus.FINALIZED
    encounter.finalized_at = timezone.now()
    encounter.save(update_fields=['status', 'finalized_at', 'updated_at'])
    prescription = prescription_for(encounter)
    if prescription.issued_at is None:
        prescription.issued_at = encounter.finalized_at
        prescription.save(update_fields=['issued_at', 'updated_at'])
    return encounter
