"""Cross-app operations: standing an organization up, and document numbering.

Financial documents are numbered from a locked counter row rather than a
database sequence — sequences are not gap-free, and a gap in an invoice run
reads as a deleted transaction. See
docs/adr/0008-invoice-numbering-and-derived-balances.md.

``create_organization`` is here rather than in ``organizations`` because it is
the one operation both ``bootstrap_clinic`` and ``bootstrap_demo`` share, and a
new organization is never only an ``Organization`` row: it is that row plus its
first branch, written in the organization's own scope.
"""

import zoneinfo

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core.context import organization_context
from core.exceptions import CannotCreateOrganization
from core.models import DocumentSequence
from organizations.models import Branch, Organization

__all__ = ['create_organization', 'current_period', 'next_document_number']


@transaction.atomic
def create_organization(
    *,
    name: str,
    slug: str = '',
    timezone_name: str = 'UTC',
    branch: dict | None = None,
    **fields,
) -> tuple[Organization, Branch]:
    """An organization and its first branch. Returns both.

    ``branch`` is that branch's own fields, defaulting to a single ``Main``;
    ``fields`` are the organization's, so a caller that wants a currency or a
    consultation fee sets them here rather than saving twice.

    The zone is validated rather than defaulted, because a wrong one is not a
    crash: the request middleware falls back to UTC and the clinic finds out
    when a visit saved at 01:00 files itself under yesterday (ADR 0011).
    """
    name = name.strip()
    if not name:
        raise CannotCreateOrganization('A clinic needs a name.')

    zone = timezone_name.strip()
    try:
        zoneinfo.ZoneInfo(zone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
        raise CannotCreateOrganization(f'{zone!r} is not an IANA time zone.') from error

    slug = (slug or slugify(name))[:60]
    if not slug:
        raise CannotCreateOrganization(f'{name!r} does not make a usable slug.')
    if Organization.objects.filter(slug=slug).exists():
        raise CannotCreateOrganization(
            f'An organization with slug {slug!r} already exists.'
        )

    organization = Organization.objects.create(
        name=name, slug=slug, timezone=zone, **fields
    )
    branch_fields = {'name': 'Main', 'code': 'MAIN', **(branch or {})}
    # Branch is org-scoped, so it is written inside the scope it belongs to
    # rather than by passing the FK past a manager that would refuse to read
    # it back (docs/adr/0005-org-scoped-default-manager.md).
    with organization_context(organization):
        first_branch = Branch.objects.create(organization=organization, **branch_fields)
    return organization, first_branch


def current_period() -> str:
    """The year financial documents are numbered within."""
    return f'{timezone.localdate().year}'


@transaction.atomic
def next_document_number(
    organization,
    *,
    kind: str,
    prefix: str,
    period: str = '',
    width: int = 4,
    start_after: int = 0,
) -> str:
    """Allocate the next number for ``kind``, e.g. ``INV-2026-0007``.

    Must be called inside the transaction that writes the document: the row
    lock is held until that transaction ends, so concurrent callers serialize,
    and a rollback returns the number rather than burning it.

    ``start_after`` is a floor read off the rows themselves, for a run that was
    not always numbered from here — patient codes predate this counter, and the
    seed loader writes them directly. The counter stays the allocator; the floor
    only stops it handing out a number already on a row.

    Reads through ``all_objects`` and filters explicitly, because the counter is
    also allocated from management commands where no organization is active.
    """
    sequence, _ = DocumentSequence.all_objects.get_or_create(
        organization=organization, kind=kind, period=period
    )
    # Re-fetch under the lock: get_or_create cannot take one, and the row may
    # have been incremented between the two statements.
    locked = DocumentSequence.all_objects.select_for_update().get(pk=sequence.pk)
    locked.last_number = max(locked.last_number, start_after) + 1
    locked.save(update_fields=['last_number', 'updated_at'])

    parts = [prefix, period, f'{locked.last_number:0{width}d}']
    return '-'.join(part for part in parts if part)
