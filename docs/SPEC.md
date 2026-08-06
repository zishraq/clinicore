# Clinicore — Clinic Management & Digital Prescription System
## Build Specification

## 0. Your role and how we work

You are a senior Django engineer and product-minded collaborator. I am a backend
software engineer with production experience in Python (Django, Pyramid),
PostgreSQL, Celery, Redis, and Docker. Calibrate accordingly:

- Do NOT explain Python, Django ORM, migrations, git, SQL, or REST basics.
- DO explain, briefly, any HTMX/Alpine/Tailwind pattern that isn't obvious, and
  any deployment or infrastructure step. These are my weak areas.
- Propose before you build. For each unit of work: state the approach in a few
  lines, list files you'll touch, then implement once I confirm.
- Work in small, reviewable, PR-sized increments. Never dump the whole app at once.
- Prefer boring, idiomatic Django over clever abstractions. If you're reaching for
  a metaclass, a custom base class hierarchy, or a plugin registry, stop and
  justify it first.
- When my spec is ambiguous or wrong, say so and recommend an alternative. Don't
  silently pick.
- Flag anything you add that is not in this spec.

**Current phase: 0. Do not begin Phase 1 without explicit confirmation.**
This applies to every phase boundary in §11, not just this one.

## 1. Product context

Clinicore is a clinic management and digital prescription system for small
private clinics and practices. Primary users: practitioners (doctors) and
receptionists. Roughly 5 concurrent users, internal use only, accessed from
desktop, laptop, tablet, and phone over the public internet.

**Critical constraint: this must not be built as bespoke software for one clinic.**
It is a generic clinic/practice management product that will be deployed for one
customer first and reused or resold for others later. No specialty-specific
concepts (homeopathy, dentistry, physiotherapy) may be hardcoded in models,
templates, URLs, or naming. Specialty-specific behaviour is configuration and
seed data, never code branches.

The repository is also a public portfolio piece. Code quality, README, tests,
and commit history are part of the deliverable, not an afterthought.

## 2. Non-goals (do not build these; do not design around them either)

- Microservices, event sourcing, CQRS, DDD tactical patterns, GraphQL.
- Schema-per-tenant or database-per-tenant isolation.
- WebSockets / realtime subscriptions.
- Native printer or hardware integration.
- Kubernetes, service mesh, autoscaling.
- Horizontal scale planning. Assume < 50 concurrent users, forever.
- Public patient signup (Phase 4 only, see roadmap).

## 3. Locked stack

Do not propose alternatives to these unless something is genuinely blocking.

| Layer           | Choice                                                                     |
|-----------------|----------------------------------------------------------------------------|
| Backend         | Django 5.x, Python 3.12+                                                   |
| Database        | PostgreSQL 16                                                              |
| Frontend        | Django templates + HTMX + Alpine.js                                        |
| Styling         | Tailwind CSS (via django-tailwind or the standalone CLI)                   |
| Components      | Flowbite or DaisyUI — pick one, justify briefly, then stay consistent      |
| Forms           | django-crispy-forms with a Tailwind template pack                          |
| Auth            | Django auth, custom user model, phone as USERNAME_FIELD                    |
| Background jobs | Celery + Redis — ONLY when a real need appears (Phase 3+)                  |
| PDF             | WeasyPrint (archival only; primary print path is CSS)                      |
| History         | django-simple-history on clinical and financial models                     |
| Testing         | pytest, pytest-django, factory-boy                                         |
| Lint/format     | ruff                                                                       |
| Container       | Docker + docker compose                                                    |
| Web server      | Gunicorn behind Caddy                                                      |
| Deploy target   | Single VPS, Singapore region                                               |
| PWA             | Manifest + minimal service worker (app shell + offline fallback page only) |

## 4. Architecture and conventions

- Standard Django project layout, apps split by domain:
  `accounts`, `organizations`, `patients`, `scheduling`, `clinical`,
  `catalog`, `inventory`, `billing`, `reporting`, `core`.
- Business logic lives in `services.py` per app (plain functions taking explicit
  arguments). Views orchestrate, models hold data and invariants, templates
  render. No business logic in views or templates.
- Settings split: `base.py` / `dev.py` / `prod.py`. All secrets via environment
  variables, read with django-environ. Commit a `.env.example`, never a `.env`.
- Every business model inherits a base that provides `organization` FK,
  `created_at`, `updated_at`, `created_by`, and a default manager scoped to the
  current organization. Cross-organization data leakage must be structurally
  difficult, not a matter of remembering a filter.
- Money: `DecimalField`, never float. Store currency code on the organization.
- All datetimes timezone-aware; per-organization timezone setting.
- Soft delete only where it's genuinely needed (patients, clinical records);
  hard delete elsewhere. Don't blanket-apply it.

## 5. Domain model

Design the full schema before writing any model code, and show me an ERD
(Mermaid) plus your reasoning on the parts you consider debatable. Deliberately
neutral naming — no specialty terms:

- **Organization** — tenant root. Name, logo, address, currency, timezone,
  branding colors, and a `terminology` JSON map (e.g. `{"encounter": "Visit",
  "practitioner": "Doctor"}`) that templates read for user-facing labels.
- **Branch** — a physical location ("chamber"). Each has its own schedule,
  queue, and stock.
- **User** — custom model, phone as login identifier, optional email, role
  (`OWNER` / `PRACTITIONER` / `STAFF`), status, last login. Membership in an
  Organization, with per-branch access.
- **Patient** — demographics, contact, an org-scoped human-readable patient
  code, and structured intake fields. Sensitive clinical narrative fields
  (history, lifestyle/constitutional notes) must be modelled separately from
  demographics so access control can differ.
- **Appointment** — patient, branch, practitioner, when, and how it came to
  exist (`BOOKED` / `WALK_IN`).
  **Amended 2026-08-07:** the confirmed workflow treats booked patients and
  walk-ins as one day list, so a walk-in is an `Appointment` created already
  arrived. `QueueEntry` is struck — a booked patient who arrives would otherwise
  exist in two tables that can disagree about whether they are still waiting.
  Scheduling is loose: there are no fixed slots, "Tuesday morning" is a real
  answer, and double-booking is allowed. Status is **derived** from
  `arrived_at` / `seen_at` / `resolution`, never stored. Reasoning in
  `docs/adr/0010-appointments-as-one-day-list.md`.
- **Encounter** — one consultation. Patient, practitioner, branch, datetime,
  chief complaint, examination notes, assessment, plan, follow-up date.
  Full history tracking, append-only corrections.
- **Prescription** + **PrescriptionItem** — a prescription has **two sections,
  medicines and advice**, and an item belongs to exactly one of them
  (`item_type` = `MEDICATION` / `ADVICE`). An item's source is exactly one of a
  catalog Product, a catalog AdviceTemplate, or free text — enforced by a check
  constraint, and consistent with `item_type`. Medicines use dosage, frequency,
  duration, instructions; advice has no dosage (the column is null for advice).
  Every item also carries a flexible `attributes` JSON field for
  specialty-specific data (e.g. potency and dilution for homeopathy) driven by
  an org-level field-definition config, and a **`name_snapshot`** frozen at save
  time. Printed and historical prescriptions render the snapshot, never a name
  resolved through the live catalog row: renaming or deactivating a catalog
  entry must not rewrite what a patient was handed.
- **AdviceTemplate** — a reusable non-substance instruction ("walk 30 minutes
  daily"): text, category (`DIET` / `EXERCISE` / `SLEEP` / `LIFESTYLE` /
  `OTHER`), default frequency and duration, `is_active`. Advice is half of what
  a practitioner prescribes and is repeated near-verbatim across patients, so it
  is catalogued rather than retyped. Deliberately small — no dosage, no stock,
  no price.
- **Attachment** — files/images on a patient or encounter, with an access level.
- **Product** — generic catalog item (medicine, consumable, retail good).
  SKU, name, category, unit, `is_stock_tracked`, `is_sellable`, tax rate,
  `default_attributes` (specialty defaults copied onto each prescribed item).
  `is_sellable = False` covers things a clinic recommends but does not dispense.
- **StockBatch** — product, branch, batch/lot number, expiry date, cost price.
- **StockMovement** — immutable ledger: batch, type (`PURCHASE` / `SALE` /
  `DISPENSE` / `ADJUSTMENT` / `RETURN` / `WASTAGE`), quantity signed, reference
  to the source document, actor, timestamp. **Current stock is always computed
  from this ledger.** Never store a mutable quantity column as the source of truth
  (a denormalized cache column is fine if it's rebuilt from the ledger).
- **Invoice** + **InvoiceItem** + **Payment** — invoice items reference either a
  Product or a Service. Keep the invoice model channel-agnostic so a future
  online order can produce one without schema changes.
- **AuditLog** — who did what to which record, when. Covers reads of sensitive
  clinical data too, not just writes.

Design `Invoice`, `Product`, and `Patient` such that a Phase 5 e-commerce layer
(public catalog, cart, order, delivery address) is additive. Do not build any of
it now, but tell me in one paragraph what those additions would look like, so I
can sanity-check the schema.

## 6. Modules and functional requirements

### 6.1 Authentication and access control
- Phone-number + password login. Show/hide password. No public registration, no
  role selector on the login form. Role is derived from the account.
- Users are created by an Organization owner through the app, not through Django
  admin (admin exists for me, not for the customer).
- Role permissions enforced at the **queryset and permission-class layer**.
  Hiding a template block is presentation, not access control. Write tests that
  assert a STAFF user receives 403/404 when requesting a restricted object by
  direct URL.
- Baseline role matrix (must be data-driven and adjustable per organization,
  not hardcoded `if role == "..."` scattered through views):
  - **OWNER**: everything, including user management, pricing, revenue, settings.
  - **PRACTITIONER**: all clinical data across branches, prescriptions,
    encounters, catalog, own revenue view.
  - **STAFF (receptionist)**: patient search and creation, demographic edits,
    queue and appointment management, billing operations, follow-up calls.
    Explicitly denied: clinical narrative fields, private attachments, encounter
    notes, organization-wide revenue.
    **Amended 2026-08-03:** billing is *not* a STAFF surface in this build. The
    confirmed clinic workflow has the practitioner raise the bill and collect
    payment with no handoff, so every billing screen sits behind the same
    PRACTITIONER/OWNER check as the clinical app, and a STAFF user gets a 403.
    Restoring the original matrix for a clinic that does have a cashier is a
    `role_required` change plus a test, not a redesign.
- Rate-limited login (django-axes), session expiry, secure cookie settings,
  password validators.

### 6.2 Patients
- Fast search by name, phone, or patient code — HTMX live search, debounced,
  server-side paginated.
- Patient profile: demographics, encounter timeline, prescriptions, attachments,
  billing history, and outstanding balance — clinical sections rendered only for
  authorized roles.
- Deduplication guard on create: warn on matching phone or close name match
  before saving.

### 6.3 Scheduling and queue
- A per-branch, per-day list that both roles use, in three bands: waiting,
  expected, and closed. Add walk-in, mark arrived, no-show, cancel.
- HTMX polling on the day list (3–5s) for near-live updates. No WebSockets.
- Follow-up tracking: patients due for follow-up, with call outcome logging.
- **Amended 2026-08-07:** "reorder" and "slot templates per practitioner per
  branch" are struck. There are no slots to template, and the order that matters
  is who has waited longest, which `arrived_at` answers without a `position`
  column anyone can forget to maintain. See
  `docs/adr/0010-appointments-as-one-day-list.md`.

### 6.4 Clinical and prescriptions
- Encounter form optimized for speed of entry: keyboard-navigable, "repeat last
  prescription" action, templates for common regimens.
- **One unified autocomplete over both catalogs.** A practitioner prescribing
  does not think "now I will add a medicine, now I will add advice" — they think
  of the thing and type it. The prescription row therefore has a single search
  box returning medicines and advice together, grouped and labelled by type;
  selecting an entry sets `item_type` and prefills its defaults. Debounced, and
  navigable with arrows plus enter, because this is used at speed with a patient
  in the room.
- **Quick-add from the encounter form.** If the typed text matches nothing,
  offer to create it as a medicine or as advice inline, without leaving the page
  or losing form state. A catalog that can only be maintained in a settings
  screen goes stale within a month.
- Prescription print view: **two sections — medicines (with a dosage column) and
  advice (without)** — each omitted entirely when it has no items, so an
  advice-only prescription never prints an empty medicines table. A dedicated
  print stylesheet, `@page` sized A5 and A4 (user-selectable), clinic letterhead
  from Organization branding, and a clean browser print with no app chrome.
  Verify the print CSS actually renders — this is the single most-used feature
  in the building.
- Optional PDF generation via WeasyPrint for attachment/archive.
- Corrections to a finalized encounter create a history entry, never a silent
  overwrite. The edit history must be viewable by authorized roles.

### 6.5 Inventory
- Product CRUD with categories and units.
- Goods receipt: create batches with expiry and cost price.
- Automatic `DISPENSE` movements when a prescription is dispensed; automatic
  `SALE` movements from invoices.
- Stock views: current on-hand per branch, batch-level detail, movement history
  for any product.
- Alerts: below-reorder-level, expiring within N days, expired (blocked from
  dispensing).
- Manual stock adjustments require a reason and are always attributed.

**Amended 2026-08-04**, and in progress — see
`docs/adr/0009-ledger-based-stock.md`. The bullet above asking for automatic
`DISPENSE` movements *and* automatic `SALE` movements describes the same
physical handover twice in this clinic's workflow, where the practitioner writes
the prescription and raises the bill for the same box. Only invoice lines
decrement stock: a prescription item carries dosage and duration, not a
quantity, so it has no number to decrement by. `DISPENSE` survives for a
hand-out-without-billing screen. Stock also leaves first-expiry-first-out
without anyone choosing a batch, which the bullets above left open.

### 6.6 Billing
- Create an invoice from an encounter or standalone. Services plus products.
- Discounts, partial payments, outstanding balance, payment methods.
- Printable/downloadable receipt using the same print system as prescriptions.

Built, and the shape it settled on (see
`docs/adr/0008-invoice-numbering-and-derived-balances.md`):

- **The practitioner bills and collects.** No receptionist handoff, so every
  billing screen is PRACTITIONER/OWNER — see the amendment in §6.1.
- **The consultation fee is its own line**, prefilled from a per-organization
  default that is editable in settings, never folded into a total. Product lines
  use the same catalog autocomplete as the prescription form.
- **Partial payment is the normal case.** Several payments per invoice, each
  with a method and an actor, and the remaining balance shown prominently on the
  invoice, on the patient record, and on the printed receipt.
- **Balance and payment status are derived, never stored.** Amount due is the
  sum of the line snapshots; amount paid is the sum of the payments that were
  not voided. `UNPAID` / `PARTIALLY_PAID` / `PAID` are computed from those.
  Overpayment is rejected with the figure the practitioner should type instead.
- **Invoice numbers are gap-free per organization**, allocated from a locked
  `core.DocumentSequence` row inside the transaction that writes the invoice.
  Gaps read as deleted transactions.
- **Nothing financial is deleted or silently edited.** A payment or an invoice
  raised in error is voided with a reason and an actor; an invoice with payments
  against it cannot be edited, only voided and re-issued.
- **Money is `Decimal`, rounded half-up at two places in exactly two places:**
  when a line total is saved and when a payment is recorded. Everything else is
  a sum of rounded columns, so a receipt always adds up.
- Not built here: stock movements from invoice lines, tax rates, and refunds
  (a refund today is a voided payment). Inventory is the next change.

### 6.7 Reporting
- Dashboards per role: today's queue, upcoming appointments, low stock,
  revenue summary (owner/practitioner only).
- Reports: revenue by period/branch/practitioner, patient volume, top products,
  stock valuation, outstanding dues.
- CSV and XLSX export on every report table.
- Keep charts minimal — a couple of simple ones (Chart.js), not a BI suite.

### 6.8 Settings
- Organization profile, branding (name, logo, color tokens), terminology map,
  branches, users, service catalog, tax rates, prescription field definitions.
- Medicine and advice catalogs: search, create, edit, and **deactivate — never
  delete**, since issued prescriptions reference these rows.
- Everything a new customer would need to change on day one must be editable
  here. If I have to touch code to onboard a second clinic, the design failed.

## 7. UI/UX direction

- Clean, calm, clinical. Minimal, high-contrast, generous spacing, large touch
  targets, large readable type. Minimal animation. No dense enterprise chrome.
- Responsive: full sidebar layout on desktop, collapsible on tablet,
  bottom-nav or drawer on mobile.
- Define the palette as CSS custom properties / Tailwind theme tokens, seeded
  per organization so rebranding is a settings change, not a rebuild.
  Default seed palette:
  primary `#176BCE`, primary-dark `#124E96`, accent `#16B8C8`,
  accent-light `#DFF8FA`, surface-alt `#EEF7FF`, background `#F7FAFC`,
  surface `#FFFFFF`, text `#1E293B`, text-muted `#64748B`,
  success `#16A34A`, warning `#D97706`, danger `#DC2626`.
- Use the chosen component library's patterns for tables, modals, toasts,
  forms, and empty states. Do not hand-roll components that already exist there.
- Every destructive action gets a confirmation. Every list gets an empty state.
  Every form gets inline validation. Every HTMX action gets a loading indicator.

## 8. Security and data protection

- Contains real medical records. Treat accordingly.
- HTTPS only, HSTS, secure/HttpOnly/SameSite cookies, CSRF everywhere.
- Attachments must not be served from a public URL — authenticated, permission-
  checked download views only.
- Nightly `pg_dump` to off-server object storage, encrypted, with retention.
  **Include a documented, tested restore procedure.** An untested backup isn't one.
- Audit log for access to sensitive clinical records, not just modifications.
- Seed/demo data in the repository must be entirely synthetic. No real patient
  data, no real clinic identity, ever committed.

## 9. Quality, testing, and repository standards

This repo goes on my public GitHub profile. Treat it as a portfolio artifact.

- pytest + factory-boy. Test the business rules that matter: permission
  boundaries, stock ledger arithmetic, invoice totals and payments, prescription
  history/versioning, tenant isolation. Do not chase a coverage percentage.
- GitHub Actions CI: ruff check, ruff format --check, pytest against a real
  Postgres service container, docker build. CI must be green on every commit.
- pre-commit hooks mirroring CI.
- Clean, conventional commit messages with meaningful granularity.
- README with: one-paragraph pitch, screenshots or a short GIF, feature list,
  architecture overview, Mermaid ERD, local setup in under five commands,
  deployment guide, and a roadmap.
- A `docs/adr/` directory with short architecture decision records for the
  non-obvious calls (HTMX over SPA, shared-schema multi-tenancy, ledger-based
  stock). Two or three paragraphs each.
- One-command local bootstrap: `docker compose up` plus a `make seed` that
  loads a realistic demo organization with synthetic data.
- Licence: **MIT**. Add a `LICENSE` file at the repo root. Add an SPDX header
  convention across source files only if you think it's warranted — say so and
  wait for confirmation rather than applying it unilaterally.

## 10. Deployment

- `docker compose` with services: web (Gunicorn), db (Postgres), caddy.
  Add redis + worker only when Celery genuinely arrives.
- Target: a single small VPS, Singapore region. Caddy terminates TLS
  automatically. Static files via WhiteNoise; media on a mounted volume, with
  an S3-compatible backend behind a storage abstraction so it can move.
- Deployment must be reproducible and documented step by step: provision,
  DNS, first deploy, migrations, superuser, backups, log access, and how to
  ship an update. Assume I know Docker but have never run a production VPS.
- Keep the container generic enough that Render/Railway/Fly is a drop-in
  alternative, and that an AWS ECS + Terraform migration later is configuration,
  not a rewrite.

## 11. Delivery plan

Build in this order. Do not start a phase until I've confirmed the previous one.

- **Phase 0** — Schema design, ERD, ADRs, repo skeleton, Docker, CI, base
  templates and layout shell, Tailwind + component library wired up. No features.
- **Phase 1** — Organizations, branches, custom user model, auth, role framework,
  settings, branding. Tenant isolation tested.
- **Phase 2** — Patients, queue, appointments, follow-ups.
- **Phase 3** — Encounters, prescriptions, print system, attachments, history.
- **Phase 4** — Catalog, inventory, stock ledger, alerts.
- **Phase 5** — Billing, payments, reports, exports.
- **Phase 6** — PWA, polish, performance pass, seed data, README, screenshots,
  production deployment.
- **Later (do not build now)** — patient portal (self-service login, own history,
  appointment requests), e-commerce, REST API via DRF for a mobile client.
  All three must be additive against this schema; design for that, build none of it.

## 12. Start here

Before writing any code, respond with:
1. Your proposed schema and Mermaid ERD, including the points you consider
   debatable and why you decided as you did.
2. Your component library choice with a one-paragraph justification.
3. Any place where this spec is internally inconsistent, under-specified,
   or where you'd push back on my judgment.
4. The concrete file/folder tree for Phase 0.

Then stop and wait for my confirmation.
