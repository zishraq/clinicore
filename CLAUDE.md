# Clinicore

Generic clinic / practice management and digital prescription system.
Django + HTMX. Public repo, MIT licensed, also serves as a portfolio piece.

## Read this first

Read `docs/SPEC.md` in full before doing anything in this repo. It is the
single source of truth for stack, architecture, domain model, scope, and
delivery phases. Follow the working agreement in §0.

## Current status

MVP built. Catalog autocomplete, encounter amendments, and the SPEC §5
terminology map are done: `Organization.terminology` holds per-org labels,
`core.context_processors.organization` puts them in every template as `terms`,
and `{% status_label %}` (core/templatetags/terminology.py) renders stored
statuses. Defaults relabel Encounter → "Visit", DRAFT → "Open", FINALIZED and
AMENDED → "Completed", Amend → "Edit", Invoice → "Bill". Stored values, field
names, and URLs are unchanged. Any new user-facing word for a domain concept
goes through the map, never hardcoded in a template.

Billing (SPEC §6.6) is built — new `billing` app, PRACTITIONER/OWNER only.
Invoice / InvoiceItem / Payment, bills raised from a completed visit or
standalone, partial payments, void-with-reason, printable A5/A4 receipt sharing
the prescription's print system. Two rules to know before touching it, both in
`docs/adr/0008-invoice-numbering-and-derived-balances.md`:

- **The balance is never a column.** Amount due, amount paid, balance, and
  `payment_status` are derived (`InvoiceQuerySet.with_totals()` annotations and
  the matching model properties). `Invoice.status` stores only ISSUED/VOID.
  Reading `invoice.status` to answer "is this paid?" is a bug.
- **Invoice numbers are gap-free**, allocated from a locked
  `core.DocumentSequence` row inside the invoice's own transaction. The
  concurrency test needs Postgres and skips on SQLite.

Money is `Decimal` and rounds in exactly two places, both in `billing/money.py`
callers: line total on save, payment amount on record. `config/settings_test.py`
now hands the database back to `config.settings` when `POSTGRES_DB` is set, so
`docker compose up -d db` plus the env vars runs the suite on real Postgres.

Inventory (SPEC §6.5) is in progress. Increments 1 and 2 are done: the
`inventory` app's models and ledger service, then the screens — stock on hand
per branch, batch drill-down with movement history, goods receipts, and manual
adjustments. PRACTITIONER/OWNER only, like billing. Browser-verified end to end
(receive → on-hand → adjust). Rules to know before touching it, all in
`docs/adr/0009-ledger-based-stock.md`:

- **On-hand is never a column.** `StockBatch` is identity (product, branch, lot,
  expiry, cost); quantity is `Sum(StockMovement.quantity)` via
  `StockBatchQuerySet.with_on_hand()` or the `on_hand` property. Same call as
  the derived invoice balance.
- **The ledger is append-only, enforced.** `StockMovement.save()` on an existing
  row and `.delete()` both raise `LedgerIsAppendOnly`. Corrections are an
  `ADJUSTMENT` with a reason, which a check constraint also requires.
- **The invoice is the stock event, not the prescription.** Confirmed decision:
  prescription items carry no quantity, so only invoice lines will decrement.
  `DISPENSE` and the `prescription_item` FK exist for a later hand-out-without-
  billing screen, not as a hook.
- **Stock leaves FEFO, automatically**, splitting across batches with one
  movement each; expired batches are excluded from allocation but still counted
  in `on_hand()`. Nobody picks a batch at the counter.
- **`allocate_fefo` locks then counts, in two statements.** Combining them into
  one `SELECT … FOR UPDATE` with the on-hand subquery lets every waiting seller
  read the pre-lock snapshot under READ COMMITTED — a real oversell bug, caught
  by `inventory/tests/test_concurrency.py` (Postgres only, skips on SQLite).

Both deferred schema changes landed with increment 2. `Invoice.branch` is a
nullable FK resolved by `billing.services.resolve_invoice_branch` — visit's
branch, then where that practitioner last worked, then the only branch there is
— and the form field only appears when a multi-branch org gives no signal.
`Membership` still has no branch FK (SPEC §5 wants per-branch access; not
built), which is why "the practitioner's branch" is inferred from their last
encounter. `catalog.Product.reorder_level` exists; zero means no alert.

**`templates/base.html` bottom padding is load-bearing**: `pb-24 sm:pb-24
lg:pb-6`. Tailwind emits responsive variants after base utilities, so a bare
`pb-24` loses to `sm:p-6` from 640px up and the fixed 64px bottom nav covers
the foot of every scrollable page — including submit buttons. This shipped
unnoticed because no page was tall enough to expose it until the goods receipt
form. `core/tests/test_layout.py` is a canary, not a proof; check a browser.

Increment 3 is done: the `SALE` hook off invoice lines, the batch override, the
expired-batch block, dashboard alerts, and `bootstrap_demo` batches. Browser-
verified end to end (override → expired refusal → edit freeze → void restores).
Rules to know before touching it:

- **Both invoice paths post stock, and the guarantee is per line.**
  `post_sale_movements` runs from `create_invoice` *and* `update_invoice`,
  because a fee-only bill edited to add a product is the first thing that sells
  its stock. It skips any line that already carries a movement, which is what
  makes calling it from both sides safe. `billing/tests/test_stock_posting.py`
  tests both directions across that boundary.
- **A bill that moved stock is frozen, like a paid one.**
  `billing.services.editing_blocked_reason` is the single answer for the view
  and the service; `Invoice.is_editable` now also consults
  `has_stock_movements`. The correction path is void-and-reissue, and voiding
  posts a `RETURN` per batch rather than deleting anything.
- **Expired lots are shown and then refused, never hidden.** `sellable_batches`
  offers everything with stock left so the practitioner can see the box in
  their hand; `consume_from_batch` raises `BatchExpired` on submit and takes
  the whole bill down with it (no invoice, and the number is not burned).
  Only `WASTAGE` may leave a past-date batch.
- **Alert cover is usable-only, the stock list is everything.**
  `stock_alerts` counts `usable_only=True` for below-reorder — forty expired
  boxes are not cover for the two good ones — while `stock_levels` still
  reports what is physically there. `reorder_level` of zero means no alert.
- **`bootstrap_demo` teardown order is load-bearing.** `InvoiceItem.batch`
  PROTECTs `StockBatch`, so batches must be deleted *after* invoice lines, not
  with the rest of the ledger. Nothing the loader generates uses the override,
  so only a hand-staged row catches it —
  `core/tests/test_bootstrap_demo.py` stages one.

Not browser-verified: the batch override in a **multi-branch** org. (The demo is
now two-branch, so this is re-testable; the `hx-include="[name='branch']"` half
of the options lookup in `_line_branch` still has only tests behind it.)

The clinic-feedback pass (A1–A6) and the hardening pass (B7–B11) are done, and
A1/A4/A5 are browser-verified end to end. What each one settled:

- **The visit form picks its patient, and can create one (A1).**
  `EncounterForm.patient` is a `HiddenInput` behind a search box
  (`templates/clinical/_patient_picker.html`); the field itself is unchanged, so
  validation and org scoping still hold. `itemRow` and `patientPicker` share
  `autocompleteCore()` in `static/js/item-autocomplete.js` — one keyboard
  implementation, not two. **The modal lives in base.html's `modals` block, not
  in the picker**: a `<form>` cannot nest, and the point is that the half-written
  visit survives registering someone. That is also why the created patient
  arrives as a bubbling `patient-picked` event rather than a swapped
  `[data-autoselect]` fragment — the modal is not in the picker's subtree. It
  renders `PatientForm` itself, so A2's branch default applies without restating,
  and `possible_duplicates` runs with each match offered as a selectable button.
- **`advice_enabled` is a column, not a terminology key (A3).** Terminology names
  things that exist; this decides whether they exist. Off hides the nav link, the
  autocomplete's advice half, and the quick-add offer, and 403s a direct `ADVICE`
  quick-add POST. **Detail and print needed no change** — both already gate on
  `{% if advice_items %}`, i.e. on data rather than the flag, which is exactly
  what keeps recorded advice readable after the switch goes off. Gating those on
  the flag would be a data-hiding bug. Owner-settable at
  `organizations:feature_settings`.
- **Saving a visit completes it (A4).** `save_draft` is the secondary button,
  second in the DOM so Enter takes the common path. `finalize_encounter`, the
  state machine and the amendment trail are untouched — only the default moved.
  Every edit after the first save being a reasoned amendment is intended. The
  status column leaves the visit list when nothing is open.
- **The bill opens with what was prescribed and can be sold (A5).**
  `billing.services.prescribed_product_lines` + `inventory.services.sellable_now`,
  which annotates rather than looping. Quantity is 1 because a prescription
  carries none and none should be invented (ADR 0009). In stock means usable
  `on_hand > 0` — expired lots excluded, **reorder level ignored**, because that
  is a purchasing signal and a clinic can still sell its last two boxes. Branch
  decides what counts. A convenience copy, never a link.
- **`request.POST or None` is wrong for a checkbox-only form.** An unticked
  checkbox posts nothing, so the QueryDict is empty and falsy, and the usual
  idiom silently rebuilds the form unbound and saves nothing — turning a feature
  off would look like it worked. `organizations/views.py` binds on
  `request.method` instead. Found by a test, confirmed in a browser.

Next: SPEC §11 phases remain suspended. Not deployed.

## Standing rules

- Propose before you build on schema changes and new apps. For bug fixes and
  UI work inside an existing app, just do it.
- Small increments. Never generate the whole app in one pass.
- I am an experienced Python/Django/Postgres engineer — skip the basics.
  Frontend (HTMX/Alpine/Tailwind) and deployment are my weak spots; explain
  those briefly as you go.
- Nothing specialty-specific (homeopathy, dental, etc.) in code. Configuration
  and seed data only.
- No new dependency without asking. No scope beyond the current phase.
- Never commit real patient data, real clinic branding, or a `.env` file.
- Tests and CI stay green. Run `ruff` and `pytest` before declaring work done.
- **Verify interactive features in a browser before reporting them done.**
  Tests that assert status codes do not prove a UI works. Four bugs have
  shipped past green tests this way.
- When you finish a substantial piece of work, update the "Current status"
  section of CLAUDE.md to reflect what is now done and what's next. Skip this
  for small fixes and questions.

## Code conventions

These are permanent and apply to every phase. `ruff` enforces the mechanical
ones; the rest are on you.

- **No `from __future__ import annotations`.** Target is Python 3.12. For a
  self-referencing return annotation use `typing.Self`, not a string literal.
  (`ruff` rule `UP010` catches the import.)
- **Single quotes for Python strings.** Docstrings stay `"""`. Enforced by
  `quote-style = "single"` in `[tool.ruff.format]` and `flake8-quotes` in
  `[tool.ruff.lint]` — both must stay set, or `ruff format` reverts the codebase
  on the next run.
- **Concise docstrings.** One or two lines per function, a short module
  docstring. Substantive rationale — why an approach was chosen, what was
  rejected, what the failure modes are — belongs in `docs/adr/`, referenced by
  filename from the code. Don't delete the reasoning; move it.
- **Keep module public APIs small.** Export the smallest surface that does the
  job and declare it in `__all__`. If a helper only exists so one caller can
  avoid a `with` block, it shouldn't be public.

## Commands

```bash
docker compose up                                     # local stack (web :8000, db :5433)
docker compose exec web python -m pytest
docker compose exec web python manage.py migrate
docker compose exec web python manage.py bootstrap_demo --reset   # synthetic demo data
ruff check . && ruff format --check .
python manage.py check
```

Outside Docker the project runs on SQLite (`POSTGRES_DB` unset) against the
`.venv_clinicore` virtualenv. `make seed` and a `Makefile` do not exist yet.
