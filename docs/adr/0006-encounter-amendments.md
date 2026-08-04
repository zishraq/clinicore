# 0006 — Encounter amendments via django-simple-history

- **Status:** Accepted
- **Date:** 2026-08-01
- **Relates to:** SPEC §6.4 (corrections to a finalized encounter), SPEC §4
  (tenant isolation), `docs/adr/0005-org-scoped-default-manager.md`

## Context

SPEC §6.4 requires that *"corrections to a finalized encounter create a history
entry, never a silent overwrite"*, and that the edit history is viewable by
authorized roles. The MVP shipped without this: `django-simple-history` was not
installed, adding a dependency needed asking, and so a finalized encounter was
simply made read-only. That was the wrong trade — it is safe against silent
overwrites but it makes a legitimate, routine clinical act (correcting a note
after a lab result arrives) impossible, which pushes the correction into a
place the system cannot see.

## Decision

`django-simple-history` (3.13.0) provides revisions on `Encounter`,
`Prescription`, and `PrescriptionItem`. A finalized encounter is editable again
by PRACTITIONER and OWNER; every save after finalization requires a reason,
which is stored as the history row's `history_change_reason`, and moves the
encounter to status `AMENDED`.

### Why simple-history rather than a hand-rolled revision table

An `EncounterAmendment` table storing changed fields is the obvious
alternative, and it is what a bespoke build reaches for first. It loses on three
counts. It stores a *diff*, so reconstructing the record as it stood on a given
date means replaying every amendment — and a prescription printed six months ago
must be reproducible exactly. simple-history stores the whole prior row, so
point-in-time reconstruction is a single query. Second, it would need parallel
handling for `Prescription` and `PrescriptionItem`, including deletions of items,
which the library gets right for free (`history_type='-'`). Third, the pieces we
would write by hand — actor, timestamp, reason, diff — are exactly the library's
API surface (`history_user`, `history_date`, `history_change_reason`,
`diff_against`), and `diff_against` in particular is fiddly to get right for
foreign keys.

The cost is a doubled write volume on three tables and a dependency. At fewer
than 50 concurrent users this is not a performance consideration.

Applied to clinical models only. `Patient` also carries clinical weight and SPEC
§3 lists it, but the surface stays deliberately small for now; the org and
account models are configuration, not clinical record, and are excluded.

### Why the actor is set explicitly, not by middleware

simple-history ships `HistoryRequestMiddleware`, which picks the user out of the
current request. We do not install it. ADR 0005 already commits to one piece of
ambient request state (the active organization) and justifies it as a safety
net for the ORM; a second one would be a habit rather than a decision. Instead
`clinical/services.py` sets `_history_user` and `_change_reason` on each instance
before saving, which keeps the services callable from a management command or a
test with an explicit actor and no request in sight.

### Why the reason is checked twice

`EncounterForm` marks `change_reason` required when the encounter is locked, so
the user gets an inline field error. `save_encounter()` *also* raises
`AmendmentReasonRequired` when a locked encounter is saved with an empty reason.
The form is a user-experience gate that any future caller — a management
command, an import, a later API — can bypass. The service is the one that
cannot.

## The tenancy consequence, which is the sharp edge

**Historical models do not inherit `OrgScopedManager`.** simple-history builds
`HistoricalEncounter` as a plain `models.Model` with a copy of the fields and its
own `HistoryManager`. It is not an `OrgOwnedModel` subclass, so:

- `Encounter.history.all()` returns **every organization's revisions**.
- The parametrized isolation suite in `core/tests/test_org_scoping.py` does not
  cover it, because that suite walks `OrgOwnedModel` subclasses and historical
  models are not among them. The guarantee ADR 0005 establishes stops at the
  live tables.

This is a real leak waiting for the first developer who writes an audit screen
over `Encounter.history`. Three things hold the line:

1. **Every history query filters on `organization_id` explicitly.** The only
   supported readers are `encounter_revisions()` and `revision_timeline()` in
   `clinical/services.py`, both of which take `organization` as their first
   argument and filter on it — including when handed an encounter belonging to
   someone else, which returns empty rather than leaking.
2. **`clinical/tests/test_history_isolation.py`** asserts the filtered path is
   clean, asserts the view returns 404 for another tenant's encounter, and
   *documents the trap* with a test that shows the raw manager returning both
   organizations' rows. If a future version of the library changes that, the
   test fails and someone reads this ADR.
3. **A structural test** asserts every historical model of an org-owned model
   keeps an `organization` column, since without it tenant filtering is not
   possible at all.

A stronger fix — a custom `HistoricalRecords` subclass whose default manager is
org-scoped — was considered and rejected for now. It would make the ambient
contextvar a requirement for reading history in management commands and data
migrations, which is where history is most often read, and it would make the
"loud failure" of `ActiveOrganizationRequired` fire in exactly the batch
contexts that are hardest to debug. Explicit filtering at the three call sites
is smaller and more honest. Revisit if history readers multiply.

## Consequences

- A finalized encounter is no longer read-only. `Encounter.is_editable` is now
  always true and `Encounter.is_locked` is what gates the reason requirement;
  any caller that used `is_editable` to mean "not finalized" must be updated.
- `EncounterStatus` gains `AMENDED`, matching SPEC §5. An amended encounter can
  be amended again — each one is another revision with its own reason.
- Adding history to a new model adds a test obligation: the organization-column
  assertion covers it automatically, but any new query over `.history` must
  filter by organization.
- Prescription and item revisions inherit the encounter's amendment reason, so a
  changed dose and the note explaining it stay connected.
