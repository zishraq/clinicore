"""Ambient active-organization state.

The organization-scoped default manager needs to know which organization is
current. That value lives in a contextvar here, set in exactly one place per
entry point: the request middleware, or an explicit ``organization_context()``
block in a management command, Celery task, or test.

The clinic's clock travels with it. ``organization_timezone()`` is a separate
context manager rather than part of ``organization_context()`` because the two
take different things — a pk is enough to scope a query, but reading a zone name
needs the row — and because scoping must work for callers that only have a pk.
Anything that activates one should activate the other; see
docs/adr/0011-organization-timezone-per-request.md.

Rationale, rules, and escape hatches: docs/adr/0005-org-scoped-default-manager.md.
"""

import logging
import zoneinfo
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.utils import timezone as django_timezone

__all__ = [
    'get_active_organization_id',
    'is_scoping_enabled',
    'organization_context',
    'organization_timezone',
    'unscoped',
]

logger = logging.getLogger(__name__)

_active_organization_id: ContextVar[int | None] = ContextVar(
    'clinicore_active_organization_id', default=None
)
_scoping_enabled: ContextVar[bool] = ContextVar(
    'clinicore_scoping_enabled', default=True
)


def _as_pk(organization: Any) -> int | None:
    """Accept an Organization, a bare pk, or None; return a pk or None."""
    if organization is None:
        return None
    pk = getattr(organization, 'pk', organization)
    if pk is None:
        raise ValueError('Cannot activate an unsaved Organization (pk is None).')
    return int(pk)


def get_active_organization_id() -> int | None:
    """Return the active organization's pk, or None if none is active."""
    return _active_organization_id.get()


@contextmanager
def organization_context(organization: Any) -> Iterator[None]:
    """Run a block with ``organization`` active, restoring the prior value.

    Passing None is meaningful: it activates "no organization", which is how the
    middleware establishes a known-clean state for an unauthenticated request.
    """
    token = _active_organization_id.set(_as_pk(organization))
    try:
        yield
    finally:
        _active_organization_id.reset(token)


def _resolve_zone(organization: Any) -> zoneinfo.ZoneInfo | None:
    """The organization's zone, or None meaning "use ``settings.TIME_ZONE``".

    A bad zone name must not take the request down. ``Organization.timezone`` is
    a plain char column that loaders and the admin can write, and one tenant's
    typo is not a reason to 500 their clinic — or, once this is a real
    deployment, anyone else's. It falls back to UTC, which is what storage uses
    regardless, and says so in the log rather than silently.
    """
    name = getattr(organization, 'timezone', None)
    if not name:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        logger.warning(
            'Organization %s has an unusable timezone %r; falling back to %s.',
            getattr(organization, 'pk', '?'),
            name,
            'settings.TIME_ZONE',
        )
        return None


@contextmanager
def organization_timezone(organization: Any) -> Iterator[None]:
    """Run a block on the organization's clock, restoring the previous zone.

    Storage is unaffected: ``USE_TZ`` is on and ``settings.TIME_ZONE`` stays
    UTC, so every datetime is still *stored* in UTC. What moves is presentation
    — ``timezone.localtime``, ``timezone.localdate``, the template ``date``
    filter, and therefore every ``datetime-local`` form default — onto the
    clinic's wall clock.

    ``django.utils.timezone.override`` already saves and restores in a finally,
    which is the discipline this needs; passing None deactivates for the block
    so an unauthenticated request gets a known-clean UTC rather than whatever
    the last request on this thread left behind.
    """
    with django_timezone.override(_resolve_zone(organization)):
        yield


def is_scoping_enabled() -> bool:
    """Return whether the organization filter is currently being applied."""
    return _scoping_enabled.get()


@contextmanager
def unscoped() -> Iterator[None]:
    """Disable organization filtering for the block.

    The escape hatch for genuinely cross-tenant work. Explicit and greppable on
    purpose; for a single query ``Model.all_objects`` is narrower and better.
    """
    token = _scoping_enabled.set(False)
    try:
        yield
    finally:
        _scoping_enabled.reset(token)
