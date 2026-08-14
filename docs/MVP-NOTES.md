# Build notes — what is deliberately missing, and what was learned the hard way

The diff between what exists and what `docs/SPEC.md` asks for, so the gap is
documented rather than forgotten. Everything below is a deliberate omission or a
recorded trap, not a to-do list.

Substantive reasoning about decisions that were *made* lives in `docs/adr/`;
this file covers what was **not** built and what keeps biting.

> This file was substantially rewritten on 2026-08-08. It had been describing a
> project that stopped before scheduling and inventory, and claimed the
> terminology map was unmodelled and that there was no `StockBatch`,
> `StockMovement` or goods receipt. All of that had been built, tested and
> browser-verified in the meantime. If something here reads as stale again,
> trust the code and `CLAUDE.md`.

## Not built

### Still genuinely absent

- **`RolePermission` and the custom auth backend** (SPEC §6.1,
  `docs/phase-0-proposal.md` §3(a)) — replaced by plain role comparisons. All
  eight are marked `# MVP: replace with permission layer` and concentrated in
  `accounts/permissions.py`; `grep -rn 'MVP: replace with permission layer'`
  finds them. The swap is meant to stay mechanical, and
  [ADR 0012](adr/0012-authorisation-at-the-view-boundary.md) explains why it
  must happen at the same layer rather than moving checks downward.
- **`FieldDefinition`** — the org-level, data-driven field configuration in
  SPEC §5. The *terminology* half of that section is built
  (`Organization.terminology`, the context processor, `{% status_label %}`), but
  nothing drives the **shape** of `PrescriptionItem.attributes`: the column
  exists and is written, and what goes in it is decided in code.
- **Reporting** (SPEC §6.7) — the dashboard is a few counts and recent activity,
  not the per-role reporting screens. No revenue, no stock valuation, no
  follow-ups-due list, although `follow_up_date` is indexed and waiting.
- **PWA / offline** — not started.
- **The audit log** — not started. History exists on clinical models only
  (below), which is not the same thing.
- **Attachments beyond photographs on a visit.** `clinical.EncounterPhoto`
  covers SPEC §5's `Attachment` only where it points at an encounter and only
  for images: PDFs are refused on content, and there is no `access_level`
  because the whole clinical app is already one role gate. Attachments on a
  **patient** — an ID card, a signed consent form — are genuinely absent, and
  adding them is a second model rather than a change to this one. See
  [ADR 0014](adr/0014-encounter-photos-served-through-a-view.md).
- **`Membership` has no branch FK.** SPEC §5 wants per-branch access; it is not
  built, which is why "the practitioner's branch" is inferred from their last
  encounter in `billing.services.resolve_invoice_branch`.
- **Pre-commit hooks, the ERD in the README, ADRs 0001–0004, and the
  `Makefile`** — `make seed` does not exist.

### Left out on purpose, and recorded as a decision

- **Password reset by email.** There is no `PasswordResetView`, no SMTP
  configuration, and no "forgot your password" link, and that is a decision
  rather than an oversight —
  [ADR 0013](adr/0013-user-management-without-email.md). `User.email` is
  optional and never verified, phone is the identifier, and a self-hosted box
  has no mail sending: an SMTP dependency that fails silently is worse than no
  feature, because the user is told to check an inbox nothing will ever reach.
  Recovery is an administrator setting a temporary password on the team screen
  and reading it out, with `User.must_change_password` forcing a replacement.
  Adding real email later is additive and moves nothing.
- **A permission matrix screen.** Three fixed roles and a dropdown. SPEC §6.1
  asks for per-organization `RolePermission` rows; a UI for editing them is
  configuration a five-person clinic will get wrong rather than a feature it
  wants. Still listed as genuinely absent above, because the *data-driven* half
  is what is missing, not the screen.

### Struck from the spec on purpose

- **`QueueEntry`** and SPEC §6.3's "reorder" and "slot templates". A walk-in is
  an `Appointment` with `source=WALK_IN`, created already arrived. Two tables
  that can disagree about whether a patient is still waiting is a worse problem
  than the one they solve. See
  [ADR 0010](adr/0010-appointments-as-one-day-list.md).

## Deviations chosen, with reasons

- **Settings are one module, not `base/dev/prod`** (SPEC §4). Everything
  environment-specific reads from the environment, so the two deployments differ
  by `.env` rather than by module and there is nothing left for the split to
  separate. `django-environ` is not installed; `os.environ` with two small
  helpers does the same job.
- **Django 6.0, not 5.x** (SPEC §3). Nothing depends on a 6.0-only API, but
  `CheckConstraint(condition=…)` is 5.1+ syntax (`check=` was removed in 6.0).
- **No `django-crispy-forms`.** Forms are rendered field-by-field in templates
  with daisyUI classes.
- **History is on clinical models only** — `Encounter`, `Prescription`,
  `PrescriptionItem`. SPEC §3 also lists `Patient`; the surface was kept small
  deliberately. See [ADR 0006](adr/0006-encounter-amendments.md), including the
  tenancy caveat: historical tables are **not** organization-scoped and every
  history query must filter on `organization_id` by hand.
- **No soft delete on `Encounter`.** SPEC §4 lists clinical records as
  soft-delete; there is no encounter-delete path at all, so the mixin would be
  unused columns. `Patient` and `PatientClinicalProfile` do soft delete.
- **`Encounter.practitioner` is a FK to `User`, not to `Membership`**
  (`docs/phase-0-proposal.md` §1.3). Simpler while there is one organization per
  session; changing it later is a data migration, so it is worth revisiting
  before real data exists.
- **Frontend assets come from a CDN.** Tailwind (Play CDN), daisyUI, HTMX, and
  Alpine load from jsDelivr in `templates/base.html`. The proposal called for a
  Tailwind build step and vendored JS with no CDN; that needs Node in the image.
  **The app therefore needs internet access in the browser to look right.** The
  print views are deliberately exempt — standalone, hand-written CSS, correct
  offline. The app's *own* CSS and JS are served locally by WhiteNoise.
- **Branding is applied as `--cc-*` CSS custom properties, not a compiled
  daisyUI theme.** daisyUI's theme variables are OKLCH triplets, which would
  mean converting hex at request time. `static/css/app.css` consumes the
  `--cc-*` tokens for the handful of brand-coloured classes. Rebranding is still
  a settings change, which is what SPEC §7 asks for.
- **No factory-boy.** Fixtures build models directly.

## Traps worth knowing before the next session

### Tenancy and forms

- **`core/forms.py` exists because of a real trap.** `ForeignKey.formfield()`
  reads `Model._default_manager` at *class definition* time, so any `ModelForm`
  with a relation to an org-scoped model raises `ActiveOrganizationRequired` on
  import — before a request, before any organization is active. Forms with such
  relations set `Meta.formfield_callback = staticmethod(org_scoped_formfield)`,
  which starts from `all_objects.none()` and narrows per organization in
  `__init__`. Any new form touching an org-owned FK needs the same line.
- **`Membership` is not an `OrgOwnedModel`.** The middleware queries it to
  *establish* the active organization, so it must be readable before any
  organization is active. It carries an explicit `organization` FK and a plain
  manager. **The team screen is therefore the one surface where a forgotten
  `.filter(organization=…)` shows another clinic's staff instead of an empty
  page.** Every lookup there goes through `services.organization_members`, which
  takes the organization as an argument precisely so it cannot be omitted
  silently, and `accounts/tests/test_team.py` asserts both the list and the
  by-pk routes directly.
- **A `disabled` form field is a server-side guard, not decoration.** Django
  ignores submitted data for one and uses the initial value instead, which is
  what stops an administrator demoting themselves with a hand-built POST
  (`MemberUpdateForm`). Worth knowing before someone "fixes" it into a
  `clean_role` refusal that a disabled field could never reach anyway.
- **The admin reads through `all_objects`** (`core/admin.py`). Registering an
  org-owned model with a plain `ModelAdmin` will raise on the changelist —
  subclass `OrgOwnedAdmin`.
- **`dosage` is nullable, deliberately.** Advice has no dose, and empty string
  would read as "none recorded" rather than "not applicable". This is the single
  `# noqa: DJ001` in the codebase.
- **`request.POST or None` is wrong for a checkbox-only form.** An unticked
  checkbox posts nothing, so the QueryDict is empty and falsy, and the usual
  idiom silently rebuilds the form unbound and saves nothing — turning a feature
  off would look like it worked. Bind on `request.method` instead.

### The frontend

- **The prescription autocomplete is the most JS-dependent thing in the repo**,
  and every one of its bugs was invisible to the test suite. Three worth
  remembering: `hx-vals="js:{q: event.target.value}"` throws once a `delay:` is
  on the trigger (`event` is gone) and silently sends `"undefined"` if you
  switch to `this` — htmx 2.0.4 binds neither, so the view reads the row's own
  `display_name` parameter instead; Alpine's `$el` is the *evaluating* element,
  not the component root, so `$el.querySelector` inside an event handler
  searches the input rather than the row (use `$root`); and a formset row
  removed from the DOM posts nothing, which Django's default `has_changed()`
  reads as a filled-in row, so `PrescriptionItemForm` judges emptiness by
  content instead.
- **Bugs found by opening a browser, not by tests**, all invisible to the suite
  because they were about rendering rather than status codes: multi-line
  `{# … #}` is not a Django comment and rendered as visible text on the print
  page (twice); `{% block %}` does not cross an `{% include %}`, so the topbar's
  title and action buttons never rendered; the HTMX search pushed a
  partial-only URL into the address bar that broke on reload; and the encounter
  history labelled an amendment as "Created". Keep looking at actual pages.
- **`templates/base.html` bottom padding is load-bearing**: `pb-24 sm:pb-24
  lg:pb-6`. Tailwind emits responsive variants after base utilities, so a bare
  `pb-24` loses to `sm:p-6` from 640px up and the fixed bottom nav covers the
  foot of every scrollable page, submit buttons included.
  `core/tests/test_layout.py` is a canary, not a proof.
- **Nothing inside a polled container may hold typed input.** `#day-rows`
  refetches every 5s, so the walk-in and cancellation forms live in modals. A
  swap over a half-written cancellation reason gets reported as "it clears what
  I type".
- **A visibility-guarded poll cannot be observed from a driven browser tab.** An
  automated tab reports `visibilityState === 'hidden'`, so the guard suppresses
  every tick and "did my typed text survive the poll?" passes with no swap ever
  having happened. Force the identical swap by hand or the check is worthless.

### Deployment

- **Docker maps Postgres to host port 5433**, not 5432, because 5432 was already
  in use on the dev machine. Inside the compose network it is still `db:5432`.
- **Compose derives the project name from the directory**, so `docker-compose.yml`
  and `docker-compose.prod.yml` would both be project "clinicore" and both
  resolve `postgres_data` to the *same volume* — production silently adopting
  the development database, demo patients and all. The prod file pins
  `name: clinicore-prod` for this reason. Found by running both in one
  directory.
- **`.env` must stay in `.dockerignore`.** `COPY . .` otherwise bakes a real
  `SECRET_KEY` and database password into an image layer, where they survive in
  the history even if a later step deletes the file. Observed in an image built
  here before the entry was added.
- **`collectstatic` is not optional now.** WhiteNoise's manifest storage
  resolves `{% static %}` through `staticfiles.json`; a missing entry raises at
  render time. That is the intended trade — the failure it replaced was three JS
  files 404ing while the CDN kept the page looking correct, so the patient
  picker and invoice line editor were dead on a page that appeared fine.
- **A `pg_dump` is no longer a complete backup, and nothing will tell you.**
  Visit photographs live in the `media_data` volume; the database only holds
  rows pointing at them. Restore the dump on its own and every visit is intact
  with every photograph missing — no error, no corruption, nothing to notice
  until someone looks for a lab report. The backup set is the dump **and** the
  volume, and the tested restore SPEC §8 asks for has to cover both. Commands
  are in the README's deployment section;
  [ADR 0014](adr/0014-encounter-photos-served-through-a-view.md) has the
  reasoning.
- **`MEDIA_URL` is routed by nothing, deliberately, in every mode.** Photographs
  are served by `clinical.views.encounter_photo` behind login, a role check and
  organization scoping. Adding `if settings.DEBUG: urlpatterns += static(...)`
  looks like a development convenience and is the bug: development would stop
  exercising the protected view, so a missing decorator there would first show
  up in production. `core/tests/test_media_not_served.py` is the canary.
- **The prod volume needs its mount point to exist in the image.** Docker seeds
  a fresh named volume from the image directory's ownership, so `/app/media` is
  created in the Dockerfile *before* the `chown` — otherwise the mount point is
  root-owned and the non-root user cannot write a single upload. Silent until
  the first photograph.

## Lessons

### A suite that only tests the default configuration proves nothing

The organization's timezone was written and never read for the whole MVP —
`Organization.timezone` existed, `bootstrap_demo` wrote `Asia/Dhaka` to it, and
no code called `timezone.activate`. Every datetime rendered six hours behind the
clinic. Fixed in [ADR 0011](adr/0011-organization-timezone-per-request.md).

The lesson generalises well beyond timezones, which is why it is kept here:

- **The bug was invisible because the default `Organization.timezone` is
  `'UTC'`,** where storage, display and "today" all agree. Every test passed.
  `core/tests/test_organization_timezone.py` now runs on `Asia/Dhaka` throughout
  and pins `timezone.now` to 19:30 UTC — 01:30 next day in Dhaka — so the
  calendars genuinely disagree. **Keep a non-UTC org in any future work here.**
- **The corruption ran the opposite way from how it looked.** Accepting the
  wrong-looking default was *safe*: render and parse both used UTC, so the value
  round-tripped. **Correcting** the field was what wrote bad data — a doctor
  seeing "02:06 PM" at eight in the evening fixes it to `20:06`, which was
  parsed as UTC and stored as 02:06 the *following day* in Dhaka. A clinic that
  never looked at the clock kept good data by doing nothing.
- **Existing hand-edited datetimes are still wrong.** There is no migration,
  because nothing distinguishes them from values that were always right. Treat
  any hand-corrected datetime from before 2026-08-07 as suspect.

### "No configuration" is not the same as "sensible defaults"

There was no `LOGGING` block at all until 2026-08-08, and the assumption that
Django's defaults would cover it was wrong in two different directions:

- Application warnings — including the bad-timezone warning above, which exists
  precisely so a misconfigured tenant is noticed — reached stderr only through
  `logging.lastResort`, the fallback for when no handler exists anywhere. That
  writes the bare message: no timestamp, no level, no logger name.
- **Unhandled 500s were lost entirely.** `django.request` propagates to the
  `django` logger, which Django's defaults give a console handler filtered by
  `require_debug_true` and an `AdminEmailHandler` with `ADMINS = []`. Because
  handlers existed, `lastResort` never fired; because both dropped the record,
  nothing was written anywhere at all.

`core/tests/test_logging.py` guards both by reading what the *configured*
handlers emit. Note that `caplog` cannot test this — it attaches a handler of
its own, so it measures pytest rather than the settings.

### A library's default can be wrong for your model and say nothing

`django-axes` defaults `AXES_USERNAME_FORM_FIELD` to
`get_user_model().USERNAME_FIELD`, which is `phone` here — but
`AuthenticationForm` names its field `username` whatever the model calls it.
Left at the default, axes found no key it recognised and recorded **every**
attempt as `username=None`, silently collapsing the lockout key to the IP alone:
one attacker locking out the entire clinic.

Nothing user-visible changes when this is wrong. The lockout still fires, at the
wrong granularity. It was caught by printing `AccessAttempt` rows during the
first browser-shaped check, and is now pinned by
`accounts/tests/test_login_lockout.py`.
