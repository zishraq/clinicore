# 0020 — The case record is one document per patient, named for what it records

Status: **accepted, 2026-08-29**, with one decision amended — see
*Amended: temperature is stored in Fahrenheit* below, which supersedes the
Celsius-only paragraph in §7. Built in three increments: the `Patient` columns,
the temperature setting, and the case record itself. §11's `Encounter` columns
and the print view are **not built** and remain proposals.

Source document: `docs/reference/case-taking-form.md` — a transcription of the
paper sheet Dr Anwar uses at Global Homeopathy Clinic, sixteen sections.

Extends `docs/adr/0015-prescribed-strength.md` and
`docs/adr/0017-dispensing-details.md`, which settled how a specialty's word for
a thing is kept out of the schema, and
`docs/adr/0019-read-clinical-and-may-be-booked-are-two-facts.md`, which owns the
role sets this gates on.

## Context

The clinic takes a full case on paper before treating anyone, and wants it in the
system: one structured clinical document per patient, reached from the patient
profile. The paper form is sixteen sections — four repeating tables and twelve
sections of ruled prose boxes.

Two of those sections are already built and are not rebuilt here:

- **§15 Prescription** is `Prescription` + `PrescriptionItem` (remedy, strength,
  pack size, preparation, dosage, frequency, duration, instructions) and
  `Encounter.follow_up_date`.
- **§16 Follow-up record** is the encounter timeline.

Restating either would be a remedy recorded in two tables that can disagree —
the objection that struck `QueueEntry` (ADR 0010) and that kept payment status
off `Appointment`. The case record **links** to them.

One section is per-visit rather than per-patient: **§11 Clinical examination**
holds pulse, blood pressure, temperature, respiratory rate, SpO₂, height and
weight, which are observations at a moment. A weight from three years ago
printed beside today's assessment is wrong.

And two sections are written in the clinic's own vocabulary — §13's "Miasmatic /
constitutional assessment" and the whole of §14, "Repertorization / remedy
analysis". SPEC §1 forbids specialty concepts in models, templates, URLs and
naming, permanently, because the second customer is the point of the product.

## Decision

### 1. The naming question: generic columns and the terminology map, not a gate

**Picked: name every column for what it measures, and route the clinic's word
through `Organization.terminology`.** This is ADR 0015's answer applied to
eleven more labels, and it is picked over the alternative — accepting `miasm`
and `repertorization` in the schema behind a capability switch — for four
reasons.

**A flag is not a quarantine; there is no "inside the gate".**
`Organization.case_record_enabled` is a column on the shared tenant root. The
models, their migrations, their `simple_history` twins, their URLs, their admin
registrations and their templates exist in *every* deployment whether the switch
is on or off. A gate hides a screen. It does not remove the word from the
schema, from `makemigrations`, from a `dumpdata`, or from a public repository
that SPEC §1 says is a portfolio piece. The specialty naming would be in the
product; only the pixels would be conditional.

**The cost is one-directional and this repo has already priced it.** ADR 0015
settled `strength` rather than `potency` on exactly this ground: renaming a
column later is a data migration plus every call site. Here it would be a model
name, a table name, a URL segment, five field names and two history tables —
and history tables are the ones you cannot casually rewrite, because they are
the record of what the document said in 2027.

**Generic names are not empty names.** §14 is a worked shortlist of candidate
treatments scored against the findings that suggest them. A practitioner of
Ayurveda, of TCM, or anyone else who reasons from a framework fills the same
table in; so does a physician writing out a ranked differential. §13's "what
underlying pattern does this case belong to, as opposed to what disease is it"
is likewise a slot, not a specialty. The paper form supplies the neutral word
itself — it reads "Miasmatic / **constitutional** assessment".

**And the repo has already been burned by the opposite move.**
`PrescriptionItem.attributes` is vestigial (ADR 0017) because a generic escape
hatch was built for specialty data and never used; three specialty-shaped values
bypassed it and became plainly-named columns. The rule that replaced it is the
one being followed here: *if a value is read back by a human under a name of its
own, it is a column* — and the name is what it measures.

The honest cost, stated: a developer reading `CaseAnalysisEntry.grade` cannot
tell from the code what it is for. That is what this ADR is. And the doctor's
own words disappear from the codebase and live only in
`DEFAULT_TERMINOLOGY` and in one clinic's overrides — which is the same trade
already taken for `strength`, `pack_size`, `preparation` and `role_owner`.

**This is not a hedge, and the capability flag below is not the other half of a
compromise.** The two mechanisms answer different questions and `strength`
already carries both: `terminology` decides *what a thing is called*,
`*_enabled` decides *whether the clinic records it at all* (A3). Picking generic
naming settles the first question outright. The second is answered separately in
§4 below.

**Enforcement.** A hardcoded "Repertorization", "Rubric", "Miasm" or "Remedy" in
a template or a form is a bug, exactly like `get_role_display` (ADR 0013) and a
hardcoded "Potency" (ADR 0015). A grep test over `templates/` and `*/forms.py`
makes that mechanical rather than aspirational — the same shape as
`core/tests/test_date_inputs.py`.

New keys in `DEFAULT_TERMINOLOGY`, with this clinic's override beside the
default:

| key | default | Global Homeopathy Clinic |
|---|---|---|
| `case_record` / `case_record_plural` | Case record / Case records | Case study |
| `complaint` / `complaint_plural` | Complaint / Complaints | — |
| `modality` / `modality_plural` | Modality / Modalities | — |
| `investigation` / `investigation_plural` | Investigation / Investigations | — |
| `case_analysis` | Case analysis | Repertorization |
| `finding` | Finding | Rubric |
| `grade` | Grade | Grade |
| `candidate` | Candidate | Remedy |
| `constitutional_assessment` | Constitutional assessment | Miasmatic assessment |

Every one of these **must** be in `DEFAULT_TERMINOLOGY`: `Organization.terms`
drops overrides for unknown keys, so a missing default means the clinic's word
is silently ignored. That is the `role_developer` lesson from ADR 0019 and it
applies nine times here.

**The Features screen gets the switch and no label boxes.** ADR 0015 put a
label box beside the switch because there was no terminology screen; ADR 0017
made that three. Doing it nine more times is untenable and would make the
Features screen unreadable, which is the thing the standing rules forbid. The
line drawn here: **Features carries switches, and labels belong to SPEC §6.8's
terminology screen.** Until that lands, the nine overrides are seeded for this
clinic and edited in the Django admin, which is what the DEVELOPER role (ADR
0019) exists for.

### 2. Shape: one parent with prose columns, four child tables

```
patients.CaseRecord            OneToOne → Patient        §3–§8, §10, §13
  ├─ CaseComplaint             FK, growable              §2
  ├─ CaseModality              FK, fixed 8 rows          §9
  ├─ CaseInvestigation         FK, growable              §12
  └─ CaseAnalysisEntry         FK, growable              §14
```

All five are `OrgOwnedModel`, all five carry `HistoricalRecords`, and they live
in **`patients`** — not a new app. The record hangs off `Patient`, it is
per-patient rather than per-visit, and `PatientClinicalProfile` already sits
there behind the same access boundary. A `casenotes` app would be a new app for
one screen.

**The prose boxes are columns — about seventy of them.** Not a JSON blob (ADR
0017 closed that), and not one textarea per section. The prompts *are* the
product: the reason a case-taking form exists on paper is that it asks the
fifteen questions of §7 one at a time, and a doctor scanning the record at the
next visit reads them back one at a time. Collapsing §5 into a single "Family
history" box loses the checklist and produces a wall of text nobody rereads.

Field names are prefixed by section so seventy names stay groupable and the form
and template can render by looping over declared fieldsets rather than naming
fields: `hpc_progression`, `past_operations`, `family_father`,
`habits_alcohol`, `mental_anxiety`, `generals_thirst`, `systems_respiratory`,
`assessment_differential`.

Three of the ~72 are not prose: `hpc_first_noticed_on` is a `DateField` (drawn
by `core.forms.date_widget`, never `type="date"` — ADR 0016), `hpc_onset_type`
is a two-value choice (Sudden / Gradual), and `generals_thermal_state` is
discussed below.

**What this costs, stated plainly:**

- `patients/models.py` grows by ~70 field declarations and the migration is
  unreviewable line by line. The review is "does the field list match the
  reference document", which is a test rather than a reading (test 5).
- Five history tables. One save of a fully-edited record writes up to five
  history rows.
- Adding a section later is a migration, not a configuration change. Accepted:
  this is a stable paper artifact, not a per-clinic questionnaire. SPEC §5's
  `FieldDefinition` is the thing that would make it configurable and it is
  unbuilt and out of scope.
- Seventy mostly-empty columns per row. Storage is nothing; the honest cost is
  that a partly-filled record looks empty on screen, which the section layout
  has to handle.

**Rejected: one model per section** (eight one-to-one children for the prose).
It buys nothing — §5 is never loaded without §6 — and costs eight more
`organization` FKs, eight more history tables and a join chain on every read.

**Rejected: twenty-four columns for §9** instead of a child table. Defensible —
the grid is fixed at eight rows that never vary, so a formset is machinery for
variability that does not exist. Rejected anyway because it makes §9 the one
section that renders and saves differently from the other three tables, and
because a `factor` column leaves room for a ninth factor without a migration.
The fixed shape is expressed as `extra=0`, `can_delete=False`, eight rows seeded
with the record, and a `unique_together (case_record, factor)`.

**`generals_thermal_state` is a plain `TextField`, and this is the one place
drift is knowingly accepted.** The reference doc flags it as the only closed
list on the form (`Hot / Chilly / Variable`). A `TextChoices` of those three
values is a specialty list in code, which decision 1 just refused. The ADR 0017
machinery — an `Organization.<key>_options` column plus `closed_choices` — is
the correct answer and is over-built for one field out of seventy. So it is free
text, `chilly` and `Chilly` will both appear, and if that ever needs to be
queried the fix is `closed_choices` over an options column, which is sitting
there ready.

**§12's "Attachment / Report No." is free text**, not a file. Patient-level
attachments are SPEC §5's unbuilt `Attachment` model and are out of scope;
`EncounterPhoto` is per-visit by design (ADR 0014). The column records the
report number and where the paper is filed.

**§14's candidate is free text, not an FK to `catalog.Product`.** Tempting —
the clinic's 333 remedies are in the catalog — but the analysis is a scratchpad
that names candidates the clinic does not stock, and an FK would `PROTECT` a
product because somebody once considered it.

### 3. `PatientClinicalProfile` is absorbed and removed

**Picked: the case record absorbs it.** §4 asks for allergies and a past
medical history; `PatientClinicalProfile` holds `medical_history` and
`allergies`. Keeping both gives the clinic two places to record an allergy, and
the failure mode is the dangerous one — the blank allergy box on the screen the
doctor happens to be looking at.

`PatientClinicalProfile` is a two-field model with one screen, one form and one
template, behind exactly the same access boundary and with exactly the same
one-per-patient shape. The case record is a strict superset of its purpose.

The migration, and its one honest limitation:

- `allergies` → `past_allergies` (§4's "Allergies / sensitivities"). An exact
  match.
- `medical_history` → `past_other_history` (§4's "Other relevant history"),
  **verbatim**. This is the section's catch-all box, and the value is moved
  whole rather than distributed across §4's eight prompts. Nothing distinguishes
  "had measles as a child" from "appendix out in 2019" mechanically, and a guess
  would corrupt a clinical record — the same rule that stopped ADR 0015
  splitting `dosage` values apart. The doctor may want to redistribute it by
  hand; on this dataset that is minutes of work, because `import_patients` does
  not write clinical profiles and the only rows with content are what has been
  typed since the 2026-08-23 deployment.
- The data migration is **reversible** — the reverse copies back — and the
  release takes a backup first, per the runbook.
- `ClinicalProfileForm`, `clinical_profile_edit`,
  `templates/patients/clinical_profile_form.html`, the `patients:clinical_profile`
  URL and its entry in `core/tests/test_url_smoke.py::_argument_sources` all go.
  The smoke walk's floor count moves.

### 4. One capability switch, gating the offer and nothing else

`Organization.case_record_enabled`, **default off**, on the Features screen —
same posture as `strength_enabled`, and for the same reason: a general practice
that sees a patient for fifteen minutes does not take a seventy-box
constitutional history, and a switch the owner cannot reach is not a product
feature (ADR 0015).

**The switch gates creation and the offer. It never gates reading or editing
what exists.** With it off:

- a patient with no case record shows no "Start case record" button, and the
  create view refuses;
- a patient who *has* one still shows the card, still opens it, and can still
  have a typo fixed in it.

That is A3's rule — read surfaces gate on the data, not on the switch — and
turning the switch off must not hide a clinical record any more than it may
erase one. The inverse failure is ADR 0015's: a field left on a form and merely
hidden gets rebuilt as empty by `construct_instance` on the next save. Neither
happens here because nothing is popped; only the offer is conditional.

### 5. One record per patient, revised, not one per episode

**Picked: `OneToOneField(Patient)`.** The paper is one sheet per patient and the
doctor's model is "the case", singular. A constitutional case is taken once and
revised, and `simple_history` already answers the only question an episode split
would buy: what did this say in 2027.

The four-years-later, unrelated-complaint case resolves without new structure.
The new complaint is a **visit** (`Encounter.chief_complaint`) and, if it
belongs in the case, a **new row in §2** with its own onset date. §5 family
history, §7 mental generals and §8 physical generals do not change because the
presenting complaint changed — that is the premise of a constitutional case.
§13 is amended and the amendment is in the history.

Episodes would need a lifecycle, a title, a rule for which one is current, a
rule for which one a visit reads, and a picker on the profile — five decisions
in service of a case nobody has hit. **What would reopen it:** a clinic running
genuinely episodic care (a physiotherapist with a shoulder in 2026 and a knee in
2030). The change then is additive and small: `CaseRecord` gains a nullable
`episode_label` and `taken_at` becomes the ordering key, and the `OneToOne`
becomes an `FK`. Recorded here so it is a migration and not a redesign.

### 6. §6 and §8 ask once, in §8

The paper asks for appetite, thirst and food aversions in both sections, and for
sleep in both. **The digital form asks once.**

A second identical box is a box that gets left blank, and a blank box cannot be
told apart from "asked, nothing to report" — the same ambiguity the day list
already refuses when it prints "No bill" rather than nothing. On paper the
duplication is harmless because the doctor's pen skips it; in a form it is two
fields, one of which is permanently empty and permanently ambiguous.

Where each lands, on the rule *the section where the answer is used*:

- **appetite, thirst, cravings, aversions, food intolerances → §8**, the
  physical generals. These are the differentiating data; §6's framing of them is
  incidental.
- **§6 keeps** diet, water intake, sleep, dreams, exercise, tobacco, alcohol,
  caffeine, bowel habit, urination — habits, a different question class.
- **Sleep is one box, in §6**, labelled to carry §8's prompt ("hours, position,
  quality"). §8's "Sleep position / quality" is dropped.
- §6's "Diet / appetite" becomes **"Diet"**; the appetite half is in §8.

**What would reverse this:** the doctor saying he asks twice on purpose and gets
different answers in the two framings. The answer then is two *differently
named* boxes with distinct labels, not the restoration of a duplicate — and it
is a question worth putting to him before the form is built.

### 7. §11 is `Encounter` columns; the prose stays in `examination`

Seven measurements become columns on `Encounter`:

| column | type |
|---|---|
| `pulse_bpm` | `PositiveSmallIntegerField`, null |
| `bp_systolic`, `bp_diastolic` | two `PositiveSmallIntegerField`s, null |
| `temperature_c` | `DecimalField(4, 1)`, null |
| `respiratory_rate` | `PositiveSmallIntegerField`, null |
| `spo2_percent` | `PositiveSmallIntegerField`, null, 0–100 |
| `height_cm`, `weight_kg` | `DecimalField(5, 1)`, null |

- **Blood pressure is two columns, not "120/80".** It is two numbers, a range
  check on each is meaningful, and the point of recording it over years is to
  plot it. Two facts in one column is the `dosage`/`strength` bug (ADR 0015).
- **Temperature is stored in Celsius only**, and the field is labelled °C. The
  paper offers °F or °C; a dual-unit input is a silent conversion bug, and a
  clinician who types `98.6` into a Celsius box has just recorded a corpse. A
  `clean` that refuses anything outside 30–45 °C catches exactly that mistake
  and nothing legitimate.
- **BMI is never a column.** `Encounter.bmi` is a property returning `None` when
  either input is missing — the same call as the derived invoice balance (ADR
  0008), batch on-hand (ADR 0009) and appointment status (ADR 0010).
- Height rarely changes, so `height_cm` **prefills** from the most recent
  encounter that recorded one. A prefill, never a lookup: BMI reads this row's
  two values, so a stale height cannot silently poison a later visit. Same
  posture as `prescribed_product_lines` being "a convenience copy, never a
  link".

**§11's prose does not become six more `Encounter` columns.** General
appearance, pallor/cyanosis/icterus/edema, lymph nodes, other findings and the
local/systemic findings box are exactly what `Encounter.examination` already is.
The visit form is the speed-critical screen (SPEC §6.4, "the whole consultation
is one page"); the case record is the slow, thorough one. Numbers go on the
visit because they are fast to type and worth charting; prose goes in the box
that already exists.

**No capability switch for vitals.** Unlike advice and strength, every specialty
this product targets takes a pulse, and a compact row of seven narrow number
inputs is one line rather than a page. An eleventh switch on the Features screen
for something universal is the "screen that needs explaining" the standing rules
forbid.

**The case record's §11 panel is a read.** It shows the most recent encounter
carrying any measurement, with its date and a link to it, plus a "Record today's"
link to the visit form. With none, it says "No measurements recorded" — never
blank, which reads as zero (the day list's "No bill" rule).

### 8. Entry point and layout

**Where.** The existing "Clinical profile" card on
`templates/patients/detail.html` **becomes** the case record card — first card
in the right-hand column, above Bills and Visits, inside the existing
`{% if show_clinical %}` block. It is deliberately **not** a fourth button in
`{% block page_actions %}`: that row already carries Edit / Next appointment /
New visit, and the 2026-08-14 responsive pass is a fresh record of what a
crowded action row does to a 375px screen.

**What it says.**

- No record, switch on: a single `btn-brand` reading **"Start case record"** —
  through `terms.case_record`, so this clinic reads "Start case study". "Start"
  rather than "Add", because it is a twenty-minute document and the word should
  say so.
- Record exists: the card shows the top chief complaints from §2, `Taken
  <date>` and `Last updated <date>`, with **"Open case record"**. The summary
  earns its place the way the Bills card's outstanding total does — the glance
  answers a question.
- No record, switch off: nothing. Not a disabled button.

**One page, one form, one Save.** Not a wizard.

- One POST is one transaction and one history entry. A wizard writes sixteen
  partial saves and sixteen history rows, and "at which step was this record
  valid" becomes a question the model has to answer.
- It is the call this repo already made for the screen this most resembles:
  "the whole consultation is one page — notes, prescription instructions, and
  item rows — submitted once" (`clinical/forms.py`).
- A wizard needs a progress indicator, a back button, a resume rule and an
  unsaved-changes guard. Four things to explain, for three users.

Length is handled by **sixteen anchored section cards, all open**, a sticky
section jump-list from `lg` up, and a **Save button that is reachable at every
scroll position**. Sections are not collapsed by default: the prompts are the
form's whole value, and a closed section is an unasked question.

**The record is created on the first Save**, with whatever is filled in, and the
page redisplays with everything intact — so "save early, save often" is a real
strategy rather than advice. **Autosave is declined for now**: it is new
machinery, a conflict story, and a half-saved clinical record. If the doctor
reports losing work, the answer is HTMX per-section autosave and it is an
increment of its own.

**One layout trap to check in a browser, not in a test:** a sticky save bar sits
in the same space as the fixed 64px bottom nav that `templates/base.html`'s
`pb-24 sm:pb-24 lg:pb-6` exists to clear. `core/tests/test_layout.py` is a
canary, not a proof.

Four formsets and a parent form post together — §2, §12 and §14 growable with
the existing HTMX add-row idiom, §9 fixed at eight.

### 9. New `Patient` columns — demographics, so STAFF edits them

Seven columns, all optional, all on `PatientForm`, **none behind the clinical
gate**. A receptionist takes every one of these at the desk; none is clinical
narrative, so none belongs in the split that SPEC §6.1 draws.

| column | type | note |
|---|---|---|
| `marital_status` | `CharField(choices=MaritalStatus)` | mirrors `Sex` exactly, including an `UNKNOWN` = "Not recorded" default |
| `occupation` | `CharField(100)` | |
| `email` | `EmailField`, blank | |
| `alt_phone` | `CharField(32)`, indexed | |
| `emergency_contact_name` | `CharField(200)` | |
| `emergency_contact_phone` | `CharField(32)` | |
| `referred_by` | `CharField(200)` | free text, not an FK |

- **Emergency contact is two columns, not one box.** The entire point of an
  emergency contact is dialling it quickly, and a combined free-text box cannot
  produce a `tel:` href. It gets an `emergency_dial` property beside the
  existing `Patient.dial`.
- **`email` is not a channel.** Nothing sends mail from this box — there is no
  SMTP on it and ADR 0013 settled that there never will be a recovery path that
  assumes one. It is a contact detail, recorded and displayed. Building on it
  later is a decision, not an increment.
- **`alt_phone` is included**, and it brings two obligations with it.
  `services.search_patients` searches `phone`; a second number that the search
  cannot find is worse than no second number, because the receptionist concludes
  the patient is not registered and creates a duplicate. So `alt_phone` joins
  the phone search **and** `services.possible_duplicates`. That is the real cost
  of the field and it is why it is worth stating rather than adding a column
  quietly.

**`PatientForm` goes from six fields to thirteen, and the walk-in modal renders
that same form** (deliberately — one form definition, nothing to drift). Five
of the seven go behind a `<details>` disclosure labelled "More details":
occupation, email, alt phone, emergency contact, referred by, and marital
status. The desk set stays visible: name, phone, sex, date of birth, address,
branch.

This is ADR 0017's disclosure, reused exactly, including the rule that makes it
safe: **the fields stay on the form and in the DOM**. A closed `<details>` still
posts what is inside it. Omitting them with a template conditional, or popping
them in `__init__`, would let `construct_instance` rebuild them as empty and
erase an occupation recorded last month.

### 10. Access control, tenancy and history

- `@clinical_access_required` on **every** view — the record page, the edit
  page, and each HTMX add-row fragment. Fragments are URLs; ADR 0012 states the
  obligation and it is easiest to forget exactly here.
- The decorator reads `CLINICAL_ROLES` (ADR 0019), so PRACTITIONER, OWNER **and
  DEVELOPER** pass and STAFF gets a 403 by direct URL. No inlined role pair.
- Lookups go through `Patient.objects` / `CaseRecord.objects`, which are
  org-scoped, so another clinic's record is a 404 rather than a permission
  message.
- `HistoricalRecords(excluded_fields=['created_at', 'updated_at'],
  related_name='history_rows')` on all five models. **Historical models are not
  org-scoped** — `simple_history` generates its own manager — so every query
  against `.history` filters `organization` explicitly, and there is a test per
  model in the shape of `clinical/tests/test_history_isolation.py` (ADR 0006).
- **No lock state and no required change reason.** `Encounter` demands a reason
  because it is a finished document that gets corrected; a case record is a
  living one that is added to at every visit for years, and a "why are you
  editing this?" prompt on that screen is friction in the wrong place. Every
  save still writes a history row with who and when, which is what stops a
  silent overwrite. A History link shows the diff, like the encounter's.


## Amended: temperature is stored in Fahrenheit, and the unit is presentation

**2026-08-29. This supersedes the "Temperature is stored in Celsius only"
paragraph in §7 above**, which is left in place so the reasoning that was
rejected can still be read.

The original decision refused a dual-unit input on the ground that it is a
silent conversion bug, and it was right about the danger and wrong about the
remedy. Refusing the clinic's own unit does not remove the conversion; it moves
it into the clinician's head, which is where a wrong conversion becomes
invisible. What is settled instead:

- **One canonical column, always Fahrenheit.** `core/temperature.py` converts on
  the way in and on the way out. Fahrenheit rather than Celsius because it is
  the unit this clinic works in, and the canonical unit is the one whose stored
  values are exactly what somebody typed.
- **`Organization.temperature_unit` (`F` or `C`, default `F`) controls the label,
  what the form accepts, and what is rendered back — never what a stored number
  means.** A unit flag that reinterprets stored values would make flipping the
  setting silently rewrite every reading ever recorded: a 38 taken in Celsius
  becomes a 38 read as Fahrenheit, with nothing to tell the two apart afterwards
  and no migration able to guess. It is the same family as the rule that keeps
  an invoice balance derived rather than stored (ADR 0008) and stock on hand
  summed rather than counted (ADR 0009).
- **The range check validates against the unit that was entered** — 90–110 °F,
  32–43 °C — and converts afterwards. Checking a converted value would report a
  bound in a unit nobody typed. Both halves of the original mistake are still
  caught: 98.6 in a Celsius box and 37 in a Fahrenheit box are each refused.
- **One decimal place, and Fahrenheit is the finer grid.** 0.1 °F is smaller
  than 0.1 °C, so every distinct Celsius reading lands on a distinct stored
  value and returns as itself. A Celsius canonical column would have rounded a
  Fahrenheit clinic's own numbers under it.
- **The setting is on the Features screen**, owner-settable like every other
  capability, with help text saying in as many words that changing it converts
  what you see rather than altering what is recorded. An operator who believes
  it rewrites history will never touch it.

**Built ahead of the vitals it exists for**, deliberately: one migration rather
than two, and the case record's §8 thermal-state prompt reads the same unit
label. `Encounter` has no temperature column yet.

**Also settled by production, not by argument:** `PatientClinicalProfile` had
zero rows with content on the deployed database, so §3's absorption is a schema
move and the data migration carries no risk. It is still written to copy both
fields, and still reversible, because the next clinic to run these migrations
will not have an empty table.

## Delivery

Four increments, in order. The first two ship value on their own and de-risk the
third.

1. **`Patient` columns** — seven columns, the `More details` disclosure,
   `alt_phone` in the phone search and the dedupe guard. No new models.
2. **`Encounter` vitals** — seven columns, the `bmi` property, the height
   prefill, the compact row on the visit form, the temperature range check.
3. **The case record** — five models, the absorption migration, the capability
   switch, nine terminology keys, the views, the one-page form, the patient-page
   card, `bootstrap_demo` seeding one so the URL smoke walk exercises it.
4. **The print view** — see below, and only on the doctor's word.

## The print view: wanted, and not now

**My read is that he will want it**, and the reasons are specific rather than
sentimental. He has kept a physical file for years, and unlike the prescription
and the receipt — which are *handouts* — the case record's paper ancestor is a
*filed artifact*, which is the kind of document people keep printing long after
the software works. The clinic also runs on one box in a place where power cuts
are routine, and the runbook already reasons about the day the stack is down; a
printed case is the only thing readable that morning.

The marginal cost is small because the system exists: A5/A4 print sizes, the
letterhead, and the print CSS are all shared with the prescription.

**But not in this increment.** It is a read-only template over a model that does
not exist yet, and the real work is a layout question — sixteen sections across
how many A4 pages, and which of seventy boxes are suppressed when empty. That
last part has a precedent and a warning: ADR 0017's rule that optional print
columns gate on the data means a printed case record would not be
section-for-section identical between two patients, which is fine but should be
decided deliberately.

**Recommendation: ask him after two weeks of real use**, and build it as its own
increment against what he actually reaches for.

## Test list

Not the tests — the list, for sign-off.

**Access and tenancy**

1. STAFF gets 403 on every case-record URL, parameterised over the URL list, so
   a new view that forgets the decorator fails rather than passing quietly.
2. PRACTITIONER, OWNER and DEVELOPER all get 200 — reading `CLINICAL_ROLES`, not
   a hardcoded pair (ADR 0019).
3. Another organization's patient's case record is a 404, not a 403.
4. Cross-tenant leak on `HistoricalCaseRecord` and each of the four child
   history models, in the shape of `test_history_isolation.py`.

**Shape**

5. Every prompt in `docs/reference/case-taking-form.md` §2–§10 and §12–§14 maps
   to a field on one of the five models, or appears in an explicit
   `DELIBERATELY_DROPPED` list with the §6/§8 dedupe reasons. **This is the test
   that makes a seventy-column migration reviewable**, and it fails when the
   reference doc and the models drift apart in either direction.
6. `CaseRecord` is one per patient — a second create raises.
7. BMI is not a column on `Encounter` (introspect `_meta`), and
   `Encounter.bmi` is `None` when either input is missing.
8. §9 renders exactly eight rows in the declared order, with no add control and
   no delete control.

**Capability switch**

9. Switch off, no record: no button on the patient page, and the create view
   refuses.
10. Switch off, record exists: the card renders, the record opens, and an edit
    saves. The switch never hides or erases recorded data (A3).

**Terminology**

11. All nine new keys resolve through `Organization.terms`, and an override for
    each survives — a key missing from `DEFAULT_TERMINOLOGY` is silently dropped
    (the `role_developer` lesson).
12. No template or form hardcodes "Repertorization", "Rubric", "Miasm",
    "Remedy" or "Potency" — a grep test in the shape of
    `core/tests/test_date_inputs.py`. This is what makes decision 1 real.

**Absorbing `PatientClinicalProfile`**

13. The field mapping used by the data migration covers both source fields and
    names only fields that exist on `CaseRecord`, asserted against the mapping
    table itself. *Caveat for sign-off:* testing the migration end to end wants
    `django-test-migrations`, which is a new dependency and is not being asked
    for; the mapping test plus a manual verification step in the release notes
    is the proposal.
14. `patients:clinical_profile` is gone from the URLconf. The smoke walk's own
    `test_every_parameterised_url_declares_its_arguments` covers the other half
    automatically.

**Form behaviour**

15. The parent form and all four formsets save in one POST.
16. A validation error in one formset redisplays the other three with typed
    values intact — "a refusal never costs the note".
17. A saved record round-trips: every value renders back into the form it came
    from.
18. The seven new `Patient` fields are on `PatientForm`, a STAFF user can save
    them, and none of them appears on a clinical-gated form.
19. The quick-create modal saves values typed into the closed `<details>` — a
    closed disclosure still posts (ADR 0017).
20. `alt_phone` is found by `search_patients` and considered by
    `possible_duplicates`.

**Vitals**

21. Vitals round-trip through the visit form.
22. `temperature_c` outside 30–45 is refused with a field error (the
    °F-typed-into-°C guard).
23. The case record's §11 panel shows the most recent encounter carrying any
    measurement with its date, and says "No measurements recorded" rather than
    rendering blank.

**History**

24. Editing a case record writes a history row on the parent and on each child
    that changed, with the actor.

**Standing suites that cover this for free**

25. `core/tests/test_url_smoke.py` — new URLs added to `_argument_sources`; the
    floor in `test_the_walk_actually_reaches_most_of_the_application` moves.
26. `core/tests/test_org_scoping.py` picks up five new `OrgOwnedModel`
    subclasses automatically.
27. `core/tests/test_template_comments.py` and `test_date_inputs.py` cover the
    new templates automatically — §12's date must use `date_widget()`.

## Consequences

- `patients` becomes the largest app in the repo by column count, and
  `patients/models.py` is read by section comment rather than by scrolling.
- Five new history tables. A `pg_dump` grows accordingly; the backup and restore
  story does not change.
- `bootstrap_demo` seeds one case record so the URL smoke walk's populated
  organization exercises the new pages. Teardown order matters again: `Patient`
  CASCADEs the record and its children, so the queryset delete is safe here —
  unlike ADR 0014's photos, nothing on disk is orphaned.
- The Features screen grows one switch and no label boxes, and this ADR is where
  the line between the Features screen and SPEC §6.8's terminology screen is
  drawn.
- SPEC §5's `Patient` entry gains seven demographic columns and its "structured
  intake fields" clause is finally satisfied by something other than two
  textareas.
- `PatientClinicalProfile` ceases to exist. Anyone with the
  `/patients/<pk>/clinical/` URL in their notes needs to know.
