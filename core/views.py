"""Landing page. Per-role dashboards proper are SPEC §6.7, not the MVP."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

__all__ = ['csrf_failure', 'dashboard']


def csrf_failure(request, reason: str = ''):
    """What a receptionist sees instead of Django's debug 403.

    Every cause of this failure that a clinic will actually meet is the same
    one: a page was left open, somebody signed in, the token rotated, and the
    old page is now stale. So the page says the true thing in one sentence and
    offers the one action that fixes it, rather than explaining CSRF.

    Deliberately standalone rather than extending base.html — the page a
    session-expiry lands on should not itself depend on a session, a
    membership, or an organization's palette. ``reason`` is accepted because
    Django passes it, and dropped because it is for a log, not a person.
    """
    return render(request, 'core/csrf_failure.html', status=403)


@login_required
def dashboard(request):
    """A few counts and the recent clinical activity the role may see."""
    if request.membership is None:
        return render(request, 'core/no_organization.html', status=403)
    return render(request, 'core/dashboard.html', _dashboard_context(request))


#: How many rows of each stock alert the dashboard shows before linking away.
ALERT_ROWS = 5


def _dashboard_context(request) -> dict:
    """Deferred import keeps core free of a hard dependency on the feature apps."""
    from django.utils import timezone

    from clinical.models import Encounter, EncounterStatus
    from patients.models import Patient

    context = {'patient_count': Patient.objects.count()}
    # MVP: replace with permission layer
    if request.membership.can_view_clinical:
        today = timezone.localdate()
        context.update(
            {
                'encounters_today': Encounter.objects.filter(
                    occurred_at__date=today
                ).count(),
                'draft_encounters': Encounter.objects.filter(
                    status=EncounterStatus.DRAFT
                ).count(),
                'recent_encounters': list(
                    Encounter.objects.select_related('patient', 'practitioner')[:8]
                ),
                **_stock_alerts(request.organization),
            }
        )
    return context


def _stock_alerts(organization) -> dict:
    """Below reorder, expiring, expired — SPEC §6.5's three alerts.

    Counted in full and listed in part: a practitioner needs to know that six
    things are short, not read all six on the landing page. Stock is a
    PRACTITIONER/OWNER surface, so this only runs behind the clinical check.
    """
    from inventory.services import stock_alerts

    alerts = stock_alerts(organization)
    return {
        'stock_alerts': {
            key: {'rows': list(alerts[key][:ALERT_ROWS]), 'total': alerts[key].count()}
            for key in ('below_reorder', 'expiring', 'expired')
        },
        'expiry_horizon_days': alerts['within_days'],
    }
