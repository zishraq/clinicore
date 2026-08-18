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
from django.db.models.functions import Lower

from core.models import OrgOwnedModel
from organizations.models import STRENGTH_MAX_LENGTH

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
    # How strong this preparation usually is — "30C", "500mg", "1:10". Prefills
    # the prescription row and stays editable there, because the same remedy is
    # prescribed at different strengths to different patients. Only meaningful
    # where the organization has ``strength_enabled``; see
    # docs/adr/0015-prescribed-strength.md.
    default_strength = models.CharField(max_length=STRENGTH_MAX_LENGTH, blank=True)
    # Specialty defaults copied onto each prescribed item. Strength has its own
    # column above: it is prescribing data that gets printed and read by a
    # patient, not metadata. This stays for values that really are arbitrary.
    default_attributes = models.JSONField(default=dict, blank=True)
    # The current asking price. A billed line copies it into its own column at
    # issue time, so repricing here never rewrites a receipt already printed.
    sale_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Prefilled when this is added to a bill.',
    )
    # Stays False. Quick-add happens with a patient in the room and nobody is
    # booking in a goods receipt at that moment, so a product that defaulted to
    # tracked would start life with a ledger nothing has ever posted to.
    is_stock_tracked = models.BooleanField(
        default=False, help_text='Count this product in and out of stock.'
    )
    # Only meaningful when the product is stock tracked. Zero disables the
    # alert rather than meaning "warn at nothing", which is what a clinic that
    # has not thought about a level yet actually wants.
    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Warn when stock falls to or below this. Zero means no alert.',
    )
    # Defaults True because the clinic sells most of what it prescribes, and
    # because this is what quick-add gets: a medicine created mid-consultation
    # with the default off never reached the bill raised from that same visit,
    # which read as the bill being broken. "Recommended, bought elsewhere" is
    # the exception, so it is the box you tick rather than the one you forget.
    # A default for new rows only — existing products keep their flags.
    is_sellable = models.BooleanField(
        default=True,
        help_text='Untick for things recommended but not dispensed here.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'sku'],
                condition=~models.Q(sku=''),
                name='product_sku_unique_per_org',
            ),
            # One row per medicine per clinic, and case-insensitively so, because
            # that is the lookup quick-add matches on: without this, two
            # practitioners typing "Paracetamol 500mg" and "paracetamol 500mg"
            # mid-consultation fork the catalog, and each new prescription then
            # points at whichever half its author happened to reach.
            models.UniqueConstraint(
                Lower('name'),
                'organization',
                name='product_name_unique_per_org',
            ),
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
        constraints = [
            # Same reasoning as ``Product``: advice is repeated near-verbatim
            # across patients, so a second copy of one sentence is a fork of the
            # catalog rather than a second piece of advice.
            models.UniqueConstraint(
                Lower('text'),
                'organization',
                name='advice_text_unique_per_org',
            ),
        ]
        indexes = [models.Index(fields=['organization', 'category'])]

    def __str__(self) -> str:
        return self.prescribing_name

    @property
    def prescribing_name(self) -> str:
        """First line of the advice; the full text is the item's instructions."""
        return self.text.strip().splitlines()[0] if self.text.strip() else ''
