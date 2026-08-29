"""Patient demographics, kept separate from the clinical narrative.

The split is an access-control boundary, not tidiness: STAFF may read and edit
``Patient`` but never ``PatientClinicalProfile`` (SPEC §6.1).
"""

from django.db import models
from django.utils import timezone

from core.managers import AliveOrgScopedManager
from core.models import OrgOwnedModel, SoftDeleteModel
from patients.phone import dial_string

__all__ = ['MaritalStatus', 'Patient', 'PatientClinicalProfile', 'Sex']


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


class PatientClinicalProfile(OrgOwnedModel):
    """PRACTITIONER/OWNER only. Never rendered on a STAFF request."""

    patient = models.OneToOneField(
        Patient, on_delete=models.CASCADE, related_name='clinical_profile'
    )
    medical_history = models.TextField(blank=True)
    allergies = models.TextField(blank=True)

    class Meta:
        verbose_name = 'patient clinical profile'

    def __str__(self) -> str:
        return f'Clinical profile — {self.patient.full_name}'
