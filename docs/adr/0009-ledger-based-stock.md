# 0009 — Stock is a ledger, and it leaves by expiry date

- **Status:** Accepted
- **Date:** 2026-08-04
- **Relates to:** SPEC §5 (domain model), SPEC §6.5 (inventory),
  `docs/adr/0008-invoice-numbering-and-derived-balances.md`,
  `docs/adr/0007-catalogs-and-name-snapshots.md`

## Context

SPEC §5 already fixed the shape of this: `StockMovement` is an immutable ledger
and "current stock is always computed from this ledger". What it left open is
everything around that sentence — how a movement points at the document that
caused it, which batch stock leaves from when nobody chooses one, and which
document decrements the shelf in a clinic where the practitioner writes the
prescription *and* raises the bill for the same box of tablets.

That last one is the decision that had to be made first, because §6.5 asks for
automatic `DISPENSE` movements from prescriptions **and** automatic `SALE`
movements from invoices, and in the confirmed workflow (§6.6) both documents
describe the same physical handover. Wiring both hooks naively takes the stock
off twice.

## Decision

### On-hand is never a column

`StockBatch` holds identity — product, branch, lot, expiry, cost — and no
quantity. What is on the shelf is `Sum(StockMovement.quantity)` over the batch,
annotated by `StockBatchQuerySet.with_on_hand()` for list pages and read from
`StockBatch.on_hand` for a single row. This is the same call, for the same
reason, as the derived invoice balance in ADR 0008: a cached total in the same
table as the ledger it caches goes wrong in exactly the cases that matter — a
crash between the two writes, a correction applied through a path that forgot
the recalculation, a fix typed in the shell. SPEC §5 permits a denormalized
cache column rebuilt from the ledger; there is no performance case for one at
fifty concurrent users, so there isn't one.

Movements are append-only in the strong sense: `StockMovement.save()` refuses an
update and `delete()` refuses outright, both raising `LedgerIsAppendOnly`. A
mis-keyed delivery is corrected by an `ADJUSTMENT` carrying a reason, which the
database also insists on. Editing the original would silently restate every
count taken since it was posted.

### The invoice is the stock event; the prescription is not

Only invoice lines will generate automatic movements. A prescription carries no
quantity — `PrescriptionItem` has dosage, frequency and duration, which is what
a practitioner writes and not what a dispenser counts — so it cannot decrement
anything without a schema change that invents a number nobody typed. The
invoice line already has the quantity, the price, and the product FK.

`MovementType.DISPENSE` and the `prescription_item` FK still exist, for the
separate case of handing something out without billing for it. It is a second,
explicit screen, not a hook. A clinic that genuinely dispenses against
prescriptions and bills separately would link the two documents rather than
decrementing from both — that is additive, and deliberately not built here.

### Source documents are explicit FKs, not a generic relation

A movement carries `goods_receipt_item`, `invoice_item` or `prescription_item`,
all nullable, with a check constraint saying at most one is set and only on the
movement type it belongs to. A movement may have none at all: opening stock and
manual corrections are entered by hand.

The obvious alternative is a `GenericForeignKey`. It was rejected because the
set of source documents is small and closed, and a generic relation gives up
the three things that matter here — a database constraint tying the source to
the movement type, a join for the "where did this come from" column on the
movement history page, and `on_delete=PROTECT` on the document itself. The same
reasoning produced the one-source check constraint on `PrescriptionItem` in
ADR 0007.

### Stock leaves first-expiry-first-out, automatically

`allocate_fefo()` draws down the earliest-expiring batch first, splitting across
batches and writing one movement per batch touched, so the ledger records which
lot actually left even though nobody chose it. Batches with no expiry date sort
last: a dated batch should always leave before one that can sit indefinitely.
Expired batches are excluded from allocation entirely rather than counted and
then refused, so past-date stock reads as unavailable everywhere instead of only
at the point of sale — it is still visible in `on_hand()`, because it is
physically there and still has to be written off.

Asking the practitioner to pick a batch was rejected: it adds a step to every
line at the counter, and a step taken under time pressure with a patient in the
room is a step that gets skipped. Manual batch choice belongs on the adjustment
screen, where the whole point is that a human is looking at the shelf.

### Lock first, count second

`allocate_fefo()` is a read-modify-write and therefore a race: two people
selling the last box must not both be told it is there. The batch rows are
locked with `select_for_update()`, in FEFO order, for the life of the enclosing
transaction.

The non-obvious part, and the reason this paragraph exists: the lock and the
count have to be **two separate statements**. The first version issued one
`SELECT … FOR UPDATE` with the on-hand subquery in the select list. Under
Postgres' READ COMMITTED, a query that blocks on a row lock re-evaluates its
`WHERE` clause against the updated row when the lock frees, but a subquery in
the target list keeps the snapshot it started with. Every seller queued behind
the first therefore read the shelf as still full, and five concurrent sales of
three units each came out of a batch of ten.
`inventory/tests/test_concurrency.py` reproduces it; it needs a real Postgres
and skips on SQLite, like the invoice numbering test in ADR 0008.

### Goods receipts are numbered, and not voidable

A delivery is booked in as a `GoodsReceipt` numbered from the same
`core.DocumentSequence` machinery as invoices — `core.models` anticipated this
in Phase 0 — so a clinic reconciling against supplier paperwork has an unbroken
run to check. There is deliberately no void: a wrongly booked delivery is
corrected with an `ADJUSTMENT`, which leaves both the original and the
correction on the ledger. Voiding would need compensating movements anyway, and
a "void" button that writes an adjustment is just an adjustment with a
misleading label.

An unnumbered delivery line always opens a new batch, because without a lot
number there is nothing to say two deliveries are the same stock and they may
differ in expiry and cost. A line that does carry a lot number lands in the
existing batch for that lot at that branch — the unique constraint is
conditional on the lot number being non-empty for exactly this reason.

## Consequences

- Every stock figure in the product is a `Sum` over `StockMovement`. If a list
  page becomes slow, the fix is a cache column rebuilt from the ledger, not a
  mutable quantity.
- `catalog.Product.reorder_level` does not exist yet; the below-reorder alert in
  SPEC §6.5 needs it and will add it.
- `billing.Invoice` has no branch, and a `SALE` movement needs a location. The
  billing hook will add a nullable `branch` FK prefilled from the encounter.
- Refunds and returns to a supplier both land on `RETURN`, which currently has
  no document behind it. If either grows a real workflow it gets a source FK,
  additively.
