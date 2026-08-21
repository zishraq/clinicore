"""Encounters and prescriptions.

Prescription items are free text: there is no catalog in the MVP, so
``PrescriptionItem`` carries ``free_text_name`` and gains a nullable ``product``
FK when the catalog app lands (docs/MVP-NOTES.md).
"""

from uuid import uuid4

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import OrgOwnedModel
from organizations.models import PRESCRIBING_FIELDS, PRESCRIBING_MAX_LENGTH

__all__ = [
    'Encounter',
    'EncounterPhoto',
    'EncounterStatus',
    'ItemType',
    'Prescription',
    'PrescriptionItem',
    'PrintSize',
    'encounter_photo_path',
]


class ItemType(models.TextChoices):
    """A prescription has two halves and they behave differently."""

    MEDICATION = 'MEDICATION', 'Medication'
    ADVICE = 'ADVICE', 'Advice'


class EncounterStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    FINALIZED = 'FINALIZED', 'Finalized'
    AMENDED = 'AMENDED', 'Amended'


#: Statuses that lock the record: editing one is an amendment and needs a reason.
LOCKED_STATUSES = frozenset({EncounterStatus.FINALIZED, EncounterStatus.AMENDED})


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
    # The day-list row this visit was written from, when there was one. Nullable
    # and one-to-one: a visit needs no appointment to be valid — the doctor can
    # simply write one — but two visits off a single row would make "was this
    # appointment seen?" ambiguous.
    appointment = models.OneToOneField(
        'scheduling.Appointment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='encounter',
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
    amended_at = models.DateTimeField(null=True, blank=True)

    # Historical rows are NOT organization-scoped; never query .history without
    # filtering by organization. See docs/adr/0006-encounter-amendments.md.
    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta:
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['organization', '-occurred_at'])]

    def __str__(self) -> str:
        return f'{self.patient.full_name} — {self.occurred_at:%d %b %Y}'

    @property
    def is_locked(self) -> bool:
        """Finalized or amended: further edits are amendments and need a reason."""
        return self.status in LOCKED_STATUSES

    @property
    def is_editable(self) -> bool:
        """Everything is editable by an authorized user (SPEC §6.4).

        A locked encounter is not read-only — it is *append-only*: the edit is
        recorded as a history entry with a reason rather than overwriting
        silently. ``is_locked`` is what decides whether a reason is required.
        """
        return True


def encounter_photo_path(instance, filename: str) -> str:
    """Where an uploaded photograph is stored, ignoring what it was called.

    ``filename`` is user-supplied and discarded on purpose: it is the one part
    of an upload an attacker fully controls, and a caption is where a human
    name for the picture belongs. The organization id in the path is for
    operations — per-tenant disk usage, a targeted restore, an attributable
    stray file — and is **never** access control. Nothing serves this path;
    ``clinical.views.encounter_photo`` is the only way to read the bytes.
    """
    return (
        f'encounters/{instance.organization_id}/{instance.encounter_id}/'
        f'{uuid4().hex}.jpg'
    )


class EncounterPhoto(OrgOwnedModel):
    """A photograph taken during a visit: the patient, or a document they brought.

    Deliberately not SPEC §5's ``Attachment``. That is a wider thing — files on
    a patient *or* an encounter, with an access level — and this one holds an
    ``ImageField`` whose validation refuses PDFs outright, so the wider name
    would promise lab-report attachments the model cannot accept. The
    user-facing word comes from the ``photo`` terminology key, so a clinic that
    says "Images" or "Documents" relabels without any of this moving.
    """

    encounter = models.ForeignKey(
        Encounter, on_delete=models.CASCADE, related_name='photos'
    )
    # Always JPEG, always a generated name; see encounter_photo_path and
    # clinical/images.py. Deleting a row does not delete the file — Django
    # stopped doing that in 1.3 — so removal goes through
    # services.delete_photo, which does both.
    image = models.ImageField(upload_to=encounter_photo_path, max_length=200)
    caption = models.CharField(max_length=140, blank=True)

    class Meta:
        # Oldest first: a set of photographs reads in the order they were taken,
        # which for a multi-page document is the page order.
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['encounter', 'created_at'])]

    def __str__(self) -> str:
        return self.caption or f'Photo {self.pk}'


class Prescription(OrgOwnedModel):
    encounter = models.OneToOneField(
        Encounter, on_delete=models.CASCADE, related_name='prescription'
    )
    general_instructions = models.TextField(blank=True)
    print_size = models.CharField(
        max_length=2, choices=PrintSize.choices, default=PrintSize.A5
    )
    issued_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    def __str__(self) -> str:
        return f'Prescription — {self.encounter.patient.full_name}'


class PrescriptionItem(OrgOwnedModel):
    """One prescribed line: a medicine, or a piece of advice.

    The source is exactly one of a catalog ``product``, a catalog
    ``advice_template``, or ``free_text_name``, and it must agree with
    ``item_type`` — enforced by a check constraint, not by convention.
    """

    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name='items'
    )
    item_type = models.CharField(
        max_length=12, choices=ItemType.choices, default=ItemType.MEDICATION
    )
    product = models.ForeignKey(
        'catalog.Product',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='prescription_items',
    )
    advice_template = models.ForeignKey(
        'catalog.AdviceTemplate',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='prescription_items',
    )
    free_text_name = models.CharField(max_length=200, blank=True)
    # What was actually prescribed, frozen at save time. Printed and historical
    # prescriptions must never resolve the name through the live catalog row:
    # renaming or deactivating a product cannot be allowed to rewrite history.
    name_snapshot = models.CharField(max_length=300)
    # How strong the preparation is — "30C", "500mg" — as opposed to how much
    # of it to take, which is ``dosage``. Two facts, and a clinic that
    # prescribes potencies was putting both in ``dosage`` for want of anywhere
    # else. Blank rather than null: unlike a dose, a missing strength is
    # ordinary for a clinic that does not record them at all, and the whole
    # column is hidden by ``Organization.strength_enabled`` when it does not.
    # See docs/adr/0015-prescribed-strength.md.
    strength = models.CharField(max_length=PRESCRIBING_MAX_LENGTH, blank=True)
    # How much of the preparation goes home: "2D", "1 ounce", "strip of 10".
    # A string, and deliberately not called ``quantity`` — that word already
    # means a Decimal that arithmetic is done on, on a bill line and on a stock
    # movement, and nothing here is a number in a unit this system knows.
    # Nothing decrements stock from it either: the invoice is the stock event
    # (ADR 0009), and a second path off the prescription would move the same box
    # twice. See docs/adr/0017-dispensing-details.md.
    pack_size = models.CharField(max_length=PRESCRIBING_MAX_LENGTH, blank=True)
    # The physical preparation dispensed — globules, a liquid, a cream. Not
    # ``Product.unit``, which is the noun a stock count is measured in and is
    # one value per catalog row: the same remedy goes out as globules for one
    # patient and as liquid for the next, so this belongs to the prescription.
    preparation = models.CharField(max_length=PRESCRIBING_MAX_LENGTH, blank=True)
    # Null for advice: advice has no dose, and an empty string would read as
    # "no dose recorded" rather than "not applicable".
    dosage = models.CharField(max_length=100, blank=True, null=True)  # noqa: DJ001
    frequency = models.CharField(max_length=100, blank=True)
    duration = models.CharField(max_length=100, blank=True)
    instructions = models.TextField(blank=True)
    # Specialty-specific values (potency, dilution, …) stay data, never code.
    attributes = models.JSONField(default=dict, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    history = HistoricalRecords(
        excluded_fields=['created_at', 'updated_at'],
        related_name='history_rows',
    )

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(item_type=ItemType.MEDICATION)
                        & models.Q(advice_template__isnull=True)
                        & (
                            (
                                models.Q(product__isnull=False)
                                & models.Q(free_text_name='')
                            )
                            | (
                                models.Q(product__isnull=True)
                                & ~models.Q(free_text_name='')
                            )
                        )
                    )
                    | (
                        models.Q(item_type=ItemType.ADVICE)
                        & models.Q(product__isnull=True)
                        & (
                            (
                                models.Q(advice_template__isnull=False)
                                & models.Q(free_text_name='')
                            )
                            | (
                                models.Q(advice_template__isnull=True)
                                & ~models.Q(free_text_name='')
                            )
                        )
                    )
                ),
                name='prescription_item_one_source_matching_type',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(item_type=ItemType.ADVICE) | models.Q(dosage__isnull=True)
                ),
                name='prescription_item_advice_has_no_dosage',
            ),
            # Same reasoning as the dosage rule above: advice is not a
            # substance, so it has no strength, no size and no preparation.
            # Asserted here rather than left to ``save()``, so the rule holds
            # for every write path. Generated from the same tuple that drives
            # the switches and the form, so a fourth optional field cannot
            # arrive without its constraint.
            *[
                models.CheckConstraint(
                    condition=(
                        ~models.Q(item_type=ItemType.ADVICE)
                        | models.Q(**{field.key: ''})
                    ),
                    name=f'prescription_item_advice_has_no_{field.key}',
                )
                for field in PRESCRIBING_FIELDS
            ],
        ]

    def __str__(self) -> str:
        return self.name_snapshot

    @property
    def is_advice(self) -> bool:
        return self.item_type == ItemType.ADVICE

    @property
    def source(self):
        """The catalog row behind this item, or None for free text."""
        return self.advice_template if self.is_advice else self.product

    def resolve_name(self) -> str:
        """The name to freeze into ``name_snapshot``."""
        source = self.source
        if source is not None:
            return source.prescribing_name[:300]
        return self.free_text_name[:300]

    def save(self, *args, **kwargs):
        if self.is_advice:
            self.dosage = None
            for field in PRESCRIBING_FIELDS:
                setattr(self, field.key, '')
        self.name_snapshot = self.resolve_name()
        if self.product_id and not self.attributes:
            self.attributes = dict(self.product.default_attributes or {})
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            # Callers passing update_fields must not silently skip the snapshot.
            kwargs['update_fields'] = {
                *update_fields,
                'name_snapshot',
                'dosage',
                *(field.key for field in PRESCRIBING_FIELDS),
            }
        super().save(*args, **kwargs)
