"""Ambient active-organization state.

The organization-scoped default manager needs to know which organization is
current. That value lives in a contextvar here, set in exactly one place per
entry point: the request middleware, or an explicit ``organization_context()``
block in a management command, Celery task, or test.

Rationale, rules, and escape hatches: docs/adr/0005-org-scoped-default-manager.md.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

__all__ = [
    'get_active_organization_id',
    'is_scoping_enabled',
    'organization_context',
    'unscoped',
]

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
