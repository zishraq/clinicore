"""The day list, and the four things the front desk does to it.

This is the first screen built for STAFF rather than adapted for them, so the
role posture is the loosest in the project: ``login_required`` plus
``require_membership``, the same as the patient list. No clinical or billing
data reaches these templates, so there is nothing here for
``clinical_access_required`` to protect (SPEC §6.1).

Everything that takes typed input — a walk-in, a cancellation reason — renders
into a modal in base.html's ``modals`` block and never into the polled
container. A five-second swap eating a half-written reason is a bug that would
only surface in a busy clinic, and get reported as "it clears what I type".
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import require_membership
from organizations.models import Branch
from patients.models import Patient
from scheduling import services
from scheduling.models import Appointment, AppointmentStatus

__all__ = [
    'appointment_cancel',
    'appointment_no_show',
    'day_rows',
    'day_view',
    'mark_arrived',
    'walk_in_create',
]

#: How often the day list re-fetches itself. One constant so tuning it is one
#: edit, and short enough that an arrival reaches the consulting room before the
#: patient does.
POLL_SECONDS = 5


def _param(request, key: str) -> str:
    """Which day and branch are being looked at.

    Read from the body first: the actions post from inside the polled container
    and carry the filters as hidden fields, because a POST has no query string
    and the rebuilt bands must come back for the day the user is actually on.
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


def _day_context(request) -> dict:
    on_date = _requested_date(request)
    branch = _selected_branch(request)
    context = {
        **services.day_list(request.organization, on_date=on_date, branch=branch),
        'on_date': on_date,
        'is_today': on_date == timezone.localdate(),
        'previous_date': on_date - timezone.timedelta(days=1),
        'next_date': on_date + timezone.timedelta(days=1),
        'branch': branch,
        'branches': _branches(request.organization),
        'poll_seconds': POLL_SECONDS,
    }
    # Only the roles that may see money get the bills looked up at all, so a
    # STAFF request cannot leak one through a template that forgot to check
    # (SPEC §6.1 as amended puts every billing surface behind PRACTITIONER/OWNER
    # even on a screen STAFF otherwise owns).
    membership = getattr(request, 'membership', None)
    if membership is not None and membership.can_view_clinical:
        context['closed'] = services.with_bills(request.organization, context['closed'])
    return context


def _rows(request):
    """The three bands, rebuilt. What every successful action returns."""
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


@login_required
def walk_in_create(request):
    """Record someone already at the desk.

    GET renders the modal body, POST creates and hands back the rebuilt day. The
    patient is chosen with the same picker the visit form uses, so a first-time
    walk-in is registered in the same two steps it is there.
    """
    membership = require_membership(request)
    error = ''
    if request.method == 'POST':
        patient = Patient.objects.filter(pk=request.POST.get('patient') or 0).first()
        # Not ``branch``: that name is the day list's filter, which this form
        # also carries so the rebuilt bands stay on the view the user is on.
        branch = Branch.objects.filter(
            pk=request.POST.get('walk_in_branch') or 0
        ).first()
        if patient is None:
            error = 'Search for the patient, or add them, before saving.'
        elif branch is None:
            error = 'Choose which chamber they have come to.'
        else:
            services.walk_in(
                request.organization,
                actor=membership.user,
                patient=patient,
                branch=branch,
                note=request.POST.get('note', '').strip(),
            )
            return _rows(request)
    branches = _branches(request.organization)
    response = render(
        request,
        'scheduling/_walk_in_form.html',
        {
            'branches': branches,
            'error': error,
            'default_branch': _selected_branch(request) or branches.first(),
            **_day_context(request),
        },
    )
    return _retargeted(response, '#walk-in-body') if error else response
