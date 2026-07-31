# Clinicore

A generic clinic and practice management system with digital prescriptions,
built for small private practices: patient records, consultation notes,
prescriptions, and a print output that works on the clinic's actual printer.
Nothing in the code is specific to one specialty — a second clinic is
configuration and seed data, never a code change.

Django 6 · HTMX · Alpine · Tailwind + daisyUI · PostgreSQL. MIT licensed.

> **Status: MVP.** A working slice — organizations, auth and roles, patients,
> encounters, prescriptions, and printing. Scheduling, inventory, billing, and
> reporting are not built. `docs/SPEC.md` is the full specification;
> `docs/MVP-NOTES.md` records exactly what the MVP cut and why.

## Run it locally

```bash
docker compose up -d
docker compose exec web python manage.py bootstrap_demo
```

Then open <http://localhost:8000/>. The bootstrap command prints three demo
logins (owner, practitioner, staff) and their shared password. All demo data is
synthetic.

```bash
docker compose exec web python -m pytest    # tests
ruff check . && ruff format --check .       # lint and format
```

Postgres is published on host port **5433** to avoid colliding with a local
install; inside the compose network it is `db:5432`.

## What's in it

- **Multi-tenant by construction.** Every business row carries an
  `organization` FK and the default manager filters on the active organization,
  which is set once per request by middleware. Crossing tenants requires typing
  something explicit and greppable. See
  [ADR 0005](docs/adr/0005-org-scoped-default-manager.md); a parametrized test
  walks every org-owned model and fails if a new one arrives without coverage.
- **Roles enforced at the view layer.** OWNER, PRACTITIONER, and STAFF.
  Receptionists manage patient demographics; clinical narrative, profiles, and
  encounters are practitioner-only, and a direct URL hit returns 403 rather than
  a hidden template block.
- **Patients.** Debounced HTMX live search by name, phone, or code; duplicate
  warning on create; soft delete.
- **Encounters and prescriptions.** One-page consultation form with add-as-you-go
  prescription rows, finalize-to-lock, and per-item dosage/frequency/duration
  plus a JSON `attributes` field for specialty-specific values.
- **Printing.** A standalone print view with real `@page` A5/A4 geometry, clinic
  letterhead from the organization's branding, and no app chrome — no CDN, no
  framework, so it renders the same on a machine with no internet.

## Repository layout

```
config/          settings, root urlconf
core/            abstract bases, org-scoping machinery, dashboard, bootstrap_demo
organizations/   Organization, Branch
accounts/        custom User (phone login), Membership, roles, auth views
patients/        Patient, PatientClinicalProfile
clinical/        Encounter, Prescription, PrescriptionItem, print view
templates/       app shell (daisyUI) + the standalone print sheet
docs/            SPEC.md, MVP-NOTES.md, phase-0-proposal.md, adr/
```

## Licence

MIT — see [LICENSE](LICENSE).
