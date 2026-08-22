"""``import_patients``: a clinic's existing list, from a file supplied at run time.

docs/adr/0018-importing-real-patient-data.md. Every name in this file is
invented, and every CSV is written into ``tmp_path`` — real patient data never
enters the repository, and that includes a fixture.

The test that matters most is ``test_running_it_twice_creates_nothing_the_second_time``.
Patients have no natural key, so a second run creating duplicates is the failure
this command exists to avoid.
"""

from datetime import date

import pytest
from django.core.management import CommandError, call_command

from core.context import organization_context
from organizations.models import Branch
from patients.models import Patient, Sex

pytestmark = pytest.mark.django_db

HEADER = 'full_name,date_of_birth,sex,phone'

#: Two rows, nothing awkward. Awkward things are added per test.
SIMPLE = f"""{HEADER}
Rahima Begum,1981-04-17,Female,01712345678
Imran Hossain,1994-11-02,M,01898765432
"""


def write_csv(tmp_path, text: str, *, name: str = 'list.csv', encoding='utf-8'):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def run(organization, path, **options) -> str:
    """Call the command and hand back what it printed."""
    from io import StringIO

    out = StringIO()
    call_command(
        'import_patients', organization.slug, file=str(path), stdout=out, **options
    )
    return out.getvalue()


def patients(organization) -> list[Patient]:
    with organization_context(organization):
        return list(Patient.objects.order_by('code'))


# --- the happy path --------------------------------------------------------


def test_it_imports_a_simple_file(organization, branch, tmp_path):
    output = run(organization, write_csv(tmp_path, SIMPLE))
    people = patients(organization)
    # Ordered by code, so this also says codes are allocated in file order.
    assert [p.full_name for p in people] == ['Rahima Begum', 'Imran Hossain']
    rahima = next(p for p in people if p.full_name == 'Rahima Begum')
    assert rahima.date_of_birth == date(1981, 4, 17)
    assert rahima.sex == Sex.FEMALE
    assert rahima.phone == '01712345678'
    assert rahima.registered_branch == branch
    assert 'Created' in output


def test_the_clinic_is_named_before_anything_happens(organization, branch, tmp_path):
    """A mistyped slug has to be catchable at the dry run."""
    output = run(organization, write_csv(tmp_path, SIMPLE), dry_run=True)
    assert organization.name in output
    assert organization.slug in output
    assert '0 already on file' in output


def test_codes_continue_from_what_is_already_there(organization, branch, tmp_path):
    with organization_context(organization):
        Patient.objects.create(
            organization=organization, code='P-0041', full_name='Sabina Yasmin'
        )
    run(organization, write_csv(tmp_path, SIMPLE))
    with organization_context(organization):
        assert set(Patient.objects.values_list('code', flat=True)) == {
            'P-0041',
            'P-0042',
            'P-0043',
        }


def test_the_committed_sample_file_imports(organization, branch):
    """The file the runbook points at has to actually work."""
    from pathlib import Path

    sample = Path(__file__).resolve().parents[2] / 'docs' / 'sample-patient-import.csv'
    output = run(organization, sample, dry_run=True)
    assert 'Would be created  : 6' in output
    assert 'Failed            : 0' in output


# --- sex -------------------------------------------------------------------


@pytest.mark.parametrize(
    ('written', 'expected'),
    [
        ('Male', Sex.MALE),
        ('male', Sex.MALE),
        ('M', Sex.MALE),
        ('Female', Sex.FEMALE),
        ('female', Sex.FEMALE),
        ('f', Sex.FEMALE),
        ('Other', Sex.OTHER),
        ('Unknown', Sex.UNKNOWN),
        (' Male ', Sex.MALE),
    ],
)
def test_sex_is_read_from_the_label_or_the_code(
    organization, branch, tmp_path, written, expected
):
    csv = f'{HEADER}\nNasrin Sultana,1990-01-01,{written},01700000000\n'
    run(organization, write_csv(tmp_path, csv))
    assert patients(organization)[0].sex == expected


def test_an_unrecognised_sex_imports_as_unknown_and_is_reported(
    organization, branch, tmp_path
):
    """One odd cell must not cost the other three correct fields.

    Imported, but loudly: the row number and the offending value are printed,
    and it is counted apart from a blank so it cannot hide inside that number.
    """
    csv = f'{HEADER}\nNasrin Sultana,1990-01-01,Mael,01700000000\n'
    output = run(organization, write_csv(tmp_path, csv))
    person = patients(organization)[0]
    assert person.sex == Sex.UNKNOWN
    assert person.full_name == 'Nasrin Sultana'
    assert person.date_of_birth == date(1990, 1, 1)
    assert person.phone == '01700000000'
    assert "row 2: 'Mael'" in output
    assert 'Sex unrecognised  : 1' in output
    assert 'Sex blank         : 0' in output


def test_blank_and_unrecognised_are_counted_apart(organization, branch, tmp_path):
    """Blank is legitimate absence; "Mael" is an error someone should look at."""
    csv = (
        f'{HEADER}\n'
        'Nasrin Sultana,1990-01-01,,01700000000\n'
        'Farid Uddin,1985-05-05,Mael,01700000001\n'
    )
    output = run(organization, write_csv(tmp_path, csv))
    assert 'Sex blank         : 1' in output
    assert 'Sex unrecognised  : 1' in output
    assert all(p.sex == Sex.UNKNOWN for p in patients(organization))


# --- date of birth ---------------------------------------------------------


def test_a_blank_date_of_birth_is_allowed(organization, branch, tmp_path):
    csv = f'{HEADER}\nNasrin Sultana,,Female,01700000000\n'
    run(organization, write_csv(tmp_path, csv))
    person = patients(organization)[0]
    assert person.date_of_birth is None
    # The constraint that makes the two mutually exclusive still holds.
    assert person.approx_age_years is None


@pytest.mark.parametrize('written', ['21-09-1998', '09/21/1998', 'not a date', '1998'])
def test_a_malformed_date_fails_the_row(organization, branch, tmp_path, written):
    """No format sniffing: a guess writes a birth date nobody re-checks."""
    csv = f'{HEADER}\nNasrin Sultana,{written},Female,01700000000\n'
    output = run(organization, write_csv(tmp_path, csv))
    assert patients(organization) == []
    assert f'row 2: date_of_birth {written!r} is not YYYY-MM-DD' in output
    assert 'Failed            : 1' in output


def test_a_future_date_of_birth_fails_the_row(organization, branch, tmp_path):
    csv = f'{HEADER}\nNasrin Sultana,2099-01-01,Female,01700000000\n'
    output = run(organization, write_csv(tmp_path, csv))
    assert patients(organization) == []
    assert 'is in the future' in output


def test_a_blank_name_fails_the_row(organization, branch, tmp_path):
    csv = f'{HEADER}\n,1990-01-01,Female,01700000000\n'
    output = run(organization, write_csv(tmp_path, csv))
    assert patients(organization) == []
    assert 'row 2: full_name is blank' in output


def test_one_bad_row_does_not_cost_the_good_ones(organization, branch, tmp_path):
    """The savepoint per row, end to end."""
    csv = (
        f'{HEADER}\n'
        'Rahima Begum,1981-04-17,Female,01712345678\n'
        'Broken Row,21-09-1998,Female,01700000000\n'
        'Imran Hossain,1994-11-02,M,01898765432\n'
    )
    run(organization, write_csv(tmp_path, csv))
    assert [p.full_name for p in patients(organization)] == [
        'Rahima Begum',
        'Imran Hossain',
    ]


# --- idempotency -----------------------------------------------------------


def test_running_it_twice_creates_nothing_the_second_time(
    organization, branch, tmp_path
):
    """The failure this command exists to avoid."""
    path = write_csv(tmp_path, SIMPLE)
    run(organization, path)
    output = run(organization, path)
    assert len(patients(organization)) == 2
    assert 'Created           : 0' in output
    assert 'Already on file   : 2' in output


def test_the_dry_run_of_a_second_pass_reports_nothing_to_do(
    organization, branch, tmp_path
):
    """What the runbook tells the operator to look at."""
    path = write_csv(tmp_path, SIMPLE)
    run(organization, path)
    output = run(organization, path, dry_run=True)
    assert 'Would be created  : 0' in output


def test_the_same_person_twice_in_one_file_is_created_once(
    organization, branch, tmp_path
):
    csv = (
        f'{HEADER}\n'
        'Rahima Begum,1981-04-17,Female,01712345678\n'
        'Rahima Begum,1981-04-17,Female,01712345678\n'
    )
    output = run(organization, write_csv(tmp_path, csv))
    assert len(patients(organization)) == 1
    assert 'Repeated in file  : 1' in output


def test_two_family_members_on_one_phone_both_import(organization, branch, tmp_path):
    """The case the dedupe rule is built around."""
    csv = (
        f'{HEADER}\n'
        'Rahima Begum,1981-04-17,Female,01712345678\n'
        'Sumaiya Begum,2009-06-03,Female,01712345678\n'
    )
    run(organization, write_csv(tmp_path, csv))
    assert len(patients(organization)) == 2


def test_the_phone_is_compared_with_its_separators_removed(
    organization, branch, tmp_path
):
    run(organization, write_csv(tmp_path, SIMPLE))
    spaced = f'{HEADER}\nRahima Begum,1981-04-17,Female,017 1234-5678\n'
    output = run(organization, write_csv(tmp_path, spaced, name='second.csv'))
    assert len(patients(organization)) == 2
    assert 'Already on file   : 1' in output


def test_a_removed_patient_is_skipped_not_resurrected(organization, branch, tmp_path):
    """Recreating a deliberately deleted record is worse than skipping it."""
    path = write_csv(tmp_path, SIMPLE)
    run(organization, path)
    with organization_context(organization):
        # The application's own removal path (patients/views.py patient_delete),
        # not a queryset delete: a hard delete would not exercise anything.
        Patient.objects.get(full_name='Rahima Begum').soft_delete()
        assert Patient.objects.count() == 1

    output = run(organization, path)
    with organization_context(organization):
        assert Patient.objects.count() == 1
        assert Patient.all_objects.filter(organization=organization).count() == 2
    assert 'Removed patients  : 1' in output


def test_another_clinics_patients_are_not_matched(
    organization, other_organization, branch, tmp_path
):
    with organization_context(other_organization):
        Patient.objects.create(
            organization=other_organization,
            code='P-0001',
            full_name='Rahima Begum',
            date_of_birth=date(1981, 4, 17),
            phone='01712345678',
        )
    run(organization, write_csv(tmp_path, SIMPLE))
    assert len(patients(organization)) == 2


# --- the dry run -----------------------------------------------------------


def test_the_dry_run_writes_nothing(organization, branch, tmp_path):
    output = run(organization, write_csv(tmp_path, SIMPLE), dry_run=True)
    assert patients(organization) == []
    assert 'DRY RUN' in output
    assert 'Would be created  : 2' in output
    assert 'Nothing was written' in output


def test_the_dry_run_reports_the_same_failures(organization, branch, tmp_path):
    csv = f'{HEADER}\nNasrin Sultana,21-09-1998,Female,01700000000\n'
    output = run(organization, write_csv(tmp_path, csv), dry_run=True)
    assert 'is not YYYY-MM-DD' in output
    assert patients(organization) == []


# --- what a spreadsheet actually produces ----------------------------------


def test_a_byte_order_mark_does_not_break_the_header(organization, branch, tmp_path):
    """Every Excel export has one, and ﻿full_name is not full_name."""
    path = write_csv(tmp_path, SIMPLE, encoding='utf-8-sig')
    run(organization, path)
    assert len(patients(organization)) == 2


def test_crlf_line_endings_are_read(organization, branch, tmp_path):
    run(organization, write_csv(tmp_path, SIMPLE.replace('\n', '\r\n')))
    assert len(patients(organization)) == 2


def test_a_quoted_name_containing_a_comma_stays_one_name(
    organization, branch, tmp_path
):
    csv = f'{HEADER}\n"Chowdhury, Abdul Karim",1957-02-28,Male,01700000000\n'
    run(organization, write_csv(tmp_path, csv))
    assert patients(organization)[0].full_name == 'Chowdhury, Abdul Karim'


def test_trailing_blank_rows_are_not_patients(organization, branch, tmp_path):
    output = run(organization, write_csv(tmp_path, SIMPLE + ',,,\n\n,,,\n'))
    assert len(patients(organization)) == 2
    assert 'Read              : 2' in output
    assert 'Failed            : 0' in output


def test_extra_columns_are_ignored_with_a_note(organization, branch, tmp_path):
    """Refusing because someone added an Address column is the wrong failure."""
    csv = (
        f'{HEADER},address,notes\n'
        'Rahima Begum,1981-04-17,Female,01712345678,"12 Green Road, Dhaka",called\n'
    )
    output = run(organization, write_csv(tmp_path, csv))
    assert patients(organization)[0].full_name == 'Rahima Begum'
    assert 'Ignored extra column(s): address, notes' in output


def test_the_header_may_be_capitalised(organization, branch, tmp_path):
    csv = 'Full_Name,Date_Of_Birth,Sex,Phone\nRahima Begum,1981-04-17,Female,017\n'
    run(organization, write_csv(tmp_path, csv))
    assert len(patients(organization)) == 1


# --- refusals --------------------------------------------------------------


def test_a_file_with_no_header_is_refused(organization, branch, tmp_path):
    """Never read positionally: transposed sex and phone is invisible for months."""
    csv = 'Rahima Begum,1981-04-17,Female,01712345678\n'
    with pytest.raises(CommandError) as refusal:
        run(organization, write_csv(tmp_path, csv))
    assert 'missing the column' in str(refusal.value)
    assert patients(organization) == []


def test_a_missing_required_column_is_refused(organization, branch, tmp_path):
    csv = 'full_name,date_of_birth,phone\nRahima Begum,1981-04-17,01712345678\n'
    with pytest.raises(CommandError) as refusal:
        run(organization, write_csv(tmp_path, csv))
    assert 'sex' in str(refusal.value)


def test_a_missing_file_is_refused(organization, branch, tmp_path):
    with pytest.raises(CommandError, match='No such file'):
        run(organization, tmp_path / 'nowhere.csv')


def test_an_unknown_slug_is_refused(organization, branch, tmp_path):
    path = write_csv(tmp_path, SIMPLE)
    with pytest.raises(CommandError, match='No organization with slug'):
        call_command('import_patients', 'no-such-clinic', file=str(path))


# --- the branch ------------------------------------------------------------


def test_a_single_branch_clinic_needs_no_argument(organization, branch, tmp_path):
    output = run(organization, write_csv(tmp_path, SIMPLE))
    assert patients(organization)[0].registered_branch == branch
    assert branch.code in output


def test_a_multi_branch_clinic_is_refused_rather_than_guessed(
    organization, branch, tmp_path
):
    """Several hundred patients at the wrong chamber surfaces months later."""
    with organization_context(organization):
        Branch.objects.create(
            organization=organization, name='Second Chamber', code='SECOND'
        )
    with pytest.raises(CommandError) as refusal:
        run(organization, write_csv(tmp_path, SIMPLE))
    assert '--branch is required' in str(refusal.value)
    assert 'SECOND' in str(refusal.value)
    assert patients(organization) == []


def test_the_branch_can_be_named(organization, branch, tmp_path):
    with organization_context(organization):
        second = Branch.objects.create(
            organization=organization, name='Second Chamber', code='SECOND'
        )
    run(organization, write_csv(tmp_path, SIMPLE), branch='second')
    assert all(p.registered_branch == second for p in patients(organization))


def test_an_unknown_branch_code_is_refused(organization, branch, tmp_path):
    with pytest.raises(CommandError) as refusal:
        run(organization, write_csv(tmp_path, SIMPLE), branch='NOWHERE')
    assert 'No active branch with code' in str(refusal.value)
