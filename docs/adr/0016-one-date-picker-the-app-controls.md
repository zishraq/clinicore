# 0016 — One date picker the application controls

**Status:** accepted (2026-08-19)

## Context

Every date field in the app was a native `<input type="date">`. A native date
input renders its visible text in the **operating system's** locale, not the
page's. The same field reads `03/05/2026` on a phone configured for Bangladesh
and `05/03/2026` on a laptop that shipped from the US, and the document cannot
influence this: `lang`, an attribute, a form setting and CSS all have no effect
on it, by design and per spec. There is no fix available to the page.

That is tolerable for a filter. It is not tolerable for a date of birth, an
expiry date on a box of medicine, or a follow-up the patient is told out loud —
all of which are read off the screen and repeated to someone.

Seven fields were affected:

| Where | Field | What the server parses with |
|---|---|---|
| `patients/forms.py` | `date_of_birth` | Django `DateField` |
| `clinical/forms.py` | `follow_up_date` | Django `DateField` |
| `inventory/forms.py` | `expiry_date` | Django `DateField` |
| `templates/scheduling/day.html` | `date` | `strptime('%Y-%m-%d')` |
| `templates/scheduling/_appointment_form.html` | `appointment_date` | `strptime('%Y-%m-%d')` |
| `templates/billing/invoice_list.html` | `from`, `to` | `parse_date()` |

## Decision

Replace all seven with **flatpickr 4.6.13**, loaded from jsDelivr beside
daisyUI, Tailwind, HTMX and Alpine, and initialised by one shared file,
`static/js/date-picker.js`, off a `data-datepicker` attribute.

### The posted value does not move

This is the constraint the whole change is built around. Four of the seven
consumers are ISO-only and have no fallback: `parse_date` returns `None` for
anything else, and `strptime('%Y-%m-%d')` raises. The three Django `DateField`s
survive on `DATE_INPUT_FORMATS[0]` for `en-us` being `%Y-%m-%d`.

flatpickr's `altInput` is exactly this separation and is why it was chosen over
the alternatives. The element the template declares stays the real form field —
same `name`, same value, still `Y-m-d`. flatpickr hides it and renders a text
box in front of it showing `altFormat: 'd/m/Y'`. No hidden field is hand-wired
and no synchronisation code exists to drift.

`core.forms.date_widget` also pins `format='%Y-%m-%d'` explicitly on the three
Django widgets rather than relying on the locale to keep producing it. The
rendered value is now stated, not inherited.

### `disableMobile: true` is not optional

flatpickr's default behaviour on a mobile browser is to *step aside and use the
native control*. Left at the default, the change would have been a no-op on
precisely the devices that motivated it, and would have tested clean on a
desktop. This one line is the difference between fixing the bug and appearing
to.

### `static: true` for the instances inside a `<dialog>`

`date_of_birth` (the add-patient modal) and `appointment_date` (the walk-in
modal) render inside a `<dialog>` opened with `showModal()`, which puts it in
the browser's **top layer**. A calendar appended to `document.body` — flatpickr's
default — then paints *underneath* the modal and the field looks dead. `static`
renders the calendar inside the input's own wrapper instead.

It is decided per instance at runtime, `input.closest('dialog') !== null`, not
per call site: `date_of_birth` renders both on its own page and inside the
modal, from the same widget.

### Enter has to be handed back to the form

flatpickr consumes Enter to commit whatever was typed, and calls
`preventDefault` doing it — which also cancels the browser's *implicit form
submission*. A native date input submitted on Enter; without a fix, typing a
range into the bill filters and pressing Enter silently does nothing. Found in
a browser, and invisible to every kind of test this repo has.

Two details, both learned the hard way:

- The listener is bound on the **capture** phase. flatpickr stops the key from
  propagating further, so a normally-registered listener never runs at all —
  the first attempt looked correct and did nothing.
- The submit is **deferred a tick**. flatpickr's own handler runs in between and
  is what parses the typed text into the real field; submitting inline posts the
  value the box held *before* this keypress.

It is skipped when the field declares its own `onchange` — the day list submits
from there, and both would fire on one keypress.

### The id moves to the visible box

All seven fields have a `<label for>`. flatpickr leaves that id on the input it
hides, so every label would point at something unclickable. The shared file
moves it to the alt input, and copies the daisyUI classes across with
`altInputClass` — flatpickr otherwise stamps the visible box with its own
defaults and drops the styling the template chose.

### Loaded globally, not from `{% block scripts %}`

Unlike `item-autocomplete.js`, this is in `base.html`. Two of the fields live in
modals included from several unrelated pages, and a per-page include is the
coupling that already shipped the walk-in modal without its add-patient dialog
past a green suite. A date field that silently keeps the OS format because
someone forgot a script tag is the same failure with no error attached.

## Alternatives rejected

- **Duet Date Picker.** ISO-native by design and self-registering, so
  HTMX-swapped content would need no init at all. Rejected on integration cost:
  ESM-only from the CDN, shadow-DOM form participation, and styling through CSS
  custom properties rather than the daisyUI classes every other control here
  uses.
- **vanillajs-datepicker.** Actively maintained, but it writes the display text
  straight into the input and has no alt-field concept, so keeping the ISO
  contract meant a hand-wired hidden field and a `changeDate` listener per
  instance — the custom code the library was supposed to remove.
- **Changing the server to accept `d/m/Y`.** Rejected outright. It widens the
  parsing surface of every view to fix a rendering problem, and `05/03/2026`
  remains genuinely ambiguous to a parser that accepts both orders.

## Consequences

- flatpickr is a fourth CDN dependency, and is in maintenance (last release
  2022). Accepted: it is dependency-free, stable, and the `altInput` contract is
  the feature being bought.
- The picker is what the user gets — there is no native fallback if the CDN is
  unreachable. The field degrades to a plain text box that still accepts a typed
  `Y-m-d`, which is what the server wants anyway.
- Any **new** date field must use `core.forms.date_widget` or carry
  `data-datepicker`. `core/tests/test_date_inputs.py` fails on a reintroduced
  `type="date"`, so the regression is caught rather than noticed.
