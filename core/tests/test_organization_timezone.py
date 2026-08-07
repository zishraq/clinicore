"""The clinic's clock, activated per request.

Every test here runs against an organization that is **not** in UTC, because the
defect these cover was invisible under UTC: storage, display and the server's
"today" all agree when the offset is zero, so a suite that only ever used the
default proved nothing (docs/adr/0011-organization-timezone-per-request.md).

Asia/Dhaka is UTC+6 and never observes DST, so every expected value below is
arithmetic rather than a lookup.
"""

import datetime
import zoneinfo

import pytest
from django.urls import reverse
from django.utils import timezone

from core.context import organization_context, organization_timezone

pytestmark = pytest.mark.django_db

DHAKA = zoneinfo.ZoneInfo('Asia/Dhaka')
UTC = datetime.UTC

#: 19:30 UTC is 01:30 the next morning in Dhaka. The receptionist's today and
#: the server's today disagree for the six hours after midnight, every night.
AFTER_MIDNIGHT_IN_DHAKA = datetime.datetime(2026, 8, 7, 19, 30, tzinfo=UTC)


@pytest.fixture
def dhaka_organization(organization):
    organization.timezone = 'Asia/Dhaka'
    organization.save(update_fields=['timezone', 'updated_at'])
    return organization


@pytest.fixture
def frozen_night(monkeypatch):
    """Pin ``timezone.now`` to an instant where the two calendars disagree.

    Patched on ``django.utils.timezone`` itself because ``localtime`` and
    ``localdate`` reach for the module global, so this reaches every caller
    without each one having to be found.
    """

    def _now():
        return AFTER_MIDNIGHT_IN_DHAKA

    monkeypatch.setattr(timezone, 'now', _now)
    return AFTER_MIDNIGHT_IN_DHAKA


def test_the_zone_is_not_active_until_something_activates_it(dhaka_organization):
    """The defect itself, pinned: the column exists and is inert on its own."""
    assert dhaka_organization.timezone == 'Asia/Dhaka'
    assert timezone.get_current_timezone_name() == 'UTC'


def test_the_context_manager_moves_the_clock_but_not_the_storage(
    dhaka_organization, frozen_night
):
    """Presentation moves; the instant does not."""
    with organization_timezone(dhaka_organization):
        assert timezone.get_current_timezone() == DHAKA
        # Same moment, read on the clinic's clock: 01:30 on the 8th.
        assert timezone.localtime().hour == 1
        assert timezone.localdate() == datetime.date(2026, 8, 8)
        # Storage is untouched — now() is still the UTC instant it always was.
        assert timezone.now() == AFTER_MIDNIGHT_IN_DHAKA
        assert timezone.now().date() == datetime.date(2026, 8, 7)


def test_the_zone_is_restored_afterwards(dhaka_organization):
    """A leak here would give the next request the previous clinic's clock."""
    with organization_timezone(dhaka_organization):
        assert timezone.get_current_timezone() == DHAKA
    assert timezone.get_current_timezone_name() == 'UTC'


def test_an_organization_without_a_zone_gets_a_clean_utc(organization):
    """None means "no clinic", not "whatever the last one was"."""
    with organization_timezone(organization):
        inside = timezone.get_current_timezone_name()
    assert inside == 'UTC'

    with organization_timezone(None):
        assert timezone.get_current_timezone_name() == 'UTC'


def test_an_unusable_zone_falls_back_rather_than_raising(organization, caplog):
    """One tenant's typo must not 500 their clinic, and must not be silent."""
    organization.timezone = 'Mars/Olympus_Mons'
    organization.save(update_fields=['timezone', 'updated_at'])

    with caplog.at_level('WARNING'), organization_timezone(organization):
        assert timezone.get_current_timezone_name() == 'UTC'

    assert any('Mars/Olympus_Mons' in record.getMessage() for record in caplog.records)


def test_a_request_runs_on_the_clinics_clock(client, practitioner, dhaka_organization):
    """The middleware half. Asserted through a real request, not a helper."""
    client.force_login(practitioner)
    response = client.get(reverse('core:dashboard'))

    assert response.status_code == 200
    # Read inside the view's lifetime via the request it handled.
    assert response.wsgi_request.organization.timezone == 'Asia/Dhaka'
    # And put back afterwards, so the next request starts clean.
    assert timezone.get_current_timezone_name() == 'UTC'


def test_the_visit_forms_default_is_the_clinics_wall_clock(
    client, practitioner, dhaka_organization, frozen_night
):
    """The defect that wrote bad data.

    The doctor saw a time six hours behind his own watch. Accepting it was
    harmless — it round-tripped — but correcting it to the real wall-clock time
    stored that as UTC, six hours out and on the following day.
    """
    client.force_login(practitioner)
    response = client.get(reverse('clinical:encounter_create'))

    occurred = response.context['form'].initial['occurred_at']
    # Read off the value itself, not re-converted: the zone is already restored
    # by the time this line runs, so converting here would measure UTC and the
    # assertion would be about the test rather than the form.
    assert occurred.utcoffset() == datetime.timedelta(hours=6)
    assert (occurred.hour, occurred.minute) == (1, 30)
    assert occurred.date() == datetime.date(2026, 8, 8)


def test_a_datetime_local_default_round_trips_to_the_right_instant(
    client, practitioner, dhaka_organization, branch, frozen_night
):
    """Render and parse must agree, and agree about the *instant*.

    This is the assertion that would have failed had the fix moved rendering
    without moving parsing: the widget renders 01:30, the doctor accepts it, and
    it has to come back as 19:30 UTC — the moment it actually is.
    """
    from clinical.models import Encounter
    from clinical.tests.test_encounter_flow import _payload
    from patients.models import Patient

    with organization_context(dhaka_organization):
        patient = Patient.objects.create(
            organization=dhaka_organization, code='P-0001', full_name='Rahima Begum'
        )

    client.force_login(practitioner)
    shown = (
        client.get(reverse('clinical:encounter_create'))
        .context['form']
        .initial['occurred_at']
    )
    # What the widget puts in the box: the initial is already on the clinic's
    # clock, so this is a format, not a conversion.
    rendered = shown.strftime('%Y-%m-%dT%H:%M')
    assert rendered == '2026-08-08T01:30'

    response = client.post(
        reverse('clinical:encounter_create'),
        _payload(patient, branch, practitioner, occurred_at=rendered),
    )
    assert response.status_code == 302

    with organization_context(dhaka_organization):
        encounter = Encounter.objects.get()
    assert encounter.occurred_at == AFTER_MIDNIGHT_IN_DHAKA


def test_the_day_list_opens_on_the_receptionists_today(
    client, staff, dhaka_organization, branch, frozen_night
):
    """01:30 in Dhaka is still the 7th to a UTC server, for six hours a night.

    Opening the day list on the server's date would show the receptionist
    yesterday's list every night shift, with today's bookings invisible.
    """
    client.force_login(staff)
    response = client.get(reverse('scheduling:day'))

    assert response.status_code == 200
    assert response.context['on_date'] == datetime.date(2026, 8, 8)
    assert response.context['is_today'] is True


def test_a_walk_in_lands_on_the_receptionists_today(
    client, staff, dhaka_organization, branch, frozen_night
):
    """The row has to be on the same day the screen that created it shows.

    ``scheduled_date`` is a plain date, so it is only ever as correct as the
    calendar it was read from — and the row would otherwise be filed under
    yesterday and vanish from the list that made it.
    """
    from patients.models import Patient
    from scheduling.models import Appointment

    with organization_context(dhaka_organization):
        patient = Patient.objects.create(
            organization=dhaka_organization, code='P-0001', full_name='Rahima Begum'
        )

    client.force_login(staff)
    response = client.post(
        reverse('scheduling:walk_in'),
        {'patient': patient.pk, 'walk_in_branch': branch.pk},
    )
    assert response.status_code == 200

    with organization_context(dhaka_organization):
        created = Appointment.objects.get()
    assert created.scheduled_date == datetime.date(2026, 8, 8)
    # It arrived at the instant it actually is, stored in UTC.
    assert created.arrived_at == AFTER_MIDNIGHT_IN_DHAKA
    assert 'Rahima Begum' in response.content.decode()


def test_the_goods_receipt_datetime_round_trips_too(
    client, owner, dhaka_organization, branch, frozen_night
):
    """The other ``datetime-local`` widget in the project.

    Two fields render a datetime into a box a user can edit — the visit's
    ``occurred_at`` and this one — so both are checked rather than one being
    assumed to follow the other.
    """
    from decimal import Decimal

    from catalog.models import Product
    from inventory.models import GoodsReceipt
    from inventory.tests.test_views import _receipt_payload

    with organization_context(dhaka_organization):
        product = Product.objects.create(
            organization=dhaka_organization,
            name='Paracetamol 500mg',
            sale_price=Decimal('12.00'),
            # Only tracked products are offered on a receipt line.
            is_stock_tracked=True,
        )

    client.force_login(owner)
    shown = (
        client.get(reverse('inventory:receipt_create'))
        .context['form']
        .initial['received_at']
    )
    rendered = shown.strftime('%Y-%m-%dT%H:%M')
    assert rendered == '2026-08-08T01:30'

    response = client.post(
        reverse('inventory:receipt_create'),
        _receipt_payload(product, branch=branch.pk, received_at=rendered),
    )
    assert response.status_code == 302, response.context['item_formset'].errors

    with organization_context(dhaka_organization):
        receipt = GoodsReceipt.objects.get()
    assert receipt.received_at == AFTER_MIDNIGHT_IN_DHAKA


def test_the_print_views_stamp_the_clinics_local_time(
    client, practitioner, dhaka_organization, branch, frozen_night
):
    """ "Generated <time>" on a handed-over document is a wall-clock claim."""
    from clinical.models import Encounter, EncounterStatus
    from patients.models import Patient

    with organization_context(dhaka_organization):
        patient = Patient.objects.create(
            organization=dhaka_organization, code='P-0001', full_name='Rahima Begum'
        )
        encounter = Encounter.objects.create(
            organization=dhaka_organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=AFTER_MIDNIGHT_IN_DHAKA,
            status=EncounterStatus.FINALIZED,
            finalized_at=AFTER_MIDNIGHT_IN_DHAKA,
        )

    client.force_login(practitioner)
    body = client.get(
        reverse('clinical:prescription_print', args=[encounter.pk])
    ).content.decode()

    assert 'Generated 08 Aug 2026, 01:30' in body
    assert '07 Aug 2026, 19:30' not in body


def test_a_displayed_datetime_is_rendered_on_the_clinics_clock(
    client, practitioner, dhaka_organization, branch, frozen_night
):
    """The template ``date`` filter follows the active zone, so this is the
    payoff for activating it rather than converting at each call site."""
    from django.template import Context, Template

    from clinical.models import Encounter
    from patients.models import Patient

    with organization_context(dhaka_organization):
        patient = Patient.objects.create(
            organization=dhaka_organization, code='P-0001', full_name='Rahima Begum'
        )
        encounter = Encounter.objects.create(
            organization=dhaka_organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=AFTER_MIDNIGHT_IN_DHAKA,
        )

    template = Template("{{ value|date:'d M Y, H:i' }}")
    with organization_timezone(dhaka_organization):
        shown = template.render(Context({'value': encounter.occurred_at}))
    assert shown == '08 Aug 2026, 01:30'

    # Without the zone active this is the bug, stated as an expectation so the
    # difference is on the record rather than assumed.
    assert template.render(Context({'value': encounter.occurred_at})) == (
        '07 Aug 2026, 19:30'
    )
