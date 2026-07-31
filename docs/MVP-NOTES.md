# MVP notes — what was cut, stubbed, or decided under time pressure

One evening's build against `docs/SPEC.md`. The delivery plan in SPEC §11 was
suspended for this run; blocks 1–8 of the MVP brief were built instead. This
file is the diff between what exists and what the spec asks for, so the gap is
documented rather than forgotten.

Everything below is a deliberate omission, not an oversight.

## Explicitly out of scope for the MVP brief

These were cut by the brief itself, not by me:

- **Terminology map and `FieldDefinition`** — `Organization.terminology`
  (SPEC §5) and the org-level field-definition config are not modelled. The
  `PrescriptionItem.attributes` JSON column exists and is written, but nothing
  drives its shape yet.
- **`RolePermission` and the custom auth backend** (SPEC §6.1,
  `docs/phase-0-proposal.md` §3(a)) — replaced by plain role comparisons.
  Every one is marked `# MVP: replace with permission layer` and they are
  concentrated in `accounts/permissions.py`; `grep -rn 'MVP: replace with
  permission layer'` finds all of them.
- **Catalog / `Product` FK on prescription items** — items are free text
  (`free_text_name`). SPEC §5 wants a nullable `product` FK with a check
  constraint that exactly one of the two is set; that lands with the catalog app
  and is an additive migration.
- **Scheduling, queue, inventory, billing, reporting, PWA** — whole apps, not
  started.

## Deviations I chose, with reasons

- **Settings are one module, not `base/dev/prod`** (SPEC §4). Everything
  environment-specific already reads from the environment, so splitting the file
  later is mechanical. `django-environ` is not installed; `os.environ` with a
  small `_env_bool` helper does the same job for six settings.
- **Django 6.0, not 5.x** (SPEC §3). 6.0.7 is what was already installed in the
  virtualenv. Nothing in the code depends on a 6.0-only API, but
  `CheckConstraint(condition=…)` is 5.1+ syntax (`check=` was removed in 6.0).
- **No `django-simple-history`** (SPEC §3, §6.4). Not installed, and adding a
  dependency needed asking. Consequence: **a finalized encounter is read-only**
  rather than amendable-with-history. `Encounter.is_editable` is the gate, and
  the edit view redirects with an error instead of silently overwriting. This is
  the most significant clinical-safety deviation in the MVP — the spec wants
  corrections recorded as history entries, and the MVP simply forbids them.
- **No `django-crispy-forms`, no `django-axes`** (SPEC §3, §6.1). Forms are
  rendered field-by-field in templates with daisyUI classes. **Login is not rate
  limited** — that is a real security gap against the spec and should be the
  first dependency added.
- **No soft delete on `Encounter`.** SPEC §4 lists clinical records as
  soft-delete; the MVP has no encounter-delete path at all, so the mixin would
  have been unused columns. `Patient` and `PatientClinicalProfile` do soft
  delete.
- **`Encounter.practitioner` is a FK to `User`, not to `Membership`**
  (`docs/phase-0-proposal.md` §1.3). Simpler while there is one organization per
  session; changing it later is a data migration, so it is worth revisiting
  before real data exists.
- **Frontend assets come from a CDN.** Tailwind (Play CDN), daisyUI, HTMX, and
  Alpine load from jsDelivr in `templates/base.html`. The proposal called for a
  Tailwind build step and vendored JS with no CDN; that needs Node in the build,
  which is deployment work. **The app therefore needs internet access in the
  browser to look right.** The prescription print view is deliberately exempt —
  it is standalone, hand-written CSS and renders correctly offline.
- **Branding is applied as `--cc-*` CSS custom properties, not a compiled
  daisyUI theme.** daisyUI's theme variables are OKLCH triplets, which would
  mean converting hex at request time. `static/css/app.css` consumes the
  `--cc-*` tokens for the handful of brand-coloured classes. Rebranding is still
  a settings change, which is what SPEC §7 asks for.
- **Tests are minimal by instruction.** The tenant-isolation suite, the STAFF
  403s, login, and an encounter create→finalize round trip. No factory-boy (not
  installed) — fixtures build models directly.

## Things worth knowing before the next session

- **`core/forms.py` exists because of a real trap.** `ForeignKey.formfield()`
  reads `Model._default_manager` at *class definition* time, so any `ModelForm`
  with a relation to an org-scoped model raises `ActiveOrganizationRequired` on
  import — before a request, before any organization is active. Forms with such
  relations set `Meta.formfield_callback = staticmethod(org_scoped_formfield)`,
  which starts from `all_objects.none()` and narrows per organization in
  `__init__`. Any new form touching an org-owned FK needs the same line. This
  belongs in ADR 0005's consequences list.
- **`Membership` is not an `OrgOwnedModel`.** The middleware queries it to
  *establish* the active organization, so it must be readable before any
  organization is active. It carries an explicit `organization` FK and a plain
  manager.
- **The admin reads through `all_objects`** (`core/admin.py`). Registering an
  org-owned model with a plain `ModelAdmin` will raise on the changelist —
  subclass `OrgOwnedAdmin`.
- **Three bugs found by opening a browser, not by tests**, all invisible to the
  test suite because they were about rendering rather than status codes:
  multi-line `{# … #}` is not a Django comment and rendered as visible text on
  the print page; `{% block %}` does not cross an `{% include %}`, so the
  topbar's page title and action buttons never rendered; and the HTMX search
  pushed a partial-only URL into the address bar that broke on reload. Keep
  looking at the actual pages.
- **Docker maps Postgres to host port 5433**, not 5432, because 5432 was already
  in use on the dev machine. Inside the compose network it is still `db:5432`.

## Not done at all

CI (`.github/workflows/ci.yml`), pre-commit hooks, the ERD in the README,
ADRs 0001–0004, `.env.example`, deployment (gunicorn, Caddy, WhiteNoise,
backups), attachments, audit log, and the `Makefile`.
