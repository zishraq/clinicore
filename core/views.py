"""Landing page. Per-role dashboards proper are SPEC §6.7, not the MVP."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

__all__ = ['dashboard']


@login_required
def dashboard(request):
    """A few counts and the recent clinical activity the role may see."""
    if request.membership is None:
        return render(request, 'core/no_organization.html', status=403)
    return render(request, 'core/dashboard.html', _dashboard_context(request))


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
            }
        )
    return context
