"""Tenant root and its physical locations."""

import re
from decimal import Decimal

from django.db import models
from django.utils.text import slugify

from core.models import OrgOwnedModel, TimeStampedModel

__all__ = [
    'DEFAULT_TERMINOLOGY',
    'Branch',
    'Organization',
    'default_branding',
    'default_terminology',
    'hex_color_or',
]

# Branding is org-editable JSON that ends up inside a <style> block, so colours
# are validated rather than escaped — escaping does not protect inside CSS.
_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def hex_color_or(value, fallback: str) -> str:
    """Return ``value`` if it is a plain hex colour, else ``fallback``."""
    return str(value) if _COLOR_RE.match(str(value)) else fallback


# SPEC §7 seed palette. Copied onto every new Organization so rebranding is a
# settings edit rather than a rebuild; base.html emits these as CSS variables.
SEED_PALETTE = {
    'primary': '#176BCE',
    'primary-dark': '#124E96',
    'accent': '#16B8C8',
    'accent-light': '#DFF8FA',
    'surface-alt': '#EEF7FF',
    'background': '#F7FAFC',
    'surface': '#FFFFFF',
    'text': '#1E293B',
    'text-muted': '#64748B',
    'success': '#16A34A',
    'warning': '#D97706',
    'danger': '#DC2626',
}


def default_branding() -> dict:
    """Default value for ``Organization.branding`` (callable, so it migrates)."""
    return {'palette': dict(SEED_PALETTE), 'logo_text': '', 'letterhead': ''}


# SPEC §5 terminology map. Every user-facing word for a domain concept comes
# from here, so a clinic that says "Consultation" or "Appointment" is relabelled
# by editing data — stored values, field names, and URLs never move.
# ``status_*`` keys are looked up as ``status_<stored value lowercased>``.
DEFAULT_TERMINOLOGY = {
    'encounter': 'Visit',
    'encounter_plural': 'Visits',
    'status_draft': 'Open',
    'status_finalized': 'Completed',
    # A locked record that was later corrected. Deliberately the same label as
    # FINALIZED: staff see two states, and "last edited" on the detail page
    # carries the fact that a correction happened.
    'status_amended': 'Completed',
    'amend': 'Edit',
    # Scheduling. One day list covers booked patients and walk-ins alike, so
    # there is one word for the row rather than "appointment" and "queue entry".
    'appointment': 'Appointment',
    'appointment_plural': 'Appointments',
    'walk_in': 'Walk-in',
    # Appointment states. Derived from the row's timestamps, never stored, but
    # they still reach the UI as labels and so still go through the map.
    'status_booked': 'Booked',
    'status_arrived': 'Arrived',
    'status_seen': 'Seen',
    'status_no_show': 'No show',
    'status_cancelled': 'Cancelled',
    # Billing. "Bill" by default because that is what a patient is handed and
    # what a practitioner says; an organization that invoices corporate clients
    # maps these back to "Invoice" without a migration.
    'invoice': 'Bill',
    'invoice_plural': 'Bills',
    'payment': 'Payment',
    'payment_plural': 'Payments',
    'consultation_fee': 'Consultation fee',
    # What a bill is made of. "Lines" is accounting's word for it; the person
    # reading the bill is looking at what they are being charged for.
    'invoice_line_plural': 'Charges',
    # Payment states are derived from the payments received, never stored, but
    # they still reach the UI as labels and so still go through the map.
    'status_unpaid': 'Unpaid',
    'status_partially_paid': 'Part paid',
    'status_paid': 'Paid',
    'status_void': 'Void',
    # Inventory. A clinic that calls a delivery a "purchase order" or a lot a
    # "batch number" relabels here rather than in a template.
    'stock': 'Stock',
    'batch': 'Batch',
    'batch_plural': 'Batches',
    'goods_receipt': 'Goods receipt',
    'goods_receipt_plural': 'Goods receipts',
    'adjustment': 'Adjustment',
    # Stored movement types, rendered through {% status_label %} like any other
    # stored value.
    'status_purchase': 'Received',
    'status_sale': 'Sold',
    'status_dispense': 'Dispensed',
    'status_adjustment': 'Adjusted',
    'status_return': 'Returned',
    'status_wastage': 'Written off',
}

#: Longest an override may be. These are chrome — a nav item, a badge, a button.
_TERM_MAX_LENGTH = 40


def default_terminology() -> dict:
    """Default value for ``Organization.terminology`` (callable, so it migrates)."""
    return dict(DEFAULT_TERMINOLOGY)


class Organization(TimeStampedModel):
    """The tenant. Not org-owned itself, so it keeps a plain manager."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=60, unique=True)
    currency = models.CharField(max_length=3, default='BDT')
    timezone = models.CharField(max_length=64, default='UTC')
    # Prefills the consultation line on a new bill. Money is Decimal everywhere,
    # never float (SPEC §4).
    default_consultation_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Prefilled on the consultation line of a new bill.',
    )
    branding = models.JSONField(default=default_branding, blank=True)
    terminology = models.JSONField(default=default_terminology, blank=True)
    # A capability switch, deliberately a column rather than a terminology key:
    # ``terminology`` names things that exist, this decides whether they exist
    # at all. Turning it off hides the feature, never the data — advice already
    # recorded stays readable on the visits that carry it (A3).
    advice_enabled = models.BooleanField(
        default=True,
        help_text='Offer structured advice alongside medicines when prescribing.',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:60]
        super().save(*args, **kwargs)

    @property
    def palette(self) -> dict:
        return {**SEED_PALETTE, **(self.branding or {}).get('palette', {})}

    @property
    def terms(self) -> dict:
        """User-facing labels: the defaults, overlaid with this org's overrides.

        Unknown keys are dropped and values are trimmed, so a typo in the JSON
        cannot leave a template rendering nothing.
        """
        overrides = {
            key: str(value).strip()[:_TERM_MAX_LENGTH]
            for key, value in (self.terminology or {}).items()
            if key in DEFAULT_TERMINOLOGY and str(value).strip()
        }
        return {**DEFAULT_TERMINOLOGY, **overrides}

    @property
    def primary_color(self) -> str:
        """Brand colour, safe to interpolate into CSS."""
        return hex_color_or(self.palette.get('primary'), SEED_PALETTE['primary'])

    @property
    def letterhead(self) -> str:
        """Free-text address block printed under the clinic name."""
        return (self.branding or {}).get('letterhead', '')


class Branch(OrgOwnedModel):
    """A physical location — chamber, clinic room, second site."""

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'code'], name='branch_code_unique_per_org'
            )
        ]

    def __str__(self) -> str:
        return self.name
