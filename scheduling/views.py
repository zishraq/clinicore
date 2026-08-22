"""The day list, and the things the front desk does to it.

This is the first screen built for STAFF rather than adapted for them, so the
role posture is the loosest in the project: ``login_required`` plus
``require_membership``, the same as the patient list. No clinical or billing
data reaches these templates for a STAFF user, so there is nothing here for
``clinical_access_required`` to protect (SPEC §6.1).

One list, one filter, one way to add a row. The three bands and the separate
walk-in path were both a layout the receptionist had to learn before she could
read the day; a booking and a walk-in differ by one checkbox, which is all the
difference there ever was.

Everything that takes typed input renders into a modal in base.html's ``modals``
block and never into the polled container. A five-second swap eating a
half-written reason is a bug that would only surface in a busy clinic, and get
reported as "it clears what I type".
"""

import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import require_membership
from accounts.services import prescribing_users
from organizations.models import Branch
from patients.models import Patient
from scheduling import services
from scheduling.models import Appointment, AppointmentStatus, DayPart

__all__ = [
    'appointment_cancel',
    'appointment_create',
    'appointment_no_show',
    'day_rows',
    'day_view',
    'mark_arrived',
]

#: How often the day list re-fetches itself. One constant so tuning it is one
#: edit, and short enough that an arrival reaches the consulting room before the
#: patient does.
POLL_SECONDS = 5

#: The status filter, as (url value, terminology key). Labelled from the map so
#: the dropdown and the row badges say the same word — "Waiting" in the filter
#: and "Arrived" on the row would be two names for one state.
STATUS_OPTIONS = (
    ('expected', 'status_booked'),
    ('waiting', 'status_arrived'),
    ('seen', 'status_seen'),
    ('cancelled', 'status_cancelled'),
    ('no_show', 'status_no_show'),
)


def _param(request, key: str) -> str:
    """Which day, branch, status and search are being looked at.

    Read from the body first: the actions post from inside the polled container
    and carry the filters as hidden fields, because a POST has no query string
    and the rebuilt list must come back for the day the user is actually on.
    """
    return (request.POST.get(key) or request.GET.get(key) or '').strip()


def _requested_date(request):
    """Bad input falls back to today rather than raising."""
    raw = _param(request, 'date')
    try:
        return timezone.datetime.strptime(raw, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return timezone.localdate()


def _selected_branch(request):
    """``None`` means every branch, which is what a single-site clinic gets."""
    raw = _param(request, 'branch')
    if not raw.isdigit():
        return None
    return Branch.objects.filter(pk=int(raw)).first()


def _branches(organization):
    return Branch.objects.for_organization(organization).filter(is_active=True)


def _status_options(terms) -> list:
    """(value, label) for the filter, "All" first."""
    return [('', 'All')] + [(key, terms[term_key]) for key, term_key in STATUS_OPTIONS]


def _day_context(request) -> dict:
    on_date = _requested_date(request)
    branch = _selected_branch(request)
    status = _param(request, 'status')
    search = _param(request, 'q')
    rows = services.day_list(
        request.organization,
        on_date=on_date,
        branch=branch,
        status=status,
        search=search,
    )
    # Only the roles that may see money get the bills looked up at all, so a
    # STAFF request cannot leak one through a template that forgot to check
    # (SPEC §6.1 as amended puts every billing surface behind PRACTITIONER/OWNER
    # even on a screen STAFF otherwise owns).
    membership = getattr(request, 'membership', None)
    if membership is not None and membership.can_view_clinical:
        rows = services.with_bills(request.organization, rows)
    # Evaluated once here so the empty state can be decided without the
    # template triggering a second pass over the queryset.
    rows = list(rows)
    # Only looked up when there is nothing to show, and only when no filter is
    # narrowing the day — with a filter on, "nothing here" already has an
    # obvious cause and pointing at another date would be answering a question
    # the receptionist did not ask.
    nearest = (
        services.nearest_booked_days(
            request.organization, on_date=on_date, branch=branch
        )
        if not rows and not status and not search
        else {'previous': None, 'next': None}
    )
    return {
        'rows': rows,
        'on_date': on_date,
        'is_today': on_date == timezone.localdate(),
        'previous_date': on_date - timezone.timedelta(days=1),
        'next_date': on_date + timezone.timedelta(days=1),
        'nearest': nearest,
        'branch': branch,
        'branches': _branches(request.organization),
        'status': status,
        'search': search,
        'status_options': _status_options(request.organization.terms),
        'poll_seconds': POLL_SECONDS,
    }


def _rows(request):
    """The day list, rebuilt. What every successful action returns."""
    return render(request, 'scheduling/_rows.html', _day_context(request))


def _retargeted(response, target: str):
    """Send a re-rendered form back to the modal instead of the day list.

    The buttons target ``#day-rows`` because that is what a success replaces.
    A refusal has to land somewhere else or it would swap an error message over
    the whole day, so the response redirects itself.
    """
    response['HX-Retarget'] = target
    response['HX-Reswap'] = 'innerHTML'
    return response


@login_required
def day_view(request):
    """Who is here and who is coming, for one day and optionally one branch."""
    require_membership(request)
    return render(request, 'scheduling/day.html', _day_context(request))


@login_required
def day_rows(request):
    """The polled fragment. Holds no typed input, by design."""
    require_membership(request)
    return _rows(request)


@login_required
@require_POST
def mark_arrived(request, pk: int):
    """The receptionist's main job. Idempotent, because this gets double-clicked."""
    membership = require_membership(request)
    appointment = get_object_or_404(Appointment, pk=pk)
    services.transition(
        appointment, to=AppointmentStatus.ARRIVED, actor=membership.user
    )
    return _rows(request)


@login_required
@require_POST
def appointment_no_show(request, pk: int):
    membership = require_membership(request)
    appointment = get_object_or_404(Appointment, pk=pk)
    services.transition(
        appointment, to=AppointmentStatus.NO_SHOW, actor=membership.user
    )
    return _rows(request)


@login_required
def appointment_cancel(request, pk: int):
    """Cancel with a reason.

    One modal for the page rather than one per row: a form rendered beside the
    rows would be swapped away mid-sentence by the poll. The row's button
    fetches this body into the modal, so which row is being cancelled costs a
    request rather than markup repeated down the list.
    """
    membership = require_membership(request)
    appointment = get_object_or_404(Appointment, pk=pk)
    error = ''
    if request.method == 'POST':
        try:
            services.transition(
                appointment,
                to=AppointmentStatus.CANCELLED,
                actor=membership.user,
                reason=request.POST.get('reason', ''),
            )
        except services.AppointmentError as failure:
            error = str(failure)
        else:
            return _rows(request)
    response = render(
        request,
        'scheduling/_cancel_form.html',
        {'appointment': appointment, 'error': error, **_day_context(request)},
    )
    return _retargeted(response, '#cancel-body') if error else response


def _requested_time(raw: str):
    """``None`` for blank or unparseable, which the form treats as "no time"."""
    try:
        return datetime.datetime.strptime(raw.strip(), '%H:%M').time()
    except (ValueError, AttributeError):
        return None


def _create_appointment(request, membership) -> str:
    """Book, or record someone already standing there. Returns an error or ''.

    One form for both because there was only ever one difference between them:
    whether the patient is here yet. The checkbox is that difference, and it
    routes to ``walk_in`` — which files the row under today and marks it
    arrived in one step — rather than reimplementing either.
    """
    patient = Patient.objects.filter(pk=request.POST.get('patient') or 0).first()
    # Not ``branch``: that name belongs to the day list's own filter, which this
    # form also carries so the rebuilt list stays on the view the user is on.
    branch = Branch.objects.filter(
        pk=request.POST.get('appointment_branch') or 0
    ).first()
    if patient is None:
        return 'Search for the patient, or add them, before saving.'
    if branch is None:
        return 'Choose which chamber they are coming to.'

    practitioner = (
        prescribing_users(request.organization)
        .filter(pk=request.POST.get('practitioner') or 0)
        .first()
    )
    note = request.POST.get('note', '').strip()
    already_here = bool(request.POST.get('already_here'))

    if already_here:
        # The date is deliberately not read: "already here" means now, and the
        # form hides the date when it is ticked so the two cannot disagree.
        services.walk_in(
            request.organization,
            actor=membership.user,
            patient=patient,
            branch=branch,
            practitioner=practitioner,
            note=note,
        )
        return ''

    day_part = request.POST.get('day_part', '').strip()
    scheduled_time = _requested_time(request.POST.get('time', ''))
    if day_part and day_part not in DayPart.values:
        day_part = ''
    try:
        services.book(
            request.organization,
            actor=membership.user,
            patient=patient,
            branch=branch,
            scheduled_date=_requested_date_field(request),
            practitioner=practitioner,
            scheduled_time=scheduled_time,
            day_part='' if scheduled_time else day_part,
            note=note,
        )
    except services.AppointmentError as failure:
        return str(failure)
    return ''


def _requested_date_field(request):
    """The date the appointment is *for*, which is not the day being viewed."""
    raw = (request.POST.get('appointment_date') or '').strip()
    try:
        return timezone.datetime.strptime(raw, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return timezone.localdate()


@login_required
def appointment_create(request):
    """Add a row to a day — booked, or standing at the desk already.

    GET renders the modal body, POST creates and hands back the rebuilt list.
    The patient is chosen with the same picker the visit form uses, so a
    first-time patient is registered in the same two steps as there.
    """
    membership = require_membership(request)
    error = ''
    if request.method == 'POST':
        error = _create_appointment(request, membership)
        if not error:
            # Opened from somewhere that has no day list to swap — the patient
            # page — so send the browser there instead of handing back rows it
            # has nowhere to put. Rebuilt from the patient's pk, never echoed
            # from the request: reflecting a posted URL into HX-Redirect is an
            # open redirect, which is what the test below actually posts.
            destination = _redirect_target(request)
            if destination:
                response = HttpResponse(status=204)
                response['HX-Redirect'] = destination
                return response
            return _rows(request)
    branches = _branches(request.organization)
    response = render(
        request,
        'scheduling/_appointment_form.html',
        {
            'branches': branches,
            'error': error,
            'default_branch': _selected_branch(request) or branches.first(),
            'practitioners': prescribing_users(request.organization),
            'day_parts': DayPart.choices,
            'selected_patient': _requested_patient(request),
            'redirect_to': _redirect_target(request),
            **_day_context(request),
        },
    )
    return _retargeted(response, '#appointment-body') if error else response


def _requested_patient(request):
    """Preselect whoever the caller named, so the patient page needs no picker."""
    raw = _param(request, 'patient')
    if not raw.isdigit():
        return None
    return Patient.objects.filter(pk=int(raw)).first()


def _redirect_target(request) -> str:
    """Where to send the browser after a save, when it was not the day list.

    Rebuilt from the patient's own pk rather than echoed from the query string:
    a caller-supplied URL reflected into ``HX-Redirect`` is an open redirect.
    """
    patient = _requested_patient(request)
    if patient is None or not _param(request, 'redirect_to'):
        return ''
    return reverse('patients:detail', args=[patient.pk])
