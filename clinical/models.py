"""Encounters and prescriptions.

Prescription items are free text: there is no catalog in the MVP, so
``PrescriptionItem`` carries ``free_text_name`` and gains a nullable ``product``
FK when the catalog app lands (docs/MVP-NOTES.md).
"""

from django.conf import settings
from django.db import models

from core.models import OrgOwnedModel

__all__ = [
    'Encounter',
    'EncounterStatus',
    'Prescription',
    'PrescriptionItem',
    'PrintSize',
]


class EncounterStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    FINALIZED = 'FINALIZED', 'Finalized'


class PrintSize(models.TextChoices):
    A5 = 'A5', 'A5'
    A4 = 'A4', 'A4'


class Encounter(OrgOwnedModel):
    patient = models.ForeignKey(
        'patients.Patient', on_delete=models.PROTECT, related_name='encounters'
    )
    practitioner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='encounters'
    )
    branch = models.ForeignKey(
        'organizations.Branch', on_delete=models.PROTECT, related_name='encounters'
    )
    occurred_at = models.DateTimeField()
    chief_complaint = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    assessment = models.TextField(blank=True)
    plan = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=EncounterStatus.choices, default=EncounterStatus.DRAFT
    )
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['organization', '-occurred_at'])]

    def __str__(self) -> str:
        return f'{self.patient.full_name} — {self.occurred_at:%d %b %Y}'

    @property
    def is_editable(self) -> bool:
        """MVP: finalized encounters are read-only.

        SPEC §6.4 wants amendments recorded as history entries via
        django-simple-history; that library is not installed tonight, so the
        MVP locks the record instead of silently overwriting it.
        """
        return self.status == EncounterStatus.DRAFT


class Prescription(OrgOwnedModel):
    encounter = models.OneToOneField(
        Encounter, on_delete=models.CASCADE, related_name='prescription'
    )
    general_instructions = models.TextField(blank=True)
    print_size = models.CharField(
        max_length=2, choices=PrintSize.choices, default=PrintSize.A5
    )
    issued_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f'Prescription — {self.encounter.patient.full_name}'


class PrescriptionItem(OrgOwnedModel):
    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name='items'
    )
    free_text_name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100, blank=True)
    frequency = models.CharField(max_length=100, blank=True)
    duration = models.CharField(max_length=100, blank=True)
    instructions = models.TextField(blank=True)
    # Specialty-specific values (potency, dilution, …) stay data, never code.
    attributes = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self) -> str:
        return self.free_text_name
