"""Patient code allocation (B11).

Codes now come off the same locked ``DocumentSequence`` row as invoice numbers.
What they replace is ``max(code) + 1``, which two receptionists registering in
the same moment turned into an IntegrityError 500 — the unique constraint was
doing the allocating. The Postgres proof that the lock serialises lives in
``billing/tests/test_numbering.py``; these cover allocation and the floor.
"""

import pytest

from core.context import organization_context
from core.models import DocumentSequence
from patients import services
from patients.forms import PatientForm
from patients.models import Patient

pytestmark = pytest.mark.django_db


def _register(organization, actor, name: str) -> Patient:
    with organization_context(organization):
        form = PatientForm(
            data={
                'full_name': name,
                'phone': '',
                'sex': 'U',
                'date_of_birth': '',
                'address': '',
                'registered_branch': '',
            },
            organization=organization,
        )
        assert form.is_valid(), form.errors
        return services.create_patient(organization, actor=actor, form=form)


def test_codes_are_issued_in_order(organization, staff):
    assert _register(organization, staff, 'First Patient').code == 'P-0001'
    assert _register(organization, staff, 'Second Patient').code == 'P-0002'
    assert _register(organization, staff, 'Third Patient').code == 'P-0003'


def test_each_organization_runs_its_own_series(organization, other_organization, staff):
    assert _register(organization, staff, 'Ours').code == 'P-0001'
    assert _register(other_organization, staff, 'Theirs').code == 'P-0001'


def test_the_counter_row_is_unperiodded(organization, staff):
    """A patient keeps one code for life, so the run never restarts."""
    _register(organization, staff, 'First Patient')
    sequence = DocumentSequence.all_objects.get(
        organization=organization, kind=services.PATIENT_SEQUENCE
    )
    assert sequence.period == ''
    assert sequence.last_number == 1


def test_codes_written_before_the_counter_raise_the_floor(organization, staff):
    """The seed loader writes codes directly, and real rows predate the counter.

    Without the floor the counter starts at 1 and hands out a code that is
    already on a row.
    """
    with organization_context(organization):
        Patient.objects.create(
            organization=organization, code='P-0042', full_name='Legacy Patient'
        )
    assert _register(organization, staff, 'Next Patient').code == 'P-0043'


def test_a_removed_patients_code_is_not_reissued(organization, staff, practitioner):
    """Uniqueness holds against soft-deleted rows too, or restoring one collides."""
    first = _register(organization, staff, 'First Patient')
    first.soft_delete(actor=practitioner)

    assert _register(organization, staff, 'Second Patient').code == 'P-0002'


def test_a_non_conforming_code_does_not_break_allocation(organization, staff):
    """Imported rows may carry anything; they just do not raise the floor."""
    with organization_context(organization):
        Patient.objects.create(
            organization=organization, code='LEGACY/77', full_name='Imported Patient'
        )
    assert _register(organization, staff, 'Next Patient').code == 'P-0001'


def test_the_counter_overtakes_the_floor_and_stops_scanning(organization, staff):
    """Once the counter is ahead of the rows, it is the only thing allocating."""
    with organization_context(organization):
        Patient.objects.create(
            organization=organization, code='P-0009', full_name='Legacy Patient'
        )
    assert _register(organization, staff, 'Tenth').code == 'P-0010'
    assert _register(organization, staff, 'Eleventh').code == 'P-0011'
