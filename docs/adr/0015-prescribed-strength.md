# 0015 — Prescribed strength is a column, named generically and labelled per clinic

Status: accepted, 2026-08-15.

## Context

The clinic prescribes classical homeopathy and needs the potency recorded as its
own fact: `Arsenicum album 200C, 4 pills, twice daily`. Without somewhere to put
it, the doctor types `200C` into **Dosage** alongside the quantity — two facts in
one column, which cannot be searched, cannot be validated, and reads badly on a
printed prescription.

Two places already existed that looked like the answer:

- `catalog.Product.default_attributes` — a `JSONField` whose docstring names
  "potency, dilution, …" outright.
- `clinical.PrescriptionItem.attributes` — a `JSONField` the product's defaults
  are copied into on save.

They were built for exactly this and never used. The only code touching either
is three lines in `PrescriptionItem.save()`; nothing writes `default_attributes`,
nothing reads `attributes`, and no form, template or admin surface mentions
them.

SPEC §1 also says, permanently: nothing specialty-specific in code —
configuration and seed data only.

## Decision

### A real column, not the JSON

`PrescriptionItem.strength` and `catalog.Product.default_strength`, both
`CharField(max_length=40, blank=True)`.

"Surfacing the JSON" is not surfacing anything: it means building a generic
key/value editor from scratch, on a prescription row, mid-consultation, for one
key. That fails the standing rule twice — more machinery than a column, and a
screen that needs explaining.

The positive case is stronger than the negative one. Strength is *prescribing
data*: it is printed and handed to a patient, and it is read back at the next
visit to decide whether to repeat or go higher. That is the same class of fact
as `dosage`, `frequency` and `duration`, all of which are columns. The proof is
that it is already going into `dosage` — the fix for two facts in one column is
a second column, not a JSON blob beside it.

A column is also cheaper at every point of use: a plain `ModelForm` field, a
plain `{{ item.strength }}`, a `simple_history` diff that reads, an indexable
value, and a `clean_strength` that could validate. Writing one JSON key from a
non-model form field needs hand-written plumbing in the form, the save path and
the template.

The JSON fields stay. They are now correctly scoped to values that really are
arbitrary; nothing is lost by leaving them empty.

### `strength`, not `potency`

A column named `potency` with `['6C', '30C', '200C', '1M', '10M']` beside it in
code is homeopathy in the schema, which SPEC §1 forbids. It is also not even
true to the domain: "how strong is this preparation" is one slot, and 30C, 500mg
and 1:10 all fill it.

So the column is named for what it measures, and the clinic's word for it comes
from the terminology map (`terminology['strength']`, defaulting to "Strength").
This is the repo's established answer to exactly this problem: `OWNER` is stored
and "Administrator" is displayed; `Encounter` is stored and "Visit" is displayed.
Renaming a column later is a data migration plus every call site, so it is
settled now.

Consequence: a `get_..._display`-style hardcoded "Potency" anywhere is a bug.
The label comes from `terms.strength` in templates and
`organization.terms['strength']` in forms.

### Gated by `Organization.strength_enabled`, defaulting to **off**

Same pattern as `advice_enabled` (A3), opposite default. A general practice puts
the strength in the medicine's name — "Paracetamol 500mg" — and would find the
column redundant clutter on every row. A classical homeopath cannot prescribe
without it. Off is the right default because the majority case is the one that
does not want it.

The field is **dropped from the form**, not hidden in the template. A field left
on the form and merely omitted from the markup is still settable by anyone who
can build a POST, and — worse — is rebuilt as empty by `construct_instance` on
every subsequent save. That second failure is the sharp one: turning the
capability off would quietly erase strengths recorded while it was on, the next
time anyone edited the visit. `PrescriptionItemForm.bind_organization` and
`ProductForm.__init__` pop the field; `test_editing_a_visit_with_the_capability_off_keeps_the_potency`
is the guard.

### The read surfaces gate on the data, never on the switch

The visit detail table and the printed prescription show the strength column
when *this prescription* has strengths on it (`show_strength` from
`clinical.views._prescription_sections`), regardless of what the clinic records
today.

This is the same rule that keeps recorded advice readable after A3's switch goes
off, and it matters more here: reprinting a visit from two years ago must
reproduce what the patient was actually handed. Gating a read surface on a
current setting is a data-hiding bug, not a feature.

### The suggested values are org data, offered as a native `<datalist>`

`Organization.strength_options` is a JSON list, edited on the Features settings
screen as one value per line. A list of potencies in code would be the SPEC §1
violation the naming decision just avoided, and the clinic dispensing dilutions
next door wants different values anyway.

The input is `<input list="strength-options">` with a `<datalist>`. That gives
the requested "dropdown of standard values with a free-text escape" as *one*
control: the box is ordinary text, so an unusual potency is simply typed. An
explicit "Other…" option would be a second control and a rule to explain, for
behaviour the native element already has.

`Organization.strengths` cleans the stored list on the way out — blanks and
case-insensitive duplicates dropped, values truncated, original order kept —
because the column is org-editable JSON and a datalist must survive whatever is
in it.

### The Features screen gained two controls, not one

There is no terminology settings screen yet (SPEC §6.8 defers it), so the label
is edited here alongside the switch and the values. A capability whose label
still says "Strength" at a clinic that says "Potency" is the feature not
working — the same argument that put `advice_enabled` on a screen at all.

A blank label clears the override rather than storing `''`, which `terms` would
drop anyway while leaving a dead key in the JSON.

### Advice carries no strength

Advice is not a substance. `PrescriptionItem.save()` blanks it for `ADVICE`, and
a check constraint — `prescription_item_advice_has_no_strength` — asserts it, so
the rule holds for every write path rather than only the one that goes through
`save()`. This mirrors `prescription_item_advice_has_no_dosage`.

Unlike `dosage`, `strength` is blank rather than nullable. A dose of null means
"not applicable"; a *missing* strength is ordinary — most clinics never record
one — so there is nothing for a third state to say.

## Consequences

- Existing prescription items have `strength = ''`, and there is no migration to
  split `dosage` values like `"30C 4 pills"` apart. Nothing distinguishes those
  from doses that were always doses, and a guess would corrupt real records. The
  clinic's historical rows keep whatever was typed; new ones are recorded
  properly.
- The prescription row is six fields wide where the capability is on, which does
  not fit one twelve-column line with the search box at four. Instructions moves
  to its own full-width row in that case — it is the longest thing typed there.
  Where the capability is off the row is unchanged.
- `catalog.Product.default_strength` prefills the row and stays editable: the
  same remedy is prescribed at different strengths to different patients, so it
  is a starting point and never a constraint. It rides to the browser as
  `data-strength` on the autocomplete suggestion, handled by the existing
  `prefill()` in `static/js/item-autocomplete.js`.
- A future "find every patient given Sulphur 200C" report is now a plain query
  rather than a JSON containment lookup.
