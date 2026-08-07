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
from clinical.models import Encounter, EncounterStatus, PrintSize
from organizations.services import default_branch
from patients.models import Patient
from scheduling import services as scheduling_services
from scheduling.models import Appointment, AppointmentStatus

__all__ = [
    'encounter_create',
    'encounter_detail',
    'encounter_finalize',
    'encounter_history',
    'encounter_list',
    'encounter_update',
    'prescription_item_row',
    'prescription_print',
]

PAGE_SIZE = 25


#: The status filter's options, in the order the dropdown shows them. Only
#: DRAFT is named: a visit is either still being written or it is just a visit,
#: and the second case is every other row on the page.
STATUS_FILTERS = {
    'draft': [EncounterStatus.DRAFT],
    'finished': [EncounterStatus.FINALIZED, EncounterStatus.AMENDED],
}


@login_required
@clinical_access_required
def encounter_list(request):
    """One list, filtered. The status column is a dropdown, not a column."""
    encounters = Encounter.objects.select_related('patient', 'practitioner', 'branch')
    query = request.GET.get('q', '').strip()
    if query:
        encounters = encounters.filter(patient__full_name__icontains=query)
    status = request.GET.get('status', '').strip().lower()
    if status in STATUS_FILTERS:
        encounters = encounters.filter(status__in=STATUS_FILTERS[status])
    page = Paginator(encounters, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(
        request,
        'clinical/encounter_list.html',
        {
            'encounters': page,
            'query': query,
            'status': status if status in STATUS_FILTERS else '',
        },
    )


@login_required
@clinical_access_required
def encounter_detail(request, pk: int):
    encounter = get_object_or_404(
        Encounter.objects.select_related('patient', 'practitioner', 'branch'), pk=pk
    )
    prescription = getattr(encounter, 'prescription', None)
    items = list(prescription.items.all()) if prescription else []
    # Deferred import: billing depends on clinical, and the encounter page only
    # needs to know whether a bill was already raised for this visit.
    from billing.services import invoice_for_encounter

    return render(
        request,
        'clinical/encounter_detail.html',
        {
            'encounter': encounter,
            'prescription': prescription,
            'medicines': [item for item in items if not item.is_advice],
            'advice_items': [item for item in items if item.is_advice],
            'invoice': invoice_for_encounter(request.organization, encounter),
        },
    )


def _encounter_form_context(request, encounter=None):
    """Bind the three forms that make up the one-page consultation form."""
    organization = request.organization
    prescription = services.prescription_for(encounter) if encounter else None
    data = request.POST or None
    form = EncounterForm(
        data,
        instance=encounter,
        organization=organization,
        requires_reason=bool(encounter and encounter.is_locked),
    )
    prescription_form = PrescriptionForm(data, instance=prescription)
    item_formset = PrescriptionItemFormSet(
        data, instance=prescription, organization=organization
    )
    return form, prescription_form, item_formset


#: The secondary submit button's name. Its absence means "complete this visit",
#: which is what the primary button and the Enter key both do (A4).
DRAFT_SUBMIT = 'save_draft'


def _save_and_report(request, encounter, *, actor) -> str:
    """Complete the visit unless a draft was asked for, and say which happened.

    The doctor writes the note at the end of the consultation, so Open/Completed
    is bookkeeping he should not have to carry: saving completes. Drafts remain
    for interruptions, and the state machine and amendment trail are unchanged —
    only which one you get by default (A4).
    """
    terms = request.organization.terms
    label = terms['encounter']
    if request.POST.get(DRAFT_SUBMIT):
        return f'{label} saved as {terms["status_draft"].lower()}.'
    services.finalize_encounter(encounter, actor=actor)
    # Just "saved". The doctor pressed one button and wrote one note; naming the
    # state it landed in describes bookkeeping he never asked to do.
    return f'{label} saved.'


def _selected_patient(form):
    """Whoever the patient field currently points at, for the picker's text box.

    Read off the bound field rather than the instance so it survives all three
    cases the same way: editing a saved visit, a ``?patient=`` prefill, and a
    redisplay after a validation error — the last of which would otherwise show
    an empty search box above a hidden pk that is still set.
    """
    raw = form['patient'].value()
    return Patient.objects.filter(pk=raw).first() if raw else None


def _requested_appointment(request):
    """The day-list row this visit is being written from, if there is one.

    Read from the body first: the form carries the row as a hidden field so the
    link survives the round trip, and a POST has no query string. Scoping is the
    ambient one on ``Appointment.objects``, so another tenant's pk finds nothing.
    """
    raw = request.POST.get('appointment') or request.GET.get('appointment') or ''
    if not raw.isdigit():
        return None
    return Appointment.objects.filter(pk=int(raw)).first()


def _prefill_from_appointment(form, appointment) -> None:
    """The row's own answers beat the form's generic defaults.

    Assigned rather than ``setdefault``: the receptionist already recorded who
    came, where, and to see whom, and re-answering that is the click this link
    exists to remove. Every one stays editable — the doctor covering a colleague
    changes the practitioner, and the visit is his.
    """
    form.initial['patient'] = appointment.patient_id
    form.initial['branch'] = appointment.branch_id
    if appointment.practitioner_id:
        form.initial['practitioner'] = appointment.practitioner_id


def _book_follow_up(request, encounter, *, actor) -> None:
    """Turn the visit's "next appointment" date into a row somebody will see.

    Failure must not cost the note, for the same reason marking the row seen
    must not: the visit is saved and complete either way, and a follow-up that
    could not be booked is worth a sentence rather than a rollback.
    """
    if not encounter.follow_up_date:
        return
    try:
        scheduling_services.schedule_follow_up(
            request.organization,
            actor=actor,
            encounter=encounter,
            on_date=encounter.follow_up_date,
        )
    except scheduling_services.AppointmentError as failure:
        messages.warning(request, str(failure))


def _mark_seen(request, appointment, encounter, *, actor) -> None:
    """Consume the day-list row the visit was written from.

    A refusal must not cost the doctor the note. The visit is complete and valid
    with no appointment at all (ADR 0010), so a row that stopped being arrived
    while the consultation was being written — cancelled at the desk, or already
    consumed in another tab — is reported and nothing is rolled back.
    """
    try:
        scheduling_services.transition(
            appointment, to=AppointmentStatus.SEEN, actor=actor, encounter=encounter
        )
    except scheduling_services.AppointmentError:
        terms = request.organization.terms
        messages.warning(
            request,
            f'The {terms["appointment"].lower()} was not marked '
            f'{terms["status_seen"].lower()} — it is no longer waiting. '
            f'The {terms["encounter"].lower()} itself is saved.',
        )


@login_required
@clinical_access_required
def encounter_create(request):
    membership = require_membership(request)
    appointment = _requested_appointment(request)
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
        if appointment is not None:
            _prefill_from_appointment(form, appointment)
    elif form.is_valid() and prescription_form.is_valid() and item_formset.is_valid():
        encounter = services.save_encounter(
            request.organization,
            actor=membership.user,
            form=form,
            prescription_form=prescription_form,
            item_formset=item_formset,
        )
        messages.success(
            request, _save_and_report(request, encounter, actor=membership.user)
        )
        if appointment is not None:
            _mark_seen(request, appointment, encounter, actor=membership.user)
        _book_follow_up(request, encounter, actor=membership.user)
        return redirect('clinical:encounter_detail', pk=encounter.pk)
    return render(
        request,
        'clinical/encounter_form.html',
        {
            'form': form,
            'prescription_form': prescription_form,
            'item_formset': item_formset,
            'selected_patient': _selected_patient(form),
            'appointment': appointment,
            'is_create': True,
        },
    )


@login_required
@clinical_access_required
def encounter_update(request, pk: int):
    """Edit a draft, or amend a locked encounter with a recorded reason."""
    membership = require_membership(request)
    encounter = get_object_or_404(Encounter, pk=pk)
    is_amendment = encounter.is_locked

    form, prescription_form, item_formset = _encounter_form_context(request, encounter)
    if (
        request.method == 'POST'
        and form.is_valid()
        and prescription_form.is_valid()
        and item_formset.is_valid()
    ):
        try:
            saved = services.save_encounter(
                request.organization,
                actor=membership.user,
                form=form,
                prescription_form=prescription_form,
                item_formset=item_formset,
                reason=form.cleaned_data.get('change_reason', ''),
            )
        except services.AmendmentReasonRequired as error:
            # The form asks for the reason first, so this is the backstop —
            # reached only if the record was locked between rendering the form
            # and posting it. Nothing was written; show it as a field error
            # rather than a 500.
            form.add_error('change_reason', str(error))
        else:
            # An amendment already carries its own status; only a draft being
            # finished has a completion decision left to make.
            messages.success(
                request,
                'Changes saved.'
                if is_amendment
                else _save_and_report(request, saved, actor=membership.user),
            )
            # Also on edit: a follow-up date added or moved on a saved visit is
            # the same intention as one set while writing it.
            _book_follow_up(request, saved, actor=membership.user)
            return redirect('clinical:encounter_detail', pk=encounter.pk)
    return render(
        request,
        'clinical/encounter_form.html',
        {
            'form': form,
            'prescription_form': prescription_form,
            'item_formset': item_formset,
            'selected_patient': _selected_patient(form),
            'encounter': encounter,
            'is_create': False,
            'is_amendment': is_amendment,
        },
    )


@login_required
@clinical_access_required
def encounter_history(request, pk: int):
    """Who changed what, when, and why (SPEC §6.4)."""
    encounter = get_object_or_404(Encounter.objects.select_related('patient'), pk=pk)
    return render(
        request,
        'clinical/encounter_history.html',
        {
            'encounter': encounter,
            # Explicitly organization-filtered: historical models are not scoped.
            'timeline': services.revision_timeline(request.organization, encounter),
        },
    )


@login_required
@clinical_access_required
def encounter_finalize(request, pk: int):
    membership = require_membership(request)
    encounter = get_object_or_404(Encounter, pk=pk)
    if request.method == 'POST' and not encounter.is_locked:
        services.finalize_encounter(encounter, actor=membership.user)
        terms = request.organization.terms
        messages.success(request, f'{terms["encounter"]} finished.')
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
    formset = PrescriptionItemFormSet(organization=request.organization)
    html = render_to_string(
        'clinical/_item_row.html', {'form': formset.empty_form}, request=request
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
    items = list(prescription.items.all())
    return render(
        request,
        'print/prescription.html',
        {
            'encounter': encounter,
            'prescription': prescription,
            # Two sections: medicines carry a dose, advice does not. Each is
            # omitted entirely when empty rather than printing a bare header.
            'medicines': [item for item in items if not item.is_advice],
            'advice_items': [item for item in items if item.is_advice],
            'page_size': size,
            # Interpolated into CSS, so it comes from the validated accessor.
            'letterhead_color': request.organization.primary_color,
            'letterhead': request.organization.letterhead,
            'now': timezone.localtime(),
        },
    )
