# 0018 — Importing real patient data is shaped differently from importing a catalog

Status: accepted, 2026-08-22.

## Context

The clinic has handed over a CSV of the patients it already has on paper:
`full_name`, `date_of_birth`, `sex`, `phone`. It needs to be in the application
before the clinic starts using it, and it is **real patient data** — it must
never enter the repository, the Docker image, or a test fixture.

`catalog.import_remedies` already exists and looks like the obvious template. It
is the wrong template, and the difference is worth stating rather than leaving
the next reader to infer that the two commands drifted.

## Decision

### The file is never bundled, and `--file` is required

`import_remedies` has a module-level `DEFAULT_FILE` pointing inside the app, so
it can be run with no path at all. That is correct there: a materia medica index
is public-domain reference data, and shipping it means a new clinic gets its
medicine list in one command.

`import_patients` has **no default, no fallback, and a required `--file`**. There
is deliberately no way to run it against a file that shipped with the code,
because any such file would be a patient list in the repository. `.gitignore`
blocks `*patients*.csv` and `/catalog/data/*.csv` for the same reason.

The one committed CSV is `docs/sample-patient-import.csv` — six invented people
showing the accepted format. It lives in `docs/` rather than any app's `data/`
directory precisely because a `data/` directory is where a real file would be
dropped by mistake. `.gitignore` carries an exact-path negation for it: a no-op
today, but it keeps the sample if someone later widens the pattern to
`*patient*.csv`, and being an exact path rather than a glob it cannot let a real
file through beside it.

### A header row is required

The first line must name the columns. A headerless file is refused rather than
read positionally, because a column order that silently transposes `sex` and
`phone` is a mistake nobody notices until they open a record months later. Names
are matched case-insensitively and after stripping a byte-order mark: every
Excel and Google Sheets export is UTF-8 with a BOM, and `﻿full_name` is not
`full_name`.

**Extra columns are ignored with a note listing them**, not refused. A clinic
that added an `address` column has not made a mistake worth stopping an import
for. A *missing required* column is still an error.

### Idempotency is an exact match on three fields

Patients have no natural key. `phone` is `blank=True` and two family members
share one, so it cannot be one. The rule is therefore conservative: a CSV row is
skipped when a patient already exists in the organization with **all three** of

- `full_name` equal case-insensitively,
- the same `date_of_birth` (both null counts as the same),
- the same phone after `dial_string()` normalisation (both blank counts as the
  same).

Matched through `Patient.all_objects`, so a **soft-deleted** patient is found and
the row is skipped rather than silently resurrected — recreating a record someone
deliberately removed is worse than skipping it, and it is reported as its own
count. The same key twice inside one file also creates one patient.

Two family members on one phone differ by name, so both import. That is the case
the rule is built around.

**The honest limit**: if the operator corrects a spelling and re-runs, that row
becomes a second patient. Nothing in the data distinguishes "I fixed a typo" from
"this is a different person", and guessing would merge two real people. The guard
is the dry run — on a second pass it must report *would create 0*, and any other
number is the signal that the file changed. The runbook says so in those words.

A second run creating duplicate patients is the failure this command exists to
avoid, so `test_running_it_twice_creates_nothing_the_second_time` is the test to
keep working.

### An unrecognised `sex` is imported as unknown, and reported loudly

`Male`/`Female`/`Other`/`Unknown` and the stored codes `M`/`F`/`O`/`U` all map,
case-insensitively. Anything else becomes `UNKNOWN` — it does **not** fail the
row. Sex is visible and editable on the patient screen, `UNKNOWN` is a value the
model supports by design, and one odd cell should not cost the other three
correct fields.

The condition is that it is actionable: every one is printed with its row number
and the offending value, and **blank and unrecognised are counted separately**.
Blank is legitimate absence; `Mael` is a data-entry error someone should look at,
and merging them into one number hides the second inside the first.

### Dates are strict, and there is no format sniffing

`date.fromisoformat`, nothing else. Blank is allowed and means unknown (the model
permits it, and `approx_age_years` stays null so `patient_dob_xor_approx_age`
holds). Anything unparseable, or any date in the future, fails the row with the
offending value.

No `dateutil`, no heuristics: `01/02/1998` is two different days on two
continents, and a guess writes a birth date nobody ever re-checks. A clinic
exporting `d/m/Y` gets an explicit `--date-format` option, added when a real file
turns out to need one.

### The branch is refused, not guessed

`--branch <code>` names it. Where the organization has exactly one active branch
the argument may be omitted and that branch is used, named in the output. Where
it has several and none was given, the command **refuses** and lists the codes.
Several hundred patients filed at the wrong chamber — or at none — is a
data-quality problem that surfaces months later as an empty filter.

### One transaction, one savepoint per row

The import is a single operational act: either the clinic's list is in or it is
not, and a crash halfway through must leave nothing to reconcile. So the run is
one `transaction.atomic()`.

Inside it, each row gets its own savepoint, for the reason `import_remedies`
uses them: an `IntegrityError` poisons the enclosing transaction, so a refused
row has to be rolled back to a point before the next insert is attempted. One bad
row therefore does not cost the other 199.

Codes come from `patients.services.generate_patient_code`, unchanged — the same
locked `DocumentSequence` row the registration form uses, called inside the
transaction that writes the patient as its docstring requires. Two consequences
worth knowing: the sequence row stays **locked for the whole run**, so reception
cannot register a patient concurrently (sub-second for a few hundred rows, and
the runbook says to run it when nobody is in the app); and its `start_after`
floor rescans the organization's codes per row, which is O(n²) and entirely
acceptable at this size — reusing the audited allocator unchanged is worth more
than the scan costs.

### `--dry-run` validates, it does not write and roll back

It parses, validates and checks the dedupe with reads, and opens no write path at
all. "Do the whole thing and roll back" would be a smaller diff and a worse
promise: the operator is told nothing was written, and that has to be true by
construction rather than by a transaction behaving.

What it therefore cannot predict is a unique-constraint collision that only
appears on insert — here only `patient_code_unique_per_org`, which the allocator
owns. That limit is in the help text.

## Consequences

- **The command cannot undo itself.** There is no `--undo`, and soft-deleting a
  bad import would leave the codes burned. The reversal is a restore, so the
  runbook takes a backup *before* the import rather than after.
- The file has to be copied **into the container** — production has no bind mount
  for the code (`docker-compose.prod.yml` says why) — which means it exists in
  two more places than the operator started with. The runbook deletes both, and
  says that a third copy is still in whatever email or WhatsApp it arrived by.
- Every test builds its CSV in `tmp_path` with invented names. A fixture file of
  "sample patients" would be the thing this ADR exists to prevent, arriving
  through the back door.
