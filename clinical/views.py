"""Encounter list, create/edit, detail, and the prescription print view.

The whole app is PRACTITIONER/OWNER only: ``clinical_access_required`` runs
before every view here, so a STAFF user hitting any of these URLs directly gets
a 403 rather than a hidden template block (SPEC §6.1).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import clinical_access_required, require_membership
from accounts.services import default_practitioner
from clinical import services
from clinical.forms import (
    EncounterForm,
    PhotoUploadForm,
    PrescriptionForm,
    PrescriptionItemFormSet,
)
from clinical.models import Encounter, EncounterPhoto, EncounterStatus, PrintSize
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
    'encounter_photo',
    'encounter_photo_delete',
    'encounter_photo_upload',
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


#: Optional medicine columns, in the order they are printed. Every one of them
#: appears only when *this* prescription carries a value for it.
MEDICINE_COLUMNS = (
    'strength',
    'pack_size',
    'preparation',
    'dosage',
    'frequency',
    'duration',
    'instructions',
)


def _prescription_sections(items: list) -> dict:
    """Split a prescription into its two halves, plus one flag per column.

    Each ``show_*`` is decided by the data and never by the organization's
    capability switches. A clinic that turns one off must go on being able to
    read — and reprint — what it already recorded; gating a read surface on a
    current setting would be a data-hiding bug. It is the same rule that keeps
    recorded advice readable after A3's switch goes off.

    The four fields that are always offered are gated the same way, so a clinic
    that handles dosage verbally does not print four empty columns beside the
    three it does fill in. See docs/adr/0015-prescribed-strength.md and
    docs/adr/0017-dispensing-details.md.
    """
    medicines = [item for item in items if not item.is_advice]
    return {
        'medicines': medicines,
        'advice_items': [item for item in items if item.is_advice],
        **{
            f'show_{column}': any(getattr(item, column) for item in medicines)
            for column in MEDICINE_COLUMNS
        },
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
    invoice = None
    if request.organization.billing_enabled:
        # Deferred import: billing depends on clinical, and the encounter page
        # only needs to know whether a bill was already raised for this visit.
        # Inside the check rather than above it, so a clinic with billing off
        # runs neither the import nor the query — the action and the pill are
        # both gone from the page, so there is nothing to look up.
        from billing.services import invoice_for_encounter

        invoice = invoice_for_encounter(request.organization, encounter)

    return render(
        request,
        'clinical/encounter_detail.html',
        {
            'encounter': encounter,
            'prescription': prescription,
            **_prescription_sections(items),
            'invoice': invoice,
            'photos': encounter.photos.all(),
            'photo_form': PhotoUploadForm(),
        },
    )


def _encounter_form_context(request, encounter=None):
    """Bind the three forms that make up the one-page consultation form."""
    organization = request.organization
    prescription = services.prescription_for(encounter) if encounter else None
    data = request.POST or None
    # The form is multipart now. `or None` is safe here where it would not be
    # for a checkbox-only form: a consultation always posts text fields, so an
    # empty POST means no POST rather than "everything was left blank".
    files = request.FILES or None
    form = EncounterForm(
        data,
        files,
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


def _attach_form_photos(request, encounter, form, *, actor) -> None:
    """Store any photographs that rode along with the consultation form.

    Runs after the encounter is saved, because a photograph needs a row to hang
    on and a visit being created has none yet. The files were already decoded
    and re-encoded by ``MultipleImageField.clean``, so nothing here can fail on
    a bad upload — a rejection happened while the note was still on screen.
    """
    images = form.cleaned_data.get('photos') or []
    if not images:
        return
    services.attach_photos(
        encounter,
        images,
        actor=actor,
        caption=form.cleaned_data.get('photo_caption', ''),
    )
    terms = request.organization.terms
    label = terms['photo'] if len(images) == 1 else terms['photo_plural']
    messages.success(request, f'{len(images)} {label.lower()} added.')


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
        # The signed-in user when they treat patients, else the clinic's only
        # prescriber if it has exactly one. An administrator who sees nobody is
        # not on that list (ADR 0019), and prefilling them would render the
        # select with nothing chosen — a field that looks broken rather than
        # unanswered.
        practitioner = default_practitioner(request.organization, membership)
        if practitioner is not None:
            form.initial.setdefault('practitioner', practitioner.pk)
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
        _attach_form_photos(request, encounter, form, actor=membership.user)
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
            _attach_form_photos(request, saved, form, actor=membership.user)
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


def _sidebar_sections(encounter) -> list[dict]:
    """The printed sheet's left column: what was found, and room to write.

    ``rules`` is how many ruled lines the clinic's paper form prints under each
    heading, used when nothing is recorded — the sheet is still something a
    doctor writes on. A ``range`` rather than an int so the template can loop
    over it directly, and because an empty one is falsy, which lets one
    condition cover both "has content" and "has room to write".

    An empty range means the section is simply absent when empty, which is what
    Complaint gets: it is not a heading on the paper design at all, and appears
    here only so that a complaint already recorded goes on being printed.

    **Investigation maps to no field, and that is deliberate.**
    ``Encounter.plan`` is the plan; putting it under this heading would be the
    same misuse as a potency typed into ``dosage`` (ADR 0015). The case record's
    investigations table is per-patient and unbuilt, so ruled lines are the
    honest answer until it exists.
    """
    return [
        {'label': 'Complaint', 'text': encounter.chief_complaint, 'rules': range(0)},
        {
            'label': 'Clinical Findings',
            'text': encounter.examination,
            'rules': range(3),
        },
        {'label': 'Investigation', 'text': '', 'rules': range(2)},
        {'label': 'Diagnosis', 'text': encounter.assessment, 'rules': range(1)},
    ]


@login_required
@clinical_access_required
def prescription_print(request, pk: int):
    """Chrome-free print page. Size comes from the query string, A5 by default.

    Every specific thing on this sheet is the clinic's own data — the
    practitioner's degrees, the chamber's hours, the footer chambers, the notice
    line, the contact strip, the watermark and the colour. Nothing about one
    clinic's design is in the template, so onboarding a second one is a settings
    job (SPEC §6.8).
    """
    from accounts.services import practitioner_letterhead
    from organizations.services import prescription_branches

    encounter = get_object_or_404(
        Encounter.objects.select_related('patient', 'practitioner', 'branch'), pk=pk
    )
    prescription = services.prescription_for(encounter)
    size = request.GET.get('size', prescription.print_size).upper()
    if size not in PrintSize.values:
        size = PrintSize.A5
    items = list(prescription.items.all())
    organization = request.organization
    return render(
        request,
        'print/prescription.html',
        {
            'encounter': encounter,
            'prescription': prescription,
            # Two sections: medicines carry a dose, advice does not. Each is
            # omitted entirely when empty rather than printing a bare header.
            **_prescription_sections(items),
            'sidebar_sections': _sidebar_sections(encounter),
            'page_size': size,
            # Interpolated into CSS, so all three come from validated accessors.
            # The template derives the darker tone and the tint from the first
            # with ``color-mix`` and falls back to these, so a clinic that sets
            # one colour gets a coherent sheet.
            'letterhead_color': organization.primary_color,
            'letterhead_dark': organization.primary_dark_color,
            'letterhead_tint': organization.primary_tint_color,
            # The branch's own address, falling back to the organization's
            # block. A visit at the second chamber must not print the main
            # chamber's address at the top; a single-branch clinic that only filled
            # in the letterhead prints exactly what it printed before.
            'letterhead': organization.letterhead,
            'letterhead_practitioner': practitioner_letterhead(
                organization, encounter.practitioner
            ),
            'footer_branches': prescription_branches(
                organization, exclude_pk=encounter.branch_id
            ),
            'contacts': organization.contacts,
            'watermark': organization.watermark_text,
            'now': timezone.localtime(),
        },
    )


#: Long enough that a grid of thumbnails is not refetched on every visit to the
#: page. `private` is the load-bearing half: `public` would let a shared proxy
#: hold a patient's photograph, which is the leak this view exists to prevent.
PHOTO_CACHE_CONTROL = 'private, max-age=3600'


@login_required
@clinical_access_required
def encounter_photo(request, pk: int):
    """Serve one photograph's bytes.

    This is the only way to read an uploaded file: MEDIA_URL is routed nowhere,
    in any mode, so there is no second path that skips these three checks
    (docs/adr/0014-encounter-photos-served-through-a-view.md). Cross-tenant is
    structural rather than a remembered filter — ``EncounterPhoto.objects`` is
    the organization-scoped default manager, so another clinic's pk is a 404
    here for the same reason it is everywhere else (ADR 0005).
    """
    photo = get_object_or_404(EncounterPhoto, pk=pk)
    response = FileResponse(
        # .open(), never .path: the latter raises on any storage that is not a
        # local filesystem, and this line is what keeps SPEC §10's move to S3 a
        # settings change rather than a rewrite.
        photo.image.open('rb'),
        content_type='image/jpeg',
        # The uploaded name was discarded at upload; this one is generated for
        # the same reason. as_attachment is left False so the browser renders
        # it inline and a phone gets native pinch-zoom.
        filename=f'photo-{photo.pk}.jpg',
    )
    response.headers['Cache-Control'] = PHOTO_CACHE_CONTROL
    return response


@login_required
@clinical_access_required
@require_POST
def encounter_photo_upload(request, pk: int):
    """Add photographs to a saved visit, from the detail page."""
    membership = require_membership(request)
    encounter = get_object_or_404(Encounter, pk=pk)
    terms = request.organization.terms
    form = PhotoUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        # Post-redirect-get like every other write here. There is no typed note
        # at risk on this form, so a message beats re-rendering a page whose
        # other half is a saved record.
        for error in form.errors.get('photos', []):
            messages.error(request, error)
    else:
        images = form.cleaned_data['photos']
        if not images:
            messages.info(request, f'No {terms["photo_plural"].lower()} were chosen.')
        else:
            services.attach_photos(
                encounter,
                images,
                actor=membership.user,
                caption=form.cleaned_data['caption'],
            )
            label = terms['photo'] if len(images) == 1 else terms['photo_plural']
            messages.success(request, f'{len(images)} {label.lower()} added.')
    return redirect('clinical:encounter_detail', pk=encounter.pk)


@login_required
@clinical_access_required
@require_POST
def encounter_photo_delete(request, pk: int):
    """Remove one photograph, row and file.

    PRACTITIONER/ADMINISTRATOR only — which ``clinical_access_required`` already
    is, so upload, view and delete share one gate and STAFF never reaches the
    visit page that offers them.
    """
    photo = get_object_or_404(EncounterPhoto, pk=pk)
    encounter_pk = photo.encounter_id
    services.delete_photo(photo)
    messages.success(request, f'{request.organization.terms["photo"]} deleted.')
    return redirect('clinical:encounter_detail', pk=encounter_pk)
