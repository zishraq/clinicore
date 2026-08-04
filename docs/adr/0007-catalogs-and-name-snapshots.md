# 0007 — Two catalogs, one autocomplete, and a frozen name on every item

- **Status:** Accepted
- **Date:** 2026-08-01
- **Relates to:** SPEC §5 (domain model), SPEC §6.4 (clinical and prescriptions),
  `docs/adr/0005-org-scoped-default-manager.md`

## Context

SPEC §5 modelled `Product` and stopped there. That covers half of what a
practitioner actually writes on a prescription: the other half is advice — "walk
30 minutes daily", "avoid fried food for two weeks" — which is not a substance,
has no dose, is never dispensed or stocked, and is repeated almost verbatim
across hundreds of patients. Leaving it out meant every clinic retyping the same
sentences forever, and it meant the printed prescription had nowhere structured
to put them. This was a gap in the spec, not merely an unbuilt feature, and the
spec has been corrected rather than worked around.

## Decision

Two org-scoped catalogs in a new `catalog` app: `Product` (substances and goods)
and `AdviceTemplate` (reusable instructions). `PrescriptionItem` gains
`item_type` and points at exactly one source.

### Why two models rather than one polymorphic "prescribable"

A shared table with a `kind` column and mostly-null fields would let one
autocomplete query one table, which is superficially attractive. But the two
things differ in nearly every column that matters: a product has a SKU, a unit,
stock and sellability flags, and default attributes; advice has a category,
default frequency and duration, and a body of text rather than a name. A merged
table is half nulls, and the Phase 4 inventory work would then hang stock
columns off rows that can never have stock. Two small honest tables cost one
extra query in the autocomplete and nothing else.

### Why the constraint is in the database

`PrescriptionItem` must have exactly one source — a product, an advice template,
or free text — and that source must agree with `item_type`. This is expressed as
a `CheckConstraint`, not as form validation, because the form is only one of the
ways rows get created: management commands, data imports, and the shell all
bypass it. The form derives the source in `clean()` so the constraint is never
the first thing a user meets, but the constraint is what makes the invariant
true rather than merely usual.

`dosage` is nullable, which is a deliberate exception to the "no null on
`CharField`" rule that `ruff`'s DJ001 enforces (hence the one `noqa` in the
model). For advice, empty string would mean "no dose was recorded"; null means
"a dose is not a thing that exists here". The distinction matters on a document
someone dispenses from, so the second constraint enforces it.

### Why `name_snapshot` exists — the important one

A prescription is a record of what a patient was handed on a day. A catalog is a
live list that gets renamed, corrected, and deactivated. Resolving the printed
name through the live foreign key silently couples the two: rename
"Paracetamol 500mg" to "Paracetamol 650mg" next year and every prescription ever
issued now claims a dose that was never given. Reprinting an old prescription,
or reading a historical revision, would show a document that never existed.

So every item freezes its name at save time and every display path — print view,
encounter detail, history diff — reads `name_snapshot`. The foreign key is
retained for reporting ("how often do we prescribe this?"), which is a question
about the catalog entry and *should* follow renames. Two tests hold this line:
one renames a product, one deactivates an advice template.

The same reasoning already applies to `Invoice.currency` and
`InvoiceItem.unit_price` in SPEC §5, and to the amendment history in ADR 0006.
It is the same rule each time: documents snapshot, catalogs live.

### Why one autocomplete instead of two

A practitioner does not think "now I will add a medicine, now I will add
advice" — they think of the thing and type it. Two boxes, or a type selector
before the search, forces the internal data model onto the person using it at
the fastest moment of their day. One box searches both, groups results under
"Medicines" and "Advice", and sets `item_type` from whatever is chosen.

Keyboard navigation is a requirement rather than a nicety for the same reason:
the mouse is not reachable while a patient is talking. HTMX fetches suggestions
(debounced 250ms); a small Alpine component owns highlight, arrow keys, enter,
and writing the choice into the row's hidden inputs. The fragment carries each
entry's defaults as `data-` attributes so selection costs no second request.

### Why quick-add is on the encounter form

A catalog maintainable only through a settings screen goes stale in a month:
nobody abandons a consultation to go and curate a list, so they type free text
instead and the catalog stops reflecting reality. Quick-add creates the entry
from whatever was typed, selects it, and returns — no navigation, no lost form
state. An exact-name match reuses the existing row so two practitioners typing
the same thing do not fork the catalog.

Free text is still allowed. Forcing every item through the catalog would mean
either a bad entry at the worst moment or a practitioner unable to prescribe.

## Consequences

- **Catalog entries are deactivated, never deleted.** Both FKs are `PROTECT`,
  the UI offers only a toggle, and `name_snapshot` means a deactivated entry
  never breaks an existing document.
- **The prescription formset renders one empty row, not three.** With
  autocomplete and add-on-demand, blank rows are just things to skip past.
- **`PrescriptionItemForm` uses a non-model `display_name` field** for the
  visible box; the real source is decided in `clean()`. A consequence worth
  knowing: with JavaScript disabled the row still works and degrades to free
  text, which is why `item_type` is not a required form field.
- **Two more models inherit the ADR 0005 tenancy obligations** and are covered
  by the isolation suite. The autocomplete filters by organization through the
  scoped manager, with a test asserting one tenant's catalog never appears in
  another's suggestions.
- **Stock is still not built.** `is_stock_tracked` and `is_sellable` exist so
  the Phase 4 inventory app attaches without a schema change, and mean nothing
  yet beyond documentation.
