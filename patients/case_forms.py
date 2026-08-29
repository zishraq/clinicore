"""The case record: one parent form, four formsets, one Save.

Seventy-odd prose boxes are rendered by looping over ``SECTIONS`` rather than
by naming fields in a template — the field list and the section layout are one
declaration, so a column added to the model without a home here fails a test
rather than disappearing off the page.

Labels are the paper form's prompts. Where a prompt names a domain concept the
clinic has its own word for, the label comes from ``Organization.terminology``
instead — a hardcoded "Repertorization", "Rubric", "Miasm" or "Remedy" here is
a bug, exactly like a hardcoded "Potency" (ADR 0015). See
docs/adr/0020-the-case-record.md §1.
"""

from django import forms

from core.forms import date_widget
from patients.models import (
    CaseAnalysisEntry,
    CaseComplaint,
    CaseInvestigation,
    CaseModality,
    CaseRecord,
)

__all__ = [
    'PAGE_ORDER',
    'SECTIONS',
    'CaseAnalysisFormSet',
    'CaseComplaintFormSet',
    'CaseInvestigationFormSet',
    'CaseModalityFormSet',
    'CaseRecordForm',
    'page_sections',
]

_INPUT = {'class': 'input input-bordered input-sm w-full'}
_SELECT = {'class': 'select select-bordered w-full'}
_PROSE = {'class': 'textarea textarea-bordered w-full', 'rows': 2}


class Section:
    """One anchored card on the page: its heading and the fields inside it."""

    def __init__(self, anchor: str, title: str, *fields: str):
        self.anchor = anchor
        self.title = title
        self.fields = fields


#: The prose sections of the paper form, in the order it prints them. §2, §9,
#: §12 and §14 are the four tables and are rendered as formsets between these;
#: §11 is per-visit and is not on this form at all; §15 and §16 are the
#: prescription and the visit timeline and are never restated here.
SECTIONS = (
    Section(
        'hpc',
        'History of present complaint',
        'hpc_first_noticed_on',
        'hpc_onset_type',
        'hpc_cause',
        'hpc_progression',
        'hpc_previous_episodes',
        'hpc_treatment_taken',
        'hpc_treatment_response',
        'hpc_associated_symptoms',
        'hpc_narrative',
    ),
    Section(
        'past',
        'Past medical and surgical history',
        'past_childhood_illnesses',
        'past_major_illnesses',
        'past_hospitalizations',
        'past_operations',
        'past_injuries',
        'past_allergies',
        'past_chronic_treatment',
        'past_other_history',
    ),
    Section(
        'family',
        'Family history',
        'family_father',
        'family_mother',
        'family_siblings',
        'family_spouse_children',
        'family_diabetes_hypertension',
        'family_cancer_tb',
        'family_mental_illness',
        'family_tendencies',
    ),
    Section(
        'habits',
        'Personal history and habits',
        'habits_diet',
        'habits_water_intake',
        'habits_sleep',
        'habits_dreams',
        'habits_exercise',
        'habits_tobacco',
        'habits_alcohol',
        'habits_caffeine',
        'habits_bowel',
        'habits_urination',
    ),
    Section(
        'mental',
        'Mental and emotional generals',
        'mental_temperament',
        'mental_anxiety',
        'mental_anger',
        'mental_grief',
        'mental_jealousy',
        'mental_company',
        'mental_concentration',
        'mental_work',
        'mental_relationships',
        'mental_other',
        'mental_expressions',
    ),
    Section(
        'generals',
        'Physical generals',
        'generals_thermal_state',
        'generals_perspiration',
        'generals_appetite',
        'generals_thirst',
        'generals_cravings',
        'generals_aversions',
        'generals_food_intolerances',
        'generals_energy',
        'generals_weather_sensitivity',
        'generals_menstrual',
        'generals_other',
    ),
    Section(
        'systems',
        'System review',
        'systems_general',
        'systems_respiratory',
        'systems_cardiovascular',
        'systems_gastrointestinal',
        'systems_genitourinary',
        'systems_musculoskeletal',
        'systems_neurological',
        'systems_skin',
        'systems_ent_eyes',
        'systems_endocrine',
    ),
    Section(
        'assessment',
        'Clinical assessment',
        'assessment_provisional',
        'assessment_differential',
        'assessment_constitutional',
        'assessment_totality',
        'assessment_characteristic',
    ),
)

#: The paper's prompt for each column. Written out rather than derived from the
#: field name, because "family_cancer_tb" reads back as "Cancer / TB" and no
#: rule produces that. Every one of these is generic; the two that name a
#: domain concept the clinic has its own word for are relabelled in
#: ``CaseRecordForm.__init__`` from ``terminology``.
_LABELS = {
    'taken_on': 'Date the case was taken',
    'hpc_first_noticed_on': 'First noticed on',
    'hpc_onset_type': 'Onset',
    'hpc_cause': 'Possible cause / exciting factor',
    'hpc_progression': 'Progression',
    'hpc_previous_episodes': 'Previous episodes',
    'hpc_treatment_taken': 'Treatment already taken',
    'hpc_treatment_response': 'Response to treatment',
    'hpc_associated_symptoms': 'Associated symptoms',
    'hpc_narrative': 'Chronology / narrative',
    'past_childhood_illnesses': 'Childhood illnesses',
    'past_major_illnesses': 'Major illnesses',
    'past_hospitalizations': 'Hospitalizations',
    'past_operations': 'Operations / surgeries',
    'past_injuries': 'Injuries / accidents',
    'past_allergies': 'Allergies / sensitivities',
    'past_chronic_treatment': 'Previous chronic treatment',
    'past_other_history': 'Other relevant history',
    'family_father': 'Father',
    'family_mother': 'Mother',
    'family_siblings': 'Siblings',
    'family_spouse_children': 'Spouse / children',
    'family_diabetes_hypertension': 'Diabetes / hypertension',
    'family_cancer_tb': 'Cancer / TB',
    'family_mental_illness': 'Mental / neurological illness',
    'family_tendencies': 'Hereditary / constitutional tendencies',
    'habits_diet': 'Diet',
    'habits_water_intake': 'Water intake',
    # Carries §8's prompt too: the paper asks about sleep in both sections and
    # this is the one box (ADR 0020 §6).
    'habits_sleep': 'Sleep — hours, position, quality',
    'habits_dreams': 'Dreams',
    'habits_exercise': 'Exercise / activity',
    'habits_tobacco': 'Tobacco / nicotine',
    'habits_alcohol': 'Alcohol / substance use',
    'habits_caffeine': 'Caffeine / tea / coffee',
    'habits_bowel': 'Bowel habit',
    'habits_urination': 'Urination',
    'mental_temperament': 'Temperament / disposition',
    'mental_anxiety': 'Anxiety / fears',
    'mental_anger': 'Anger / irritability',
    'mental_grief': 'Grief / disappointment',
    'mental_jealousy': 'Jealousy / suspicion',
    'mental_company': 'Company / solitude',
    'mental_concentration': 'Concentration / memory',
    'mental_work': 'Work / responsibility response',
    'mental_relationships': 'Relationships / social behaviour',
    'mental_other': 'Other striking mental symptoms',
    'mental_expressions': 'Important mental generals / exact expressions',
    'generals_thermal_state': 'Thermal state — hot / chilly / variable',
    'generals_perspiration': 'Perspiration',
    'generals_appetite': 'Appetite',
    'generals_thirst': 'Thirst',
    'generals_cravings': 'Cravings',
    'generals_aversions': 'Aversions',
    'generals_food_intolerances': 'Food intolerances',
    'generals_energy': 'Energy / vitality',
    'generals_weather_sensitivity': 'Sensitivity to weather',
    'generals_menstrual': 'Menstrual / hormonal history',
    'generals_other': 'Other physical generals',
    'systems_general': 'General / constitutional',
    'systems_respiratory': 'Respiratory',
    'systems_cardiovascular': 'Cardiovascular',
    'systems_gastrointestinal': 'Gastrointestinal',
    'systems_genitourinary': 'Genitourinary',
    'systems_musculoskeletal': 'Musculoskeletal',
    'systems_neurological': 'Neurological',
    'systems_skin': 'Skin',
    'systems_ent_eyes': 'ENT / eyes',
    'systems_endocrine': 'Endocrine',
    'assessment_provisional': 'Provisional diagnosis',
    'assessment_differential': 'Differential diagnosis',
    'assessment_totality': 'Totality of symptoms',
    'assessment_characteristic': 'Characteristic / peculiar symptoms',
}

#: The prose boxes that want more than two rows: the four the paper prints as
#: ruled free-prose blocks rather than as a single line.
_TALL = frozenset(
    {
        'hpc_narrative',
        'mental_expressions',
        'assessment_totality',
        'assessment_characteristic',
    }
)


#: Every card on the page, in the order the paper prints them, prose sections
#: and tables alike. One declaration, so the jump list and the page cannot
#: disagree about what is on it or what it is called. §11 is absent: it is
#: per-visit rather than per-patient and is not built. §15 and §16 are the
#: prescription and the visit timeline, which the record links to and never
#: restates.
PAGE_ORDER = (
    ('complaints', 'complaint_plural', 'Chief complaints'),
    ('hpc', None, 'History of present complaint'),
    ('past', None, 'Past medical and surgical history'),
    ('family', None, 'Family history'),
    ('habits', None, 'Personal history and habits'),
    ('mental', None, 'Mental and emotional generals'),
    ('generals', None, 'Physical generals'),
    ('modalities', 'modality_plural', 'Modalities and concomitants'),
    ('systems', None, 'System review'),
    ('investigations', 'investigation_plural', 'Investigations and reports'),
    ('assessment', None, 'Clinical assessment'),
    ('analysis', 'case_analysis', 'Case analysis'),
)


def page_sections(organization) -> list:
    """(anchor, heading) for the jump list, with the clinic's own words in it."""
    terms = organization.terms if organization is not None else {}
    return [
        {'anchor': anchor, 'title': terms.get(key, fallback) if key else fallback}
        for anchor, key, fallback in PAGE_ORDER
    ]


class _BoundSection:
    """One rendered card: its heading and the bound fields inside it."""

    def __init__(self, section: Section, fields: list):
        self.anchor = section.anchor
        self.title = section.title
        self.fields = fields


class CaseRecordForm(forms.ModelForm):
    """The prose sections: §3 to §8, §10 and §13. What the parent row holds."""

    class Meta:
        model = CaseRecord
        fields = [
            'taken_on',
            *(name for section in SECTIONS for name in section.fields),
        ]

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        terms = organization.terms if organization is not None else {}
        for name, field in self.fields.items():
            field.label = _LABELS.get(name, field.label)
            if name in ('taken_on', 'hpc_first_noticed_on'):
                # Never type="date": the native control renders its text in the
                # device's locale (ADR 0016).
                field.widget = date_widget()
            elif name == 'hpc_onset_type':
                field.widget = forms.Select(attrs=_SELECT)
            else:
                field.widget = forms.Textarea(
                    attrs={**_PROSE, 'rows': 4 if name in _TALL else 2}
                )
        # The one prose box on this form that names a domain concept the clinic
        # has its own word for. The column is named for what it records; the
        # label is the clinic's.
        self.fields['assessment_constitutional'].label = terms.get(
            'constitutional_assessment', 'Constitutional assessment'
        )

    @property
    def section_map(self) -> dict:
        """Anchor -> the card, so the page can place tables between the prose.

        A dict rather than a list because the four tables interleave with the
        eight prose sections and the paper's order is the product; looping a
        flat list would put every table at the end.
        """
        return {
            section.anchor: _BoundSection(
                section, [self[name] for name in section.fields]
            )
            for section in SECTIONS
        }


class _RowForm(forms.ModelForm):
    """Shared behaviour for the four table rows.

    ``has_changed`` judges a row by its content rather than by Django's default.
    Removing an unsaved row deletes its inputs, so that index posts nothing at
    all and the default reads the missing ``sort_order`` as a filled-in row —
    the same trap the prescription rows hit (clinical/forms.py).
    """

    #: Set by each subclass: the columns that mean somebody typed something.
    CONTENT_FIELDS: tuple = ()

    def has_changed(self) -> bool:
        if self.instance.pk:
            return super().has_changed()
        return any(self.data.get(self.add_prefix(name)) for name in self.CONTENT_FIELDS)


class CaseComplaintForm(_RowForm):
    CONTENT_FIELDS = ('complaint', 'onset', 'duration', 'character', 'intensity')

    class Meta:
        model = CaseComplaint
        fields = ('complaint', 'onset', 'duration', 'character', 'intensity')
        widgets = {name: forms.TextInput(attrs=_INPUT) for name in fields}


class CaseModalityForm(_RowForm):
    """§9's row. ``factor`` is seeded and shown, never typed."""

    CONTENT_FIELDS = ('better', 'worse', 'notes')

    class Meta:
        model = CaseModality
        fields = ('better', 'worse', 'notes')
        widgets = {
            'better': forms.TextInput(attrs=_INPUT),
            'worse': forms.TextInput(attrs=_INPUT),
            'notes': forms.TextInput(attrs=_INPUT),
        }


class CaseInvestigationForm(_RowForm):
    CONTENT_FIELDS = (
        'performed_on',
        'name',
        'result',
        'impression',
        'attachment_reference',
    )

    class Meta:
        model = CaseInvestigation
        fields = (
            'performed_on',
            'name',
            'result',
            'impression',
            'attachment_reference',
        )
        widgets = {
            'performed_on': date_widget(
                **{'class': 'input input-bordered input-sm w-full'}
            ),
            'name': forms.TextInput(attrs=_INPUT),
            'result': forms.TextInput(attrs=_INPUT),
            'impression': forms.TextInput(attrs=_INPUT),
            'attachment_reference': forms.TextInput(attrs=_INPUT),
        }


class CaseAnalysisForm(_RowForm):
    """§14's row. Every label here comes from the map — see the module docstring."""

    CONTENT_FIELDS = ('finding', 'grade', 'candidate', 'score', 'remarks')

    class Meta:
        model = CaseAnalysisEntry
        fields = ('finding', 'grade', 'candidate', 'score', 'remarks')
        widgets = {name: forms.TextInput(attrs=_INPUT) for name in fields}


class _CaseFormSet(forms.BaseInlineFormSet):
    """Carries the organization down so each saved row can be stamped with it."""

    def __init__(self, *args, organization=None, **kwargs):
        self.organization = organization
        super().__init__(*args, **kwargs)

    def add_fields(self, form, index):
        super().add_fields(form, index)
        deletion = form.fields.get(forms.formsets.DELETION_FIELD_NAME)
        if deletion is not None:
            # Driven by the row's Remove button, like the prescription rows.
            deletion.widget = forms.CheckboxInput(
                attrs={'class': 'hidden', 'data-role': 'item-delete'}
            )


class _LabelledFormSet(_CaseFormSet):
    """A formset whose column headings are the clinic's own words."""

    #: field name -> terminology key.
    TERM_LABELS: dict = {}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        terms = organization.terms if organization is not None else {}
        for form in self.forms:
            self._relabel(form, terms)

    def _relabel(self, form, terms) -> None:
        for name, key in self.TERM_LABELS.items():
            if name in form.fields and key in terms:
                form.fields[name].label = terms[key]

    @property
    def empty_form(self):
        form = super().empty_form
        terms = self.organization.terms if self.organization is not None else {}
        self._relabel(form, terms)
        return form


class BaseCaseComplaintFormSet(_LabelledFormSet):
    TERM_LABELS = {'complaint': 'complaint'}


class BaseCaseAnalysisFormSet(_LabelledFormSet):
    TERM_LABELS = {
        'finding': 'finding',
        'grade': 'grade',
        'candidate': 'candidate',
    }


CaseComplaintFormSet = forms.inlineformset_factory(
    CaseRecord,
    CaseComplaint,
    form=CaseComplaintForm,
    formset=BaseCaseComplaintFormSet,
    extra=1,
    can_delete=True,
)

#: Fixed at eight. ``extra=0`` and no delete: the eight rows are seeded with the
#: record and the grid never varies, so an add control would offer a ninth the
#: unique constraint refuses and a delete control would leave a gap nothing
#: refills.
CaseModalityFormSet = forms.inlineformset_factory(
    CaseRecord,
    CaseModality,
    form=CaseModalityForm,
    formset=_CaseFormSet,
    extra=0,
    can_delete=False,
)

CaseInvestigationFormSet = forms.inlineformset_factory(
    CaseRecord,
    CaseInvestigation,
    form=CaseInvestigationForm,
    formset=_CaseFormSet,
    extra=1,
    can_delete=True,
)

CaseAnalysisFormSet = forms.inlineformset_factory(
    CaseRecord,
    CaseAnalysisEntry,
    form=CaseAnalysisForm,
    formset=BaseCaseAnalysisFormSet,
    extra=1,
    can_delete=True,
)
