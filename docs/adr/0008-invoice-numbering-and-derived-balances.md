# 0008 — Gap-free invoice numbers, and a balance that is never a column

- **Status:** Accepted
- **Date:** 2026-08-03
- **Relates to:** SPEC §5 (domain model), SPEC §6.6 (billing), SPEC §4 (money),
  `docs/adr/0007-catalogs-and-name-snapshots.md`,
  `docs/phase-0-proposal.md` §1.5

## Context

Billing was specified in SPEC §6.6 as three lines: create an invoice from an
encounter or standalone, discounts and partial payments, printable receipt. The
clinic workflow it is actually being built for narrows that usefully:

- the practitioner raises the bill and collects the money — there is no
  receptionist handoff, so billing is a PRACTITIONER/OWNER surface here;
- the consultation fee is always its own line, never folded into a total;
- patients pay in instalments as a matter of routine, not as an exception.

The last point is the one that shapes the schema. If partial payment is normal,
the balance is read far more often than it is written, is asked about at the
counter, and gets printed on paper the patient takes home. It has to be right
every time, including after a payment is reversed.

## Decision

### Balance and payment status are derived, always

`Invoice` stores no `balance`, no `amount_paid`, and no paid flag. Amount due is
the sum of `InvoiceItem.line_total`; amount paid is the sum of `Payment.amount`
where the payment is not voided; the balance is the difference; the status
(`UNPAID` / `PARTIALLY_PAID` / `PAID`) is read off that.

A stored balance is a cache of a ledger sitting in the same table as the ledger,
and it goes wrong in exactly the situations that matter: a crash between writing
the payment and updating the total, a payment voided by a path that forgot the
recalculation, a data fix applied in the shell. The failure is silent and the
symptom is a patient being asked for money they already paid. At this size —
SPEC §2 caps the whole product at fifty concurrent users — there is no
performance argument on the other side.

The cost is that a list page cannot simply select a column. `InvoiceQuerySet.
with_totals()` annotates the three values with two **correlated subqueries**,
not two joins: joining items and payments in one query multiplies the rows and
silently doubles both sums. The model properties (`amount_due`, `amount_paid`,
`balance`) read the annotation when it is present and fall back to an aggregate
when it is not, so a detail page and a list page share one meaning of "balance"
with no N+1 on the list.

The one thing `Invoice.status` does store is `ISSUED` / `VOID`, because that is
a decision a person makes rather than a fact derivable from the payments.

### Rounding happens in two places, and only two

`billing/money.py` holds `to_money()` — `Decimal`, two places, `ROUND_HALF_UP`.
It is applied when a line total is computed on save, and when a payment amount is
recorded. Every downstream number is a sum of already-rounded columns, so the
receipt total is the sum of the lines printed above it, exactly, and display
never rounds. Rounding at display time instead would let a receipt fail to add
up in front of the patient holding it.

### Numbering: a locked counter row, not a database sequence

`core.DocumentSequence` holds `(organization, kind, period) → last_number`, and
`core.services.next_document_number()` increments it under `select_for_update()`
inside the caller's transaction. Invoice numbers come out as `INV-2026-0001`.

Postgres sequences are explicitly not gap-free — a rolled-back transaction burns
the value — and are not per-tenant without DDL per organization. Gaps in a
financial run are not cosmetic: to an auditor, or to an owner reading their own
books, a missing number reads as a deleted transaction. Because the number is
allocated inside the transaction that writes the invoice, the lock is held to
commit, concurrent creators serialize, and an abandoned save returns its number
rather than burning it. A row lock held for the length of one insert costs
nothing at five concurrent users.

The counter lives in `core`, not `billing`, because it is not billing-specific:
goods receipts want the same guarantee when inventory lands.

`billing/tests/test_numbering.py` runs eight threads through a barrier so the
allocations genuinely overlap. It skips on SQLite, which has no row-level
locking, and asserts a contiguous `0001…0008` on Postgres. Removing the
`select_for_update()` makes six of the eight threads fail on the unique
constraint, which is what a test of a lock should do.

### Correction is reversal, never deletion or a silent edit

A payment recorded in error is voided: the row stays, gains `voided_at`,
`voided_by`, and a required reason, and stops counting towards the balance.
An invoice raised in error is voided the same way, and refuses to void while
live payments hang off it — money that was actually collected has to be
reversed one payment at a time, so each reversal carries its own reason instead
of being swept up by one click.

An invoice with payments against it is also not editable. The patient is holding
a receipt that has to keep matching the row it was printed from; the correction
path is to void and re-issue, which leaves both documents on the record. This is
the financial analogue of ADR 0006's rule for finalized encounters: append, do
not overwrite.

### Snapshots, again

`InvoiceItem` freezes `name_snapshot` and `unit_price` at issue time, and
`Invoice` freezes `currency` from the organization. Repricing a product next
month must not restate a receipt printed today — the identical rule, and the
identical reasoning, as ADR 0007's `PrescriptionItem.name_snapshot`. The product
FK is kept for reporting, which is a question about the catalog entry and
*should* follow renames.

`Product` gains `sale_price` for this, which is the field the Phase 0 schema
already had; it prefills a line and is then copied, never referenced.

## Consequences

- **Billing is PRACTITIONER/OWNER**, which contradicts SPEC §6.1's line giving
  STAFF "billing operations". SPEC §6.1 has been amended: the confirmed workflow
  has no handoff step. A `role_required` swap is all that changes if a clinic
  with a cashier turns up.
- **`Invoice.status` is a two-value column** and anything about money owed is
  computed. Code reading `invoice.status` to answer "is this paid?" is a bug;
  `payment_status` is the property, and `with_payment_status()` the filter.
- **Filtering by an uncomputed status costs a subquery per row set**, which is
  fine at this size and would be the first thing to reconsider if invoice
  volumes ever grew by two orders of magnitude. The fix then is a materialized
  summary rebuilt from the ledger, never a hand-maintained column.
- **Three more models inherit the ADR 0005 tenancy obligations** and are in the
  parametrized isolation suite, along with `DocumentSequence`.
- **Nothing about stock moved.** Inventory is the next change; `Invoice` carries
  no branch and writes no `StockMovement` yet, and adding both is additive.
