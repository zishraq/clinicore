"""Abstract bases every business model inherits, plus ``DocumentSequence``.

The bases are abstract; ``DocumentSequence`` is the one concrete model here,
and it lives in ``core`` because document numbering is not billing-specific —
goods receipts want the same counter when inventory lands. ``AuditLog`` lands in
Phase 1 alongside the models it audits.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.managers import OrgScopedManager

__all__ = [
    'DocumentSequence',
    'OrgOwnedModel',
    'SoftDeleteModel',
    'TimeStampedModel',
]


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrgOwnedModel(TimeStampedModel):
    """Base for every tenant-owned row.

    Manager order matters and ``base_manager_name`` deliberately points at the
    unfiltered manager — see docs/adr/0005-org-scoped-default-manager.md.
    """

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.PROTECT,
        related_name='%(app_label)s_%(class)s_set',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    # Declaration order is load-bearing: objects must be first so it becomes
    # _default_manager. DJ012 wants managers before fields; ADR 0005 wins.
    objects = OrgScopedManager()
    all_objects = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True
        base_manager_name = 'all_objects'


class DocumentSequence(OrgOwnedModel):
    """Counter behind a gap-free, per-organization document number.

    Never incremented directly: ``core.services.next_document_number`` takes a
    row lock so two clerks billing at once cannot collide. Rationale, and why
    this is not a Postgres sequence, in
    docs/adr/0008-invoice-numbering-and-derived-balances.md.
    """

    kind = models.CharField(max_length=32, help_text='INVOICE, RECEIPT, …')
    # Numbering restarts each year for financial documents; a kind that wants
    # one unbroken run leaves this empty.
    period = models.CharField(max_length=8, blank=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['kind', 'period']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'kind', 'period'],
                name='document_sequence_unique_per_org_kind_period',
            )
        ]

    def __str__(self) -> str:
        return f'{self.kind} {self.period} @ {self.last_number}'


class SoftDeleteModel(models.Model):
    """Mixed into the few models SPEC §4 lists, never applied blanket-wide.

    Concrete subclasses declare ``objects = AliveOrgScopedManager()``, leaving
    ``all_objects`` as the way to reach deleted rows.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, *, actor=None) -> None:
        self.deleted_at = timezone.now()
        self.deleted_by = actor
        self.save(update_fields=['deleted_at', 'deleted_by', 'updated_at'])

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['deleted_at', 'deleted_by', 'updated_at'])
