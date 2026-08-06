"""Cross-app operations. Currently: gap-free document numbering.

Financial documents are numbered from a locked counter row rather than a
database sequence — sequences are not gap-free, and a gap in an invoice run
reads as a deleted transaction. See
docs/adr/0008-invoice-numbering-and-derived-balances.md.
"""

from django.db import transaction
from django.utils import timezone

from core.models import DocumentSequence

__all__ = ['current_period', 'next_document_number']


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
