"""Patient operations. Every function takes ``organization`` explicitly."""

from django.db import transaction
from django.db.models import Q

from core.services import next_document_number
from patients.models import Patient

__all__ = [
    'case_record_for',
    'case_record_revisions',
    'create_patient',
    'generate_patient_code',
    'possible_duplicates',
    'save_case_record',
    'search_patients',
]

CODE_PREFIX = 'P'

#: ``DocumentSequence.kind`` for the patient code run. No period: a patient
#: keeps one code for life, so the run never restarts.
PATIENT_SEQUENCE = 'PATIENT'


def _code_number(code: str) -> int:
    """The numeric part of ``P-0007``, or 0 for anything else."""
    prefix = f'{CODE_PREFIX}-'
    digits = code[len(prefix) :] if code.startswith(prefix) else ''
    return int(digits) if digits.isdigit() else 0


def _highest_code_on_file(organization) -> int:
    """The largest code already issued, soft-deleted patients included.

    Reads through ``all_objects``: a code must stay unique against a removed row
    too, or restoring one collides.
    """
    codes = Patient.all_objects.filter(organization=organization).values_list(
        'code', flat=True
    )
    return max((_code_number(code) for code in codes), default=0)


def generate_patient_code(organization) -> str:
    """Next org-scoped human-readable code, e.g. ``P-0007``.

    Allocated from a locked counter row, like every other document number
    (``core.services.next_document_number``). Reading the maximum and adding one
    is what it replaces: two receptionists registering at the same moment both
    read the same maximum, and one of them met the unique constraint as a 500.

    Call inside the transaction that writes the patient — ``create_patient``
    does — so the lock is held until the row exists.
    """
    return next_document_number(
        organization,
        kind=PATIENT_SEQUENCE,
        prefix=CODE_PREFIX,
        # Codes predate the counter, and the demo loader writes them directly,
        # so the floor comes off the rows rather than being assumed to be zero.
        start_after=_highest_code_on_file(organization),
    )


def search_patients(organization, query: str):
    """Name, phone, or code search — what reception actually types (SPEC §6.2).

    ``alt_phone`` is searched alongside ``phone`` because a second number the
    search cannot find is worse than no second number at all: reception dials
    what the patient gave them, finds nobody, and registers them again. That
    obligation is the price of the column existing — see
    docs/adr/0020-the-case-record.md §9.
    """
    queryset = Patient.objects.for_organization(organization)
    query = (query or '').strip()
    if query:
        queryset = queryset.filter(
            Q(full_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(alt_phone__icontains=query)
            | Q(code__icontains=query)
        )
    return queryset.select_related('registered_branch')


def possible_duplicates(
    organization, *, full_name: str, phone: str, alt_phone: str = '', exclude_pk=None
):
    """Dedupe guard for the create form: same number, or same name (SPEC §6.2).

    Both numbers on the form are matched against both numbers on file, all four
    ways. A patient whose spouse's number was recorded as their alternative and
    who now gives that number as their own is the same person, and the guard
    that cannot see it is the guard that lets the duplicate through.
    """
    numbers = [number for number in (phone, alt_phone) if number]
    if not (full_name or numbers):
        return Patient.objects.none()
    matches = Q()
    if numbers:
        matches |= Q(phone__in=numbers) | Q(alt_phone__in=numbers)
    if full_name:
        matches |= Q(full_name__iexact=full_name.strip())
    queryset = Patient.objects.for_organization(organization).filter(matches)
    if exclude_pk:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset


@transaction.atomic
def create_patient(organization, *, actor, form) -> Patient:
    """Save a validated PatientForm, assigning the next code."""
    patient = form.save(commit=False)
    patient.organization = organization
    patient.created_by = actor
    patient.code = patient.code or generate_patient_code(organization)
    patient.save()
    return patient


# --- The case record -------------------------------------------------------


def case_record_for(patient):
    """This patient's case record, or ``None``.

    A helper rather than ``getattr(patient, 'case_record', None)`` at each call
    site, because the reverse accessor raises rather than returning None and
    every caller would have to remember which.
    """
    from patients.models import CaseRecord

    return CaseRecord.objects.filter(patient=patient).first()


@transaction.atomic
def save_case_record(organization, *, patient, actor, form, formsets) -> 'CaseRecord':  # noqa: F821
    """One POST, one transaction, one history row per changed model.

    The record is created on this first save with whatever is filled in, so
    "save early, save often" is a real strategy rather than advice. §9's eight
    rows are seeded here rather than by a signal: they are part of what it means
    for a case record to exist, and a signal would put that fact somewhere a
    reader of this function cannot see it.
    """
    from patients.models import MODALITY_FACTORS, CaseModality

    record = form.save(commit=False)
    record.patient = patient
    record.organization = organization
    record.created_by = record.created_by or actor
    is_new = record.pk is None
    record.save()

    if is_new:
        CaseModality.objects.bulk_create(
            [
                CaseModality(
                    organization=organization,
                    created_by=actor,
                    case_record=record,
                    factor=factor,
                    sort_order=index,
                )
                for index, factor in enumerate(MODALITY_FACTORS)
            ]
        )

    for formset in formsets:
        formset.instance = record
        for row in formset.save(commit=False):
            row.organization = organization
            row.created_by = row.created_by or actor
            row.save()
        for row in formset.deleted_objects:
            row.delete()
    return record


def case_record_revisions(organization, record):
    """History rows for one case record, filtered by tenant.

    ``simple_history`` generates its own manager, which is **not** org-scoped —
    so every query against ``.history`` filters ``organization`` explicitly or
    it reads every clinic's rows. See docs/adr/0006-encounter-amendments.md.
    """
    return record.history.filter(organization=organization).order_by('-history_date')
