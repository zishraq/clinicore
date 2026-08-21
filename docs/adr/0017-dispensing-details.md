# 0017 — What is dispensed is two more columns, and the rest of the row collapses

Status: accepted, 2026-08-21. Amended 2026-08-22 — the two closed fields
became `<select>`s, and the native-popup finding is recorded below.

Extends `docs/adr/0015-prescribed-strength.md`, which settled the shape this
follows: a generically-named column, a label from the terminology map, an
org-editable suggestion list, a capability switch, the field dropped from the
form rather than hidden, and read surfaces gated on the data rather than on the
switch.

## Context

The clinic writes a prescription as four facts: the remedy, its potency, how
much of it goes home, and whether it goes home as globules or as a liquid. Two
of those exist — `name_snapshot` and `strength` (ADR 0015). The other two were
being typed into whatever box was nearest, which is exactly the failure ADR 0015
was written about.

They also asked for the other four fields on the row — dosage, frequency,
duration, instructions — to go away. They handle all four verbally.

## Decision

### `preparation`, not `Product.unit`, and not `form`

`Product.unit` ("Tablet, ml, drops") is the noun a **stock count** is measured
in. It is one value per catalog row, it is rendered on the stock screens and the
catalog list, and nothing has ever written it onto a prescription. The physical
preparation is a **per-prescription** decision: the same remedy goes out as
globules for one patient and as a liquid for the next, and this clinic's catalog
was loaded by `import_remedies` as 333 bare remedy names with no form at all.
Encoding the form in the catalog would fork that list into 666 rows, each
`PROTECT`ed by the prescriptions pointing at it, and turn "which Arsenicum" into
a picking problem in front of a patient.

It is also prescribing data by ADR 0015's own test: it is printed, handed over,
and read back at the next visit. So it is a column on `PrescriptionItem`.

Named `preparation` rather than `form` — `form` is the most shadowed name in a
Django view or template, and `form.form` in the row template is unreadable — and
rather than `dosage_form`, the correct pharmaceutical term, which sits next to
`dosage` on the same model and reads as a typo. `preparation` covers Globule,
Liquid, Tablet, Cream, Drops without naming a specialty (SPEC §1). This clinic
maps the `preparation` terminology key to "Type".

### `pack_size`, because `quantity` is taken and is a number

The values are `2D`, `1/2 ounce`, `1 ounce`, `2 ounce`, `4 ounce` — container
sizes, as
strings. `quantity` already means a `DecimalField` that arithmetic is done on,
on `InvoiceItem` and on `StockMovement`, and reusing the word for a string on a
third model would be a collision in the worst place: money and stock.

`dispense_amount` was the runner-up and was rejected for the same reason the
column exists at all — it sits one word from `MovementType.DISPENSE` and would
read to the next developer as a stock hook. `pack_size` describes what the value
is (a two-dram vial, a one-ounce bottle, a strip of ten) and connects to nothing.

Consequence worth knowing: this clinic labels `pack_size` "Quantity", so the
word "Quantity" appears on a prescription row meaning a string while "Qty" on a
bill line means a number. That is the terminology map doing its job, and the two
screens are far enough apart that it reads correctly on both.

### Neither field moves stock, and neither ever should

`DISPENSE` movements and the `prescription_item` FK stay unused. ADR 0009
settled that the invoice is the stock event, and nothing here changes it:

- `pack_size` is a string in a unit nothing else knows. Turning "1/2 ounce" into
  a `Decimal` against a product counted in "Tablet" needs a unit-conversion
  table that does not exist and that nobody has asked for.
- It would double-decrement. `billing.services.prescribed_product_lines` already
  copies prescribed products onto the bill (A5), and `post_sale_movements` runs
  from both `create_invoice` and `update_invoice`. A third path off the
  prescription would move the same box twice, and voiding the bill would return
  stock that was taken twice.
- `prescribed_product_lines` still sets `quantity=1` deliberately. Mapping
  "2 ounce" to `2` would invent a number in the wrong unit, which is worse than
  the one it replaces.

Both fields are prescribing-and-printing facts. If the clinic ever hands stock
out without billing it, that is the `DISPENSE` screen ADR 0009 reserved, with a
numeric quantity of its own.

### Two closed lists and one open one, which decides the control

The clinic confirmed that preparation and pack size are **closed**: globules or
liquid, and five container sizes. Nothing else is ever handed over. Strength is
**open**: an atypical potency genuinely does get typed, which is the whole
argument ADR 0015 made for a free-text box with a `<datalist>` rather than a
dropdown with an "Other…" escape.

So the control follows the list, not the field's shape:

| field | list | control |
|---|---|---|
| `strength` | open | text box + `<datalist>` of suggestions |
| `pack_size` | closed | `<select>` |
| `preparation` | closed | `<select>` |

`PrescribingField.closed_list` carries the distinction, so it is one flag next
to the field rather than a rule spread across the form and two templates. The
options still come from `Organization.<key>_options` in both cases — a list of
preparations in code would be the SPEC §1 violation the naming decision avoided.

Two consequences that are easy to get wrong:

- **The stored value is always an option, even when the clinic has dropped it.**
  A `<select>` that does not contain the current value renders with nothing
  selected, so the browser posts the *first* option — blank — and the next save
  of that visit erases a value that was correct when it was recorded. It is the
  same data loss ADR 0015 records for a popped field, arriving by a different
  route, and it is invisible until a clinic edits its list.
  `PrescriptionItemForm._closed_choices` appends the row's current value when it
  is missing, and `test_a_value_the_clinic_no_longer_offers_survives_a_resave`
  models a browser rather than asserting a hardcoded value — it reads back what
  the rendered page would submit.
- **The field stays a plain `CharField`.** Adding choice validation would turn
  that same situation into a refusal to save the row at all: a practitioner
  blocked from fixing a typo in a note by a settings change made months ago.
  Refusing to store is worse than storing a value the list has outgrown.

Blank stays selectable in both: recording neither is normal.

### Native popups: what `color-scheme` fixes, and what it cannot

Measured 2026-08-22, with the OS in dark mode, in **both Chrome and Firefox**,
on a page that renders light:

- A native **`<select>` popup follows the page's `color-scheme`** and renders
  light, correctly.
- A **`<datalist>` popup ignores it and renders dark.** It stays dark with
  `color-scheme: light` declared on `:root` *and* directly on the input, after a
  hard reload. It is browser chrome — drawn by the browser process in Chrome and
  the parent process in Firefox — and takes the browser/OS theme. **There is no
  CSS fix.**

`daisyUI`'s `[data-theme=light]` already declared `color-scheme: light`, so the
computed value on `:root` was `light` before any of this. `app.css` now declares
it too, as insurance against the CDN stylesheet failing to load and to pin
scrollbars — not as a fix for anything.

This cost real time to establish. **Do not re-investigate it.** The practical
consequence: moving the two closed fields to `<select>` removed two of the three
native popups from the prescription row, and the one on strength stays dark on a
dark OS for as long as it is a `<datalist>`. That is a known, accepted wart, not
an oversight.

### One capability per field, and the three are built by a loop

`strength_enabled`, `pack_size_enabled` and `preparation_enabled` are three
independent switches, all defaulting off. A combined switch would be a special
case in a taxonomy that has none, and "I turned on Type and a Quantity column
appeared" is a screen that needs explaining.

Three copies of ADR 0015's hand-written plumbing is what makes that expensive,
so `organizations.models.PRESCRIBING_FIELDS` — a tuple of `PrescribingField`,
each deriving `<key>_enabled`, `<key>_options` and the datalist id from one name
— now drives the settings form, the item form, the check constraints and
`save()`. The refactor deletes more code than it adds; a fourth optional field
is an entry in that tuple plus its two columns.

`STRENGTH_MAX_LENGTH` became `PRESCRIBING_MAX_LENGTH` (same value, so no column
changes width) because three fields share it.

### The other four collapse, and nothing about them is deleted

Dosage, frequency, duration and instructions move behind a `<details>`
disclosure, closed by default. The clinic that changes its mind must not have
lost either the columns or the data.

**The fields stay on the form and in the DOM.** Omitting them with a template
conditional, or popping them in `__init__`, would let `construct_instance`
rebuild them as empty on the next save and silently erase what an older visit
carries — the failure ADR 0015 records for `strength`, which is the one bug in
this codebase that destroys data rather than hiding it. Only a closed
disclosure hides them, and a closed `<details>` still posts everything inside it.

**The disclosure opens server-side.** `PrescriptionItemForm.has_details` is true
when any of the four already holds a value, read through `BoundField.value()` so
it covers a saved instance, a redisplay after a validation error, and an
HTMX-added row (which is empty, so it starts closed) with one expression. Doing
this in JavaScript would make "editing an older visit never hides what is on it"
depend on scripting, and this is the row where four bugs have shipped past green
tests.

The autocomplete forces it open too: a catalog entry whose `default_frequency`
lands in the collapsed half would otherwise arrive unseen, which is the one
thing the disclosure must not cause. `prefill()` now reports whether it wrote.

### Every optional print column is gated on the data, including the old four

`_prescription_sections` emits one `show_*` flag per optional column, each true
only when *this* prescription carries a value. That already applied to
`strength`; it now covers all seven.

Seven optional columns do not fit across an A5 sheet, and four of them empty is
the worse document. Gating on the capability switch instead would be a
data-hiding bug — reprinting a two-year-old visit must reproduce what the
patient was handed, whatever the clinic records today.

**This changes the layout of historic printouts**, and that is a knowing
consequence rather than an oversight: a visit recorded with no dosages now
reprints without an empty Dosage column, where before it printed the column with
a dash in every cell. No content changes — nothing that was printed stops being
printed — but a reprint of an old visit is not column-for-column identical to
the sheet handed over at the time.

The advice table is deliberately untouched. It is four columns, it fits, and it
was not in scope.

### What `PrescriptionItem.attributes` is now

**It is vestigial, and this ADR is where that is written down.** Three
specialty-shaped values have now bypassed it. Nothing writes it except three
lines in `save()` copying `Product.default_attributes`, nothing writes *those*,
and no form, template, print view or query reads either. Adding a value to it
today is writing into a hole.

ADR 0015 argued the point once, for one field: the JSON fields "were built for
exactly this and never used", and they "stay… correctly scoped to values that
really are arbitrary". After three fields that scoping is close to empty, so the
rule is stated positively here instead:

> If a value is **printed on the prescription and read back by a human**, it is
> a column. The JSON is for values that are genuinely arbitrary per clinic and
> are never rendered under a name of their own.

Nothing currently meets the second half. The column is kept rather than dropped
because removing it is a destructive migration across `PrescriptionItem` *and*
its `simple_history` twin, because SPEC §5 still specifies it alongside the
`FieldDefinition` config that would drive it (unbuilt, and listed as a remaining
gap), and because keeping an empty column costs nothing while a wrong guess
about historic rows costs records. The next specialty field should be a column
too, unless it fails the test above.

### Pricing is untouched

The clinic also asked that prescription pricing be editable when the catalog
carries a price. It already is: `InvoiceItem.unit_price` is an editable box on
every bill line, prefilled from `Product.sale_price` and never overwritten once
typed, beside a per-line discount. No price is being added to the prescription
row — the prescription is a clinical document that gets printed and handed to
the patient, and a second place to record a price would force the bill to decide
which one wins. Whether a whole-bill discount is wanted is a separate question
and gets its own ADR.

## Consequences

- Existing rows have `pack_size = ''` and `preparation = ''`. As with
  `strength`, no migration tries to split those facts out of a `dosage` value
  that already holds them: nothing distinguishes them from doses that were
  always doses.
- A visit's row is four boxes wide by default at this clinic, and one box wide
  at a clinic running none of the capabilities — the search box takes whatever
  is left of the twelve columns.
- Advice carries none of the three. `save()` blanks them and three check
  constraints — generated from `PRESCRIBING_FIELDS`, so a fourth field cannot
  arrive without one — assert it for every write path.
- The Features screen is ten controls. Each capability is a switch, a label and
  a list, ruled off from the next; the shared settings template grew one
  conditional to draw the rule.
- `templates/partials/_strength_options.html` is replaced by
  `partials/_options_datalist.html`, which takes an id and a list of values. The
  product form is its second caller.
- `Organization.prescribing_datalists` yields only the **open** enabled fields,
  because a closed one carries its options inline. A clinic running only closed
  fields renders no datalist at all.
