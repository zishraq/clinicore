"""The seven demographic columns from docs/adr/0020-the-case-record.md §9.

None of them is clinical narrative, so all seven sit on ``PatientForm`` and
STAFF records them at the desk — that is the point of the increment, and the
split SPEC §6.1 draws stays exactly where it was.

``alt_phone`` is the field that brings obligations with it: a second number the
search cannot find is worse than no second number, because reception concludes
the patient is not registered and creates a duplicate.
"""

import pytest
from django.urls import reverse

from core.context import organization_context
from patients import services
from patients.forms import PatientForm
from patients.models import MaritalStatus, Patient

pytestmark = pytest.mark.django_db

#: Every column the increment adds, with a value that is unmistakably itself.
NEW_VALUES = {
    'marital_status': MaritalStatus.MARRIED,
    'occupation': 'Schoolteacher',
    'email': 'rahima@example.com',
    'alt_phone': '01999888777',
    'emergency_contact_name': 'Karim Uddin',
    'emergency_contact_phone': '01888777666',
    'referred_by': 'Dr Hasan, Mirpur',
}


def _make_patient(organization, **fields):
    with organization_context(organization):
        return Patient.objects.create(
            organization=organization,
            code=fields.pop('code', 'P-0001'),
            full_name=fields.pop('full_name', 'Rahima Begum'),
            **fields,
        )


def test_every_new_column_is_on_the_patient_form():
    """A column reception cannot reach is a column nobody fills in."""
    missing = [name for name in NEW_VALUES if name not in PatientForm().fields]
    assert not missing, f'not offered on PatientForm: {missing}'


def test_staff_can_save_all_seven(client, organization, branch, staff):
    """The whole reason these are demographics rather than clinical data."""
    client.force_login(staff)
    response = client.post(
        reverse('patients:create'),
        {
            'full_name': 'Rahima Begum',
            'phone': '01712345678',
            'sex': 'F',
            'date_of_birth': '',
            'address': 'Mirpur',
            'registered_branch': branch.pk,
            **NEW_VALUES,
        },
    )
    assert response.status_code == 302

    with organization_context(organization):
        patient = Patient.objects.get(full_name='Rahima Begum')
    for name, value in NEW_VALUES.items():
        assert getattr(patient, name) == value, name


def test_none_of_them_reaches_the_clinical_form():
    """The permission boundary is structural: two forms, not a hidden block."""
    from patients.forms import ClinicalProfileForm

    overlap = set(NEW_VALUES) & set(ClinicalProfileForm().fields)
    assert not overlap, f'demographics leaked onto the clinical form: {overlap}'


def test_marital_status_defaults_to_not_recorded(organization):
    """Mirrors ``Sex``: an explicit unknown, not a blank meaning two things."""
    patient = _make_patient(organization)
    assert patient.marital_status == MaritalStatus.UNKNOWN
    assert patient.get_marital_status_display() == 'Not recorded'


def test_the_emergency_number_dials(organization):
    patient = _make_patient(organization, emergency_contact_phone='+880 (17) 1234-5678')
    assert patient.emergency_dial == '+8801712345678'
    assert patient.alt_dial == ''


def test_the_alternative_number_dials(organization):
    patient = _make_patient(organization, alt_phone='017 1234 5678')
    assert patient.alt_dial == '01712345678'


# --- the two obligations alt_phone brings with it -------------------------


def test_a_patient_is_found_by_their_second_number(organization):
    _make_patient(organization, phone='01712345678', alt_phone='01999888777')

    with organization_context(organization):
        found = list(services.search_patients(organization, '01999888777'))

    assert [row.full_name for row in found] == ['Rahima Begum']


def test_the_second_number_is_a_partial_match_like_the_first(organization):
    _make_patient(organization, alt_phone='01999888777')

    with organization_context(organization):
        assert services.search_patients(organization, '999888').exists()


def test_a_second_number_already_on_file_is_a_possible_duplicate(organization):
    """Someone giving their spouse's number as their own is the same household.

    The guard has to see it in *both* directions or the duplicate goes in.
    """
    _make_patient(organization, phone='01712345678', alt_phone='01999888777')

    with organization_context(organization):
        # Typed as the primary number, matching a stored alternative.
        assert services.possible_duplicates(
            organization, full_name='Someone Else', phone='01999888777'
        ).exists()
        # Typed as the alternative, matching a stored primary.
        assert services.possible_duplicates(
            organization, full_name='Someone Else', phone='', alt_phone='01712345678'
        ).exists()


def test_an_unrelated_second_number_is_not_a_duplicate(organization):
    _make_patient(organization, phone='01712345678', alt_phone='01999888777')

    with organization_context(organization):
        assert not services.possible_duplicates(
            organization, full_name='Someone Else', phone='', alt_phone='01555444333'
        ).exists()


def test_the_create_form_warns_on_a_matching_second_number(
    client, organization, branch, staff
):
    """The view has to pass the field through, or the service never sees it."""
    _make_patient(organization, phone='01712345678')
    client.force_login(staff)

    response = client.post(
        reverse('patients:create'),
        {
            'full_name': 'Completely Different',
            'phone': '',
            'alt_phone': '01712345678',
            'sex': 'U',
            'marital_status': MaritalStatus.UNKNOWN,
            'registered_branch': branch.pk,
        },
    )

    assert response.status_code == 200
    assert response.context['duplicates']


# --- the disclosure -------------------------------------------------------


def test_the_desk_set_is_what_reception_fills_in_for_everybody():
    form = PatientForm()
    assert [field.name for field in form.desk_fields] == list(form.DESK_FIELDS)
    assert set(NEW_VALUES) == {field.name for field in form.detail_fields}


def test_a_closed_disclosure_still_posts_its_fields(
    client, organization, branch, staff
):
    """ADR 0017's rule, reused. The fields are in the DOM, not conditional on it.

    Asserted through the quick-create modal because that is the path where the
    disclosure is closed by default and the values are posted by hx-include.
    """
    client.force_login(staff)
    response = client.post(
        reverse('patients:quick_create'),
        {
            'full_name': 'Jahanara Khatun',
            'phone': '01712000000',
            'sex': 'F',
            'registered_branch': branch.pk,
            **NEW_VALUES,
        },
    )
    assert response.status_code == 200

    with organization_context(organization):
        patient = Patient.objects.get(full_name='Jahanara Khatun')
    assert patient.occupation == 'Schoolteacher'
    assert patient.marital_status == MaritalStatus.MARRIED


def test_the_form_renders_every_field_it_declares(client, organization, staff, branch):
    """A field on the form and off the template is a field that saves as empty."""
    patient = _make_patient(organization, **NEW_VALUES)
    client.force_login(staff)

    body = client.get(reverse('patients:update', args=[patient.pk])).content.decode()

    for name in PatientForm().fields:
        assert f'name="{name}"' in body, f'{name} is on the form but not on the page'


def test_the_disclosure_opens_when_it_already_holds_something(organization):
    """Editing a patient with an occupation on file must show it, script or not."""
    filled = _make_patient(organization, **NEW_VALUES)
    bare = _make_patient(organization, code='P-0002', full_name='Bare Record')

    assert PatientForm(instance=filled).has_details
    # Every row carries marital_status 'U'; treating that as content would open
    # the disclosure for every patient in the clinic.
    assert not PatientForm(instance=bare).has_details
