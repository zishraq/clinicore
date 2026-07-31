"""Managers and querysets that enforce organization scoping.

Rationale: docs/adr/0005-org-scoped-default-manager.md.
"""

from typing import Self

from django.db import models
from django.db.models import Model, QuerySet

from core.context import get_active_organization_id, is_scoping_enabled
from core.exceptions import ActiveOrganizationRequired

__all__ = [
    'AliveOrgScopedManager',
    'OrgScopedManager',
    'OrgScopedQuerySet',
    'SoftDeleteQuerySet',
]


class OrgScopedQuerySet(models.QuerySet):
    def for_organization(self, organization: object) -> Self:
        """Filter explicitly, ignoring ambient context."""
        pk = getattr(organization, 'pk', organization)
        return self.filter(organization_id=pk)


class OrgScopedManager(models.Manager.from_queryset(OrgScopedQuerySet)):
    """Default manager for every organization-owned model."""

    # Historical models in migrations must never inherit this filtering.
    use_in_migrations = False

    def get_queryset(self) -> QuerySet[Model, Model]:
        queryset = super().get_queryset()
        if not is_scoping_enabled():
            return queryset
        organization_id = get_active_organization_id()
        if organization_id is None:
            raise ActiveOrganizationRequired(
                f'{self.model._meta.label}.objects was queried with no active '
                f'organization. Wrap the call in core.context.'
                f'organization_context(org), or use {self.model.__name__}.'
                f'all_objects if this is deliberately cross-tenant.'
            )
        return queryset.filter(organization_id=organization_id)


class SoftDeleteQuerySet(OrgScopedQuerySet):
    def alive(self) -> Self:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> Self:
        return self.filter(deleted_at__isnull=False)


class AliveOrgScopedManager(OrgScopedManager.from_queryset(SoftDeleteQuerySet)):
    """Organization-scoped and soft-delete aware; deleted rows need all_objects."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return super().get_queryset().alive()
