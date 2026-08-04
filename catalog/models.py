"""Reusable things a practitioner prescribes: substances, and advice.

SPEC §5 planned ``Product`` and stopped there, which models only half of a real
prescription — the other half is advice ("walk 30 minutes daily"), which is not
a substance, has no dosage, and is repeated verbatim across patients.
``AdviceTemplate`` is that half. See docs/adr/0007-catalogs.md.

Catalogs only. Stock lives in the Phase 4 inventory app; ``is_stock_tracked``
and ``is_sellable`` exist now so that app attaches without a schema change.
"""

from decimal import Decimal

from django.db import models

from core.models import OrgOwnedModel

__all__ = ['AdviceCategory', 'AdviceTemplate', 'Product']


class AdviceCategory(models.TextChoices):
    """Generic on purpose — nothing specialty-specific in code (SPEC §1)."""

    DIET = 'DIET', 'Diet'
    EXERCISE = 'EXERCISE', 'Exercise'
    SLEEP = 'SLEEP', 'Sleep'
    LIFESTYLE = 'LIFESTYLE', 'Lifestyle'
    OTHER = 'OTHER', 'Other'


class Product(OrgOwnedModel):
    """A substance or good: medicine, consumable, supplement, retail item."""

    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, blank=True)
    # Free text, not choices: category vocabulary is per-specialty and must
    # stay configuration rather than a code branch.
    category = models.CharField(max_length=100, blank=True, db_index=True)
    unit = models.CharField(
        max_length=50, blank=True, help_text='Tablet, capsule, ml, drops, …'
    )
    # Specialty defaults (potency, dilution, …) copied onto each prescribed item.
    default_attributes = models.JSONField(default=dict, blank=True)
    # The current asking price. A billed line copies it into its own column at
    # issue time, so repricing here never rewrites a receipt already printed.
    sale_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Prefilled when this is added to a bill.',
    )
    is_stock_tracked = models.BooleanField(
        default=False, help_text='Reserved for inventory; no stock is tracked yet.'
    )
    is_sellable = models.BooleanField(
        default=False,
        help_text='False for things recommended but not dispensed here.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'sku'],
                condition=~models.Q(sku=''),
                name='product_sku_unique_per_org',
            )
        ]
        indexes = [models.Index(fields=['organization', 'name'])]

    def __str__(self) -> str:
        return self.name

    @property
    def prescribing_name(self) -> str:
        """What gets written onto a prescription item."""
        return self.name


class AdviceTemplate(OrgOwnedModel):
    """A reusable non-substance instruction. Deliberately small."""

    text = models.TextField()
    category = models.CharField(
        max_length=20, choices=AdviceCategory.choices, default=AdviceCategory.OTHER
    )
    default_frequency = models.CharField(max_length=100, blank=True)
    default_duration = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'text']
        indexes = [models.Index(fields=['organization', 'category'])]

    def __str__(self) -> str:
        return self.prescribing_name

    @property
    def prescribing_name(self) -> str:
        """First line of the advice; the full text is the item's instructions."""
        return self.text.strip().splitlines()[0] if self.text.strip() else ''
