"""Tenant root and its physical locations."""

import re

from django.db import models
from django.utils.text import slugify

from core.models import OrgOwnedModel, TimeStampedModel

__all__ = ['Branch', 'Organization', 'default_branding', 'hex_color_or']

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


class Organization(TimeStampedModel):
    """The tenant. Not org-owned itself, so it keeps a plain manager."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=60, unique=True)
    currency = models.CharField(max_length=3, default='BDT')
    timezone = models.CharField(max_length=64, default='UTC')
    branding = models.JSONField(default=default_branding, blank=True)
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
