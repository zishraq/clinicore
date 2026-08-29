"""``PatientClinicalProfile`` was absorbed into the case record and removed.

Two fields, one screen, one form, one template, behind exactly the same access
boundary and with exactly the same one-per-patient shape — and §4 of the case
record asks for both. Keeping the pair gave the clinic two places to record an
allergy, and the failure mode is the dangerous one: the blank allergy box on
whichever screen the doctor happens to be looking at.

Testing the migration end to end wants ``django-test-migrations``, which is a
new dependency and was not asked for. What is tested instead is the mapping the
migration is built on, asserted against the models it names — plus the fact that
nothing anywhere still reaches for the model that is gone.
"""

from importlib import import_module

import pytest
from django.apps import apps
from django.urls import NoReverseMatch, reverse

from patients.models import CaseRecord

#: The migration module is not a legal identifier (it starts with a digit), so
#: the mapping is reached by name. Read out of the migration itself rather than
#: restated here: a copy would be a second mapping that can drift from the one
#: that actually moved the data.
MIGRATION = import_module('patients.migrations.0004_absorb_clinical_profile')
FIELD_MAP = MIGRATION.FIELD_MAP


def test_the_mapping_covers_both_of_the_old_columns():
    """A field left out of the map is a field whose data is dropped in silence."""
    assert set(FIELD_MAP) == {'allergies', 'medical_history'}


def test_every_destination_exists_on_the_case_record():
    """The map names columns; a typo here would move data into nothing."""
    columns = {field.name for field in CaseRecord._meta.get_fields()}

    missing = sorted(set(FIELD_MAP.values()) - columns)

    assert not missing, f'the migration writes to columns that do not exist: {missing}'


def test_the_medical_history_lands_in_the_catch_all_box():
    """Moved whole, never distributed across §4's eight prompts.

    Nothing mechanically distinguishes "had measles as a child" from "appendix
    out in 2019", and a guess would corrupt a clinical record — the rule that
    stopped ADR 0015 splitting `dosage` values apart. The doctor redistributes
    it by hand if he wants to.
    """
    assert FIELD_MAP['medical_history'] == 'past_other_history'
    assert FIELD_MAP['allergies'] == 'past_allergies'


def test_the_model_is_gone():
    with pytest.raises(LookupError):
        apps.get_model('patients', 'PatientClinicalProfile')


def test_the_url_is_gone():
    """Anyone with /patients/<pk>/clinical/ in their notes needs to know."""
    with pytest.raises(NoReverseMatch):
        reverse('patients:clinical_profile', args=[1])


def test_nothing_still_imports_the_form():
    import patients.forms

    assert not hasattr(patients.forms, 'ClinicalProfileForm')
