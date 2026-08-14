"""Encounter and prescription operations.

Corrections to a locked encounter are amendments: they write a history row with
an actor and a reason, never a silent overwrite (SPEC §6.4). Rationale and the
tenancy caveat on historical tables: docs/adr/0006-encounter-amendments.md.
"""

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from clinical.models import Encounter, EncounterPhoto, EncounterStatus, Prescription

__all__ = [
    'AmendmentReasonRequired',
    'attach_photos',
    'delete_photo',
    'encounter_revisions',
    'finalize_encounter',
    'prescription_for',
    'revision_timeline',
    'save_encounter',
]


class AmendmentReasonRequired(ValueError):
    """Raised when a locked encounter is saved without a reason.

    The form asks for the reason first; this is the backstop that keeps a
    silent overwrite impossible even if a caller skips the form.
    """


def _stamp(instance, *, actor, reason: str) -> None:
    """Attach the actor and reason simple-history reads when it writes a row."""
    instance._history_user = actor
    if reason:
        instance._change_reason = reason


@transaction.atomic
def save_encounter(
    organization, *, actor, form, prescription_form, item_formset, reason: str = ''
):
    """Persist an encounter with its prescription and items in one transaction.

    ``reason`` is required when the encounter is already locked; it lands on
    every history row the save produces, so the prescription and item revisions
    carry the same justification as the encounter itself.
    """
    encounter = form.save(commit=False)
    # form.instance carries the status loaded from the database: `status` is not
    # a form field, so this is the pre-save state, which is what decides whether
    # this save is an amendment.
    is_amendment = encounter.pk is not None and encounter.is_locked
    reason = (reason or '').strip()
    if is_amendment and not reason:
        raise AmendmentReasonRequired(
            'Amending a finalized encounter requires a reason.'
        )

    encounter.organization = organization
    encounter.created_by = encounter.created_by or actor
    if is_amendment:
        encounter.status = EncounterStatus.AMENDED
        encounter.amended_at = timezone.now()
    _stamp(encounter, actor=actor, reason=reason)
    encounter.save()

    prescription = prescription_form.save(commit=False)
    prescription.encounter = encounter
    prescription.organization = organization
    prescription.created_by = prescription.created_by or actor
    _stamp(prescription, actor=actor, reason=reason)
    prescription.save()

    items = item_formset.save(commit=False)
    for index, item in enumerate(items):
        item.prescription = prescription
        item.organization = organization
        item.created_by = item.created_by or actor
        item.sort_order = item.sort_order or index
        _stamp(item, actor=actor, reason=reason)
        item.save()
    for deleted in item_formset.deleted_objects:
        # Stamp before delete: simple-history writes the removal row from the
        # instance it is handed.
        _stamp(deleted, actor=actor, reason=reason)
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
def finalize_encounter(encounter: Encounter, *, actor=None) -> Encounter:
    """Lock the record. Later corrections become amendments, not overwrites."""
    # The reason is read back on the history page, so it is worded from the
    # organization's terminology map rather than the stored status (SPEC §5).
    reason = encounter.organization.terms['status_finalized']
    encounter.status = EncounterStatus.FINALIZED
    encounter.finalized_at = timezone.now()
    _stamp(encounter, actor=actor, reason=reason)
    encounter.save(update_fields=['status', 'finalized_at', 'updated_at'])
    prescription = prescription_for(encounter)
    if prescription.issued_at is None:
        prescription.issued_at = encounter.finalized_at
        _stamp(prescription, actor=actor, reason=reason)
        prescription.save(update_fields=['issued_at', 'updated_at'])
    return encounter


def encounter_revisions(organization, encounter: Encounter):
    """Revisions of one encounter, newest first.

    Historical models get their own manager from simple-history and do **not**
    inherit ``OrgScopedManager``, so ``Encounter.history`` is unfiltered across
    tenants. Every history query therefore filters on ``organization_id``
    explicitly — see docs/adr/0006-encounter-amendments.md.
    """
    return (
        Encounter.history.filter(id=encounter.pk, organization_id=organization.pk)
        .select_related('history_user')
        .order_by('-history_date')
    )


def _change_value(field: str, value, terms: dict):
    """Stored values never reach the UI; a status renders through the map."""
    if field != 'status':
        return value
    return terms.get(f'status_{str(value).lower()}') or value


def revision_timeline(organization, encounter: Encounter) -> list[dict]:
    """Revisions paired with what changed since the revision before them."""
    revisions = list(encounter_revisions(organization, encounter))
    terms = organization.terms
    labels = {
        field.name: field.verbose_name
        for field in Encounter._meta.get_fields()
        if hasattr(field, 'verbose_name')
    }
    # The lock fields are named after the stored statuses, so their labels come
    # from the terminology map rather than from the model (SPEC §5).
    labels['finalized_at'] = terms['status_finalized']
    labels['amended_at'] = f'Last {terms["amend"].lower()}'

    timeline = []
    for index, revision in enumerate(revisions):
        previous = revisions[index + 1] if index + 1 < len(revisions) else None
        changes = []
        if previous is not None:
            for change in revision.diff_against(previous).changes:
                old = _change_value(change.field, change.old, terms)
                new = _change_value(change.field, change.new, terms)
                if old == new:
                    # Two stored statuses the map labels identically — under
                    # the defaults, FINALIZED → AMENDED. Nothing happened that
                    # this row could usefully show.
                    continue
                changes.append(
                    {
                        'label': labels.get(change.field, change.field),
                        'old': old,
                        'new': new,
                    }
                )
        timeline.append(
            {
                'revision': revision,
                'changes': changes,
                # The row's own type, not its position: records that predate the
                # history tables have a first revision that is an edit, and
                # calling that "Created" would misreport what happened.
                'is_creation': revision.history_type == '+',
                'has_previous': previous is not None,
            }
        )
    return timeline


def attach_photos(encounter: Encounter, images, *, actor, caption: str = ''):
    """Store already-normalized JPEG bytes against a visit.

    ``images`` comes from ``clinical.forms.MultipleImageField``, which has
    already decoded, downscaled and re-encoded every file — a rejection has to
    reach the practitioner as a form error while the consultation note is still
    on screen, so it cannot happen down here.

    ``caption`` applies to the whole batch. Three photographs of one lab report
    share one description, which is what an upload usually is; a file input set
    to ``multiple`` has nowhere to put a caption per file anyway.
    """
    photos = []
    for data in images:
        photo = EncounterPhoto(
            organization_id=encounter.organization_id,
            encounter=encounter,
            caption=caption,
            created_by=actor,
        )
        # The name is discarded by encounter_photo_path, which generates its
        # own; save=False so the row is written once, below, rather than twice.
        photo.image.save('upload.jpg', ContentFile(data), save=False)
        photo.save()
        photos.append(photo)
    return photos


def delete_photo(photo: EncounterPhoto) -> None:
    """Remove the row and the file behind it.

    File first: the reverse order risks deleting the only pointer to a file the
    storage then refuses to remove, leaving bytes on disk that nothing in the
    application can ever name again. A row briefly outliving its file is the
    recoverable direction — it renders as one broken thumbnail.

    Hard delete, not the SPEC §4 soft delete for clinical records: a
    soft-deleted row pointing at a file that has been erased claims to hold
    something it does not.
    """
    photo.image.delete(save=False)
    photo.delete()
