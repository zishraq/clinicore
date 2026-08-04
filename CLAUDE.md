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

Inventory (SPEC §6.5) is in progress. Increment 1 — the `inventory` app's
models, the ledger service, and its tests — is done; there is **no UI yet**, so
nothing here has been browser-verified and nothing is reachable from the app.
Rules to know before touching it, all in `docs/adr/0009-ledger-based-stock.md`:

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

Next, in order: goods receipt UI; stock views (on-hand per branch, batch detail,
movement history); the `SALE` hook off invoice lines, which needs a nullable
`branch` FK on `Invoice`; then alerts, which need `reorder_level` on
`catalog.Product`. Phases in SPEC §11 otherwise remain suspended. Not deployed.

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
