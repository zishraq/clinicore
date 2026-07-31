"""Patient operations. Every function takes ``organization`` explicitly."""

from django.db import transaction
from django.db.models import Q

from patients.models import Patient

__all__ = [
    'create_patient',
    'generate_patient_code',
    'possible_duplicates',
    'search_patients',
]

CODE_PREFIX = 'P'


def generate_patient_code(organization) -> str:
    """Next org-scoped human-readable code, e.g. ``P-0007``.

    Reads through ``all_objects`` because codes must stay unique against soft
    deleted rows too. The unique constraint is the real guard; a race just means
    the caller retries.
    """
    last = (
        Patient.all_objects.filter(organization=organization)
        .order_by('-id')
        .values_list('code', flat=True)
        .first()
    )
    next_number = 1
    if last and last.startswith(f'{CODE_PREFIX}-') and last[2:].isdigit():
        next_number = int(last[2:]) + 1
    return f'{CODE_PREFIX}-{next_number:04d}'


def search_patients(organization, query: str):
    """Name, phone, or code search — what reception actually types (SPEC §6.2)."""
    queryset = Patient.objects.for_organization(organization)
    query = (query or '').strip()
    if query:
        queryset = queryset.filter(
            Q(full_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(code__icontains=query)
        )
    return queryset.select_related('registered_branch')


def possible_duplicates(organization, *, full_name: str, phone: str, exclude_pk=None):
    """Dedupe guard for the create form: same phone, or same name (SPEC §6.2)."""
    if not (full_name or phone):
        return Patient.objects.none()
    matches = Q()
    if phone:
        matches |= Q(phone=phone)
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
