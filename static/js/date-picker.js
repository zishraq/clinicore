/* One date picker for the whole application.
 *
 * A native <input type="date"> renders its text in the *operating system's*
 * locale, not the page's. The same field reads 03/05/2026 on the clinic's
 * phone and 05/03/2026 on a laptop that shipped from the US, and nothing in
 * the document can change it — not lang, not an attribute, not CSS. A date of
 * birth read off the screen is then ambiguous, which is the whole reason this
 * file exists.
 *
 * flatpickr's altInput is that split made explicit. The element the template
 * declares stays the real form field: it keeps its name, and it keeps posting
 * Y-m-d. flatpickr hides it and puts a text box in front showing d/m/Y. Every
 * consumer on the server still receives the ISO string it always did — see
 * docs/adr/0016-one-date-picker-the-app-controls.md.
 *
 * Markup contract:
 *   [data-datepicker]   any input that should get a calendar
 *
 * Nothing else is required, and nothing per-field is configured here. The file
 * is loaded from base.html rather than a per-page {% block scripts %} because
 * two of the fields render inside modals that are themselves included from
 * several unrelated pages — the coupling that already shipped the patient
 * picker without its dialog once.
 */

(function () {
  'use strict';

  var SELECTOR = '[data-datepicker]';

  function attach(input) {
    /* htmx:load fires for content that is already bound after a partial swap,
     * and flatpickr stamps this property on every element it owns. */
    if (input._flatpickr) {
      return;
    }

    var label = input.id;

    var picker = flatpickr(input, {
      /* What the server receives, unchanged. Every consumer is ISO-only:
       * Django's DATE_INPUT_FORMATS, billing's parse_date, scheduling's
       * strptime('%Y-%m-%d'). */
      dateFormat: 'Y-m-d',

      /* What a human reads. Fixed, never derived from the device. */
      altInput: true,
      altFormat: 'd/m/Y',

      /* flatpickr would otherwise stamp the visible box with its own default
       * classes and drop the daisyUI ones the template chose. */
      altInputClass: input.className,

      /* The field stays typeable. Reception enters a date of birth from a
       * form far faster than they can arrow back through sixty years. */
      allowInput: true,

      /* Without this flatpickr hands mobile browsers back to the native
       * control, which is the entire problem this file exists to solve. */
      disableMobile: true,

      /* A <dialog> opened with showModal() sits in the browser's top layer, so
       * a calendar appended to document.body renders *underneath* it and the
       * field looks broken. `static` puts the calendar in the input's own
       * wrapper instead. See .flatpickr-wrapper in static/css/app.css. */
      static: input.closest('dialog') !== null
    });

    /* Every one of these fields has a <label for>, and that id now belongs to
     * an input the user cannot see or click. Move it to the visible box. */
    if (label) {
      input.removeAttribute('id');
      picker.altInput.id = label;
    }

    /* Says which order the two numbers go in, on a screen whose whole purpose
     * is that the device no longer gets to decide. */
    picker.altInput.placeholder = input.placeholder || 'dd/mm/yyyy';
    picker.altInput.setAttribute('autocomplete', 'off');

    /* Give Enter back to the form.
     *
     * flatpickr consumes Enter to commit whatever was typed, and calls
     * preventDefault doing it — which also cancels the browser's implicit form
     * submission. A native date input submitted on Enter, so without this,
     * typing a range into the bill filters and pressing Enter silently does
     * nothing. Observed in a browser; no status-code test can see it.
     *
     * Bound on the *capture* phase, because flatpickr stops the key from
     * propagating any further and a listener registered normally never runs.
     * The submit is then deferred a tick so flatpickr's own handler — which
     * runs in between — has parsed the typed text into the real field first;
     * submitting inline posts the value the box held before this keypress.
     *
     * Skipped when the field declares its own onchange: the day list submits
     * from there, and both would fire on one keypress. */
    if (!input.hasAttribute('onchange')) {
      picker.altInput.addEventListener(
        'keydown',
        function (event) {
          if (event.key !== 'Enter' || !input.form) {
            return;
          }
          window.setTimeout(function () {
            input.form.requestSubmit();
          }, 0);
        },
        true
      );
    }
  }

  function bindAll(root) {
    if (root.matches && root.matches(SELECTOR)) {
      attach(root);
    }
    if (root.querySelectorAll) {
      Array.prototype.forEach.call(root.querySelectorAll(SELECTOR), attach);
    }
  }

  bindAll(document);
  document.addEventListener('DOMContentLoaded', function () {
    bindAll(document);
  });
  document.addEventListener('htmx:load', function (event) {
    bindAll(event.target);
  });
})();
