"""The SPEC §5 terminology map: labels are data, stored values never move.

A second clinic that calls a consultation something else must be relabelled by
editing ``Organization.terminology`` — not by touching templates, and not by a
migration. These tests are what stops the defaults from being hardcoded back in.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from clinical.models import Encounter, EncounterStatus
from core.context import organization_context
from organizations.models import DEFAULT_TERMINOLOGY
from patients.models import Patient

pytestmark = pytest.mark.django_db


@pytest.fixture
def encounter(organization, branch, practitioner):
    with organization_context(organization):
        patient = Patient.objects.create(
            organization=organization, code='P-0001', full_name='Rahima Begum'
        )
        return Encounter.objects.create(
            organization=organization,
            patient=patient,
            practitioner=practitioner,
            branch=branch,
            occurred_at=timezone.now(),
            status=EncounterStatus.DRAFT,
        )


def _sign_in(client, user):
    client.force_login(user)
    return client


def test_defaults_apply_to_an_organization_that_sets_nothing(organization):
    organization.terminology = {}
    assert organization.terms == DEFAULT_TERMINOLOGY


def test_overrides_win_and_unknown_keys_are_dropped(organization):
    organization.terminology = {'encounter': 'Consultation', 'nonsense': 'Ignored'}
    terms = organization.terms
    assert terms['encounter'] == 'Consultation'
    assert terms['encounter_plural'] == DEFAULT_TERMINOLOGY['encounter_plural']
    assert 'nonsense' not in terms


def test_blank_override_falls_back_rather_than_rendering_nothing(organization):
    organization.terminology = {'encounter': '   '}
    assert organization.terms['encounter'] == DEFAULT_TERMINOLOGY['encounter']


def test_list_page_renders_the_default_labels(client, practitioner, encounter):
    response = _sign_in(client, practitioner).get(reverse('clinical:encounter_list'))
    body = response.content.decode()
    assert 'Visits' in body
    assert 'Open' in body
    assert 'Encounter' not in body


def test_relabelling_the_organization_relabels_the_page(
    client, organization, practitioner, encounter
):
    organization.terminology = {
        'encounter': 'Consultation',
        'encounter_plural': 'Consultations',
        'status_draft': 'Pending',
    }
    organization.save(update_fields=['terminology'])

    response = _sign_in(client, practitioner).get(reverse('clinical:encounter_list'))
    body = response.content.decode()
    assert 'New consultation' in body
    assert 'Consultations' in body
    assert 'Pending' in body
    # Singular and plural are independent keys, so both have to be overridden
    # for the default wording to disappear entirely.
    assert 'Visit' not in body


def test_relabelling_does_not_touch_stored_values(
    client, organization, practitioner, encounter
):
    organization.terminology = {'status_draft': 'Pending'}
    organization.save(update_fields=['terminology'])
    _sign_in(client, practitioner).get(reverse('clinical:encounter_list'))

    encounter.refresh_from_db()
    assert encounter.status == EncounterStatus.DRAFT


def test_amend_label_is_configurable_on_a_locked_encounter(
    client, organization, practitioner, encounter
):
    organization.terminology = {'amend': 'Correct'}
    organization.save(update_fields=['terminology'])
    with organization_context(organization):
        Encounter.objects.filter(pk=encounter.pk).update(
            status=EncounterStatus.FINALIZED
        )

    response = _sign_in(client, practitioner).get(
        reverse('clinical:encounter_detail', args=[encounter.pk])
    )
    body = response.content.decode()
    assert 'Correct' in body
    assert 'Amend' not in body
