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
