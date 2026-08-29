"""Patient demographics, and the case record that hangs off them.

The split is an access-control boundary, not tidiness: STAFF may read and edit
``Patient`` — every column on it is a demographic the desk takes — but never
``CaseRecord`` or its four child tables (SPEC §6.1, ADR 0020 §10).
"""

from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from core.managers import AliveOrgScopedManager
from core.models import OrgOwnedModel, SoftDeleteModel
from patients.phone import dial_string

__all__ = [
    'MODALITY_FACTORS',
    'CaseAnalysisEntry',
    'CaseComplaint',
    'CaseInvestigation',
    'CaseModality',
    'CaseRecord',
    'MaritalStatus',
    'OnsetType',
    'Patient',
    'Sex',
]


class Sex(models.TextChoices):
    FEMALE = 'F', 'Female'
    MALE = 'M', 'Male'
    OTHER = 'O', 'Other'
    UNKNOWN = 'U', 'Not recorded'


class MaritalStatus(models.TextChoices):
    """Mirrors ``Sex`` exactly, down to the "not recorded" default.

    A demographic the desk takes, so it has the same shape as the other one:
    single-character stored values that never move, and an explicit *unknown*
    rather than a blank — a blank cannot be told apart from a question nobody
    asked, which is the ambiguity the day list refuses when it prints "No bill".
    """

    SINGLE = 'S', 'Single'
    MARRIED = 'M', 'Married'
    WIDOWED = 'W', 'Widowed'
    DIVORCED = 'D', 'Divorced'
    OTHER = 'O', 'Other'
    UNKNOWN = 'U', 'Not recorded'


class Patient(OrgOwnedModel, SoftDeleteModel):
    code = models.CharField(max_length=20)
    full_name = models.CharField(max_length=200)
    date_of_birth = models.DateField(null=True, blank=True)
    approx_age_years = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text='Use when the patient does not know their date of birth.',
    )
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    # The second number a patient gives — a spouse's, a neighbour's shop.
    # Indexed and searched, which is the whole reason it is a column rather
    # than a line in ``address``: a number the search cannot find is worse than
    # no second number, because reception concludes the patient is not
    # registered and creates a duplicate. See docs/adr/0020-the-case-record.md.
    alt_phone = models.CharField(max_length=32, blank=True, db_index=True)
    # Recorded and displayed, never sent to. There is no SMTP on this box and
    # ADR 0013 settled that no recovery path will assume one, so this is a
    # contact detail and building on it later is a decision rather than an
    # increment.
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    marital_status = models.CharField(
        max_length=1, choices=MaritalStatus.choices, default=MaritalStatus.UNKNOWN
    )
    occupation = models.CharField(max_length=100, blank=True)
    # Two columns rather than one box, because the entire point of an emergency
    # contact is dialling it quickly and a combined free-text field cannot
    # produce a ``tel:`` href.
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)
    # Free text, not a relation. Whoever sent the patient is as often a former
    # patient, a pharmacist or a neighbour as anyone this system knows about.
    referred_by = models.CharField(max_length=200, blank=True)
    registered_branch = models.ForeignKey(
        'organizations.Branch',
        on_delete=models.PROTECT,
        related_name='patients',
        null=True,
        blank=True,
    )

    objects = AliveOrgScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['full_name']
        base_manager_name = 'all_objects'
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'code'], name='patient_code_unique_per_org'
            ),
            # A fabricated date of birth poisons every age calculation, so the
            # two are mutually exclusive rather than one filling in for the other.
            models.CheckConstraint(
                condition=(
                    models.Q(date_of_birth__isnull=True)
                    | models.Q(approx_age_years__isnull=True)
                ),
                name='patient_dob_xor_approx_age',
            ),
        ]
        indexes = [models.Index(fields=['organization', 'full_name'])]

    def __str__(self) -> str:
        return f'{self.full_name} ({self.code})'

    @property
    def age_display(self) -> str:
        """Age from date of birth when known, otherwise the recorded estimate."""
        if self.date_of_birth:
            today = timezone.localdate()
            born = self.date_of_birth
            years = today.year - born.year
            if (today.month, today.day) < (born.month, born.day):
                years -= 1
            return f'{years} yrs'
        if self.approx_age_years is not None:
            return f'~{self.approx_age_years} yrs'
        return '—'

    @property
    def dial(self) -> str:
        """``phone`` with its separators removed, for a ``tel:`` href.

        A property rather than a template filter because ``phone`` is a free
        text field — "01712 345678" and "(017) 12345678" are both things a
        receptionist types — and a ``tel:`` link built from the raw value is
        unreliable. Display still shows what was typed.
        """
        return dial_string(self.phone)

    @property
    def alt_dial(self) -> str:
        """The second number, ready for a ``tel:`` href. See ``dial``."""
        return dial_string(self.alt_phone)

    @property
    def emergency_dial(self) -> str:
        """The emergency contact's number, ready for a ``tel:`` href."""
        return dial_string(self.emergency_contact_phone)


# --- The case record -------------------------------------------------------
#
# One structured clinical document per patient, from the sixteen-section paper
# form transcribed in docs/reference/case-taking-form.md. Read by section
# comment rather than by scrolling: the prompts are the product, so each is its
# own column rather than one textarea per section.
#
# Every column is named for what it records, and the clinic's own word for it
# comes from ``Organization.terminology``. A specialty term in a field name, a
# template or a form here is a bug — see docs/adr/0020-the-case-record.md §1
# and patients/tests/test_case_naming.py, which enforces it.


class OnsetType(models.TextChoices):
    """§3's Sudden / Gradual. The only closed list on the parent record."""

    SUDDEN = 'SUDDEN', 'Sudden'
    GRADUAL = 'GRADUAL', 'Gradual'


#: §9's eight printed rows, in the order the paper prints them. A column rather
#: than a choices list on the model, so a clinic wanting a ninth factor is a
#: seed change and not a migration. Generic by construction: every one of these
#: is a thing that makes a complaint better or worse in any tradition.
MODALITY_FACTORS = (
    'Time',
    'Position',
    'Motion / Rest',
    'Temperature',
    'Weather',
    'Food / Drink',
    'Pressure / Touch',
    'Other',
)


class CaseRecord(OrgOwnedModel):
    """The whole case, one per patient, revised rather than re-taken.

    ``OneToOne`` because the paper is one sheet per patient and the doctor's
    model is "the case", singular. A later complaint is a visit, and a row in
    §2 if it belongs in the case; ``simple_history`` already answers the only
    question an episode split would buy, which is what this said in 2027. What
    would reopen it, and the small migration it would take, are recorded in
    docs/adr/0020-the-case-record.md §5.

    PRACTITIONER / OWNER / DEVELOPER only, enforced at the view boundary.
    """

    patient = models.OneToOneField(
        Patient, on_delete=models.CASCADE, related_name='case_record'
    )
    # When the case was *taken*, which is not when it was typed in: a clinic
    # entering a paper case from two years ago needs to be able to say so.
    # ``created_at`` answers the other question.
    taken_on = models.DateField(null=True, blank=True)

    # §3 History of present complaint
    hpc_first_noticed_on = models.DateField(null=True, blank=True)
    hpc_onset_type = models.CharField(
        max_length=8, choices=OnsetType.choices, blank=True
    )
    hpc_cause = models.TextField(blank=True)
    hpc_progression = models.TextField(blank=True)
    hpc_previous_episodes = models.TextField(blank=True)
    hpc_treatment_taken = models.TextField(blank=True)
    hpc_treatment_response = models.TextField(blank=True)
    hpc_associated_symptoms = models.TextField(blank=True)
    hpc_narrative = models.TextField(blank=True)

    # §4 Past medical and surgical history. ``past_allergies`` and
    # ``past_other_history`` are where PatientClinicalProfile's two fields
    # landed when it was absorbed — see migration 0004.
    past_childhood_illnesses = models.TextField(blank=True)
    past_major_illnesses = models.TextField(blank=True)
    past_hospitalizations = models.TextField(blank=True)
    past_operations = models.TextField(blank=True)
    past_injuries = models.TextField(blank=True)
    past_allergies = models.TextField(blank=True)
    past_chronic_treatment = models.TextField(blank=True)
    past_other_history = models.TextField(blank=True)

    # §5 Family history
    family_father = models.TextField(blank=True)
    family_mother = models.TextField(blank=True)
    family_siblings = models.TextField(blank=True)
    family_spouse_children = models.TextField(blank=True)
    family_diabetes_hypertension = models.TextField(blank=True)
    family_cancer_tb = models.TextField(blank=True)
    family_mental_illness = models.TextField(blank=True)
    family_tendencies = models.TextField(blank=True)

    # §6 Personal history and habits. Appetite, thirst, cravings, aversions and
    # food intolerances are asked once, in §8 — the paper asks them twice in two
    # framings, which on a screen is one box that is permanently empty and
    # permanently ambiguous (ADR 0020 §6). Sleep is the reverse: one box here,
    # labelled to carry §8's prompt.
    habits_diet = models.TextField(blank=True)
    habits_water_intake = models.TextField(blank=True)
    habits_sleep = models.TextField(blank=True)
    habits_dreams = models.TextField(blank=True)
    habits_exercise = models.TextField(blank=True)
    habits_tobacco = models.TextField(blank=True)
    habits_alcohol = models.TextField(blank=True)
    habits_caffeine = models.TextField(blank=True)
    habits_bowel = models.TextField(blank=True)
    habits_urination = models.TextField(blank=True)

    # §7 Mental and emotional generals
    mental_temperament = models.TextField(blank=True)
    mental_anxiety = models.TextField(blank=True)
    mental_anger = models.TextField(blank=True)
    mental_grief = models.TextField(blank=True)
    mental_jealousy = models.TextField(blank=True)
    mental_company = models.TextField(blank=True)
    mental_concentration = models.TextField(blank=True)
    mental_work = models.TextField(blank=True)
    mental_relationships = models.TextField(blank=True)
    mental_other = models.TextField(blank=True)
    mental_expressions = models.TextField(blank=True)

    # §8 Physical generals. ``generals_thermal_state`` is free text and it is
    # the one place drift is knowingly accepted: the paper prints it as a closed
    # list, and a TextChoices of those three values would be a specialty list in
    # code. "chilly" and "Chilly" will both appear. If it ever needs querying,
    # the fix is an org-editable options column plus ``core.forms.closed_choices``,
    # which is already built and sitting there (ADR 0017).
    generals_thermal_state = models.TextField(blank=True)
    generals_perspiration = models.TextField(blank=True)
    generals_appetite = models.TextField(blank=True)
    generals_thirst = models.TextField(blank=True)
    generals_cravings = models.TextField(blank=True)
    generals_aversions = models.TextField(blank=True)
    generals_food_intolerances = models.TextField(blank=True)
    generals_energy = models.TextField(blank=True)
    generals_weather_sensitivity = models.TextField(blank=True)
    generals_menstrual = models.TextField(blank=True)
    generals_other = models.TextField(blank=True)

    # §10 System review
    systems_general = models.TextField(blank=True)
    systems_respiratory = models.TextField(blank=True)
    systems_cardiovascular = models.TextField(blank=True)
    systems_gastrointestinal = models.TextField(blank=True)
    systems_genitourinary = models.TextField(blank=True)
    systems_musculoskeletal = models.TextField(blank=True)
    systems_neurological = models.TextField(blank=True)
    systems_skin = models.TextField(blank=True)
    systems_ent_eyes = models.TextField(blank=True)
    systems_endocrine = models.TextField(blank=True)

    # §13 Clinical assessment. ``assessment_constitutional`` is the paper's
    # "Miasmatic / constitutional assessment" — the neutral word is on the paper
    # itself, and the clinic's own is a terminology override.
    assessment_provisional = models.TextField(blank=True)
    assessment_differential = models.TextField(blank=True)
    assessment_constitutional = models.TextField(blank=True)
    assessment_totality = models.TextField(blank=True)
    assessment_characteristic = models.TextField(blank=True)

    # Historical rows are NOT organization-scoped; never query .history without
    # filtering by organization. See docs/adr/0006-encounter-amendments.md.
    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Case record — {self.patient.full_name}'

    @property
    def leading_complaints(self) -> str:
        """The top complaints, for the one line the patient page shows.

        The card's summary has to answer a question at a glance, the way the
        bills card's outstanding total does. Empty string when there are none,
        so the template decides what "nothing yet" looks like.
        """
        names = [row.complaint for row in self.complaints.all()[:3] if row.complaint]
        return ', '.join(names)


class CaseChild(OrgOwnedModel):
    """Shared shape for the four repeating tables of the case record."""

    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ['sort_order', 'id']


class CaseComplaint(CaseChild):
    """§2, growable. What the patient came in about, one row per complaint."""

    case_record = models.ForeignKey(
        CaseRecord, on_delete=models.CASCADE, related_name='complaints'
    )
    complaint = models.CharField(max_length=200, blank=True)
    onset = models.CharField(max_length=100, blank=True)
    duration = models.CharField(max_length=100, blank=True)
    character = models.CharField(max_length=200, blank=True)
    intensity = models.CharField(max_length=100, blank=True)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    def __str__(self) -> str:
        return self.complaint or 'Complaint'


class CaseModality(CaseChild):
    """§9, fixed at eight rows seeded with the record.

    A child table rather than twenty-four columns on the parent, so this section
    renders and saves like the other three tables rather than being the one that
    is different — and so a ninth factor is seed data rather than a migration.
    The fixed shape is expressed as ``extra=0``, no add control and no delete
    control.
    """

    case_record = models.ForeignKey(
        CaseRecord, on_delete=models.CASCADE, related_name='modalities'
    )
    factor = models.CharField(max_length=100)
    better = models.CharField(max_length=200, blank=True)
    worse = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta(CaseChild.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=['case_record', 'factor'],
                name='case_modality_unique_per_record',
            )
        ]

    def __str__(self) -> str:
        return self.factor


class CaseInvestigation(CaseChild):
    """§12, growable. A test and what it said.

    ``attachment_reference`` is free text, not a file: patient-level attachments
    are SPEC §5's unbuilt ``Attachment`` model, and ``EncounterPhoto`` is
    per-visit by design (ADR 0014). This records the report number and where the
    paper is filed.
    """

    case_record = models.ForeignKey(
        CaseRecord, on_delete=models.CASCADE, related_name='investigations'
    )
    performed_on = models.DateField(null=True, blank=True)
    name = models.CharField(max_length=200, blank=True)
    result = models.TextField(blank=True)
    impression = models.TextField(blank=True)
    attachment_reference = models.CharField(max_length=100, blank=True)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    def __str__(self) -> str:
        return self.name or 'Investigation'


class CaseAnalysisEntry(CaseChild):
    """§14, growable. A worked shortlist: findings, and what they point to.

    ``candidate`` is free text rather than an FK to ``catalog.Product``. The
    analysis is a scratchpad that names candidates the clinic does not stock,
    and an FK would PROTECT a product because somebody once considered it.
    """

    case_record = models.ForeignKey(
        CaseRecord, on_delete=models.CASCADE, related_name='analysis_entries'
    )
    finding = models.CharField(max_length=300, blank=True)
    grade = models.CharField(max_length=20, blank=True)
    candidate = models.CharField(max_length=200, blank=True)
    score = models.CharField(max_length=20, blank=True)
    remarks = models.TextField(blank=True)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    def __str__(self) -> str:
        return self.finding or 'Analysis entry'
