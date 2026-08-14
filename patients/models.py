"""Patient demographics, kept separate from the clinical narrative.

The split is an access-control boundary, not tidiness: STAFF may read and edit
``Patient`` but never ``PatientClinicalProfile`` (SPEC §6.1).
"""

from django.db import models
from django.utils import timezone

from core.managers import AliveOrgScopedManager
from core.models import OrgOwnedModel, SoftDeleteModel
from patients.phone import dial_string

__all__ = ['Patient', 'PatientClinicalProfile', 'Sex']


class Sex(models.TextChoices):
    FEMALE = 'F', 'Female'
    MALE = 'M', 'Male'
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
    address = models.TextField(blank=True)
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
