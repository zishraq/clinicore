# Clinicore

A generic clinic and practice management system with digital prescriptions,
built for small private practices: patient records, appointments, consultation
notes, prescriptions, billing, stock, and a print output that works on the
clinic's actual printer. Nothing in the code is specific to one specialty — a
second clinic is configuration and seed data, never a code change.

Django 6 · HTMX · Alpine · Tailwind + daisyUI · PostgreSQL. MIT licensed.

> **Status: feature-complete for daily clinic use, and in production at one
> clinic since August 2026** — a single Oracle Cloud Always Free box (Ubuntu
> 24.04 on ARM) running Docker Compose behind Caddy for TLS.
> Appointments, patients, encounters, prescriptions, printing, billing and
> inventory are built, tested and browser-verified. Reporting, attachments, an
> audit log and the data-driven permission layer are not. `docs/SPEC.md` is the
> full specification; `docs/MVP-NOTES.md` records what is deliberately missing
> and why.

## Run it locally

```bash
docker compose up -d
docker compose exec web python manage.py bootstrap_demo
```

Then open <http://localhost:8000/>. The bootstrap command prints three demo
logins (owner, practitioner, staff) and their shared password, and books a full
day of appointments so the day list has something in it. All demo data is
synthetic — and `bootstrap_demo` refuses to run with `DJANGO_DEBUG` off, because
what it invents cannot be deleted once a prescription, bill or stock movement
points at it. A real clinic is created by `bootstrap_clinic`, which seeds
nothing; see "Setting up a new clinic" in [docs/RUNBOOK.md](docs/RUNBOOK.md).

```bash
docker compose exec web python -m pytest    # tests
ruff check . && ruff format --check .       # lint and format
```

Postgres is published on host port **5433** to avoid colliding with a local
install; inside the compose network it is `db:5432`.

Running `manage.py` directly, outside Docker, needs two variables — the app
refuses to start in a production posture without them, which is the point:

```bash
export DJANGO_DEBUG=true          # off by default; see .env.example
export POSTGRES_DB=clinicore POSTGRES_HOST=localhost POSTGRES_PORT=5433
```

With `POSTGRES_DB` unset it falls back to SQLite, which is fine for most work —
but two concurrency tests skip themselves there, so run the suite against
Postgres before trusting it.

## Deploying it

Configuration is entirely environmental; copy `.env.example` to `.env` and fill
it in. There is no base/dev/prod settings split because there is nothing left to
split.

```bash
cp .env.example .env      # set DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate
```

`docker-compose.prod.yml` is a separate file rather than an override of the dev
one: gunicorn, no bind mount, no credentials in the file, a restart policy, the
database unpublished, and **migrations not run on boot** — they are an explicit
step, because replicas racing the same migration and a crash-looping container
at 3am are both worse than a deployment that stops and waits.

Two behaviours worth knowing before the first deploy:

- **`DEBUG` is off unless you turn it on, and the app will not start without a
  real `SECRET_KEY` when it is off.** It raises `ImproperlyConfigured` rather
  than falling back to the development key, which is committed to this public
  repository.
- **Set `DJANGO_BEHIND_PROXY` only when a proxy you control terminates TLS.**
  `X-Forwarded-Proto` is a client-supplied header; trusting it with nothing
  upstream overwriting it lets anyone claim HTTPS over plain HTTP.

Static files are served by the app through WhiteNoise, so a reverse proxy needs
no rules beyond forwarding. `GET /healthz` is an unauthenticated liveness check
that touches the database — a process accepting sockets with a dead connection
pool is exactly the state it exists to catch.

### Running it unattended

`deploy/` holds the operational layer, and **[docs/RUNBOOK.md](docs/RUNBOOK.md)**
is written for whoever looks after the box rather than for a developer: is it
running, how to restart it, what to do when the site is down, how to restore, how
to deploy an update, and what to say to the clinic meanwhile.

- **Surviving a power cut** takes three things, and only one is in the compose
  file: `restart: unless-stopped` for a crash, `systemctl enable docker` for the
  reboot, and `deploy/heal.sh` on a timer for a container that is *running but
  unhealthy* — which Docker records and never acts on.
- **Nightly encrypted backups** of the database and the photographs
  (`deploy/backup.sh`), rotated 30 daily / 12 monthly, pushed off-box with
  rclone. Encrypted with `age` to a **public key**, so the server cannot decrypt
  its own backups if it is stolen.
- **A monthly restore check** (`deploy/verify-restore.sh`) loads the newest
  backup into a scratch database and asserts it contains a clinic. A backup
  nobody has restored is a guess.
- **The dashboard shows administrators the age of the last successful backup**
  and shouts when it is stale. There is no email on the box, so a job that
  quietly stopped three weeks ago has nowhere else to report itself.

### Backups: `pg_dump` alone is no longer enough

Visits can carry photographs, which live in the `media_data` Docker volume and
**not** in the database. A nightly `pg_dump` now captures rows that point at
files it does not contain, so restoring it produces a clinic whose visits are
all intact and every photograph is missing — with no error anywhere, because
nothing is corrupt. The rows are fine. The pictures are gone.

The backup set is both:

```bash
docker compose -f docker-compose.prod.yml exec -T db \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > clinicore-db.sql.gz

docker run --rm -v clinicore-prod_media_data:/media -v "$PWD":/backup alpine \
    tar czf /backup/clinicore-media.tar.gz -C /media .
```

Uploaded files are never served from a URL — they go through a permission-checked
view, so there is no `/media/` route to protect. See
[ADR 0014](docs/adr/0014-encounter-photos-served-through-a-view.md).

## What's in it

- **Multi-tenant by construction.** Every business row carries an
  `organization` FK and the default manager filters on the active organization,
  which is set once per request by middleware. Crossing tenants requires typing
  something explicit and greppable. See
  [ADR 0005](docs/adr/0005-org-scoped-default-manager.md); a parametrized test
  walks every org-owned model and fails if a new one arrives without coverage.
- **Roles enforced at the view layer.** OWNER, PRACTITIONER, and STAFF.
  Receptionists manage patient demographics and the day's appointments;
  clinical narrative, profiles, encounters, billing and stock are
  practitioner-only, and a direct URL hit returns 403 rather than a hidden
  template block. That the check lives at the view and nowhere below it is a
  decision, not an omission —
  [ADR 0012](docs/adr/0012-authorisation-at-the-view-boundary.md) says what it
  obliges every new view to do.
- **Every user-facing word is configurable.** `Organization.terminology` maps
  domain concepts to whatever the clinic calls them, applied through a context
  processor and a `{% status_label %}` tag. Out of the box an Encounter is a
  "Visit", a DRAFT is "Open", an Invoice is a "Bill". Stored values, field
  names and URLs never move, so relabelling is not a migration.
- **Appointments as one day list.** A single `/schedule/` screen for the day:
  booked patients and walk-ins in one table, mark-arrived, cancel, no-show,
  start-visit, and a payment column. There is no separate queue model — a
  walk-in is an appointment created already arrived, because a patient in two
  tables is a patient the two tables can disagree about
  ([ADR 0010](docs/adr/0010-appointments-as-one-day-list.md)). Status is
  computed from timestamps rather than stored, and the list polls itself.
- **Patients.** Debounced HTMX live search by name, phone, or code; duplicate
  warning on create; soft delete. The visit form can register a new patient
  without losing the half-written visit.
- **Encounters and prescriptions.** One-page consultation form with
  add-as-you-go prescription rows, per-item dosage/frequency/duration, and a
  JSON `attributes` field for specialty-specific values. Saving completes the
  visit; every edit afterwards is a reasoned amendment.
- **Two catalogs, one search box.** Medicines *and* reusable advice, searched
  together from the prescription row with arrow-key navigation, defaults
  prefilled on selection, and inline quick-add when something is missing — so
  the catalogs stay current instead of going stale. Prescriptions freeze the
  name they were written with, so renaming a catalog entry never rewrites a
  document a patient already holds
  ([ADR 0007](docs/adr/0007-catalogs-and-name-snapshots.md)).
- **Amendments, never silent overwrites.** Finalizing locks an encounter;
  correcting it afterwards is an amendment that requires a reason and writes a
  revision, viewable as a per-field diff of who changed what, when, and why.
  See [ADR 0006](docs/adr/0006-encounter-amendments.md) — including why the
  history tables need explicit tenant filtering.
- **Billing that survives instalments.** Bills raised from a completed visit or
  standalone, the consultation fee as its own prefilled line, product lines from
  the same catalog search, and as many part-payments as a patient needs. The
  balance is derived from the ledger rather than stored, overpayment is refused
  with the figure to type instead, invoice numbers are gap-free per clinic under
  concurrent creation, and a mistake is voided with a reason rather than deleted
  ([ADR 0008](docs/adr/0008-invoice-numbering-and-derived-balances.md)).
- **Stock as an append-only ledger.** Goods receipts, batches with lot and
  expiry, manual adjustments with a reason, and on-hand computed as the sum of
  movements rather than stored in a column. Selling a product on a bill
  decrements stock automatically, FEFO across batches; expired lots are shown
  and then refused rather than hidden, and a bill that moved stock is frozen —
  the correction is void-and-reissue, which posts a return
  ([ADR 0009](docs/adr/0009-ledger-based-stock.md)).
- **Printing.** A standalone print view with real `@page` A5/A4 geometry, clinic
  letterhead from the organization's branding, and no app chrome — no CDN, no
  framework, so it renders the same on a machine with no internet. Medicines and
  advice print as separate sections, each omitted when empty, and the receipt
  reuses the same sheet so a bill and a prescription look like one clinic.
- **The clinic's own clock.** The organization's timezone is activated per
  request beside its tenancy context; storage stays UTC and only presentation
  moves ([ADR 0011](docs/adr/0011-organization-timezone-per-request.md)).
- **Login rate limiting.** Failed sign-ins are locked on phone *and* address
  together, database-backed so a lockout holds across workers. Locking on
  address alone would let anyone who can reach the login page shut an entire
  clinic out of its own records.

## Repository layout

```
config/          settings, root urlconf, wsgi
core/            abstract bases, org-scoping machinery, dashboard, healthz,
                 terminology tags, bootstrap_clinic / bootstrap_demo
organizations/   Organization (branding, terminology, timezone), Branch
accounts/        custom User (phone login), Membership, roles, auth views
patients/        Patient, PatientClinicalProfile
catalog/         Product, AdviceTemplate, autocomplete, quick-add
scheduling/      Appointment, the day list, walk-ins, follow-ups
clinical/        Encounter, Prescription, PrescriptionItem, print view
billing/         Invoice, InvoiceItem, Payment, receipt print view
inventory/       StockBatch, StockMovement, GoodsReceipt, FEFO allocation
templates/       app shell (daisyUI) + the standalone print sheets
docs/            SPEC.md, MVP-NOTES.md, phase-0-proposal.md, adr/
```

## Tests and CI

373 test functions run on every push against a real Postgres
(`.github/workflows/ci.yml`), alongside `ruff`, `manage.py check`, and a
`makemigrations --check` that catches a model edited without a migration.

Postgres in CI is not incidental. Gap-free invoice numbering and FEFO stock
allocation are row-lock guarantees, and both tests call `pytest.skip` on SQLite
— they report green without executing. CI fails explicitly if either one skips.

## Licence

MIT — see [LICENSE](LICENSE).

Hind Siliguri, self-hosted in `static/fonts/`, is a separate work: copyright © 2015
Indian Type Foundry, licensed under the SIL Open Font License 1.1. The licence
travels with the font files as required by its section 2 — see
[static/fonts/OFL.txt](static/fonts/OFL.txt).
