"""Every prompt on the paper form has a home, and every column has a prompt.

**This is the test that makes a seventy-two-column migration reviewable.** The
migration itself cannot be read line by line to any useful purpose; the question
worth answering is "does the field list match the paper the doctor actually
uses", and that is a question a test can answer and a reader cannot.

It reads ``docs/reference/case-taking-form.md`` — the transcription of the paper
sheet, kept beside the original — and fails when the document and the models
drift apart in *either* direction: a prompt that lost its column, or a column
that answers no prompt.

The prompts that are deliberately not fields are named in ``DROPPED``, with the
reason, because "we decided not to" and "we forgot" look identical otherwise.
"""

import re
from pathlib import Path

import pytest

from patients.case_forms import SECTIONS, CaseRecordForm
from patients.models import (
    MODALITY_FACTORS,
    CaseAnalysisEntry,
    CaseComplaint,
    CaseInvestigation,
    CaseModality,
    CaseRecord,
)

REFERENCE = (
    Path(__file__).resolve().parents[2] / 'docs' / 'reference' / 'case-taking-form.md'
)

#: The sections this form covers. §1 is ``Patient``; §11 is per-visit and is
#: not built; §15 and §16 are the prescription and the encounter timeline, which
#: the record links to and never restates (ADR 0020's context section).
COVERED_SECTIONS = ('2', '3', '4', '5', '6', '7', '8', '9', '10', '12', '13', '14')

#: Paper prompt -> where it lives. A column on ``CaseRecord`` unless the value
#: names a child model and its field.
MAPPING = {
    # §2, the growable complaints table.
    'Complaint / Site': (CaseComplaint, 'complaint'),
    'Onset': (CaseComplaint, 'onset'),
    'Duration': (CaseComplaint, 'duration'),
    'Character / Sensation': (CaseComplaint, 'character'),
    'Intensity': (CaseComplaint, 'intensity'),
    # §3
    'First noticed on': 'hpc_first_noticed_on',
    'Sudden / Gradual': 'hpc_onset_type',
    'Possible cause / exciting factor': 'hpc_cause',
    'Progression': 'hpc_progression',
    'Previous episodes': 'hpc_previous_episodes',
    'Treatment already taken': 'hpc_treatment_taken',
    'Response to treatment': 'hpc_treatment_response',
    'Associated symptoms': 'hpc_associated_symptoms',
    'Chronology / narrative': 'hpc_narrative',
    # §4
    'Childhood illnesses': 'past_childhood_illnesses',
    'Major illnesses': 'past_major_illnesses',
    'Hospitalizations': 'past_hospitalizations',
    'Operations / surgeries': 'past_operations',
    'Injuries / accidents': 'past_injuries',
    'Allergies / sensitivities': 'past_allergies',
    'Previous chronic treatment': 'past_chronic_treatment',
    'Other relevant history': 'past_other_history',
    # §5
    'Father': 'family_father',
    'Mother': 'family_mother',
    'Siblings': 'family_siblings',
    'Spouse / Children': 'family_spouse_children',
    'Diabetes / Hypertension': 'family_diabetes_hypertension',
    'Cancer / TB': 'family_cancer_tb',
    'Mental / neurological illness': 'family_mental_illness',
    'Hereditary / constitutional tendencies': 'family_tendencies',
    # §6
    'Diet / appetite': 'habits_diet',
    'Water intake': 'habits_water_intake',
    'Sleep': 'habits_sleep',
    'Dreams': 'habits_dreams',
    'Exercise / activity': 'habits_exercise',
    'Tobacco / nicotine': 'habits_tobacco',
    'Alcohol / substance use': 'habits_alcohol',
    'Caffeine / tea / coffee': 'habits_caffeine',
    'Bowel habit': 'habits_bowel',
    'Urination': 'habits_urination',
    # §7
    'Temperament / disposition': 'mental_temperament',
    'Anxiety / fears': 'mental_anxiety',
    'Anger / irritability': 'mental_anger',
    'Grief / disappointment': 'mental_grief',
    'Jealousy / suspicion': 'mental_jealousy',
    'Company / solitude': 'mental_company',
    'Concentration / memory': 'mental_concentration',
    'Work / responsibility response': 'mental_work',
    'Relationships / social behavior': 'mental_relationships',
    'Other striking mental symptoms': 'mental_other',
    'Important mental generals / exact expressions': 'mental_expressions',
    # §8
    'Thermal state': 'generals_thermal_state',
    'Perspiration': 'generals_perspiration',
    'Appetite': 'generals_appetite',
    'Thirst': 'generals_thirst',
    'Cravings': 'generals_cravings',
    'Aversions': 'generals_aversions',
    'Food intolerances': 'generals_food_intolerances',
    'Energy / vitality': 'generals_energy',
    'Sensitivity to weather': 'generals_weather_sensitivity',
    'Menstrual / hormonal history (where applicable)': 'generals_menstrual',
    'Other physical generals': 'generals_other',
    # §9, the fixed eight-row grid.
    'Factor': (CaseModality, 'factor'),
    'Better': (CaseModality, 'better'),
    'Worse': (CaseModality, 'worse'),
    'Notes / Concomitant': (CaseModality, 'notes'),
    # §10
    'General / constitutional': 'systems_general',
    'Respiratory': 'systems_respiratory',
    'Cardiovascular': 'systems_cardiovascular',
    'Gastrointestinal': 'systems_gastrointestinal',
    'Genitourinary': 'systems_genitourinary',
    'Musculoskeletal': 'systems_musculoskeletal',
    'Neurological': 'systems_neurological',
    'Skin': 'systems_skin',
    'ENT / Eyes': 'systems_ent_eyes',
    'Endocrine': 'systems_endocrine',
    # §12, the growable investigations table.
    'Date': (CaseInvestigation, 'performed_on'),
    'Investigation': (CaseInvestigation, 'name'),
    'Result': (CaseInvestigation, 'result'),
    'Reference / Impression': (CaseInvestigation, 'impression'),
    'Attachment / Report No.': (CaseInvestigation, 'attachment_reference'),
    # §13
    'Provisional diagnosis': 'assessment_provisional',
    'Differential diagnosis': 'assessment_differential',
    'Miasmatic / constitutional assessment': 'assessment_constitutional',
    'Totality of symptoms': 'assessment_totality',
    'Characteristic / peculiar symptoms': 'assessment_characteristic',
    # §14, the growable analysis table.
    'Rubric / Symptom': (CaseAnalysisEntry, 'finding'),
    'Grade': (CaseAnalysisEntry, 'grade'),
    'Candidate Remedy': (CaseAnalysisEntry, 'candidate'),
    'Score / Rank': (CaseAnalysisEntry, 'score'),
    'Remarks': (CaseAnalysisEntry, 'remarks'),
}

#: Prompts on the paper that are deliberately **not** fields, and why. Named
#: rather than silently absent: "we decided not to" and "we forgot" look
#: identical from the outside.
DROPPED = {
    # §6 and §8 ask for these twice, in two framings. On paper the doctor's pen
    # skips the second; in a form it is a box that is permanently empty and
    # permanently ambiguous — a blank cannot be told apart from "asked, nothing
    # to report". They are asked once, in §8, where the answer is used.
    'Food preferences / aversions': 'asked once, in §8 as Cravings and Aversions',
    # §6's "Diet / appetite" keeps the diet half; the appetite half is §8's.
    # §8's sleep prompt folds into §6's one box, which carries its wording.
    'Sleep position / quality': 'asked once, in §6, whose label carries this',
}


def _parse() -> tuple[dict, list]:
    """The document's prompts, and §9's printed row labels, read separately.

    Bullets and table *headers* are both prompts — the paper uses both and the
    difference is a layout choice, not a difference in what is asked. A table's
    *body* rows are not prompts: the only section with any is §9, whose eight
    printed rows are seed data rather than questions, and reading them as
    prompts would demand eight columns that must never exist.
    """
    text = REFERENCE.read_text(encoding='utf-8')
    prompts, printed_rows = {}, []
    section, header_seen = None, False
    for line in text.splitlines():
        heading = re.match(r'^## (\d+)\.', line)
        if heading:
            section, header_seen = heading.group(1), False
            continue
        if section not in COVERED_SECTIONS:
            continue
        bullet = re.match(r'^- (.+)$', line)
        if bullet:
            # "- **Chronology / narrative** — three ruled lines" and
            # "- Thermal state — printed as `Hot / Chilly / Variable`".
            prompt = bullet.group(1).split(' \u2014 ')[0].strip().strip('*')
            prompts[prompt] = section
        elif line.startswith('|') and '---' not in line:
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if not header_seen:
                header_seen = True
                for cell in cells:
                    if cell:
                        prompts[cell] = section
            elif section == '9' and cells[0]:
                printed_rows.append(cells[0])
    return prompts, printed_rows


def _prompts() -> dict:
    return _parse()[0]


def test_the_reference_document_is_where_this_test_thinks_it_is():
    """A vanished document would make every assertion below pass on nothing."""
    assert REFERENCE.exists(), REFERENCE
    prompts = _prompts()
    assert len(prompts) > 70, f'only {len(prompts)} prompts parsed; the parser is wrong'


def test_every_prompt_on_the_paper_has_a_column_or_a_stated_reason():
    unaccounted = sorted(
        prompt
        for prompt in _prompts()
        if prompt not in MAPPING and prompt not in DROPPED
    )

    assert not unaccounted, (
        'these prompts on the paper form map to nothing:\n'
        + '\n'.join(unaccounted)
        + '\n\nAdd a column and an entry in MAPPING, or an entry in DROPPED '
        'saying why not.'
    )


def test_every_mapped_field_actually_exists():
    """The other direction: a mapping that names a column nobody built."""
    missing = []
    for prompt, target in MAPPING.items():
        model, name = target if isinstance(target, tuple) else (CaseRecord, target)
        if name not in {field.name for field in model._meta.get_fields()}:
            missing.append(f'{prompt} -> {model.__name__}.{name}')

    assert not missing, 'MAPPING names fields that do not exist:\n' + '\n'.join(missing)


def test_no_mapping_entry_describes_a_prompt_the_paper_no_longer_asks():
    """Drift in the third direction: the doctor's form changed under us."""
    prompts = _prompts()
    stale = sorted(prompt for prompt in {**MAPPING, **DROPPED} if prompt not in prompts)

    assert not stale, 'these are mapped but no longer on the paper form:\n' + '\n'.join(
        stale
    )


def test_every_prose_column_is_on_the_form_and_in_a_section():
    """A column with no home renders nowhere and saves as empty forever."""
    declared = {name for section in SECTIONS for name in section.fields}
    on_form = set(CaseRecordForm().fields) - {'taken_on'}

    assert declared == on_form


def test_every_prose_column_is_declared_in_a_section():
    """The reverse: a column added to the model and to no section card."""
    skip = {'id', 'organization', 'created_by', 'patient', 'created_at', 'updated_at'}
    columns = {
        field.name
        for field in CaseRecord._meta.get_fields()
        if field.concrete and field.name not in skip
    }
    declared = {'taken_on', *(name for section in SECTIONS for name in section.fields)}

    assert columns == declared, (
        f'columns with no section card: {sorted(columns - declared)}; '
        f'sections naming no column: {sorted(declared - columns)}'
    )


def test_the_fixed_grid_is_the_paper_grid():
    """§9 never varies, so the seed *is* the shape rather than a starting point.

    Read off the document rather than hardcoded, so the eight factors and the
    paper cannot drift apart in silence — the same guarantee the prompt mapping
    above gives the seventy-two prose columns.
    """
    assert list(MODALITY_FACTORS) == _parse()[1]


@pytest.mark.parametrize(
    'model',
    [CaseRecord, CaseComplaint, CaseModality, CaseInvestigation, CaseAnalysisEntry],
)
def test_every_case_model_keeps_its_history(model):
    """Every save writes who changed what, which is what stops a silent overwrite."""
    assert hasattr(model, 'history')
    columns = {field.name for field in model.history.model._meta.fields}
    # Without this column the history cannot be tenant-filtered at all.
    assert 'organization' in columns
