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

The batch override in a **multi-branch** org is now browser-verified too
(2026-08-08, against the production stack): choosing a branch on the bill form
fires the `hx-include="[name='branch']"` lookup in `_line_branch` and every line
gains its "Automatic — earliest expiry first" selector. That was the last part
of the options lookup with only tests behind it.

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

Appointments (SPEC §6.3) are built. Increment 1 was the `scheduling` app's model,
services and tests plus the terminology keys; increment 2 is the screen — the day
view at `/schedule/`, walk-in creation, mark-arrived, cancel and no-show, start
visit, the payment column, both navs, and HTMX polling. **Browser-verified end to
end across two sessions** (STAFF and PRACTITIONER): propagation, both modals, the
picker and its registration offer inside a modal, start visit → seen, the payment
badges, and the refusal paths. Rules to know before touching it, all in
`docs/adr/0010-appointments-as-one-day-list.md`:

- **One model, not two.** `QueueEntry` is struck from SPEC §5 and §6.3's
  "reorder" and "slot templates" go with it. A walk-in is an `Appointment` with
  `source=WALK_IN`, created already arrived. A booked patient who turned up
  would otherwise exist in two tables that can disagree about whether they are
  still waiting.
- **Status is never a column.** The five states are computed from `resolution`,
  `seen_at` and `arrived_at` — all three have to exist anyway, so a `status`
  field would be a fourth value restating them. Same call as the derived invoice
  balance and batch on-hand. Four check constraints keep the derivation
  unambiguous rather than precedence-dependent.
- **SEEN is a timestamp, not a lookup through the encounter link.** Deriving it
  from "a visit points here" would let a future soft delete on `Encounter`
  silently revert the row to ARRIVED — this day's history rewriting itself. The
  link still says *which* visit; `seen_at` says the appointment was consumed.
  `test_seen_survives_the_visit_being_deleted` is the guard.
- **`Encounter.appointment` is a nullable OneToOne.** Nullable because a visit
  with no appointment must stay completely valid; one-to-one because two visits
  off one row would make "was this seen?" ambiguous.
- **SEEN is a consequence, not a button.** `transition(to=SEEN)` refuses without
  an encounter, so an ARRIVED row whose doctor never wrote a visit stays ARRIVED.
  That is information the receptionist needs, not a gap.
- **NO_SHOW is not terminal.** `NO_SHOW → ARRIVED` is allowed and clears the
  resolution — patients turn up late, and rebooking them to say so is a worse
  lie than the one it prevents.
- **`follow_up_date` keeps one writer.** The field stays (SPEC §6.3's
  follow-ups-due list wants an indexed date), and `scheduling.services.reschedule`
  is the only thing that writes it once an appointment exists. That single-writer
  rule is what makes the sync safe, so `test_follow_ups.py` tests it directly.
- **`bootstrap_demo` books its own day now.** `_appointments` puts all five
  states on today, through `scheduling.services` rather than by writing rows, so
  the demo cannot hold a combination the application would refuse. One seen visit
  is billed and one is not, because "no bill yet" is the other thing the payment
  column has to say. `Appointment` PROTECTs the patient and branch, so teardown
  order still matters; the hand-staged row in
  `core/tests/test_bootstrap_demo.py` stays as the case with nothing attached.

Increment 2's screen, beyond the CRUD:

- **Nothing in the polled container may hold typed input.** `#day-rows` refetches
  every 5s, so the walk-in and cancellation forms live in modals in `base.html`'s
  `modals` block and the row buttons only fetch them. A swap over a half-written
  cancellation reason gets reported as "it clears what I type".
  `test_the_polled_fragment_holds_no_typed_input` asserts it rather than trusting
  it. The poll is guarded by `[document.visibilityState === 'visible']` so tabs
  left open all day cost nothing.
- **A page that renders the patient picker owes it the add-patient dialog.**
  `templates/partials/_patient_picker.html` is now shared by the visit form and
  the walk-in modal, and `base.html`'s `modals` block is empty by design — so
  omitting `templates/patients/_add_patient_modal.html` gives an
  `htmx:targetError` and a dead "add a new patient" offer. The walk-in modal
  shipped that way past a green suite. The coupling is now asserted by reading
  the offer's own `hx-target` out of the fragment.
- **`patient_quick_create` is any-member now**, not PRACTITIONER/OWNER: the
  receptionist is who registers a walk-in. `require_membership` still runs, so it
  is exactly as open as `patient_create`. SPEC §6.1 gives STAFF patient creation
  and the clinical profile is not on that form.
- **"Start visit" is on the day list, gated by role, not moved off it.** Before
  this, `ARRIVED → SEEN` was unreachable from anywhere in the app. The doctor
  learns someone arrived here, so he acts on it here; STAFF is gated out on
  `can_view_clinical` because an offer they cannot follow is an invitation to a
  403. The link prefills the visit form from the row and the form carries the row
  through the POST as a hidden field. **A refusal never costs the note**: a row
  cancelled mid-consultation saves the visit and warns, because a visit with no
  appointment is completely valid.
- **The payment column is a read, hidden by not being looked up.**
  `scheduling.services.with_bills` reads `appointment → encounter → invoice` in
  one query for the whole day, and `_day_context` only calls it for a membership
  with clinical access — so a template that forgot its check has nothing to leak.
  A seen visit with no bill says "No bill"; blank would read as "nothing owing".

Two things the browser pass taught, both worth keeping:

- **A visibility-guarded poll cannot be observed from a driven tab.** An
  automated tab reports `visibilityState === 'hidden'`, so the guard suppresses
  every tick and "did my typed text survive the poll?" passes with no swap ever
  happening. Force the identical swap by hand — `htmx.ajax` GET of the rows URL
  into `#day-rows` — or the check is worthless. Done properly, the text survived
  byte-identical with focus intact.
- **The polled fragment needed its own tests.** `_rows.html` renders through the
  page *and* through `day_rows`; if `membership` reached one context and not the
  other, the role gate would flip five seconds after load. Both roles are now
  asserted on the fragment, not only on the page.

The organization's timezone is now activated per request (SPEC §4, ADR 0011).
It had been written and never read for the whole MVP, so every datetime rendered
six hours behind this clinic — and *correcting* a defaulted `occurred_at` to the
true wall-clock time wrote it six hours out and on the wrong date. Rules:

- **`TIME_ZONE` stays UTC and storage does not move.** `ActiveOrganizationMiddleware`
  enters `organization_timezone(organization)` beside `organization_context`, on
  the same reset-in-finally lifecycle — a leaked zone is the timezone equivalent
  of a cross-tenant leak. Only presentation moves: `localtime`, `localdate`, the
  template `date` filter, and the two `datetime-local` defaults that read them.
- **Anything that activates one activates the other.** They are separate managers
  because scoping needs only a pk while a zone needs the row. `bootstrap_demo` is
  the second caller and needs it for a real reason: it books "today", and after
  midnight in Dhaka a UTC "today" seeds a day the day list does not open on.
- **A UTC-only test proves nothing here.** The default `Organization.timezone` is
  `'UTC'`, where storage, display and "today" all agree and the bug is invisible.
  `core/tests/test_organization_timezone.py` runs on `Asia/Dhaka` throughout and
  pins `timezone.now` to 19:30 UTC — 01:30 next day in Dhaka — so the calendars
  genuinely disagree. Keep a non-UTC org in any future work on this.
- **Existing hand-edited datetimes are still wrong** and there is no migration,
  because nothing tells them apart from values that were always right.

The pre-deployment slice is done (2026-08-08): static serving, HTTPS posture,
login rate limiting, logging, CI, and a production compose file. Verified by
building and running the production stack, then **signing in and driving the two
components that fail silently when static serving is wrong** — the medicine
autocomplete and the invoice line editor — with the network log confirming they
ran off `item-autocomplete.<hash>.js`, not a fallback. A dynamically added
invoice row binds its own autocomplete, which is the case a load-time-only bind
would break. Rules to know:

- **`DEBUG` now defaults to *off*, and settings refuse to import without a real
  `SECRET_KEY` when it is.** The asymmetry is the argument: a dev machine that
  forgets `DJANGO_DEBUG=true` looks broken and gets fixed in seconds; a
  deployment that forgets it serves tracebacks quietly. `docker-compose.yml`
  sets it; bare `manage.py` runs need it exported. `settings_test.py` declares a
  throwaway key before its star-import for the same reason.
- **WhiteNoise means `collectstatic` is mandatory.** Manifest storage resolves
  `{% static %}` through `staticfiles.json` and raises on a missing entry.
  `settings_test.py` overrides back to plain storage — without that, every
  template rendering `{% static %}` fails. That trade is deliberate: the failure
  it replaces was three JS files 404ing with `DEBUG=False` while the CDN kept
  the page looking correct, so the patient picker and invoice lines were dead on
  a page that appeared to have loaded fine.
- **`SECURE_PROXY_SSL_HEADER` is opt-in via `DJANGO_BEHIND_PROXY` and must
  stay that way.** `X-Forwarded-Proto` is client-supplied; trusting it with
  nothing upstream overwriting it defeats every `is_secure()` decision below it.
  The same flag switches axes to reading `X-Forwarded-For`.
- **`django-axes` needed `AXES_USERNAME_FORM_FIELD = 'username'`.** It defaults
  to the *model's* `USERNAME_FIELD` (`phone`), but `AuthenticationForm` always
  names its field `username`. Left at the default it recorded every attempt as
  `username=None` and the lockout key collapsed to the IP — one attacker locking
  out the whole clinic. Nothing visible changes when it is wrong.
- **"No LOGGING" was two different silences, and the bigger one was 500s.**
  App warnings reached stderr bare via `logging.lastResort`; `django.request`
  errors were dropped entirely, because the `django` logger *had* handlers
  (a `require_debug_true` console and an `AdminEmailHandler` with no `ADMINS`)
  so `lastResort` never fired. `core/tests/test_logging.py` reads what the
  configured handlers emit — `caplog` cannot test this, it measures pytest.
- **`docker-compose.prod.yml` pins `name: clinicore-prod`.** Without it both
  compose files are project "clinicore" and share `postgres_data`, so
  production adopts the dev database. **`.env` is in `.dockerignore`** because
  `COPY . .` otherwise bakes the real key and password into an image layer;
  both were observed, not theorised.
- **CI's reason to exist is Postgres.** The invoice-numbering and FEFO tests
  `pytest.skip` on SQLite — green without executing — so the workflow fails
  explicitly if either skips.
- **Authorisation is at the view boundary by decision**, not by oversight;
  services take `actor` for attribution and check nothing. `docs/adr/0012-*`
  states what that obliges every new view to do — including HTMX partials, which
  are URLs.

User management is built (2026-08-09) — the last blocker before handover, and
invisible for the whole MVP because `bootstrap_demo` created every account that
had ever signed in. New screens live in `accounts`: `/team/` (list, add, edit,
reset password, remove/restore access, ADMINISTRATOR only) and `/profile/` plus
`/profile/password/` (any signed-in account, whatever its role). **Browser-
verified end to end**: add a receptionist → sign in as her → change her password
→ administrator resets it → sign in with the temporary one → forced change →
dashboard, plus remove/restore access and the STAFF 403 on `/team/`. Rules to
know before touching it, all in
`docs/adr/0013-user-management-without-email.md`:

- **`OWNER` is still the stored value; "Administrator" is a label.** `role_owner`
  / `role_practitioner` / `role_staff` joined `terminology`, `{% role_label %}`
  renders them, and `Role.OWNER`'s enum label moved too so the admin and any
  organization-less render agree. `get_role_display` in a template is now a bug —
  it reads the code-level default and ignores the clinic's map. Renaming the
  column would have been a data migration plus every `Role.OWNER` call site, for
  a change of wording.
- **There is no password reset by email, and that is the decision.** Email is
  optional and never verified, phone is the identifier, and a self-hosted box
  has no mail sending — an SMTP dependency that fails silently tells the user to
  check an inbox nothing will reach. Recovery is an administrator typing a
  temporary password and reading it out, with `User.must_change_password` and
  `accounts.middleware.ForcePasswordChangeMiddleware` making sure it cannot
  survive. That middleware exempts exactly three URLs — the password screen,
  logout, login — because a redirect that catches its own destination is a trap
  with no exit.
- **Deactivation is `Membership.is_active`, never `User.is_active`.** One
  practitioner at two clinics holds one account with two memberships, so losing
  access at one must not touch their login at the other. Nothing is ever
  hard-deleted: visits, bills and stock movements carry the user as `created_by`
  and `actor`. The consequence a deactivated account meets is
  `core/no_organization.html`, which is reachable in normal operation now.
- **The self-guard is the whole guard.** An administrator cannot demote or
  deactivate themselves, and that alone keeps one active administrator per
  organization without counting anything — the only account that could remove
  the last one is that account. The demotion half is `disabled=True` on the role
  field, which Django enforces server-side by ignoring submitted data, so it is
  not decoration and a `clean_role` refusal could never fire.
- **`Membership` has no automatic org filter**, so `/team/` is the one surface
  where a forgotten `.filter(organization=…)` shows another clinic's staff
  rather than an empty page. Everything goes through
  `services.organization_members(organization)`, and `test_team.py` asserts the
  list *and* the by-pk routes.
- **An existing phone number is refused, not joined.** Attaching an existing
  account to a second organization would tell the administrator the name behind
  a number they guessed. The form says the number is in use and names nothing.
- **`update_session_auth_hash` is not optional.** Saving a password rotates the
  hash the session is keyed on; without it the change signs you out on the next
  request, which reads as the change having failed.

Two things the browser pass caught that the suite had passed over: the role
dropdown on the add form opened on **Administrator** (`Role`'s first member), so
it now carries `initial=Role.STAFF` with a test; and `PasswordChangeForm`'s own
labels ("Old password", "New password confirmation") are relabelled in
`accounts.views._plain_password_form` rather than by subclassing, so Django keeps
owning the password rules and the mismatch check.

Visit photographs are built (2026-08-14) — the first thing in this repo that
stores a file. `clinical.EncounterPhoto`: the patient, or a document they
brought in, uploaded from the visit form *or* the visit detail page, thumbnailed
in a grid, tapped to open full size, deleted with a confirmation. **Browser-
verified end to end** against the live stack: two photos uploaded, both rendered,
one opened full size, one deleted (row and file), a non-image refused, EXIF
rotation applied, `/media/<real path>` 404ing while the served URL worked, and
STAFF 403ing on all three URLs. Rules to know, all in
`docs/adr/0014-encounter-photos-served-through-a-view.md`:

- **`MEDIA_URL` is routed by nothing, in every mode including `DEBUG`.** Adding
  `if settings.DEBUG: urlpatterns += static(...)` is the bug this is guarding
  against: development would stop exercising the protected view, so a missing
  decorator there would first appear in production, where the direct route is
  gone. `core/tests/test_media_not_served.py` fails if the route comes back.
  `photo.image.url` in a template is a bug; templates use
  `{% url 'clinical:photo' photo.pk %}`.
- **Re-encoding is a security control, not a disk one.** Every upload is decoded
  by Pillow and re-emitted as JPEG, so the stored bytes are always ours. Files
  are served same-origin, so an SVG or an HTML page named `.jpg` would otherwise
  be stored XSS against a session that reads every patient record. Neither
  survives the round trip.
- **`ImageOps.exif_transpose` is not optional and no status-code test can catch
  it.** Phones record orientation in EXIF rather than rotating pixels; without
  it every portrait photo is stored sideways and the bytes are valid either way.
  EXIF is then dropped rather than copied, which takes GPS coordinates off every
  stored file.
- **Validation is on the form, never the view.** A rejected file has to come back
  as a field error with the consultation note still typed in. Losing a
  half-written visit because one photo was 12 MB is worse than the thing being
  prevented.
- **`.open('rb')`, never `.path`.** `.path` raises on any non-filesystem storage,
  so that one call is what keeps SPEC §10's move to S3 a settings change.
- **`bootstrap_demo --reset` deletes photos row by row.** `Encounter` CASCADEs
  them, so a queryset delete never raises — it silently orphans the *files*. The
  loader seeds none (no binaries in the repo), so only the hand-staged row in
  `core/tests/test_bootstrap_demo.py` catches it; that test was confirmed to fail
  against a queryset teardown before being kept.
- **A `pg_dump` is no longer a complete backup.** Photos live in the `media_data`
  volume; restoring the database alone gives every visit intact with every
  photograph missing, and nothing errors. README and MVP-NOTES both say so.
- **`/app/media` is created in the Dockerfile before the `chown`.** Docker seeds
  a fresh named volume from the image directory's ownership; without the mkdir
  the mount point is root-owned and the non-root user cannot write an upload.
- **`conftest.py` points `MEDIA_ROOT` at `tmp_path` for every test, autouse.**
  Found the hard way: the tenant-isolation builders had been writing real files
  into the repository's `media/`. Nothing fails when a test writes a file, so
  this has to be global rather than per-module.

Two judgement calls worth not re-litigating: `capture="environment"` was
considered and **dropped** — it forces the camera and removes the gallery, which
breaks photographing a referral letter now and attaching it later; plain
`accept="image/*"` still offers the camera in the Android picker. And `MAX_EDGE`
is 1600, not 2000, because a photographed report is read by pinch-zooming on a
phone rather than printed; raising it is a one-line change in `clinical/images.py`
if the clinic ever complains.

Known, not fixed: **daisyUI's `.toast` sets `white-space: nowrap` on a
full-width fixed container**, so any message longer than the viewport runs off
the *left* edge and its beginning is unreadable — worst on a phone. Pre-existing
and app-wide; the photo rejection message was shortened to stop making it worse,
but the component itself still needs a pass.

A URL smoke walk exists now (2026-08-14) and is the coverage that was genuinely
missing: 559 tests asserted specific behaviours and **none asserted that a page
simply loads**, so a view could 500 for everyone and stay green.
`core/tests/test_url_smoke.py` enumerates the URLconf itself, resolves each
pattern's arguments from seeded data, GETs it as OWNER / PRACTITIONER / STAFF,
and fails only on 5xx — 200/302/403/404/405 are all legitimate. Rules:

- **Discovery is automatic; argument sources are declared.** `pk` means a
  different model in nearly every namespace, and a wrong guess is a 404 that
  looks like a pass. `_argument_sources()` maps URL name → row, and
  `test_every_parameterised_url_declares_its_arguments` fails when a new view
  with a `<int:pk>` arrives without an entry. That pairing is what makes a new
  view covered the day it lands rather than when someone remembers.
- **`client.raise_request_exception = False` is load-bearing.** Left at the
  default the client re-raises, so the walk dies on the first broken page and
  reports one traceback instead of listing every page that is down.
- **Both a populated and an empty organization are walked.** The populated one
  is built by `bootstrap_demo`, so it keeps seeing realistic rows for new tables
  without a second seeding path. The empty one catches empty-state crashes,
  whose first victim is always a real clinic's first morning.
- It currently walks **66 of 66** non-admin patterns with none skipped (51×200,
  4×302, 11×405). `test_the_walk_actually_reaches_most_of_the_application`
  asserts that floor, so a regression in argument resolution cannot turn the
  whole file into a silent no-op. Verified against a deliberately planted 500.

**`migrate --check` joined the standing verification list** for the same reason:
`clinical_encounterphoto` 500'd on correct code because a local SQLite database
had never had migration 0006 applied. The suite and Docker build fresh
databases, so nothing else can catch it. Run it against **each** database you
use — the SQLite one and the Postgres one fall behind independently.

The 2026-08-14 responsive pass fixed the day list, which was genuinely broken at
375px: one `flex flex-wrap items-center` line gave the middle column ~195px, so
`truncate` ate patient names to "Imra…" while the action buttons sat on top of
meta text that had wrapped to five lines. It is now a two-column grid below `sm`
(time | identity, actions spanning both on their own row) and the original
wrapping flex line from `sm` up. Also settled:

- **The phone number is a button, not an underlined number.** It read as data in
  the grey meta line and was being missed; confirming bookings by phone is the
  main use of the screen. Still a `tel:` link, now `btn-sm btn-brand-ghost` with
  an icon, and it moved into the actions group where it belongs.
- **The photo grid is 2 columns on a phone, not 3.** A 93px tile could not carry
  a thumb-sized delete control without hiding the picture; 135px can. The delete
  button is sized with plain utilities rather than `btn-xs btn-circle`, which
  fought each other — app.css grows `.btn-xs` to 44px *height* while
  `.btn-circle` holds a 24px width, and the result measured 23px across. Now a
  true 44px square on a phone, 24px from `sm` up.
- **The topbar user name became an icon below `sm`.** Spelled out it took ~110px
  of a 375px bar and was what truncated the page title to "Appointm…" — and,
  worse, the patient's name to "Jahangir H…" on every visit page.
- **`resize_window` silently does nothing in this harness and headless Chrome
  clamps its viewport at ~500px.** The only way to see a true 375 is to render
  the page inside a precisely-sized same-origin iframe, which establishes a real
  CSS viewport (`100vw` resolves to the iframe width). `scratchpad/sweep.sh`
  saves each page's HTML with its asset URLs absolutised and shoots it at 375 /
  768 / 1024. **Screenshot, do not measure** — the bottom-nav bug, the truncated
  names and two rendered `{# … #}` comments were all invisible to measurement
  and obvious in a picture.
- **A multi-line `{# … #}` renders to the page**, and it happened twice more in
  this pass. `core/tests/test_template_comments.py` catches it every time; run
  it after touching a template rather than only at the end.

The operational layer landed 2026-08-15: `deploy/` plus `docs/RUNBOOK.md`, aimed
at one small box in Bangladesh where power cuts are routine and the two
operators are the author and the doctor's son, who is capable but not a
developer. **Drill-verified end to end**, including a full restore from deleted
volumes. Rules to know:

- **Three things survive a power cut and only one is in the compose file.**
  `restart: unless-stopped` covers a crash, `systemctl enable docker` covers the
  reboot (a host step, in the runbook), and `deploy/heal.sh` on a two-minute
  timer covers *running but unhealthy*, which Docker records and never acts on.
  Confirmed in the drill: stopping the database left web `state=running
  health=unhealthy` for as long as it was left alone.
- **Gunicorn already restarts hung workers.** `--timeout` does it, so the healer
  exists for the container-level case only. Chosen over `willfarrell/autoheal`,
  which wants the Docker socket in a third-party image; systemd was already
  running the backup timers, so this is one mechanism on the box rather than two.
- **The Dockerfile CMD is shell form now, and `exec` is load-bearing.** Without
  it gunicorn is a child of `sh`, `sh` is PID 1, and PID 1 does not forward
  SIGTERM — `compose stop` would wait out the timeout and kill in-flight
  requests. The shell form also makes `GUNICORN_WORKERS` real; `.env.example`
  had documented it against a hardcoded `--workers 3` since the production
  slice.
- **Backups are `age` to a public key, not a passphrase.** Only the public key
  is on the box, so a stolen server cannot read its own backups. Chosen over
  restic/borg deliberately: restic is better engineering, but the artifact is an
  opaque repository, and the restore here is done at night by someone who is not
  a developer. An ordinary file that `age -d | pg_restore` consumes beats
  dedup at this size.
- **The private key is the single point of total data loss**, and the runbook
  says so in those words at the top of the restore section. Two copies before
  the first backup runs: password manager and printed in the clinic safe.
- **Both halves, every night.** A database dump alone restores a clinic whose
  visits are intact and whose photographs are all missing, with nothing erroring.
- **A failed run must never advance `last_success`.** Both scripts re-read the
  previous value on failure, because the dashboard reads that field and nothing
  else — overwriting it would turn every failure into a green dashboard, which
  is the exact silence the feature exists to break.
- **`trap ... ERR` is not enough, and this shipped broken for an hour.** ERR does
  not fire on an explicit `exit`, which is how `die` reports every early refusal
  — a missing key, an unset `AGE_RECIPIENT`, no backups on disk. Those failures
  wrote *no status at all*, so the dashboard went on showing the previous run's
  success and `last_attempt` did not even move. Both scripts now pair
  `trap 'FAILURE_LINE=$LINENO' ERR` (which knows the line) with
  `trap finish EXIT` (which catches everything) and a `COMPLETED` flag set only
  on the success path. Caught by deleting the key and watching the status file
  stay green — not by reading the code.
- **In `verify-restore.sh` the table-count assertion comes before any query
  against an application table.** Reversed, a dump of a never-migrated database
  fails on `relation "patients_patient" does not exist` at an arbitrary line,
  where the useful sentence is "that backup holds no clinic". The message is the
  entire product of that script.
- **Backup status is a file, not a database row.** The scripts run on the host,
  and writing through `manage.py` would mean a backup could only record itself
  while the app was up — "the night the app was down" is the run whose outcome
  matters most. Mounted read-only at `/app/run`, so the app cannot flatter its
  own status. `core/backups.py` reports *never run* for anything missing,
  malformed or truncated: a fresh box must not look healthy.
- **The private key lives on the box at `/etc/clinicore/backup-identity.key`
  (root-only), so the monthly restore check runs unattended.** Decided
  2026-08-15, and a knowing trade rather than an oversight: a stolen server can
  now decrypt the backups it made. Accepted because the alternative was a human
  ritual with a USB stick, which stops happening by the third month, and its
  failure is invisible — unverified backups look exactly like verified ones
  until the night one is needed. A year of unproven backups is the larger risk.
  Reasoning in full under "Why the private key is on the server" in
  `docs/RUNBOOK.md`, including that this loses *one copy* of the key rather than
  the key, and that a stolen box means rotating the pair and keeping the old one
  so existing backups stay readable. Only `verify-restore.sh` reads it;
  `backup.sh` needs the public key alone.

What the drill actually proved (`scratchpad/opsdrill`, a throwaway clone on its
own compose project): backup → `down -v` (both volumes destroyed) → `up -d` →
`restore.sh` → 15 patients, 10 visits and 1 photo all back, the app answering,
a practitioner signing in and seeing the list, and **the restored photograph
serving real JPEG bytes at 1067x1600** — the half of the backup a database-only
restore would have silently lost. Two runbook bugs were found by doing it: the
restore section assumed the stack already existed, and assumed it was running.
Both are now written down.

Prescribed strength landed 2026-08-15, with the clinic's real medicine list.
**Browser-verified end to end** against the running app: the Features screen set
the switch, the label and the values; the prescription row showed "Potency"; a
catalog default prefilled on selection; the visit saved with potency and dosage
apart; the A5 printout carried a POTENCY column; and with the capability
switched back off, that same printout still showed 200C while the form's row
returned to its original five fields. Rules, all in
`docs/adr/0015-prescribed-strength.md`:

- **The column is `strength`, the label is the clinic's.** A column named
  `potency` with `['6C','30C',…]` beside it is homeopathy in the schema, which
  SPEC §1 forbids — and 30C, 500mg and 1:10 are one slot anyway. `terminology`
  gains a `strength` key; the clinic maps it to "Potency". A hardcoded "Potency"
  in a template or form is now a bug, exactly like `get_role_display`.
- **The JSON fields were never the answer, and were never used.** Nothing wrote
  `Product.default_attributes` and nothing read `PrescriptionItem.attributes` —
  the only code touching either is three lines in `save()`. "Surfacing" them
  meant building a key/value editor on a prescription row mid-consultation, for
  one key. Strength is prescribing data that gets printed and handed to a
  patient, same class as `dosage`; the fix for two facts in one column is a
  second column. The JSON stays for values that really are arbitrary.
- **The field is dropped from the form, never hidden in the template.** A field
  left on the form is rebuilt as empty by `construct_instance` on every later
  save, so turning the capability off would quietly erase strengths recorded
  while it was on — the next time anyone edited the visit.
  `bind_organization` and `ProductForm.__init__` pop it.
- **The read surfaces gate on the data, not the switch.** `show_strength` comes
  from `clinical.views._prescription_sections` — does *this* prescription carry
  any — so reprinting a visit reproduces what the patient was handed whatever
  the clinic records today. Same rule that keeps recorded advice readable (A3).
- **The suggestions are a native `<datalist>`, and org data.**
  `Organization.strength_options` is a JSON list edited on the Features screen,
  one per line; `strengths` cleans it on the way out because a datalist must
  survive whatever is in an org-editable JSON column. The box stays free text,
  so an unusual potency is typed — there is no "Other…" option to explain.
  Seeded for this clinic as Q, 6C, 12C, 30C, 200C, 1M, 10M, 50M, CM.
- **Six fields do not fit one twelve-column line**, so Instructions moves to its
  own full-width row when the capability is on. With it off the row is
  byte-identical to what it always was.
- **`Organization.strength_enabled` defaults to *off*** — the opposite of
  `advice_enabled`, because a general practice writes the strength into the
  medicine's name and would find the column clutter.

`import_remedies <org-slug>` loads a clinic's own list: 333 remedies from
`catalog/data/remedies.txt`, shipped inside the app. `scripts/` is gone — its
parser is folded into the command and `parse_remedies.py` (which failed CI) is
deleted. Rules:

- **Idempotency comes from the constraint, not from reading the file.** Each row
  is inserted inside its own savepoint and an `IntegrityError` counts as a skip,
  so a second run, a name repeated inside one file, and a name differing only in
  case are all the same case. A savepoint per row is required — an
  `IntegrityError` poisons the enclosing transaction.
- **There is deliberately no `--replace` or delete.** Products are referenced by
  prescriptions, invoice lines and stock movements, so removing one either fails
  on a PROTECT or orphans history. Deactivation is the only correction.

**`bootstrap_clinic` is the real gap that closed.** There was no way to create
an organization without also acquiring twenty-five invented medicines, and since
those cannot be deleted afterwards, never seeding them is the only correct path
for a real clinic. It creates the organization, one branch and one administrator
(temporary password printed once, `must_change_password` set via `add_member`)
and stops. All five arguments are required — a defaulted `--timezone` is the one
that matters, because UTC would be accepted silently and file every late-evening
visit under the wrong date (ADR 0011); a bad zone is refused outright. The
new-clinic sequence is written up under "Setting up a new clinic" in
`docs/RUNBOOK.md`.

Not done, and a knowing gap: **existing `dosage` values that hold both facts
("30C 4 pills") are not migrated apart.** Nothing distinguishes them from doses
that were always doses, and a guess would corrupt real records.

Every date field is now drawn by the application, not the operating system
(2026-08-19). A native `type="date"` renders its text in the *device's* locale,
so the same box read d/m/Y in the clinic and m/d/Y on a laptop from elsewhere,
and nothing in the page — `lang`, an attribute, CSS — can change that. All seven
are flatpickr behind `data-datepicker`, initialised by one shared
`static/js/date-picker.js` loaded from `base.html`. **Browser-verified end to
end** on all seven: calendar opens, date picks, manual typing parses, and the
value round-trips through a real save. Rules, all in
`docs/adr/0016-one-date-picker-the-app-controls.md`:

- **The posted value did not move, and that is the whole constraint.**
  flatpickr's `altInput` keeps the declared element as the real field, still
  named the same and still `Y-m-d`; the visible box in front of it is a second
  input showing `d/m/Y`. Four of the seven consumers are ISO-only with no
  fallback — `parse_date` returns `None`, `strptime('%Y-%m-%d')` raises — so a
  display format that reached the server would be a silent data change, not a
  cosmetic one. `core.forms.date_widget` also pins `format='%Y-%m-%d'` rather
  than trusting `LANGUAGE_CODE` to keep producing it.
- **`disableMobile: true` is the line that makes this real.** flatpickr's
  default is to step aside and use the native control on a mobile browser —
  i.e. without it the change is a no-op on exactly the devices that motivated
  it, and tests clean on a desktop.
- **`static: true` for anything inside a `<dialog>`**, decided per instance at
  runtime rather than per call site, because `date_of_birth` renders both on its
  own page and inside the add-patient modal from one widget. A `showModal()`
  dialog is in the browser's top layer, so a calendar appended to
  `document.body` paints *underneath* it and the field looks dead.
- **flatpickr eats Enter, and that broke implicit form submission.** Typing a
  range into the bill filters and pressing Enter silently did nothing. The fix
  is bound on the **capture** phase — flatpickr stops the key propagating, so a
  normally-registered listener never runs — and defers the submit a tick, so
  flatpickr has parsed the typed text into the real field before the form goes.
  Skipped where the field has its own `onchange` (the day list), or both fire.
- **The id moves to the visible box.** All seven have a `<label for>`, and
  flatpickr leaves that id on the input it hides; `altInputClass` carries the
  daisyUI classes across, which flatpickr otherwise replaces with its own.
- **Loaded from `base.html`, not a per-page `{% block scripts %}`**, unlike
  `item-autocomplete.js`: two of the fields live in modals included from several
  unrelated pages, which is the coupling that already shipped the walk-in modal
  without its add-patient dialog past a green suite.
- `core/tests/test_date_inputs.py` fails on a reintroduced `type="date"`
  anywhere in the templates, so a new date field cannot quietly go back to the
  OS control.

Related and **not** changed, because it was not in scope: `occurred_at` and
`received_at` are `datetime-local` and still show the device's order (m/d/Y on
this machine). Same root cause, same fix available; flag it before adding more.

What is dispensed landed 2026-08-21, and the row collapsed around it
(`docs/adr/0017-dispensing-details.md`). Two more columns —
`PrescriptionItem.pack_size` and `.preparation` — plus the other four fields
moving behind a disclosure. **Browser-verified end to end** against the running
stack: the Features screen set all three capabilities, both new fields appeared
on an HTMX-added second row with their datalists bound, an advice pick hid all
three and forced the disclosure open, the visit saved and round-tripped, the A5
printout came out four columns wide, and switching a capability off removed the
field from the form while the printout still showed the recorded value. Rules:

- **`preparation` is not `Product.unit`.** `unit` is the noun a *stock count* is
  measured in, one value per catalog row. The preparation is a per-prescription
  decision — the same remedy goes out as globules for one patient and liquid for
  the next — and this clinic's catalog is 333 bare remedy names with no form, so
  encoding it there would fork the list into 666 PROTECTed rows.
- **`pack_size`, never `quantity`.** "2D" and "1/2 ounce" are strings;
  `quantity` already means a Decimal that arithmetic is done on, on
  `InvoiceItem` and `StockMovement`. `dispense_amount` was rejected for sitting
  one word from `MovementType.DISPENSE` and reading as a stock hook.
- **Neither field moves stock, and neither should.** The invoice is still the
  only stock event (ADR 0009). A path off the prescription would double-decrement
  against A5's `prescribed_product_lines`, and there is no conversion from
  "1/2 ounce" to a Decimal in the product's unit. `prescribed_product_lines`
  still sets `quantity=1` deliberately.
- **One capability per field, and the three are built by a loop.**
  `organizations.models.PRESCRIBING_FIELDS` derives `<key>_enabled`,
  `<key>_options` and the datalist id from one name, and drives the settings
  form, the item form, the check constraints and `save()`. A fourth field is an
  entry in that tuple plus two columns. `STRENGTH_MAX_LENGTH` became
  `PRESCRIBING_MAX_LENGTH` (same value, no column changed width).
- **The collapsed four stay on the form and in the DOM.** A closed `<details>`
  still posts what is inside it; a template conditional or an `__init__` pop
  would let `construct_instance` rebuild them as empty and erase an older
  visit's data on the next save. Only the disclosure hides them.
- **`form.has_details` decides `open` server-side**, through
  `BoundField.value()`, so a saved row, a redisplay after a validation error and
  an HTMX-added row are all right without JavaScript. `prefill()` in
  `item-autocomplete.js` now reports what it wrote and forces the disclosure
  open, because an advice template's `default_frequency` landing unseen is the
  one thing the disclosure must not cause.
- **All seven optional print columns now gate on the data**, not just
  `strength`. This **changes the layout of historic printouts** — a visit with no
  dosages reprints without an empty Dosage column. Content is unchanged; it is
  written down in the ADR because a reprint is no longer column-for-column
  identical to the sheet handed over at the time.
- **`PrescriptionItem.attributes` is now vestigial, and the ADR says so.** Three
  specialty fields have bypassed it and nothing reads it, so writing to it today
  writes into a hole. The rule that replaced it: if a value is printed on the
  prescription and read back by a human, it is a column. Kept rather than
  dropped — a destructive migration across the table and its `simple_history`
  twin, for a column SPEC §5 still names.
- Pricing was **deliberately not touched**: `InvoiceItem.unit_price` is already
  an editable per-line box prefilled from the catalog and never overwritten once
  typed, so the clinic's requirement is met at billing time. A whole-bill
  discount, if that is what they meant, gets its own ADR.

**Amended 2026-08-22**: `pack_size` and `preparation` are `<select>`s now, and
strength stays a datalist. The clinic confirmed the first two are closed lists;
an atypical potency genuinely gets typed. `PrescribingField.closed_list` carries
the distinction and decides the control. Two things to know:

- **A closed field always offers the value the row already holds**, even after
  the clinic drops it from its options. A `<select>` missing the current value
  renders with nothing selected, so the browser posts the first option — blank —
  and the next save erases it. `_closed_choices` appends it;
  `test_a_value_the_clinic_no_longer_offers_survives_a_resave` reads back what
  the rendered page would submit rather than asserting a hardcoded value, and
  was confirmed to fail against the guard removed.
- **The field stays a plain `CharField`.** Choice validation would turn that
  case into a refusal to save the row at all.

Also done in that pass: `{% comment %}` blocks containing literal `<form>` /
`<dialog>` / `<select>` markup in prose were reworded (the IDE parses markup
inside Django comments and calls them unclosed elements; the markup was always
balanced), and **every raw `&` in a template URL is now `&amp;`** — 26 of them
across nine files, `href` and `hx-get` alike.

`import_patients` landed 2026-08-22 — the clinic's existing patient list, from a
CSV handed over at run time (`docs/adr/0018-importing-real-patient-data.md`).
Verified against the demo org on Postgres: a dry run, a real run, and a second
run creating nothing. Rules:

- **Nothing ships with it.** `--file` is required, with no default and no
  bundled fallback, so there is no way to run it against a file that came with
  the code. That is the whole difference from `import_remedies`, which ships its
  materia medica *because* that data is public domain. `.gitignore` blocks
  `*patients*.csv`; the one committed CSV is `docs/sample-patient-import.csv`,
  six invented people, with an exact-path negation so widening the pattern
  cannot lose it and cannot let a real file through beside it.
- **A header row is required**, matched case-insensitively after stripping a
  BOM. A headerless file is refused rather than read positionally: a column
  order that transposes `sex` and `phone` is invisible for months. Extra columns
  are ignored *with a note*; a missing required column is an error.
- **Dedupe is an exact match on name + date of birth + normalised phone**, read
  through `all_objects` so a soft-deleted patient is skipped rather than
  resurrected. Two family members on one phone differ by name and both import.
  The limit it cannot see is a corrected spelling — that is a new patient, and
  the dry run reporting *would create 0* is the guard.
- **An unrecognised `sex` imports as UNKNOWN and is reported by row**, counted
  separately from blank: blank is legitimate absence, "Mael" is an error someone
  should look at, and one number for both hides the second inside the first.
- **Dates are `fromisoformat` and nothing else.** No sniffing — `01/02/1998` is
  two days on two continents. A `--date-format` option gets added if a real file
  needs one.
- **One transaction, one savepoint per row**, and `--dry-run` validates rather
  than writing and rolling back. The branch is refused, not guessed, when a
  multi-branch clinic gives none.
- **The command cannot undo itself**, so the runbook takes a backup first and
  deletes the file from the container *and* the host afterwards. Production has
  no bind mount for the code, so the CSV has to be `docker compose cp`'d in —
  which is why there are two copies to destroy, and a third in whatever the
  clinic sent it by.

`bootstrap_demo` was split in two on 2026-08-23, because one command was doing
two unrelated jobs and the difference between them was five characters typed at
a terminal on a live server. **This is a rename of an operator-facing command**;
anyone with the old invocation in their notes needs to know.

- **`bootstrap_clinic` is the real-clinic path**, formerly `--empty`: one
  organization, one branch, one administrator, no data. All five arguments are
  required now — `--timezone` and `--branch` no longer default, because the only
  thing a defaulted zone can do is be silently wrong (ADR 0011).
- **`bootstrap_demo` refuses when `DJANGO_DEBUG` is off**, which is to say it
  cannot run on a server at all. There is no `--force`: what it invents cannot
  be deleted once a prescription, bill or stock movement points at it, so the
  only safe answer off a development machine is no. `settings_test` has `DEBUG`
  off like production does, so the refusal is what the suite gets by default and
  every demo build in `core/tests/test_bootstrap_demo.py` and the URL smoke
  walk's `populated` fixture opts in with `override_settings(DEBUG=True)`.
- **`core.services.create_organization` is the shared half** — the organization
  row plus its first branch, written inside `organization_context`, with the
  zone validated rather than defaulted. It is in `core` rather than
  `organizations` because it is the one thing both commands do and because a new
  organization is never only an `Organization` row. `**fields` carries the
  demo's currency, fee and `advice_enabled`; `branch=` carries the first
  branch's own fields. It raises `core.exceptions.CannotCreateOrganization` and
  the commands turn that into a `CommandError` sentence.
- **The runbook no longer warns about anything**, which was the point: the
  "never run `bootstrap_demo` without `--empty`" section existed only because
  the command was misnamed, and it is gone rather than reworded.

A fourth role landed 2026-08-23: **`DEVELOPER`** — everything `OWNER` can do,
minus being eligible to treat anyone. It exists because one fact was answering
three questions, all in
`docs/adr/0019-read-clinical-and-may-be-booked-are-two-facts.md`:

- **Three questions, three frozensets, all in `accounts/models.py`.**
  `CLINICAL_ROLES` = {OWNER, PRACTITIONER, DEVELOPER} (may read a consultation
  note), `PRESCRIBING_ROLES` = {OWNER, PRACTITIONER} (may be booked or recorded
  as treating), `ADMIN_ROLES` = {OWNER, DEVELOPER} (may administer). The stored
  roles are now OWNER / PRACTITIONER / STAFF / DEVELOPER.
- **A bare role comparison or an inlined pair outside `accounts.models` is a
  bug.** The pair `[Role.OWNER, Role.PRACTITIONER]` had been inlined in four
  places, two of them byte-identical practitioner lookups in different apps —
  which is exactly how "reads clinical data" and "may treat a patient" stayed one
  fact for the whole MVP. Those two are now one function,
  `accounts.services.prescribing_users`. `accounts/tests/test_roles.py` asserts
  the sets have diverged so a future edit cannot silently re-merge them.
- **`Membership.is_owner` means "may administer" and returns true for two
  roles.** The name is knowingly stale; renaming it to `is_administrator` is a
  follow-up recorded in the ADR, not a `TODO` in the code.
- **Filtering a `ModelChoiceField` by role locks old rows unless you carry the
  current value.** A select whose stored value is outside its queryset renders
  unselected and then refuses the save, so every visit recorded by somebody who
  later stops prescribing would become unamendable.
  `clinical.forms._practitioner_choices` always offers `instance.practitioner`.
  Same guard, same reason, as `core.forms.closed_choices` (ADR 0017) — and the
  test was confirmed to fail against the guard removed.
- **`clinical/views.py` only prefills the signed-in user as practitioner when
  they are in `PRESCRIBING_ROLES`.** Prefilling a value outside the queryset
  renders a field that looks broken rather than unanswered.
- **The last-administrator guard changed shape.** It was `role.disabled = True`
  on your own row; it is now the role dropdown narrowed to `ADMIN_ROLES`, which
  is what makes OWNER → DEVELOPER reachable for the one person who needs it.
  Enforcement is still structural — `ChoiceField.validate` refuses anything
  outside `choices`, so it is not a `clean_role` that a refactor could drop. The
  invariant is unchanged: moving between two administering roles removes
  nobody's access, and only the last administrator could remove the last
  administrator. Self-demotion is now refused *visibly*, with a field error
  instead of a silent ignore, so `test_an_administrator_cannot_demote_themselves`
  asserts 200-with-errors rather than 302.
- **`role_developer` in `DEFAULT_TERMINOLOGY` is mandatory, not decorative.**
  `Organization.terms` drops overrides for unknown keys, so without the default
  a clinic renaming it to "Technician" would be silently ignored.
- The migration is **choices-only**: `sqlmigrate` prints `BEGIN; -- (no-op)
  COMMIT;` on Postgres, `max_length` was already 20, and nothing rewrites a row.

The printed prescription is the clinic's own design now (2026-08-28), and the
whole point of the increment is that **none of that design is in the code**.
`docs/reference/prescription-design.html` is one configuration of a letterhead:
A4, Bengali, teal, a two-column body, a watermark, and a footer carrying the
visiting chambers. Twelve fields and two settings screens carry it. **Browser-
verified end to end** against the running stack, in Chrome's own print preview
and by printing the page to PDF. Rules to know:

- **The practitioner's letterhead hangs off `Membership`, not `User`**, as
  `accounts.PractitionerProfile` — a OneToOne rather than six more columns.
  `resolvers.resolve_active_membership` reads `Membership` with a
  `select_related` on *every request*, `/team/` renders it, and it has no
  organization filter of its own. Somebody working at two clinics presents
  differently at each, so `User` would let one clinic rewrite the other's
  prescriptions. Not an `OrgOwnedModel`: `membership.organization` is already
  the answer and a second FK is a second answer that can disagree.
- **The header follows `encounter.practitioner`**, and degrades to the
  account's name with the degrees/designation/registration rows *dropped*
  rather than printed empty (`PractitionerProfile.has_details`).
- **The header's address is `Branch.address`, falling back to
  `branding['letterhead']`.** A visit at the Daulatpur chamber must not print
  the Mirpur address; a single-branch clinic that only filled in `letterhead`
  prints what it always printed. `consulting_hours` is on `Branch` for the same
  reason — three chambers keep three sets of hours.
- **`Branch.show_on_prescription` defaults to `False`** so the migration cannot
  grow a footer on an existing clinic's sheets; `bootstrap_clinic` sets it for
  the branch it creates, and `services.prescription_branches` excludes the
  visit's own chamber — it is already named in full at the top.
- **The schedule note is one chip, not a styled ordinal.** The design wraps just
  "২য়" out of the Bengali phrase, which from a free-text column needs either
  markup in the database — an injection surface on a document handed to
  patients — or a parser for Bengali ordinals.
- **Colours are derived, so the clinic sets one.** `--primary` comes from the
  validated `primary_color`; `--primary-dark` and `--primary-tint` are declared
  twice, the second a `color-mix` that wins where supported and is dropped at
  parse time where it is not. A literal `#007791` anywhere is a bug.
- **`print-color-adjust: exact` is load-bearing and only visible on paper.**
  Confirmed in the print preview with *Background graphics unchecked*: the
  tinted patient bar and the teal schedule chip still print. Without it they
  vanish, and nothing on screen ever shows it.
- **The watermark is an element, not a CSS `content` string.** It is
  org-editable text, and a quote in it would break out of the declaration —
  escaping does not protect inside a style block. Capped at 8 characters
  (`WATERMARK_MAX_LENGTH`): it renders at 38mm inside a fixed circle.
- **℞ is an explicit serif stack, not a fourth font file and not an SVG.**
  U+211E is absent from Hind Siliguri; `'DejaVu Serif', 'Liberation Serif',
  'Times New Roman', 'Segoe UI Symbol', Georgia, serif` renders it correctly,
  verified in the printed PDF. Hind Siliguri itself is self-hosted from
  `static/fonts/` — the design's Google Fonts link would leave a Bengali sheet
  unreadable on a box with no internet, not merely unstyled.
- **Investigation maps to no field, deliberately.** `Encounter.plan` is the
  plan; the case record's investigations table is per-patient and unbuilt. The
  section prints ruled lines — the sheet is still something a doctor writes on.
  Chief complaint is not on the paper design at all and prints only when it has
  data, so nothing that printed before stops printing.
- **A5 is not the design.** The design is A4-only; A5 collapses to one column
  with the sidebar stacked above the ℞ area, and roughly twenty spacing values
  are size-conditional because a full visit (3 medicines, 2 advice,
  instructions, follow-up, chambers, contact strip) overflowed to a second page
  by 172px before they were tuned. Measured by printing to PDF and reading the
  page count, not by eye — `pdfinfo | grep Pages` is the check to repeat after
  touching this template.
- **One assertion in `test_prescription_print.py` was reversed and is
  documented in place**: `'℞' not in body` for an advice-only sheet became
  `'℞' in body`. It was a proxy for "the medicines section is absent" when the
  mark lived inside that section; it now heads the whole right column, and
  advice is half of what a practitioner prescribes. Its inverse — an empty
  prescription carries no mark — is a new test.
- **The "Generated <time>" stamp survived the redesign** even though the design
  has no such line. It is ADR 0011's wall-clock claim on a handed-over document
  and `test_the_print_views_stamp_the_clinics_local_time` pins it; dropping it
  was caught by that test, not by reading.

Billing is behind a switch (2026-08-28). `Organization.billing_enabled`,
**default True**, so nothing changes for a clinic that never touches it; Global
Homeopathy Clinic is not ready to put money in the system and turns it off.
This is A3's `advice_enabled` pattern one size larger — a whole app rather than
half a form — and the rule is the same: **off hides the feature, never the
data.** Browser-verified in both positions and through the round trip. Rules:

- **`accounts.permissions.capability_required` refuses with 404, not 403**, and
  `billing_enabled_required` is its one instance so far. 403 says the thing
  exists and you may not have it, which invites somebody to ask for access to a
  feature their own clinic switched off; 404 says there is nothing here, which
  is true. That module now holds two kinds of gate — role checks (403, to be
  replaced by the permission layer) and capability checks (404, which will not
  be) — and its docstring says so.
- **The capability check runs above the role check** on all nine billing views,
  so STAFF gets the same 404 as everyone rather than a 403 that reveals the
  feature. Decorator order is load-bearing and asserted.
- **Hidden means not looked up, not merely not rendered.**
  `patients.views.patient_detail` skips `outstanding_balance` and
  `patient_invoices`, `clinical.views.encounter_detail` skips
  `invoice_for_encounter` (the deferred import moved *inside* the check), and
  `scheduling.views._day_context` skips `with_bills`. A template that forgot its
  conditional then has nothing to leak — the posture the day list already took
  for STAFF.
- **`Settings → Billing` went with it**, though it lives in `organizations`.
  Its only two fields, currency and the consultation fee, are read exclusively
  by billing surfaces, so with the switch off it configures nothing that
  renders. This was not on the original list and is a judgement call: a
  "Billing" link in Settings that sets a consultation fee reads as an oversight.
- **The Features screen itself is never gated**, or the clinic could not turn
  billing back on. Verified by loading it with the switch off.
- **The smoke walk would have passed while testing nothing.** It fails only on
  5xx, and 404 is not one — so with billing off a third of the application
  would go unvisited and green. Both fixtures now *assert*
  `billing_enabled`, and `test_every_billing_url_is_404_when_the_switch_is_off`
  is the other half, with a floor on how many routes it checked so a loop that
  matched nothing cannot pass quietly.
- **`bootstrap_clinic` takes `--no-billing`; it does not know the clinic's
  name.** Same rule as the chamber details: it is the generic command, and
  which clinic is being stood up next is not a fact the product holds (SPEC §1).
  `bootstrap_demo` keeps billing on — the demo exists to show the whole product.

Two consequences surfaced with it, neither decided nor built:

- **Stock drifts while billing is off.** The invoice is the only stock event
  (ADR 0009), so dispensing posts no movements and on-hand only ever rises from
  goods receipts. My recommendation is **its own switch, defaulting to on**,
  gated the same way — inventory is independently useful for expiry and reorder
  even when nothing is sold, so tying it to billing would remove a working
  feature to fix a reporting problem, while leaving it alone lets the stock
  screens quietly become fiction.
- **`Encounter` still has no per-visit measurements**, so the prescription's
  `Wt` box is still a blank rule.

The case record landed 2026-08-29 (ADR 0020) — one structured clinical document
per patient, from the sixteen-section paper form the clinic uses, reached from
the patient profile. `patients.CaseRecord` plus four child tables, seventy-two
prose columns on the parent, `PatientClinicalProfile` absorbed and deleted.
**Browser-verified end to end** against the running stack as PRACTITIONER,
STAFF and OWNER: the form saved and redisplayed every section with Bengali
intact, §9 seeded its eight rows on the first save, STAFF got 403 on the page
*and* both HTMX fragments while still editing every new demographic, and the
patient card showed both of its states. Shipped in three commits; §11 vitals and
the case-record print view are **not built** and stay proposals.

- **Generic column names, and the clinic's word comes from `terminology`.** The
  alternative — `miasm` and `repertorization` behind a capability switch — was
  refused because a flag is not a quarantine: the models, migrations, history
  tables and a public repo carry the word whether the switch is on or off.
  Thirteen new keys. A hardcoded "Repertorization", "Rubric", "Miasm", "Remedy"
  or "Potency" in a template or a `*forms.py` is a bug, and
  `patients/tests/test_case_naming.py` enforces it — over *renderable strings
  only*, parsed with `ast` for Python and comment-stripped for templates,
  because a raw grep cannot tell a rule from a note about the rule. One stated
  exception: the Features screen's "Potency" placeholder, which teaches the
  owner the field is theirs to rename. (Its own comment calls the placeholders
  "deliberately ordinary words"; that one is not. Worth a look.)
- **`patients/tests/test_case_shape.py` is what makes a seventy-two-column
  migration reviewable.** It parses `docs/reference/case-taking-form.md` and
  fails in *three* directions: a prompt with no column, a mapping naming a
  column that does not exist, and a mapping for a prompt the paper no longer
  asks. §9's eight factors are read off the document too, not hardcoded. Both
  directions were confirmed to fail against planted drift. The §6/§8 duplicates
  are in an explicit `DROPPED` dict with their reasons — "we decided not to" and
  "we forgot" look identical otherwise.
- **The add-row fragment must substitute `__prefix__`, and this shipped broken
  past a green test.** `empty_form` names its inputs
  `complaints-__prefix__-complaint`, which is not a name Django's formset binds:
  the button *appears* to work, the practitioner types into the new row, and the
  save drops it in silence. The three existing add-row views (clinical, billing,
  inventory) all replace it server-side; mine did not, and the test I wrote
  asserted the placeholder was *present*. Caught in a browser. The test now
  fetches a row, posts it, and reads it back.
- **The capability switch gates creation and the offer, never reading or
  editing.** So it is *not* `capability_required`, which is unconditional: a
  patient with no record and the switch off is a 404, a patient with one is
  always reachable and still saves. Verified in the browser in both positions,
  including an edit with the switch off.
- **§9 is eight rows seeded with the record, in `save_case_record`** rather than
  by a signal — they are part of what it means for a case record to exist, and a
  signal would hide that from a reader of the function. `extra=0`, no add
  control, no delete control, and a `unique_together`. The absorption migration
  seeds them too, or the one patient whose record came from a migration renders
  an empty §9.
- **`DELETE` is a checkbox, so a bare `visible_fields` loop renders its label.**
  Every growable row carried a stray "Delete" caption over an invisible control.
  Found in a browser, now asserted.
- **`PatientClinicalProfile` is gone, and `/patients/<pk>/clinical/` with it.**
  `allergies` → `past_allergies`, `medical_history` → `past_other_history`
  **whole**, never distributed across §4's eight prompts — nothing tells "had
  measles as a child" from "appendix out in 2019" and a guess corrupts a
  clinical record. Reversible. Verified against the dev database's fifteen real
  profile rows before reseeding: every value landed, every record got its grid.
- **`ruff`'s RUF012 per-file-ignore is now `*/*forms.py`**, so the case record
  can live in `patients/case_forms.py` rather than being buried in the
  demographics module. A form Meta is a form Meta wherever it is declared.

Not verified, and the harness is why: **the `lg` two-column layout could not be
seen at first.** Django's `X-Frame-Options: DENY` blocks CLAUDE.md's sized-iframe
technique, and Chrome clamps its CSS viewport at ~950px however the X11 frame is
resized. What worked: a real `--new-window`, `wmctrl -r <title> -e` to move it
off the second monitor, then **three `ctrl+-` zoom-outs driven by python-xlib**,
which widens the CSS viewport past the `lg` breakpoint. Add that to the toolkit —
it is the only route found to a width this harness cannot otherwise produce.

Also landed, ahead of the vitals it exists for: **temperature is stored in
Fahrenheit, always, in one column** (`core/temperature.py`, amending ADR 0020
§7, whose Celsius-only decision is superseded rather than edited away).
`Organization.temperature_unit` decides the label, what the form accepts and
what is rendered back — **never what a stored number means**. A unit flag that
reinterprets stored values would make flipping the setting rewrite every reading
ever taken, with nothing to tell the two apart afterwards. Same family as the
derived invoice balance and summed stock on hand. The range check validates
against the unit *entered* (90–110 °F, 32–43 °C) then converts, so the refusal
names a bound in the unit the person used. Fahrenheit is canonical because it is
this clinic's unit and because it is the finer grid at one decimal place — a
Celsius reading round-trips as itself, asserted rather than assumed. `Encounter`
has no temperature column yet.

The seven `Patient` demographic columns landed with it — marital status,
occupation, email, a second phone, an emergency contact (two columns, so it can
be dialled) and who referred them. All STAFF-editable, all behind a "More
details" disclosure that **still posts what it contains**. `marital_status` is
not required on the form, unlike `sex`: a required field inside a collapsed
section fails validation where the person filling it in cannot see it. The ADR
says "five of the seven" go behind the disclosure but lists six items covering
all seven columns, and names the visible desk set as exactly the original six
fields — the lists won. `alt_phone` joins `search_patients` **and**
`possible_duplicates` (all four directions, both numbers against both), which is
the whole price of the column: a second number the search cannot find is worse
than no second number, because reception concludes the patient is not registered
and creates the duplicate.

The visit form's two defaults were both wrong, and both are fixed (2026-08-30).
**Browser-verified end to end** against the running stack: the default ticked on
মিরপুর released কুষ্টিয়া ১ without an untick, and New visit opened on মিরপুর with
the signed-in practitioner already chosen.

- **`Branch.is_default` is a column; the name is the last tiebreak.** The
  default chamber used to be "first by name", so adding two Bengali-named
  chambers moved it onto one that opens on the second Friday of the month — the
  collation deciding where visits are recorded, silently.
  `organizations.services.default_branch` now reads marked → lowest
  `print_order` → name. Making the name secondary is the point: renaming a
  chamber must not be able to move it either.
- **The database enforces one default per clinic**, with a `UniqueConstraint`
  on (organization, is_default) `condition=Q(is_default=True)`. Partial on
  purpose — without the condition it would allow one default *and one
  non-default*, i.e. two chambers at most.
- **Ticking it releases the old one; the clinic never unticks first.**
  `BranchForm.clean_is_default` finds whichever chamber holds it (Django drops
  that constraint from validation because `organization` is not a form field —
  the same trap as `clean_code`, docs/MVP-NOTES.md) and `BranchForm.save`
  releases it in the same transaction as the claim. A clash that survives that
  is a race between two administrators, and `organizations/views.py` catches
  `IntegrityError` and turns it into a field error rather than a 500.
- **The migration marks nothing.** An existing clinic keeps today's behaviour
  until somebody ticks a box; `bootstrap_clinic` marks the one branch it
  creates, and `bootstrap_demo` marks Main Chamber rather than letting the demo
  demonstrate the bug.
- **The practitioner is a rule, not a "default doctor" column** — such a column
  would be wrong the day the clinic hires a second one, and quietly.
  `accounts.services.default_practitioner`: the signed-in user when they are in
  `PRESCRIBING_ROLES`, else the only prescriber there is, else nobody. Genuinely
  conditional on the count — a two-prescriber clinic gets an empty required
  field, because the wrong name preselected on a prescription is worse than a
  field that asks.
- **The appointment modal had both bugs and takes both fixes**, from the same
  two functions. Two copies of "who may treat, and where" is exactly what ADR
  0019 caught once already. "Anyone" stays its first option, so a clinic with
  two prescribers is still asked.

Next: SPEC §11 phases remain suspended. Reporting (§6.7), `FieldDefinition`,
`RolePermission`, patient-level attachments and the audit log are the remaining
gaps, along with ADR 0020's own two unbuilt halves — `Encounter` vitals (§11,
whose panel on the case record is not built either) and the case-record print
view, which the ADR recommends deciding after two weeks of real use. Deployed to production on 2026-08-23 — one Oracle Cloud Always Free box,
Ubuntu 24.04 on ARM, Docker Compose behind Caddy for TLS.

## Standing rules

- **Default to the simplest thing that works.** The users are new to software,
  so simpler beats complete. Prefer one list with a filter over several
  sections; one button over two; a familiar word over a precise one. **If a
  screen needs explaining, it is wrong.** Propose removing things, not only
  adding them. This outranks tidiness, symmetry, and my own sense of what is
  well-modelled — the data model can stay precise while the screen gets plain.
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
- Tests and CI stay green. **Run all five before declaring work done**, every
  session:

  ```bash
  ruff check . && ruff format --check .
  python manage.py check
  python manage.py makemigrations --check --dry-run
  python manage.py migrate --check          # ← the local database, not the code
  python -m pytest                          # on Postgres; see Commands below
  ```

  `python -m pytest` is the single spelling used throughout this file. Add
  `-q` when you only want the summary line.

  `migrate --check` is the odd one out and the reason the list is explicit. The
  other four inspect the *code*; this one inspects **the database you are
  actually developing against**, and exits non-zero when it is behind. Nothing
  else can catch that: the suite and Docker both build fresh databases, so an
  unapplied migration is invisible everywhere except the browser, where it
  surfaces as `OperationalError: no such table`. That has now happened twice —
  the second time to `clinical_encounterphoto`, on correct code.
- **Verify interactive features in a browser before reporting them done.**
  Tests that assert status codes do not prove a UI works. Four bugs have
  shipped past green tests this way. See **Environment and verification** for
  how — which browser, what a screenshot cannot capture, and what not to touch.
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
docker compose exec web python manage.py bootstrap_clinic \
  --name "…" --timezone "…" --branch "…" \
  --admin-phone "…" --admin-name "…"          # a real clinic, no demo data
ruff check . && ruff format --check .
python manage.py check
python manage.py makemigrations --check --dry-run   # is the code ahead of the migrations?
python manage.py migrate --check                    # is this database behind the migrations?
```

Outside Docker the project runs on SQLite (`POSTGRES_DB` unset) against the
`.venv_clinicore` virtualenv. `make seed` and a `Makefile` do not exist yet.

**Run `migrate --check` against each database you use.** They are separate
files: the SQLite one from a bare `manage.py` run and the Postgres one behind
`docker compose` fall behind independently, and a migration applied to one says
nothing about the other.

## Environment and verification

**Shell.** A bare `manage.py` run — outside Docker, against SQLite — needs
`DJANGO_DEBUG=1` exported, or it dies on `DJANGO_SECRET_KEY must be set`. This
applies to that path only: inside `docker compose` the environment comes from
compose, and exporting it in your shell changes nothing there. pytest needs it
nowhere — it uses `config.settings_test` via `pyproject.toml`.

**The test suite is pytest.** `python manage.py test` finds zero tests and
exits successfully, which looks like a pass. Always `python -m pytest`. Run it
against Postgres before reporting done: SQLite skips the two Postgres-only
row-locking tests, so the two counts always differ by exactly two. Don't
record the absolute numbers here — they move every time tests land, and a
stale count reads as a failure to whoever sees it next.

**Native popups are invisible to CDP screenshots.** `<select>` and
`<datalist>` popups are OS-drawn windows, so DevTools-protocol capture shows
an empty page. To see one: drive a real browser window with X11 input and
capture the screen with ImageMagick `import`. Install any helper (python-xlib
etc.) into a throwaway directory under /tmp, never `.venv_clinicore`.

**Datalist popups cannot be styled.** They are browser chrome in both Chrome
and Firefox and ignore page CSS, including `color-scheme`. Measured, twice —
see ADR 0017, and don't measure it again. The app now renders none: every
dropdown is a `<select>` or app-drawn, so the rule is **don't introduce one**.

**Never type a password.** For app-level browser checks, use the Chrome
window that is already signed in. Firefox is rendering-only via a standalone
replica page unless the user has signed in first. Say which was used.

**Don't change desktop or OS settings** (GTK theme, locale, resolution) to
run a test. Ask instead.

**The MCP tab is a background tab.** That is fine for the DOM and for CDP
screenshots, and useless for anything the OS draws. When a check needs a real
visible window — a native popup, a print preview — launch a separate one with
`google-chrome --new-window <url>`: same profile, so it is already signed in,
and it can be closed with `wmctrl -i -c` without touching the user's tabs.
X11 input goes to whatever is focused, so `wmctrl -i -a <id>` before every
batch, or the screenshot catches the terminal instead.

**Two CDP flakes, both seen repeatedly.** `Page.captureScreenshot` times out
after 30s roughly one call in three — just call it again, the retry works. And
clicking by `ref` silently does nothing on some buttons (the prescription
form's "Add another item", twice across two sessions): take a screenshot and
click by coordinate instead. A ref click that no-ops looks exactly like a
feature that does not work, so verify the effect, never the click.

**A `{% comment %}` block containing literal `<tag>` markup is an IDE hard
error** — PyCharm parses inside Django comments and reports the tag as
unclosed. Write "form element", not the tag. The one-off scan that finds them:
strip `{% comment %}…{% endcomment %}` and `{# … #}` from every template and
grep the comment bodies for `</?[a-z]`.
