"""Encounter list, create/edit, detail, and the prescription print view.

The whole app is PRACTITIONER/OWNER only: ``clinical_access_required`` runs
before every view here, so a STAFF user hitting any of these URLs directly gets
a 403 rather than a hidden template block (SPEC §6.1).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.permissions import clinical_access_required, require_membership
from clinical import services
from clinical.forms import EncounterForm, PrescriptionForm, PrescriptionItemFormSet
from clinical.models import Encounter, PrintSize
from organizations.services import default_branch
from patients.models import Patient

__all__ = [
    'encounter_create',
    'encounter_detail',
    'encounter_finalize',
    'encounter_list',
    'encounter_update',
    'prescription_item_row',
    'prescription_print',
]

PAGE_SIZE = 25


@login_required
@clinical_access_required
def encounter_list(request):
    encounters = Encounter.objects.select_related('patient', 'practitioner', 'branch')
    query = request.GET.get('q', '').strip()
    if query:
        encounters = encounters.filter(patient__full_name__icontains=query)
    page = Paginator(encounters, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(
        request, 'clinical/encounter_list.html', {'encounters': page, 'query': query}
    )


@login_required
@clinical_access_required
def encounter_detail(request, pk: int):
    encounter = get_object_or_404(
        Encounter.objects.select_related('patient', 'practitioner', 'branch'), pk=pk
    )
    prescription = getattr(encounter, 'prescription', None)
    return render(
        request,
        'clinical/encounter_detail.html',
        {
            'encounter': encounter,
            'prescription': prescription,
            'items': prescription.items.all() if prescription else [],
        },
    )


def _encounter_form_context(request, encounter=None):
    """Bind the three forms that make up the one-page consultation form."""
    organization = request.organization
    prescription = services.prescription_for(encounter) if encounter else None
    data = request.POST or None
    form = EncounterForm(data, instance=encounter, organization=organization)
    prescription_form = PrescriptionForm(data, instance=prescription)
    item_formset = PrescriptionItemFormSet(data, instance=prescription)
    return form, prescription_form, item_formset


@login_required
@clinical_access_required
def encounter_create(request):
    membership = require_membership(request)
    form, prescription_form, item_formset = _encounter_form_context(request)
    if request.method != 'POST':
        form.initial.setdefault('occurred_at', timezone.localtime())
        form.initial.setdefault('practitioner', membership.user_id)
        # Most clinics have one branch; preselecting it saves a click per visit.
        branch = default_branch(request.organization)
        if branch is not None:
            form.initial.setdefault('branch', branch.pk)
        requested_patient = request.GET.get('patient')
        if requested_patient:
            patient = Patient.objects.filter(pk=requested_patient).first()
            if patient:
                form.initial['patient'] = patient.pk
    elif form.is_valid() and prescription_form.is_valid() and item_formset.is_valid():
        encounter = services.save_encounter(
            request.organization,
            actor=membership.user,
            form=form,
            prescription_form=prescription_form,
            item_formset=item_formset,
        )
        messages.success(request, 'Encounter saved.')
        return redirect('clinical:encounter_detail', pk=encounter.pk)
    return render(
        request,
        'clinical/encounter_form.html',
        {
            'form': form,
            'prescription_form': prescription_form,
            'item_formset': item_formset,
            'is_create': True,
        },
    )


@login_required
@clinical_access_required
def encounter_update(request, pk: int):
    membership = require_membership(request)
    encounter = get_object_or_404(Encounter, pk=pk)
    if not encounter.is_editable:
        messages.error(request, 'Finalized encounters cannot be edited.')
        return redirect('clinical:encounter_detail', pk=encounter.pk)

    form, prescription_form, item_formset = _encounter_form_context(request, encounter)
    if (
        request.method == 'POST'
        and form.is_valid()
        and prescription_form.is_valid()
        and item_formset.is_valid()
    ):
        services.save_encounter(
            request.organization,
            actor=membership.user,
            form=form,
            prescription_form=prescription_form,
            item_formset=item_formset,
        )
        messages.success(request, 'Encounter updated.')
        return redirect('clinical:encounter_detail', pk=encounter.pk)
    return render(
        request,
        'clinical/encounter_form.html',
        {
            'form': form,
            'prescription_form': prescription_form,
            'item_formset': item_formset,
            'encounter': encounter,
            'is_create': False,
        },
    )


@login_required
@clinical_access_required
def encounter_finalize(request, pk: int):
    encounter = get_object_or_404(Encounter, pk=pk)
    if request.method == 'POST' and encounter.is_editable:
        services.finalize_encounter(encounter)
        messages.success(request, 'Encounter finalized.')
    return redirect('clinical:encounter_detail', pk=encounter.pk)


@login_required
@clinical_access_required
def prescription_item_row(request):
    """Return one blank formset row for the HTMX 'add item' button.

    The formset's management form counts rows, so the caller sends its current
    TOTAL_FORMS as ``index``; we render the empty form with ``__prefix__``
    replaced by that number, and the button increments the counter afterwards.
    """
    raw = request.GET.get('items-TOTAL_FORMS', '0')
    index = int(raw) if raw.isdigit() else 0
    html = render_to_string(
        'clinical/_item_row.html',
        {'form': PrescriptionItemFormSet().empty_form},
        request=request,
    )
    # empty_form names its inputs items-__prefix__-…; the row is only usable
    # once that placeholder becomes the row's real index.
    return HttpResponse(html.replace('__prefix__', str(index)))


@login_required
@clinical_access_required
def prescription_print(request, pk: int):
    """Chrome-free print page. Size comes from the query string, A5 by default."""
    encounter = get_object_or_404(
        Encounter.objects.select_related('patient', 'practitioner', 'branch'), pk=pk
    )
    prescription = services.prescription_for(encounter)
    size = request.GET.get('size', prescription.print_size).upper()
    if size not in PrintSize.values:
        size = PrintSize.A5
    return render(
        request,
        'print/prescription.html',
        {
            'encounter': encounter,
            'prescription': prescription,
            'items': prescription.items.all(),
            'page_size': size,
            # Interpolated into CSS, so it comes from the validated accessor.
            'letterhead_color': request.organization.primary_color,
            'letterhead': request.organization.letterhead,
            'now': timezone.localtime(),
        },
    )
