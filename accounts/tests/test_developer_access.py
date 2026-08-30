"""What a DEVELOPER reaches: everything an OWNER does, minus being bookable.

Asserted at the view boundary rather than on the role sets, because that is
where authorisation lives (docs/adr/0012-authorisation-at-the-view-boundary.md)
and because a decorator reading the wrong set is exactly the mistake these
catch. The complement — that STAFF still reaches none of it — stays in each
app's own permission tests.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.models import Encounter, Prescription, PrescriptionItem
from core.context import organization_context
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def encounter(organization, branch, practitioner):
    """A visit written by somebody else — the developer treats nobody."""
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )
        encounter = Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            chief_complaint='Persistent cough for two weeks',
            assessment='Likely viral upper respiratory infection',
        )
        prescription = Prescription.objects.create(
            organization=organization, encounter=encounter
        )
        PrescriptionItem.objects.create(
            organization=organization,
            prescription=prescription,
            free_text_name='Ambroxol syrup',
            dosage='10 ml',
        )
        return encounter


@pytest.mark.parametrize(
    'url_name',
    [
        'clinical:encounter_list',
        'clinical:encounter_create',
        'billing:invoice_list',
        'billing:invoice_create',
        'inventory:stock_list',
        'inventory:receipt_list',
        'catalog:product_list',
    ],
)
def test_a_developer_reaches_the_clinical_surfaces(client, developer, url_name):
    client.force_login(developer)
    assert client.get(reverse(url_name)).status_code == 200


@pytest.mark.parametrize(
    'url_name',
    [
        'accounts:member_list',
        'accounts:member_create',
        'organizations:billing_settings',
        'organizations:feature_settings',
    ],
)
def test_a_developer_reaches_the_administration_surfaces(client, developer, url_name):
    """Answered yes deliberately: with no SMTP, a hand-typed reset is the only
    recovery path there is, and a developer who cannot perform one leaves the
    doctor as the sole recourse — the person this role exists to insulate."""
    client.force_login(developer)
    assert client.get(reverse(url_name)).status_code == 200


def test_a_developer_can_reset_somebody_elses_password(
    client, organization, developer, staff
):
    """The reason the administration answer was yes, exercised rather than implied."""
    from accounts.models import Membership

    membership = Membership.objects.get(user=staff, organization=organization)

    client.force_login(developer)
    response = client.post(
        reverse('accounts:member_reset_password', args=[membership.pk]),
        {'password': 'temp-pass-91847'},
    )

    assert response.status_code == 302
    staff.refresh_from_db()
    assert staff.check_password('temp-pass-91847')
    assert staff.must_change_password is True


def test_a_developer_reads_the_narrative_and_the_prescription(
    client, developer, encounter
):
    """Full clinical read is the point of the role, not a side effect."""
    client.force_login(developer)
    response = client.get(reverse('clinical:encounter_detail', args=[encounter.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'Persistent cough for two weeks' in body
    assert 'Ambroxol syrup' in body


def test_a_developer_is_the_only_one_who_sees_the_backup_screen(client, developer):
    """Backup health moved off the dashboard and behind this role.

    It was gated on ``is_owner`` — "may administer" — and greeted the clinic at
    every sign-in with a sentence about the server. The screen's own tests are
    core/tests/test_backup_screen.py; this asserts the role reaches it, beside
    everything else the role reaches.
    """
    client.force_login(developer)

    assert client.get(reverse('core:backup_settings')).status_code == 200
