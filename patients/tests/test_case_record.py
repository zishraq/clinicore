"""The case record: access, the capability switch, and one POST that saves it all.

Every view is PRACTITIONER / OWNER / DEVELOPER — including the HTMX add-row
fragment, because a fragment is a URL and that is exactly where the decorator is
easiest to forget (ADR 0012).
"""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from core.context import organization_context
from patients import services
from patients.models import (
    MODALITY_FACTORS,
    CaseAnalysisEntry,
    CaseComplaint,
    CaseRecord,
)

pytestmark = pytest.mark.django_db

CASE_URLS = ('patients:case_record',)
ROW_KINDS = ('complaint', 'investigation', 'analysis')


@pytest.fixture
def case_clinic(organization):
    organization.case_record_enabled = True
    organization.save(update_fields=['case_record_enabled', 'updated_at'])
    return organization


@pytest.fixture
def patient(case_clinic, branch):
    from patients.models import Patient

    with organization_context(case_clinic):
        return Patient.objects.create(
            organization=case_clinic,
            code='P-0001',
            full_name='Rahima Begum',
            registered_branch=branch,
        )


def _page(client, patient) -> str:
    url = reverse('patients:case_record', args=[patient.pk])
    return client.get(url).content.decode()


def _blank(prefix, total='1'):
    return {
        f'{prefix}-TOTAL_FORMS': total,
        f'{prefix}-INITIAL_FORMS': '0',
        f'{prefix}-MIN_NUM_FORMS': '0',
        f'{prefix}-MAX_NUM_FORMS': '1000',
    }


def _payload(**overrides):
    data = {
        'taken_on': '2026-08-29',
        **_blank('complaints'),
        'complaints-0-complaint': 'Headache',
        'complaints-0-onset': 'Four months ago',
        **_blank('modalities', total='0'),
        **_blank('investigations'),
        **_blank('analysis'),
    }
    data.update(overrides)
    return data


# --- access and tenancy ----------------------------------------------------


@pytest.mark.parametrize('url_name', CASE_URLS)
def test_staff_is_refused_every_case_record_url(client, staff, patient, url_name):
    client.force_login(staff)

    assert client.get(reverse(url_name, args=[patient.pk])).status_code == 403


@pytest.mark.parametrize('kind', ROW_KINDS)
def test_staff_is_refused_the_add_row_fragments_too(client, staff, kind):
    """A fragment is a URL. This is the decorator easiest to forget."""
    client.force_login(staff)

    assert (
        client.get(reverse('patients:case_record_row', args=[kind])).status_code == 403
    )


@pytest.mark.parametrize('role_fixture', ['owner', 'practitioner', 'developer'])
def test_every_clinical_role_gets_in(client, patient, request, role_fixture):
    """Reads CLINICAL_ROLES, so DEVELOPER passes without being named (ADR 0019)."""
    client.force_login(request.getfixturevalue(role_fixture))

    assert (
        client.get(reverse('patients:case_record', args=[patient.pk])).status_code
        == 200
    )


def test_another_clinics_patient_is_a_404_not_a_403(
    client, practitioner, other_organization, branch
):
    """Org scoping, so the answer says nothing about whether the row exists."""
    from patients.models import Patient

    with organization_context(other_organization):
        theirs = Patient.objects.create(
            organization=other_organization, code='P-9999', full_name='Not Yours'
        )
    client.force_login(practitioner)

    response = client.get(reverse('patients:case_record', args=[theirs.pk]))

    assert response.status_code == 404
    assert b'Not Yours' not in response.content


def test_an_unknown_table_is_a_404(client, practitioner):
    client.force_login(practitioner)

    assert (
        client.get(reverse('patients:case_record_row', args=['nonsense'])).status_code
        == 404
    )


# --- the capability switch -------------------------------------------------


def test_with_the_switch_off_a_new_record_cannot_be_started(
    client, organization, patient, practitioner
):
    organization.case_record_enabled = False
    organization.save(update_fields=['case_record_enabled', 'updated_at'])
    client.force_login(practitioner)

    assert (
        client.get(reverse('patients:case_record', args=[patient.pk])).status_code
        == 404
    )


def test_with_the_switch_off_the_patient_page_offers_nothing(
    client, organization, patient, practitioner
):
    organization.case_record_enabled = False
    organization.save(update_fields=['case_record_enabled', 'updated_at'])
    client.force_login(practitioner)

    body = client.get(reverse('patients:detail', args=[patient.pk])).content.decode()

    # Absent, not a disabled button.
    assert reverse('patients:case_record', args=[patient.pk]) not in body


def test_a_record_that_exists_survives_the_switch_being_turned_off(
    client, organization, patient, practitioner
):
    """The rule the switch exists to obey: off hides a feature, never a record.

    Same posture as billing (A3). A clinic that decides it does not want to take
    new case histories must not thereby lose access to the ones it has taken.
    """
    client.force_login(practitioner)
    client.post(reverse('patients:case_record', args=[patient.pk]), _payload())
    organization.case_record_enabled = False
    organization.save(update_fields=['case_record_enabled', 'updated_at'])

    url = reverse('patients:case_record', args=[patient.pk])
    assert client.get(url).status_code == 200
    # And a typo can still be fixed in it.
    response = client.post(
        url,
        _payload(
            **{
                'complaints-INITIAL_FORMS': '1',
                'complaints-0-id': str(CaseComplaint.all_objects.first().pk),
                'complaints-0-complaint': 'Headache, both temples',
                'assessment_provisional': 'Tension headache.',
            }
        ),
    )
    assert response.status_code == 302

    with organization_context(organization):
        record = CaseRecord.objects.get(patient=patient)
    assert record.assessment_provisional == 'Tension headache.'

    body = client.get(reverse('patients:detail', args=[patient.pk])).content.decode()
    assert url in body, 'the card must still offer a record that exists'


# --- saving ----------------------------------------------------------------


def test_the_first_save_creates_the_record_and_seeds_the_fixed_grid(
    client, case_clinic, patient, practitioner
):
    """The record is created on the first Save with whatever is filled in.

    §9's eight rows are part of what it means for a case record to exist, so
    they arrive with it rather than on a later save.
    """
    client.force_login(practitioner)

    response = client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(generals_thermal_state='Chilly'),
    )

    assert response.status_code == 302
    with organization_context(case_clinic):
        record = CaseRecord.objects.get(patient=patient)
        assert record.generals_thermal_state == 'Chilly'
        assert [row.factor for row in record.modalities.all()] == list(MODALITY_FACTORS)
        assert [row.complaint for row in record.complaints.all()] == ['Headache']


def test_the_parent_and_all_four_tables_save_in_one_post(
    client, case_clinic, patient, practitioner
):
    client.force_login(practitioner)

    client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(
            hpc_narrative='Started after a change of job.',
            family_father='Hypertension.',
            **{
                'investigations-0-name': 'CBC',
                'investigations-0-result': 'Normal',
                'analysis-0-finding': 'Head, pain, band-like',
                'analysis-0-grade': '2',
                'analysis-0-candidate': 'Example',
            },
        ),
    )

    with organization_context(case_clinic):
        record = CaseRecord.objects.get(patient=patient)
        assert record.hpc_narrative == 'Started after a change of job.'
        assert record.family_father == 'Hypertension.'
        assert record.complaints.count() == 1
        assert record.investigations.get().name == 'CBC'
        assert record.analysis_entries.get().candidate == 'Example'


def test_a_saved_record_renders_back_into_the_form_it_came_from(
    client, case_clinic, patient, practitioner
):
    """Round trip. A value that saves and does not redisplay reads as lost."""
    client.force_login(practitioner)
    client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(
            mental_anxiety='Fears being alone after dark.',
            systems_skin='Dry, cracks in winter.',
        ),
    )

    body = client.get(
        reverse('patients:case_record', args=[patient.pk])
    ).content.decode()

    assert 'Fears being alone after dark.' in body
    assert 'Dry, cracks in winter.' in body
    assert 'Headache' in body


def test_bengali_text_survives_the_round_trip(
    client, case_clinic, patient, practitioner
):
    """The clinic writes in Bengali, and every box on this page is free prose."""
    bengali = 'রোগী চার মাস ধরে মাথাব্যথায় ভুগছেন।'
    client.force_login(practitioner)

    client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(hpc_narrative=bengali, **{'complaints-0-complaint': 'মাথাব্যথা'}),
    )

    with organization_context(case_clinic):
        record = CaseRecord.objects.get(patient=patient)
    assert record.hpc_narrative == bengali
    body = client.get(
        reverse('patients:case_record', args=[patient.pk])
    ).content.decode()
    assert bengali in body
    assert 'মাথাব্যথা' in body


def test_a_refusal_in_one_table_keeps_what_was_typed_in_the_others(
    client, case_clinic, patient, practitioner
):
    """A refusal never costs the note.

    Nothing is saved unless the parent form and all four formsets validate, so a
    bad date in §12 must not discard §2's row or the prose above it.
    """
    client.force_login(practitioner)

    response = client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(
            hpc_narrative='Typed and must not be lost.',
            **{'investigations-0-performed_on': 'not a date'},
        ),
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert 'Typed and must not be lost.' in body
    assert 'Headache' in body
    with organization_context(case_clinic):
        assert not CaseRecord.objects.filter(patient=patient).exists()


def test_a_growable_row_can_be_removed(client, case_clinic, patient, practitioner):
    client.force_login(practitioner)
    client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(**{'analysis-0-finding': 'Considered and dropped'}),
    )
    with organization_context(case_clinic):
        entry = CaseAnalysisEntry.objects.get()

    client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(
            **{
                'analysis-INITIAL_FORMS': '1',
                'analysis-0-id': str(entry.pk),
                'analysis-0-finding': 'Considered and dropped',
                'analysis-0-DELETE': 'on',
                'complaints-INITIAL_FORMS': '1',
                'complaints-0-id': str(CaseComplaint.all_objects.first().pk),
                'complaints-0-complaint': 'Headache',
            }
        ),
    )

    with organization_context(case_clinic):
        assert not CaseAnalysisEntry.objects.exists()


def test_only_one_record_per_patient(case_clinic, patient):
    with organization_context(case_clinic):
        CaseRecord.objects.create(organization=case_clinic, patient=patient)
        with pytest.raises(IntegrityError), transaction.atomic():
            CaseRecord.objects.create(organization=case_clinic, patient=patient)


def test_the_fixed_grid_offers_no_add_and_no_delete(
    client, case_clinic, patient, practitioner
):
    """§9 never varies, so an add control would offer a ninth row the constraint
    refuses and a delete would leave a gap nothing refills."""
    client.force_login(practitioner)
    client.post(reverse('patients:case_record', args=[patient.pk]), _payload())

    body = client.get(
        reverse('patients:case_record', args=[patient.pk])
    ).content.decode()

    assert 'name="modalities-0-DELETE"' not in body
    # No add-row button aimed at this table: there is no such URL kind at all.
    assert 'case/row/modality' not in body
    for factor in MODALITY_FACTORS:
        assert factor in body


# --- the add-row fragment --------------------------------------------------


#: The formset prefix each add-row button asks for, matching the page.
ROW_PREFIXES = {
    'complaint': 'complaints',
    'investigation': 'investigations',
    'analysis': 'analysis',
}


@pytest.mark.parametrize('kind', ROW_KINDS)
def test_the_add_row_fragment_is_named_for_the_index_it_will_occupy(
    client, practitioner, kind
):
    """The substitution is the whole job of this view.

    ``empty_form`` names its inputs ``complaints-__prefix__-complaint``, and a
    row posted under that name is not a row Django's formset can see. Left
    unsubstituted the button *looks* like it works: the row appears, the
    practitioner types into it, and the save drops it without a word. Caught in
    a browser after a test that asserted the placeholder was present — which is
    how the bug would have shipped green.
    """
    prefix = ROW_PREFIXES[kind]
    client.force_login(practitioner)

    response = client.get(
        reverse('patients:case_record_row', args=[kind]),
        {'prefix': prefix, f'{prefix}-TOTAL_FORMS': '2'},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert '__prefix__' not in body
    assert f'name="{prefix}-2-' in body


@pytest.mark.parametrize('kind', ROW_KINDS)
def test_an_added_row_actually_saves(client, case_clinic, patient, practitioner, kind):
    """End to end for the fragment: fetch a row, post it, read it back.

    The status-code test above cannot prove the names are the ones the formset
    binds. This posts what the fragment would post.
    """
    prefix = ROW_PREFIXES[kind]
    client.force_login(practitioner)
    client.post(reverse('patients:case_record', args=[patient.pk]), _payload())

    column = {'complaint': 'complaint', 'investigation': 'name', 'analysis': 'finding'}[
        kind
    ]
    added = _payload(
        **{
            'complaints-INITIAL_FORMS': '1',
            'complaints-0-id': str(CaseComplaint.all_objects.first().pk),
            'complaints-0-complaint': 'Headache',
            f'{prefix}-TOTAL_FORMS': '2',
            f'{prefix}-1-{column}': 'Added by the button',
        }
    )
    response = client.post(reverse('patients:case_record', args=[patient.pk]), added)

    assert response.status_code == 302
    with organization_context(case_clinic):
        record = CaseRecord.objects.get(patient=patient)
        related = {
            'complaint': record.complaints,
            'investigation': record.investigations,
            'analysis': record.analysis_entries,
        }[kind]
        assert 'Added by the button' in [getattr(row, column) for row in related.all()]


# --- the entry point on the patient page -----------------------------------


def test_the_card_says_start_before_and_open_after(
    client, case_clinic, patient, practitioner
):
    client.force_login(practitioner)
    url = reverse('patients:detail', args=[patient.pk])

    before = client.get(url).content.decode()
    assert 'Start case record' in before
    assert 'Not taken yet.' in before

    client.post(
        reverse('patients:case_record', args=[patient.pk]),
        _payload(**{'complaints-0-complaint': 'Headache, both temples'}),
    )

    after = client.get(url).content.decode()
    assert 'Open case record' in after
    # The summary earns its place: the glance answers a question.
    assert 'Headache, both temples' in after


def test_staff_sees_no_case_record_card_at_all(client, case_clinic, patient, staff):
    client.force_login(staff)

    body = client.get(reverse('patients:detail', args=[patient.pk])).content.decode()

    assert 'case record' not in body.lower()


# --- history ---------------------------------------------------------------


def test_editing_writes_a_history_row_with_the_actor(
    client, case_clinic, patient, practitioner
):
    client.force_login(practitioner)
    url = reverse('patients:case_record', args=[patient.pk])
    client.post(url, _payload())
    client.post(
        url,
        _payload(
            **{
                'assessment_provisional': 'Tension headache.',
                'complaints-INITIAL_FORMS': '1',
                'complaints-0-id': str(CaseComplaint.all_objects.first().pk),
                'complaints-0-complaint': 'Headache',
            }
        ),
    )

    with organization_context(case_clinic):
        record = CaseRecord.objects.get(patient=patient)
    revisions = services.case_record_revisions(case_clinic, record)

    assert revisions.count() == 2
    assert revisions.first().assessment_provisional == 'Tension headache.'


def test_the_delete_checkbox_contributes_no_stray_label(
    client, case_clinic, patient, practitioner
):
    """Found in a browser, not by a status code.

    ``DELETE`` is a checkbox rather than a hidden input — the Remove button
    ticks it — so Django counts it among ``visible_fields`` and the shared row
    loop rendered its label. Every growable row carried a "Delete" caption over
    an invisible control.
    """
    client.force_login(practitioner)

    body = _page(client, patient)

    assert '>Delete<' not in body
    # And the control itself is still there, exactly once, for the button to tick.
    assert body.count('name="complaints-0-DELETE"') == 1
