"""Landing page and the deployment healthcheck.

Per-role dashboards proper are SPEC §6.7, not the MVP.
"""

import logging

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

__all__ = ['csrf_failure', 'dashboard', 'healthz']

logger = logging.getLogger(__name__)


def healthz(request):
    """Liveness for the container healthcheck and any load balancer in front.

    It touches the database on purpose. A process that is accepting sockets
    while its connection pool is dead is exactly the state a healthcheck exists
    to catch, and answering 200 from Python alone would report that as healthy
    and keep it in rotation.

    Unauthenticated, and outside the organization scope — it runs before anyone
    signs in, so it must not need a session, a membership, or an active
    organization. It reports no version, no host, and no error detail, because
    it answers to the public internet unless something upstream says otherwise.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        # The detail belongs in the log, not the body; see the logging block in
        # config/settings.py for why this would have gone nowhere before.
        logger.exception('Healthcheck failed: the database is unreachable.')
        return JsonResponse({'status': 'error'}, status=503)
    return JsonResponse({'status': 'ok'})


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
    # Administrators only, and on the landing page rather than behind a settings
    # link: this box sends no email, so the dashboard is the only place a backup
    # that stopped three weeks ago can be noticed before it is needed.
    # MVP: replace with permission layer
    if request.membership.is_owner:
        from core.backups import backup_status, restore_check_status

        context['backup'] = backup_status()
        context['restore_check'] = restore_check_status()
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
