# 0010 — One day list, and its status is derived

- **Status:** Accepted
- **Date:** 2026-08-07
- **Relates to:** SPEC §5 (domain model), SPEC §6.3 (scheduling and queue),
  `docs/adr/0008-invoice-numbering-and-derived-balances.md`,
  `docs/adr/0009-ledger-based-stock.md`

## Context

SPEC §5 specified two models: `Appointment` (patient, branch, practitioner,
scheduled slot, status) and `QueueEntry` (patient, branch, date, position,
status, timestamps). It also specified appointment booking with "slot templates
per practitioner per branch".

The confirmed clinic workflow contradicts all three of those assumptions:

- Patients both book ahead and walk in, and the front desk treats them as one
  list. A walk-in is a patient who is here; a booking is a patient who is
  expected. That is one question — *who is in this building and who is coming* —
  and answering it from two tables means reconciling them on every screen.
- There are no fixed-length slots. "Tuesday morning" is a real answer to *when*.
- Double-booking is normal, not an error.

## Decision

### One model, `Appointment`. `QueueEntry` is struck from SPEC §5

A walk-in is an `Appointment` with `source=WALK_IN`, created already arrived
because the patient is standing at the desk. Everything the queue board needed —
who is waiting, how long, in what order — is answerable from the same row, and
there is no second table to keep in step.

The alternative kept `QueueEntry` and linked it to `Appointment` for booked
patients. It fails on the ordinary case: a booked patient who arrives exists in
both tables, and every question about them ("are they still waiting?") has two
answers that can differ.

`position` is not carried over. The order that matters is who has waited
longest, which `arrived_at` already answers correctly and cannot be dragged out
of sync by a reorder nobody remembered to do.

### Status is never a column

`Appointment` stores no `status`. The five states are computed:

```
resolution        → CANCELLED / NO_SHOW
seen_at           → SEEN
arrived_at        → ARRIVED
otherwise         → BOOKED
```

Same reasoning as the derived invoice balance (ADR 0008) and batch on-hand
(ADR 0009), and it lands harder here because all three inputs have to exist
anyway. `arrived_at` is where a waiting time comes from; `seen_at` is when the
consultation started; `resolution` is a decision nothing can infer. A `status`
field would be a fourth value restating those three and free to disagree with
them — which is precisely how a day list ends up showing a patient as waiting an
hour after they went home.

Cost: filtering by status is an annotation rather than an indexed column. At
this scale that is nothing, and `scheduled_date` — which *is* indexed — does the
real narrowing.

Four check constraints keep the derivation unambiguous rather than
precedence-dependent: seen implies arrived, seen excludes resolved, a walk-in
has arrived, and a resolution reason needs a resolution.

### SEEN is a timestamp, not a lookup through the encounter link

The obvious derivation is "SEEN if a visit points at this row". It was rejected.
SPEC §4 allows soft delete on clinical records, and `Encounter` may well gain it.
The moment it does, a reverse lookup through the default manager stops seeing a
removed visit and the appointment silently reverts from SEEN to ARRIVED — this
day's history rewriting itself, which is the exact failure the derived status is
meant to prevent.

`seen_at` is local to the row, symmetric with `arrived_at`, and immune to
whatever later becomes of the visit. The encounter link still exists and still
answers *which* visit; it just is not what the status depends on.
`scheduling/tests/test_transitions.py` deletes the encounter and asserts the
status is unchanged.

### One visit per appointment

`Encounter.appointment` is a nullable `OneToOneField`. Nullable because a visit
needs no appointment to be valid — the doctor can simply write one, and that
path must not regress. One-to-one because two visits off a single row would make
"was this appointment seen?" ambiguous in a way the derived status cannot
resolve.

### SEEN is a consequence, not a button

It is set by saving a visit against the row, and `transition` refuses
`to=SEEN` without an encounter. An ARRIVED appointment whose doctor never wrote
a visit therefore stays ARRIVED indefinitely. That is deliberate: it is exactly
what the receptionist needs to see, and cancelling is the way out.

### NO_SHOW is not terminal

`NO_SHOW → ARRIVED` is allowed, and marking arrived clears the resolution.
Patients turn up an hour late; making the front desk rebook them to record that
would be a worse lie than the one it prevents.

### Payment status stays off the appointment

The clinic asked for PAID / PARTIALLY_PAID / PAYMENT_DUE as appointment
statuses. Refused: the invoice already owns what is owed and derives
`payment_status` from its payments (ADR 0008). Copying it onto the appointment
creates a value that is right when written and wrong the moment a payment is
voided. The day list reads it through `appointment → encounter → invoice` for
display only.

Consequence worth stating: an appointment with no encounter has no payment state
at all, which is correct — nothing was billed.

Note for the UI increment: SPEC §6.1 as amended 2026-08-03 puts every billing
surface behind PRACTITIONER/OWNER, so the payment column is hidden from STAFF
even though STAFF owns the rest of this screen.

Built in increment 2 as `scheduling.services.with_bills`, which is where the
read lives so no template has to reach through two relations. Hidden from STAFF
by not looking it up at all — `_day_context` only calls it for a membership with
clinical access, so a template that forgot its check would have nothing to leak.
It is one query for the whole day, mirroring the invoice list's annotations
rather than walking rows. A seen visit with no bill says "No bill" rather than
rendering blank: blank reads as "nothing owing", which is the opposite of true.

### "Start visit" is on the day list, gated by role, not moved off it

SEEN is a consequence, not a button (above) — but something still has to be the
occasion for writing the visit, and the first version of this screen linked
nowhere clinical at all. That left `ARRIVED → SEEN` unreachable from anywhere in
the application.

The affordance belongs on the day list: it is where the doctor learns someone
has arrived, and sending him to the visit list to act on that is a worse flow
than the one link is worth. It is gated on `membership.can_view_clinical` rather
than relocated — an offer STAFF cannot follow is a 403 they were invited to walk
into.

The link opens the visit form with `?appointment=`, which prefills the patient,
branch and practitioner the receptionist already recorded, and the form carries
the row through the POST as a hidden field because a query string does not
survive one. Saving marks the row seen, so the doctor never marks anything.

Two failure modes decided the error handling, and both resolve the same way — in
favour of the note:

- The row stops being ARRIVED mid-consultation (cancelled at the desk). The
  visit is saved and the failure to consume the row is reported as a warning.
  A visit with no appointment is completely valid, so there is nothing to roll
  back and rolling back would cost the doctor his typing.
- Two tabs, two saves against one row. `transition` is idempotent, so the second
  save leaves the link alone; the second visit is real but unlinked, which is
  what `Encounter.appointment` being one-to-one is for.

Only ARRIVED rows offer the link, because ARRIVED is the only state
`transition(to=SEEN)` accepts — offering it on a booked row would be a button
that refuses.

### `Encounter.follow_up_date` stays, with exactly one writer

Three options were weighed:

1. Keep both the date and the appointment as independent records. Rejected —
   two places recording one intention.
2. Derive `follow_up_date` from the linked appointment. Rejected — SPEC §6.3
   wants a "patients due for follow-up" work list, and that wants an indexed
   date column, not a join computed per row.
3. **Chosen.** The field stays and every existing row keeps working.
   `Appointment.origin_encounter` records which visit asked for the return, and
   `scheduling.services.reschedule` is the *only* thing that writes
   `follow_up_date` once an appointment exists, in the same transaction as the
   move.

This is a sync, which is normally what this codebase avoids. It is acceptable
because there is one writer and one transaction — the same shape as
`name_snapshot` (ADR 0007). What makes it safe is the single-writer rule, so
that rule is tested directly in `scheduling/tests/test_follow_ups.py` rather
than trusted.

### The patient picker moved, and registering became a STAFF right

The walk-in modal needs the same search-and-register control the visit form has,
so `templates/clinical/_patient_picker.html` became
`templates/partials/_patient_picker.html`, parameterized by `field_name` and
`input_id`. The clinical template is now a one-line shim: the visit form binds a
ModelForm field, the walk-in modal binds a bare input in something that is not a
`<form>` at all, and the control cannot assume either.

The dialog the picker's "add a new patient" offer opens moved out with it, into
`templates/patients/_add_patient_modal.html`. This one is a trap worth naming:
`base.html`'s `modals` block is empty by design, so **a page that renders the
picker must also render the dialog**, or htmx raises `htmx:targetError` on a
target that does not exist and the offer silently does nothing. The walk-in
modal shipped exactly that way, past a green suite, because every test around it
asserted a status code. `test_the_registration_offer_has_somewhere_to_open`
now reads the offer's own `hx-target` out of the suggestions fragment and
asserts the day page contains that id, so the coupling fails in CI instead of in
a clinic.

`patients.patient_quick_create` lost `clinical_access_required` as a consequence
— the receptionist is the person registering a walk-in. This is a relaxation, so
the reasoning is recorded rather than assumed: SPEC §6.1 gives STAFF "patient
search and creation"; the view renders and posts the same `PatientForm` that
`/patients/new/` has always exposed to STAFF; and the clinical profile is not on
that form. `require_membership` still runs, so it is exactly as open as
`patient_create` and no more. The boundary that does hold — demographics yes,
narrative no — is asserted by
`test_the_modal_still_gives_staff_nothing_clinical`.

### What the browser pass established (2026-08-07)

Two sessions, STAFF and PRACTITIONER, against the seeded demo day. Recorded
because three of these could not have been caught by a status code, and one
changed the tests.

- **The polled fragment is a second render through a second view, and needed its
  own assertion.** `_rows.html` is reached both by the page and by `day_rows`.
  Had `membership` arrived in one context and not the other, the gate would read
  false for everyone and the doctor's link would vanish five seconds after he
  opened the screen — or appear for STAFF on the first tick. The fragment
  renders identically for a practitioner, and there are now tests for both roles
  on the fragment, not just on the page.
- **A visibility-guarded poll cannot be observed from an automated tab.** The
  driven tab reports `document.visibilityState === 'hidden'`, so the guard
  correctly suppresses every tick and a "did my text survive the poll?" check
  passes without a swap ever happening. The honest test is to fire the identical
  swap by hand (`htmx.ajax` GET of the rows URL into `#day-rows`) with the modal
  open. Done that way: two swaps landed, the modal stayed open, the typed reason
  was byte-identical, focus never moved. Anyone re-checking this must force the
  swap or they are testing nothing.
- **The refusal path lands where it should.** A whitespace-only reason put the
  error inside the modal, left `#day-rows` untouched, and the polled container
  measured zero inputs at every point checked.
- **The registration offer inside the walk-in modal works**, which is the
  precise thing that shipped dead. The dialog opened, the created patient came
  back selected with the hidden pk actually set, and the half-written walk-in
  survived behind it.
- **Start visit → seen completes**, including the prefill fallback: a walk-in
  with no practitioner assigned filled the field with the signed-in doctor. The
  row left Waiting, landed in Done as seen, and stopped offering the link.

One defect was found and is **not** an appointments defect: the organization's
timezone is written and never read, so absolute datetimes render in UTC. It
reaches this feature only through the visit form the day list now links to.
Recorded in `docs/MVP-NOTES.md` under "Known defects".

## Consequences

- SPEC §5 loses `QueueEntry`; SPEC §6.3's "reorder" and "slot templates" go with
  it. Amended in the same commit as this ADR.
- Sorting has to place a vague booking and a timed one on one axis, so a time is
  ranked by the part of day it falls in (`MORNING_ENDS`, `AFTERNOON_ENDS` in
  `scheduling/models.py`) rather than sorted into a block ahead of everything
  vague. A 16:00 booking belongs after "Morning" and before "Evening".
- No unique constraint on (practitioner, date, time). Double-booking is allowed,
  and the absence is deliberate rather than forgotten.
- No soft delete: SPEC §4 restricts it to patients and clinical records, and
  CANCELLED is the reversal — correction by reversal, as in billing.
