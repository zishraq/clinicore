"""Patient CRUD, HTMX live search, and the clinical profile behind a role check.

Lookups go through ``Patient.objects``, which is organization-scoped, so a direct
URL hit on another clinic's patient is a 404 rather than a permission message.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import clinical_access_required, require_membership
from patients import services
from patients.forms import ClinicalProfileForm, PatientForm
from patients.models import Patient

__all__ = [
    'clinical_profile_edit',
    'patient_create',
    'patient_delete',
    'patient_detail',
    'patient_list',
    'patient_search',
    'patient_update',
]

PAGE_SIZE = 20


def _page(request, queryset):
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))


@login_required
def patient_list(request):
    require_membership(request)
    query = request.GET.get('q', '')
    patients = services.search_patients(request.organization, query)
    return render(
        request,
        'patients/list.html',
        {'patients': _page(request, patients), 'query': query},
    )


@login_required
def patient_search(request):
    """HTMX target for the live search box; returns the results table only.

    The search box pushes this URL into the address bar, so a reload or a shared
    link lands here directly — in that case serve the whole page instead of a
    naked fragment.
    """
    require_membership(request)
    query = request.GET.get('q', '')
    patients = services.search_patients(request.organization, query)
    is_htmx = request.headers.get('HX-Request') == 'true'
    return render(
        request,
        'patients/_results.html' if is_htmx else 'patients/list.html',
        {'patients': _page(request, patients), 'query': query},
    )


@login_required
def patient_detail(request, pk: int):
    membership = require_membership(request)
    patient = get_object_or_404(Patient, pk=pk)
    # MVP: replace with permission layer
    show_clinical = membership.can_view_clinical
    profile = getattr(patient, 'clinical_profile', None) if show_clinical else None
    encounters = []
    invoices = []
    outstanding = None
    if show_clinical:
        # Deferred imports keep patients free of a hard dependency on the
        # feature apps that point at it.
        from billing.services import outstanding_balance, patient_invoices
        from clinical.models import Encounter

        encounters = list(
            Encounter.objects.filter(patient=patient).select_related('practitioner')[
                :20
            ]
        )
        # What a returning patient still owes, visible without opening a bill
        # (SPEC §6.2).
        outstanding = outstanding_balance(request.organization, patient)
        invoices = list(patient_invoices(request.organization, patient)[:10])
    return render(
        request,
        'patients/detail.html',
        {
            'patient': patient,
            'clinical_profile': profile,
            'show_clinical': show_clinical,
            'encounters': encounters,
            'invoices': invoices,
            'outstanding': outstanding,
        },
    )


@login_required
def patient_create(request):
    membership = require_membership(request)
    form = PatientForm(request.POST or None, organization=request.organization)
    duplicates = []
    if request.method == 'POST':
        duplicates = list(
            services.possible_duplicates(
                request.organization,
                full_name=request.POST.get('full_name', ''),
                phone=request.POST.get('phone', ''),
            )
        )
        # The dedupe guard warns once; a second submit with the flag set saves.
        confirmed = request.POST.get('duplicates_acknowledged') == '1'
        if form.is_valid() and (not duplicates or confirmed):
            patient = services.create_patient(
                request.organization, actor=membership.user, form=form
            )
            messages.success(request, f'{patient.full_name} added as {patient.code}.')
            return redirect('patients:detail', pk=patient.pk)
    return render(
        request,
        'patients/form.html',
        {'form': form, 'duplicates': duplicates, 'is_create': True},
    )


@login_required
def patient_update(request, pk: int):
    require_membership(request)
    patient = get_object_or_404(Patient, pk=pk)
    form = PatientForm(
        request.POST or None, instance=patient, organization=request.organization
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Patient details updated.')
        return redirect('patients:detail', pk=patient.pk)
    return render(
        request,
        'patients/form.html',
        {'form': form, 'patient': patient, 'is_create': False},
    )


@login_required
def patient_delete(request, pk: int):
    """Soft delete: clinical records must survive a mis-click (SPEC §4)."""
    membership = require_membership(request)
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST':
        patient.soft_delete(actor=membership.user)
        messages.success(request, f'{patient.full_name} removed from the list.')
        return redirect('patients:list')
    return render(request, 'patients/confirm_delete.html', {'patient': patient})


@login_required
@clinical_access_required
def clinical_profile_edit(request, pk: int):
    """PRACTITIONER/OWNER only — STAFF gets a 403 even by direct URL."""
    membership = require_membership(request)
    patient = get_object_or_404(Patient, pk=pk)
    profile = getattr(patient, 'clinical_profile', None)
    form = ClinicalProfileForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        profile = form.save(commit=False)
        profile.patient = patient
        profile.organization = request.organization
        profile.created_by = profile.created_by or membership.user
        profile.save()
        messages.success(request, 'Clinical profile saved.')
        return redirect('patients:detail', pk=patient.pk)
    return render(
        request,
        'patients/clinical_profile_form.html',
        {'form': form, 'patient': patient},
    )
