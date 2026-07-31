# Clinicore — Phase 0 Proposal

Response to `docs/SPEC.md` §12. Covers the proposed schema and ERD, the component
library choice, pushback on the spec, and the concrete Phase 0 file tree.

---

## 1. Schema and ERD

### 1.1 Foundation (`core`)

Three abstract bases, no table of their own:

- `TimeStampedModel` — `created_at`, `updated_at`.
- `OrgOwnedModel(TimeStampedModel)` — `organization` FK (`PROTECT`), `created_by` FK to `User` (`SET_NULL`), `objects = OrgScopedManager()`, `all_objects = models.Manager()`.
- `SoftDeleteModel` — `deleted_at`, `deleted_by`; applied only to `Patient`, `PatientClinicalProfile`, `Encounter`, `Prescription`, `Attachment`.

`OrgScopedManager` reads the active organization from a `contextvar` set by middleware. See §3(c) — this is the design call most in need of sign-off.

One concrete table in `core`: `AuditLog`.

### 1.2 ERD — tenancy and identity

The ERD is split into three diagrams. A single 30-entity Mermaid graph renders as
spaghetti in the README, and these are the three cluster boundaries that actually
have few edges between them.

```mermaid
erDiagram
    ORGANIZATION ||--o{ BRANCH : operates
    ORGANIZATION ||--o{ MEMBERSHIP : grants
    ORGANIZATION ||--o{ ROLE_PERMISSION : configures
    ORGANIZATION ||--o{ FIELD_DEFINITION : defines
    ORGANIZATION ||--o{ DOCUMENT_SEQUENCE : numbers
    USER ||--o{ MEMBERSHIP : holds
    MEMBERSHIP ||--o{ BRANCH_ACCESS : "scoped to"
    BRANCH ||--o{ BRANCH_ACCESS : "exposed via"
    MEMBERSHIP ||--o| PRACTITIONER_PROFILE : "may have"
    USER ||--o{ AUDIT_LOG : "acted"

    ORGANIZATION {
        bigint id PK
        string name
        string slug UK
        string currency "ISO 4217, 3 chars"
        string timezone
        json branding "color tokens, logo ref"
        json terminology "encounter->Visit, etc."
        bool is_active
    }
    BRANCH {
        bigint id PK
        bigint organization_id FK
        string name
        string code "unique per org"
        string timezone "nullable override"
        bool is_active
    }
    USER {
        bigint id PK
        string phone UK "USERNAME_FIELD"
        string email "nullable, not unique"
        string full_name
        bool is_active
        bool is_staff "django admin only"
    }
    MEMBERSHIP {
        bigint id PK
        bigint user_id FK
        bigint organization_id FK
        string role "OWNER|PRACTITIONER|STAFF"
        bool is_active
    }
    ROLE_PERMISSION {
        bigint id PK
        bigint organization_id FK
        string role
        string permission_code "from a module-level constant list"
        bool allowed
    }
    PRACTITIONER_PROFILE {
        bigint id PK
        bigint membership_id FK "OneToOne"
        string registration_number
        string qualifications
        string signature_image
    }
    FIELD_DEFINITION {
        bigint id PK
        bigint organization_id FK
        string target "PATIENT_INTAKE|ENCOUNTER|PRESCRIPTION_ITEM"
        string key
        string label
        string data_type "text|number|choice|bool|date"
        json choices
        bool required
        int sort_order
    }
    AUDIT_LOG {
        bigint id PK
        bigint organization_id FK "nullable"
        bigint actor_id FK "nullable"
        bigint patient_id FK "nullable, denormalized"
        string action "CREATE|UPDATE|DELETE|READ|PRINT|EXPORT|DOWNLOAD|LOGIN"
        string object_type
        bigint object_id
        inet ip_address
        json metadata
        datetime created_at
    }
```

`User` is deliberately **not** org-scoped. Phone is the login identifier, so it must
be globally unique; and a practitioner working at two clinics on one deployment gets
one account with two memberships. `Membership` is the tenancy join, and every
permission check resolves through it.

### 1.3 ERD — patients, scheduling, clinical

```mermaid
erDiagram
    PATIENT ||--o| PATIENT_CLINICAL_PROFILE : "has"
    PATIENT ||--o{ APPOINTMENT : books
    PATIENT ||--o{ QUEUE_ENTRY : "queues as"
    PATIENT ||--o{ ENCOUNTER : "seen in"
    PATIENT ||--o{ ATTACHMENT : "owns"
    PATIENT ||--o{ FOLLOW_UP : "due for"
    APPOINTMENT |o--o| QUEUE_ENTRY : "checked in as"
    ENCOUNTER ||--o{ PRESCRIPTION : produces
    ENCOUNTER ||--o{ ATTACHMENT : "documented by"
    ENCOUNTER ||--o{ FOLLOW_UP : schedules
    PRESCRIPTION ||--o{ PRESCRIPTION_ITEM : contains
    PRESCRIPTION_TEMPLATE ||--o{ PRESCRIPTION_TEMPLATE_ITEM : contains
    FOLLOW_UP ||--o{ FOLLOW_UP_CALL : "logged by"
    SLOT_TEMPLATE ||--o{ APPOINTMENT : "generates slots for"

    PATIENT {
        bigint id PK
        bigint organization_id FK
        string code "human-readable, unique per org"
        string full_name
        date date_of_birth "nullable"
        int approx_age_years "nullable, mutually exclusive w/ dob"
        string sex
        string phone
        string alt_phone
        string address
        bigint registered_branch_id FK
        datetime deleted_at
    }
    PATIENT_CLINICAL_PROFILE {
        bigint id PK
        bigint patient_id FK "OneToOne"
        text medical_history
        text lifestyle_notes
        text allergies
        text family_history
        json intake "validated against FIELD_DEFINITION"
    }
    APPOINTMENT {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint patient_id FK
        bigint practitioner_id FK "-> MEMBERSHIP"
        datetime start_at
        datetime end_at
        string status "BOOKED|CONFIRMED|CHECKED_IN|COMPLETED|CANCELLED|NO_SHOW"
        string source "WALK_IN|PHONE|STAFF"
    }
    QUEUE_ENTRY {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        date queue_date
        bigint patient_id FK
        bigint practitioner_id FK "nullable"
        int position
        string status "WAITING|IN_CONSULTATION|DONE|ABSENT"
        datetime arrived_at
        datetime called_at
        datetime completed_at
    }
    ENCOUNTER {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint patient_id FK
        bigint practitioner_id FK
        datetime occurred_at
        text chief_complaint
        text examination
        text assessment
        text plan
        json vitals
        json attributes
        date follow_up_date
        string status "DRAFT|FINALIZED|AMENDED"
        datetime finalized_at
    }
    PRESCRIPTION {
        bigint id PK
        bigint organization_id FK
        bigint encounter_id FK
        bigint patient_id FK
        bigint practitioner_id FK
        datetime issued_at
        string status "DRAFT|ISSUED|CANCELLED"
        text general_instructions
        string print_size "A4|A5"
    }
    PRESCRIPTION_ITEM {
        bigint id PK
        bigint prescription_id FK
        bigint product_id FK "nullable"
        string free_text_name "nullable; CHECK exactly one of the two"
        string dosage
        string frequency
        string duration
        decimal quantity
        text instructions
        json attributes "potency, dilution, etc."
        int sort_order
    }
    ATTACHMENT {
        bigint id PK
        bigint organization_id FK
        bigint patient_id FK "nullable"
        bigint encounter_id FK "nullable; CHECK exactly one"
        string file
        string original_name
        string content_type
        bigint size_bytes
        string access_level "STAFF|CLINICAL|OWNER"
    }
    FOLLOW_UP {
        bigint id PK
        bigint organization_id FK
        bigint patient_id FK
        bigint encounter_id FK "nullable"
        date due_date
        string status "PENDING|CONTACTED|BOOKED|CLOSED"
        bigint assigned_to_id FK
    }
    FOLLOW_UP_CALL {
        bigint id PK
        bigint follow_up_id FK
        datetime called_at
        bigint called_by_id FK
        string outcome "REACHED|NO_ANSWER|RESCHEDULED|DECLINED"
        text notes
    }
    SLOT_TEMPLATE {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint practitioner_id FK
        int weekday
        time start_time
        time end_time
        int slot_minutes
        date valid_from
        date valid_to
    }
```

`django-simple-history` goes on `Patient`, `PatientClinicalProfile`, `Encounter`,
`Prescription`, `PrescriptionItem`.

### 1.4 ERD — catalog, inventory, billing

```mermaid
erDiagram
    PRODUCT_CATEGORY ||--o{ PRODUCT : classifies
    UNIT ||--o{ PRODUCT : measures
    TAX_RATE ||--o{ PRODUCT : taxes
    TAX_RATE ||--o{ SERVICE : taxes
    PRODUCT ||--o{ STOCK_BATCH : "stocked as"
    PRODUCT ||--o{ STOCK_MOVEMENT : moves
    PRODUCT ||--o{ STOCK_LEVEL : "cached in"
    STOCK_BATCH ||--o{ STOCK_MOVEMENT : "debits/credits"
    GOODS_RECEIPT ||--o{ GOODS_RECEIPT_ITEM : contains
    GOODS_RECEIPT_ITEM ||--|| STOCK_BATCH : creates
    GOODS_RECEIPT ||--o{ STOCK_MOVEMENT : "sources PURCHASE"
    INVOICE ||--o{ INVOICE_ITEM : contains
    INVOICE ||--o{ PAYMENT : "settled by"
    INVOICE ||--o{ STOCK_MOVEMENT : "sources SALE"
    PRESCRIPTION ||--o{ STOCK_MOVEMENT : "sources DISPENSE"
    PRODUCT ||--o{ INVOICE_ITEM : "billed as"
    SERVICE ||--o{ INVOICE_ITEM : "billed as"
    ENCOUNTER ||--o{ INVOICE : "billed via"
    PATIENT ||--o{ INVOICE : owes

    PRODUCT {
        bigint id PK
        bigint organization_id FK
        string sku "unique per org"
        string name
        bigint category_id FK
        bigint unit_id FK
        bool is_stock_tracked
        bool is_sellable
        bool is_prescribable
        decimal sale_price
        bigint tax_rate_id FK
        decimal reorder_level
        json attributes
        bool is_active
    }
    SERVICE {
        bigint id PK
        bigint organization_id FK
        string code
        string name
        decimal default_price
        bigint tax_rate_id FK
        bool is_active
    }
    STOCK_BATCH {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint product_id FK
        string batch_number
        date expiry_date
        decimal cost_price
        datetime received_at
    }
    STOCK_MOVEMENT {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint product_id FK "denormalized from batch"
        bigint batch_id FK "nullable for untracked adj."
        string movement_type "PURCHASE|SALE|DISPENSE|ADJUSTMENT|RETURN|WASTAGE"
        decimal quantity "SIGNED"
        decimal unit_cost
        bigint goods_receipt_id FK "nullable"
        bigint invoice_id FK "nullable"
        bigint prescription_id FK "nullable"
        text reason
        bigint actor_id FK
        datetime created_at "append-only"
    }
    STOCK_LEVEL {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint product_id FK
        decimal quantity "CACHE - rebuildable from ledger"
        datetime recomputed_at
    }
    INVOICE {
        bigint id PK
        bigint organization_id FK
        bigint branch_id FK
        bigint patient_id FK "NULLABLE - counter sale / future online order"
        bigint encounter_id FK "nullable"
        string number "unique per org, via DOCUMENT_SEQUENCE"
        string currency "SNAPSHOT of org currency"
        string channel "COUNTER|ONLINE"
        string status "DRAFT|ISSUED|PARTIALLY_PAID|PAID|VOID"
        decimal subtotal
        decimal discount_amount
        string discount_reason
        decimal tax_amount
        decimal total
        decimal amount_paid
        decimal balance
        datetime issued_at
    }
    INVOICE_ITEM {
        bigint id PK
        bigint invoice_id FK
        bigint product_id FK "nullable"
        bigint service_id FK "nullable; CHECK at most one"
        string description "SNAPSHOT"
        decimal quantity
        decimal unit_price "SNAPSHOT"
        decimal discount_amount
        decimal tax_percent "SNAPSHOT"
        decimal line_total
    }
    PAYMENT {
        bigint id PK
        bigint invoice_id FK
        string kind "PAYMENT|REFUND"
        decimal amount "always positive"
        string method "CASH|CARD|MOBILE|BANK|OTHER"
        string reference
        datetime received_at
        bigint received_by_id FK
    }
```

### 1.5 Decisions considered debatable

**Stock ledger source references — three nullable FKs, not a generic FK.**
`StockMovement` points at `goods_receipt`, `invoice`, or `prescription` via three
nullable columns with a check constraint that at most one is set. A
`GenericForeignKey` would be one column pair but gives up referential integrity and
makes every "show me movements for this invoice" query a two-step. Three nullable
bigints cost nothing.

**`StockLevel` exists as an explicit cache.** §5 says current stock is always
computed from the ledger. A `SUM()` over movements per product per branch is fine at
this size, but the stock list view is per-product-per-branch and would N+1 into an
aggregate. `StockLevel` is written inside the same transaction as the movement, and
`manage.py rebuild_stock_levels` recomputes it from scratch. Tests assert
cache == ledger after every operation. Dropping the cache until it's proven
necessary is an acceptable alternative.

**Invoice numbering via a `DocumentSequence` table with `select_for_update`, not a
Postgres sequence.** Sequences aren't gap-free and aren't per-org without DDL per
tenant. A row lock at 5 concurrent users costs nothing and gives contiguous,
per-org, per-year numbering — which matters because these are financial documents.

**Queue reordering uses a plain integer `position` with no unique constraint,**
renumbered in a service function under `select_for_update` on the day's rows. The
alternative (fractional positions, insert-between) avoids the renumber but produces
ugly values and eventually needs a compaction pass anyway. At one branch-day of ~40
entries, renumbering is a single UPDATE.

**`Patient` carries either `date_of_birth` or `approx_age_years`, not both.**
Walk-in patients in small practices frequently don't know a DOB. Storing a fake DOB
poisons every age calculation; a nullable pair with a check constraint is honest.

**Corrections to a finalized `Encounter` use `simple_history`'s
`history_change_reason`, not a separate amendment table.** The library already
stores the full prior row plus actor plus a reason string. A parallel
`EncounterAmendment` table would duplicate that and then need reconciling. The
service that edits a `FINALIZED` encounter requires a reason argument, sets status
to `AMENDED`, and writes the history row. The "view edit history" screen reads
`encounter.history`.

**Integer PKs, not UUIDs, in URLs.** Sequential IDs are only an IDOR risk if object
lookup isn't org-scoped — and the entire premise of §4 is that it always is. Better
to spend the effort on the isolation test suite than on UUID indexes.

### 1.6 Phase 5 e-commerce additions

The schema is already shaped for it: `Invoice.patient_id` is nullable and
`Invoice.channel` distinguishes counter from online, so a public order produces a
normal invoice with no schema change; `Product.is_sellable`, `sale_price`, and
`tax_rate` are the public catalog's fields already. What Phase 5 adds is a new
`shop` app containing `Cart`/`CartItem` (session- or user-keyed, discarded on
checkout), `Order`/`OrderItem` referencing the resulting `Invoice`,
`DeliveryAddress` FK'd to `Patient`, `Shipment` with a carrier reference, and a
`PaymentIntent` recording gateway state that resolves into an existing `Payment` row
on capture. `Patient` gains an optional `OneToOne` to `User` so a portal login maps
to a patient record — which is also exactly what the Phase 4 patient portal needs,
so those two land together. Nothing existing changes except the addition of nullable
columns.

---

## 2. Component library: DaisyUI

**DaisyUI**, and the deciding factor is HTMX rather than aesthetics. Flowbite ships a
JavaScript layer that binds behaviour to elements on `DOMContentLoaded`; every time
HTMX swaps a fragment containing a modal, dropdown, or tooltip, that markup arrives
with no handlers attached, so you end up writing `htmx:afterSwap` re-initialisation
hooks across the app and debugging the ones you forgot. DaisyUI is a pure Tailwind
plugin — it emits semantic classes (`btn`, `card`, `modal`, `drawer`, `table`) and
ships zero JavaScript, so swapped-in HTML is styled and functional the instant it
lands, with Alpine supplying whatever interactivity the CSS-only patterns don't
cover. It also solves the branding requirement almost for free: DaisyUI themes *are*
CSS custom properties, so per-organization rebranding is a `<style>` block in
`base.html` that overrides `--color-primary` and friends from
`Organization.branding` — a settings change with no rebuild, which is precisely what
§7 asks for. The cost is that DaisyUI's stock look is rounder and more playful than
"clean, calm, clinical", so we define a custom theme from the seed palette on day
one — which we needed to do regardless.

---

## 3. Pushback on the spec

**(a) The role matrix vs. Django's permission system.** §6.1 wants per-organization,
data-driven permissions. Django already has `Permission` and `has_perm`. The proposal
is `RolePermission` as a per-org override table over a module-level list of
permission code constants, resolved by a **custom authentication backend** so that
`user.has_perm("clinical.view_narrative", obj)` works normally and
`PermissionRequiredMixin` keeps working. What this avoids is a bespoke
`can_user_do()` helper that lives beside Django's system and gets forgotten in half
the views. This is the one piece of framework-level machinery being proposed.

**(b) STAFF cannot see clinical data, but STAFF does the billing and dispensing.**
These conflict. An invoice generated from an encounter lists the medicines; the
receptionist who hands the patient a box of tablets necessarily knows what was
prescribed. The spec doesn't resolve this. Recommendation: define "clinical
narrative" as `Encounter.chief_complaint/examination/assessment/plan`,
`PatientClinicalProfile`, and `Attachment` at `CLINICAL`/`OWNER` access level — all
denied to STAFF — while **prescription items are visible to STAFF**, since
dispensing requires it. This needs an explicit ruling because it changes the
permission codes and the tests.

**(c) The organization-scoped default manager needs ambient state, which is exactly
the kind of magic §0 says to justify.** A `contextvar` set by middleware works for
requests and breaks silently in management commands, Celery tasks, and tests unless
every entry point sets it. The proposal: the contextvar-backed default manager *plus*
a hard convention that every `services.py` function takes `organization` as an
explicit first argument and never relies on ambient state, `all_objects` as the
documented escape hatch, and a parametrized test that walks every org-owned model
asserting cross-tenant queries return nothing. The alternative — no ambient state,
explicit `.filter(organization=...)` everywhere — is safer in principle but one
forgotten filter is a data breach, and there's no test that catches "you forgot".
This is the highest-consequence structural decision in the repo.

**(d) The PWA as specified provides installability, not offline capability.** An
app-shell service worker with an offline fallback page means a practitioner who
loses connectivity sees a "you're offline" screen instead of a Chrome dinosaur.
That's the entire benefit. If the clinic has unreliable internet, this doesn't help,
and real offline (queue writes buffered in IndexedDB, sync on reconnect) is a large
project that conflicts with §2's "no realtime". Recommendation: keep the spec as
written but state plainly in the README that the PWA is for installability and
home-screen presence, not offline use.

**(e) `django-simple-history` on "financial models" is partly redundant.**
`StockMovement` is an append-only ledger and `Payment` is never edited — history
tables on them are dead weight that double write volume. Apply history to `Invoice`
and `InvoiceItem` (which do get edited before issue) and skip the immutable ones.

**(f) Read-auditing needs a defined scope.** §8 wants reads of sensitive clinical
data logged. If that's middleware over every request, the audit log becomes
unreadable within a month and every patient list view writes rows. Proposal: an
explicit `@audit_read` decorator / mixin applied to a named set of views — patient
clinical profile, encounter detail, attachment download, prescription print, report
export — and nothing else.

**(g) Attachments on S3 vs. §8's "no public URL".** The obvious S3 pattern is a
short-lived presigned URL, but once issued it bypasses Django's permission check for
its lifetime. Recommendation: always stream through a permission-checked Django
view, keep the storage backend swappable behind `django-storages`, and only reach
for presigned URLs if bandwidth becomes a real problem. Worth an ADR.

**(h) `Organization.currency` alone is insufficient** — a currency change would
silently reprice every historical invoice. `Invoice.currency` is a snapshot. Same
reasoning for `InvoiceItem.unit_price` and `tax_percent`.

**(i) Models required by §6 but absent from §5.** Flagged as additions, per §0:
`Service` (§6.6 bills "services plus products", §6.8 has a "service catalog"),
`TaxRate`, `ProductCategory`, `Unit`, `GoodsReceipt`/`GoodsReceiptItem` (§6.5 goods
receipt), `SlotTemplate` (§6.3 slot templates), `FollowUp`/`FollowUpCall` (§6.3
follow-up tracking with call outcomes), `PractitionerProfile` (§6.4 needs a
registration number and signature on the prescription letterhead),
`PrescriptionTemplate`/`Item` (§6.4 regimen templates), `RolePermission` (§6.1
data-driven matrix), `FieldDefinition` (§5 prescription `attributes` + §5 patient
intake + §6.8 field definitions all describe one mechanism, unnamed),
`DocumentSequence`, `StockLevel`, `BranchAccess`.

**(j) Phase 0's layout shell will be partly rewritten in Phase 1.** The sidebar's
contents depend on role, and roles don't exist until Phase 1. Phase 0 builds the
shell with a hardcoded nav; expect to touch it again.

**(k) SPDX headers: not warranted.** One repository, one licence, `LICENSE` at root
and a line in the README. Per-file headers add noise to every source file for no
benefit at this scale.

**(l) Open question the spec doesn't lock: dependency management.** §3 fixes the
stack but not the tool. Recommendation: `uv` with `pyproject.toml` + `uv.lock` —
fast, lockfile-based, one tool for venv and deps, and it makes the Docker build cache
cleanly. `pip-tools` is the conservative choice.

**(m) Tailwind: standalone CLI, not `django-tailwind`.** `django-tailwind` adds a
`theme` Django app and requires Node inside the container. The standalone binary is a
single download that runs in a Docker build stage and produces one CSS file, leaving
the runtime image Python-only. Tailwind scans templates for class names and emits
only the CSS actually used, so it must run whenever templates change — in dev via
`--watch` alongside `runserver`, in the image build for production.

---

## 4. Phase 0 file tree

```
clinicore/
├── .github/
│   └── workflows/
│       └── ci.yml                    # ruff check, ruff format --check, pytest w/ postgres service, docker build
├── .pre-commit-config.yaml           # ruff, ruff-format, trailing-whitespace, check-merge-conflict
├── .dockerignore
├── .gitignore                        # + .venv_clinicore/, .idea/
├── .env.example
├── Dockerfile                        # multi-stage: tailwind build -> python runtime
├── docker-compose.yml                # web, db
├── docker-compose.override.yml       # dev: bind mounts, runserver, tailwind --watch
├── docker-compose.prod.yml           # web (gunicorn), db, caddy
├── Makefile                          # bootstrap, up, test, lint, fmt, migrate, seed, shell
├── pyproject.toml                    # ruff + pytest + coverage config, project deps
├── uv.lock                           # (pending the answer on 3(l))
├── LICENSE
├── README.md
├── CLAUDE.md
├── manage.py
│
├── caddy/
│   └── Caddyfile
│
├── config/
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py                   # django-environ, INSTALLED_APPS, AUTH_USER_MODEL
│   │   ├── dev.py
│   │   ├── prod.py                   # HSTS, secure cookies, whitenoise, storages
│   │   └── test.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── core/                             # ONLY app with real code in Phase 0
│   ├── __init__.py
│   ├── apps.py
│   ├── models.py                     # abstract bases only -> no migration
│   ├── managers.py                   # OrgScopedManager
│   ├── context.py                    # contextvar get/set/override for active org
│   ├── middleware.py                 # sets active org from membership
│   ├── context_processors.py         # org branding + terminology into templates
│   ├── templatetags/
│   │   └── terminology.py            # {% term "encounter" %}
│   ├── migrations/__init__.py
│   └── tests/
│       ├── __init__.py
│       └── test_org_scoping.py
│
├── accounts/            ─┐
├── organizations/        │
├── patients/             │  package skeletons only:
├── scheduling/           ├─ __init__.py, apps.py, models.py (empty),
├── clinical/             │  services.py (empty), migrations/__init__.py,
├── catalog/              │  tests/__init__.py
├── inventory/            │
├── billing/              │
└── reporting/           ─┘
│
├── templates/
│   ├── base.html                     # daisyUI shell, org theme vars, htmx+alpine
│   ├── partials/
│   │   ├── _sidebar.html
│   │   ├── _topbar.html
│   │   ├── _bottom_nav.html          # mobile
│   │   ├── _toast.html
│   │   ├── _empty_state.html
│   │   └── _loading.html             # htmx-indicator
│   ├── print/
│   │   └── base_print.html           # @page A4/A5, no app chrome
│   └── 403.html / 404.html / 500.html
│
├── static/
│   ├── src/
│   │   └── input.css                 # @tailwind + daisyui theme from seed palette
│   ├── css/                          # build output, gitignored
│   ├── js/
│   │   ├── htmx.min.js
│   │   └── alpine.min.js             # vendored, no CDN
│   └── pwa/
│       ├── manifest.webmanifest
│       └── sw.js
│
├── tailwind.config.js
│
├── docs/
│   ├── SPEC.md
│   ├── phase-0-proposal.md           # this file
│   ├── erd.md                        # the three Mermaid diagrams above
│   ├── deployment.md                 # stub, filled in Phase 6
│   └── adr/
│       ├── 0001-htmx-over-spa.md
│       ├── 0002-shared-schema-multitenancy.md
│       ├── 0003-ledger-based-stock.md
│       ├── 0004-daisyui-over-flowbite.md
│       └── 0005-org-scoped-default-manager.md
│
├── conftest.py                       # pytest-django fixtures
└── scripts/
    └── seed.py                       # stub; real synthetic data in Phase 6
```

Two notes on the tree. Apps sit at the repository root as siblings of `config/`,
which is what `startapp` produces and keeps `INSTALLED_APPS` unprefixed; an `apps/`
package is the alternative if the root should be kept tidier. And `core/models.py`
contains only abstract bases in Phase 0, so it generates no migration — `AuditLog`
lands in Phase 1 alongside the models it audits.

---

## 5. Open decisions

Proposed defaults, pending confirmation or override:

1. **Org-scoped manager (3c)** — proceed as proposed: contextvar + middleware,
   `all_objects` escape hatch, services take `organization` explicitly,
   parametrized cross-tenant isolation test over every org-owned model.
2. **STAFF and prescription items (3b)** — default to *visible*. Dispensing is a
   STAFF job; denying it makes the workflow impossible. Narrative fields,
   `PatientClinicalProfile`, and `CLINICAL`/`OWNER` attachments stay denied. This
   is a clinical-privacy policy call rather than a technical one and most needs an
   explicit ruling.
3. **Custom auth backend (3a)** — yes. Keeps `has_perm` and
   `PermissionRequiredMixin` working instead of a parallel helper nobody remembers
   to call.
4. **Dependency tool (3l)** — `uv` + `pyproject.toml` + `uv.lock`.
5. **Additional models (3i)** — accepted; all fourteen are required by §6
   requirements that §5 didn't enumerate.
6. **SPDX headers (3k)** — skip. `LICENSE` at root, one line in the README.