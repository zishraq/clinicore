# 0019 — "May read clinical data" and "may be booked" are two facts

**Status:** accepted, 2026-08-23
**Supersedes nothing. Amends SPEC §6.1's role list.**

## Context

`Role` had three members — `OWNER` (labelled Administrator), `PRACTITIONER` and
`STAFF` — and one set derived from them:

```python
CLINICAL_ROLES = frozenset({Role.OWNER, Role.PRACTITIONER})
```

That set answered "may this person read a consultation note". The same pair,
**inlined rather than imported**, also answered a different question in two
other places:

- `clinical/forms.py` `_practitioner_users` — who the visit form offers as the
  treating practitioner
- `scheduling/views.py` `_practitioners` — who the appointment modal offers to
  book a patient with

Those two functions were byte-identical queries with different names. The second
one's docstring said *"Same rule as the visit form's field"*, which is the
comment a duplicated rule leaves behind when it is still true.

And a third question — "may this person administer the clinic" — was answered by
a separate hardcoded `role == Role.OWNER` in `Membership.is_owner` and
`accounts.permissions.owner_required`.

The clinic that prompted this has an administrator who maintains the system and
**does not treat patients**. Under the old model their account appeared in the
practitioner dropdown on the visit form and in the appointment modal, where a
receptionist could book a patient with them. Nothing in the application could
express "reads every record, treats nobody", because reading and treating were
one membership in one set.

## Decision

Add a fourth role, `DEVELOPER`, and split the one fact into three named sets in
`accounts/models.py`:

```python
# may read a consultation note
CLINICAL_ROLES = frozenset({OWNER, PRACTITIONER, DEVELOPER})
# may be booked, or recorded as the treating practitioner
PRESCRIBING_ROLES = frozenset({OWNER, PRACTITIONER})
# may administer the organization
ADMIN_ROLES = frozenset({OWNER, DEVELOPER})
```

`DEVELOPER` is everything `OWNER` can do minus eligibility to treat anyone. It
is in `CLINICAL_ROLES` and `ADMIN_ROLES`, and deliberately not in
`PRESCRIBING_ROLES`.

Every gate reads one of the three. The four inlined pairs are gone:
`clinical_access_required` is `role_required(*CLINICAL_ROLES)`, `owner_required`
is `role_required(*ADMIN_ROLES)`, and both practitioner lookups are now one
function, `accounts.services.prescribing_users`. **A bare role comparison
outside `accounts.models`, or a re-inlined pair, is how these silently
re-merge** — `accounts/tests/test_roles.py` asserts the sets have diverged so a
future edit cannot quietly put them back.

### Why a full administration grant

`DEVELOPER` gets all seven `@owner_required` views, including `member_create`,
`member_update`, `member_toggle_active` and `member_reset_password`. That was
considered and chosen rather than defaulted into:

There is no SMTP on this deployment and no password reset by email (ADR 0013).
An administrator typing a temporary password and reading it out is the **only**
recovery path that exists. A developer who cannot perform one leaves the doctor
as the sole recourse for every forgotten password — and the doctor is precisely
who this role exists to insulate from system administration. The person holding
this role also has SSH and `psql` on the box, so withholding it in the UI would
be theatre rather than a boundary.

### Why `is_owner` keeps its name

`Membership.is_owner` now means "may administer" and returns true for two roles,
which makes the name wrong. It is kept anyway: renaming it touches three call
sites, and a rename landing in the same commit as a behaviour change makes the
diff harder to read on a live system. **Follow-up: rename `is_owner` to
`is_administrator`.** Recorded here rather than as a `TODO` comment, because a
marker in code is a note to nobody.

### Why the self-guard was relaxed

`MemberUpdateForm` used to set `role.disabled = True` when an administrator
edited their own row. That was the entire last-administrator guard, and the
reasoning still holds: the only account that could remove the last administrator
is that administrator, so refusing self-demotion means an organization always
has at least one, without counting anything.

But it also refused `OWNER → DEVELOPER`, which is the change this ADR exists to
make possible — leaving the new role unreachable for the person who needs it.

The guard is now **"you may not move yourself out of `ADMIN_ROLES`"**: the role
dropdown on your own row offers only the administering roles. The invariant is
unchanged, because moving between two roles that both administer removes
nobody's access.

Enforcement stays structural. `ChoiceField.validate` refuses any value outside
`choices`, so a hand-built POST of `STAFF` is rejected by the field itself — not
by a `clean_role` that a later refactor could drop. The one visible difference
is that self-demotion is now **refused with a field error instead of silently
ignored**, which is a better answer than a redirect that looks like it saved.

### The trap that comes with filtering the dropdown

Filtering `EncounterForm.practitioner` by role introduces a bug that has nothing
to do with roles. A `ModelChoiceField` whose stored value falls outside its
queryset renders with nothing selected and then refuses the save with "Select a
valid choice". So the moment somebody stops being a prescribing role, **every
visit ever recorded against them becomes unamendable** — a record locked by a
change to a different row entirely.

`_practitioner_choices` therefore always offers the instance's current
practitioner alongside the eligible ones. This is the same guard, for the same
reason, as `core.forms.closed_choices` (ADR 0017); the difference is only that
one is a queryset and the other a list of strings.

`clinical/views.py` also stopped prefilling the signed-in user as the
practitioner unless they are in `PRESCRIBING_ROLES` — prefilling a value outside
the queryset renders a select with nothing chosen, which reads as a broken field
rather than an unanswered one.

`Appointment` needs no equivalent: its modal is create-only, and the view
already filters the posted pk through the same lookup, so an ineligible id
becomes `None` rather than an error. A walk-in with no practitioner is valid by
design.

## The label

The stored value is `DEVELOPER` and never moves (SPEC §5). The word on screen
comes from the terminology map: `role_developer`, defaulting to "Developer".
Both readers derive the key from the stored value — `{% role_label %}` and
`accounts.forms._role_choices` — so a clinic that would rather call it
"Technician" or "IT" sets `Organization.terminology['role_developer']` and
changes no code.

The default entry in `DEFAULT_TERMINOLOGY` is **mandatory, not decorative**:
`Organization.terms` drops overrides for keys it does not know, so without it a
clinic's rename would be silently ignored.

There is still no terminology *screen*, so that edit is made on the Organization
row in the Django admin or from a shell. It is data either way, never a
migration.

## Consequences

- Existing rows are untouched. Both `Encounter.practitioner` and
  `Appointment.practitioner` are plain FKs to `AUTH_USER_MODEL` with **no
  `limit_choices_to`**, so nothing at the database or model layer constrains who
  may be stored there. Only the form querysets narrow.
- The migration is **choices-only** — see the deployment note below.
- `STAFF` is unchanged in every respect.
- SPEC §6.1's role list is now four roles. `FieldDefinition` / `RolePermission`
  remain the eventual replacement for all of this; these sets are still the MVP
  stand-in, and still greppable by the `MVP: replace with permission layer`
  marker.

## Deploying this

A live clinic, deployed by `git pull` and a rebuild. **Follow "Deploying an
update" in `docs/RUNBOOK.md` rather than a shorter sequence typed from memory** —
it takes a backup first, and it builds and migrates *before* starting the new
version, so the database is never behind the code that is serving requests:

```bash
cd /opt/clinicore
sudo ./deploy/backup.sh                                                   # 1
git pull                                                                  # 2
docker compose -f docker-compose.prod.yml build                           # 3
docker compose -f docker-compose.prod.yml run --rm web \
    python manage.py migrate                                              # 4
docker compose -f docker-compose.prod.yml up -d                           # 5
./deploy/status.sh                                                        # 6
```

**The migration alters no data.** `accounts/migrations/0004_alter_membership_role.py`
is a single `AlterField` that adds `DEVELOPER` to the field's `choices`. Django
does not enforce `choices` at the database level and generates no constraint for
them, so there is no schema change to apply. `sqlmigrate` on PostgreSQL confirms
it, marking the operation a no-op in as many words:

```sql
BEGIN;
--
-- Alter field role on membership
--
-- (no-op)
COMMIT;
```

`Membership.role` is already `max_length=20` and `'DEVELOPER'` is 9 characters,
so the column does not widen. No `RunPython`, no data migration, and no
management command writes to a `Membership` row.

Step 4 is the whole of the database change and it is a no-op, so there is no
window in which the two versions disagree about the schema and nothing to undo
if the deploy is abandoned between steps.

Changing your own role is then one edit on **Team → your own row**, where the
dropdown offers Administrator and Developer. Nothing else in the application
changes for anybody else, and no existing record moves.
