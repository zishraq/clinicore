/* Autocomplete components for the consultation form.
 *
 * HTMX fetches the suggestions fragment; the component owns selection state,
 * keyboard navigation, and writing the chosen entry into hidden inputs. Entry
 * is done at speed during a consultation, so arrows + enter must work without
 * the mouse ever being touched.
 *
 * Two components share one core. ``itemRow`` picks a catalog entry for a
 * prescription row; ``patientPicker`` picks the patient the visit is for. They
 * differ only in what a selection writes and what the trailing action offers —
 * everything about opening, arrowing, highlighting and closing is
 * ``autocompleteCore`` below, so there is one keyboard implementation to keep
 * correct rather than two that drift.
 *
 * Markup contract, templates/clinical/_item_row.html +
 * templates/catalog/_suggestions.html:
 *   [data-role="item-search"]   visible text box (display_name)
 *   [data-role="item-type"]     hidden MEDICATION | ADVICE
 *   [data-role="item-product"]  hidden product pk
 *   [data-role="item-advice"]   hidden advice_template pk
 *   [data-role="item-free-text"] hidden free_text_name
 *   [data-role="item-delete"]   hidden DELETE checkbox
 *   [name$="-strength"]         strength box, absent unless the org records one
 *
 * Markup contract, templates/clinical/_patient_picker.html +
 * templates/patients/_suggestions.html:
 *   [data-role="patient-search"] visible text box
 *   [data-role="patient-id"]     hidden patient pk (the real form field)
 *
 * Common to both:
 *   [data-result]               one selectable suggestion
 *   [data-autoselect]           a suggestion to take immediately on arrival
 */

/* The generic half: everything that does not know what is being picked.
 *
 * ``actionSelector`` is the trailing offer — quick-add a catalog entry, or
 * register a patient. It has to be arrow-reachable like any other option,
 * because it is the only thing on the list when nothing matched, which is
 * exactly when the user is typing something the system has never seen. */
function autocompleteCore(actionSelector) {
  return {
    open: false,
    activeIndex: -1,
    justSelected: false,

    /* $root, never $el: Alpine sets $el to whichever element the expression is
     * evaluated on, so inside @input="onInput()" it is the search box itself and
     * a querySelector under it finds nothing. $root is always the component. */
    field(role) {
      return this.$root.querySelector(`[data-role="${role}"]`);
    },

    options() {
      if (!this.$refs.results) return [];
      return Array.from(
        this.$refs.results.querySelectorAll(`[data-result], ${actionSelector}`)
      );
    },

    /* HTMX just swapped new suggestions in. */
    onResults() {
      const auto = this.$refs.results.querySelector('[data-autoselect]');
      if (auto) {
        this.select(auto);
        return;
      }
      // A request may already have been in flight when the user selected; its
      // results must not reopen the list behind them.
      if (this.justSelected) {
        this.justSelected = false;
        this.close();
        return;
      }
      // Open whenever the fragment has anything in it. Keying off results
      // alone would hide the panel on a no-match query and make the action —
      // the only useful thing at that moment — unreachable.
      this.open = this.$refs.results.children.length > 0;
      this.activeIndex = this.options().length > 0 ? 0 : -1;
      this.highlight();
    },

    move(delta) {
      const options = this.options();
      if (!options.length) return;
      this.open = true;
      this.activeIndex = (this.activeIndex + delta + options.length) % options.length;
      this.highlight();
    },

    highlight() {
      this.options().forEach((option, index) => {
        const active = index === this.activeIndex;
        option.classList.toggle('bg-[var(--cc-surface-alt)]', active);
        option.setAttribute('aria-selected', active ? 'true' : 'false');
        if (active) option.scrollIntoView({ block: 'nearest' });
      });
    },

    choose() {
      const options = this.options();
      const active = this.open && this.activeIndex >= 0 && options[this.activeIndex];
      if (!active) return;
      if (active.matches(actionSelector)) {
        // Let the button do its own thing — post to quick-add, or open the
        // registration modal. Either way the result comes back auto-selecting.
        active.click();
        return;
      }
      this.select(active);
    },

    close() {
      this.open = false;
      this.activeIndex = -1;
    },
  };
}

document.addEventListener('alpine:init', () => {
  Alpine.data('itemRow', () => ({
    ...autocompleteCore('[data-quick-add]'),
    itemType: 'MEDICATION',
    removed: false,

    init() {
      this.itemType = this.field('item-type')?.value || 'MEDICATION';
    },

    get typeLabel() {
      return this.itemType === 'ADVICE' ? 'Advice' : '';
    },

    isAdvice() {
      return this.itemType === 'ADVICE';
    },

    /* Typing invalidates any previous pick: the row falls back to free text. */
    onInput() {
      this.field('item-product').value = '';
      this.field('item-advice').value = '';
      this.field('item-free-text').value = this.field('item-search').value;
    },

    select(option) {
      const type = option.dataset.type || 'MEDICATION';
      this.itemType = type;
      this.field('item-type').value = type;
      this.field('item-search').value = option.dataset.name || '';
      this.field('item-free-text').value = '';
      this.field('item-product').value =
        type === 'MEDICATION' ? option.dataset.id || '' : '';
      this.field('item-advice').value =
        type === 'ADVICE' ? option.dataset.id || '' : '';

      // Prefill the entry's defaults, without clobbering anything already typed.
      this.prefill('strength', option.dataset.strength);
      this.prefill('frequency', option.dataset.frequency);
      this.prefill('duration', option.dataset.duration);
      this.prefill('instructions', option.dataset.instructions);
      this.justSelected = true;
      if (type === 'ADVICE') {
        // Neither applies to advice, and both are hidden rather than removed —
        // a value typed before the row became advice would otherwise still post.
        ['dosage', 'strength'].forEach((suffix) => {
          const input = this.$root.querySelector(`[name$="-${suffix}"]`);
          if (input) input.value = '';
        });
      }
      this.close();
    },

    prefill(suffix, value) {
      if (!value) return;
      const input = this.$root.querySelector(`[name$="-${suffix}"]`);
      if (input && !input.value) input.value = value;
    },

    /* Remove this row.
     *
     * A saved row must survive as markup: the formset only deletes it if its
     * DELETE checkbox comes back ticked, so the row is hidden rather than
     * dropped. An unsaved row has nothing to delete server-side, so the node
     * goes — and TOTAL_FORMS is only decremented when the row is the last one,
     * because lowering it past a surviving row would make Django skip that
     * row's data entirely. Leaving a gap is safe: a form index with no posted
     * fields is just an empty extra form.
     */
    remove() {
      const del = this.field('item-delete');
      const idInput = this.$root.querySelector('[name$="-id"]');
      const isSaved = Boolean(idInput && idInput.value);

      if (isSaved) {
        del.checked = true;
        this.removed = true;
        return;
      }

      const total = document.getElementById('id_items-TOTAL_FORMS');
      const match = /items-(\d+)-/.exec(this.field('item-search')?.name || '');
      const index = match ? parseInt(match[1], 10) : -1;
      if (total && index === parseInt(total.value, 10) - 1) {
        total.value = index;
      }
      this.$root.remove();
    },
  }));

  /* Which patient this visit is for.
   *
   * The doctor opens Visits first, so registering someone must happen here
   * rather than sending him to another screen and back. The trailing option
   * opens the registration modal; the created patient arrives as a
   * ``patient-picked`` event rather than a swapped fragment, because the modal
   * lives outside this component's subtree (a <form> cannot nest, so the modal
   * sits in base.html's modals block) and has no way to swap into it. */
  Alpine.data('patientPicker', () => ({
    ...autocompleteCore('[data-add-patient]'),

    select(option) {
      this.field('patient-id').value = option.dataset.id || '';
      this.field('patient-search').value = option.dataset.name || '';
      this.justSelected = true;
      this.close();
    },

    /* Typing invalidates the pick. Without this the box can read one patient's
     * name while the hidden field still posts another's pk — the visit would
     * be filed against whoever was selected before, silently. */
    onInput() {
      this.field('patient-id').value = '';
    },

    /* Fired by templates/patients/_picked.html once the modal has created or
     * matched someone. The typed query is already in the box; overwrite it with
     * the canonical label so what is displayed matches what is posted. */
    onPicked(event) {
      this.field('patient-id').value = event.detail.id;
      this.field('patient-search').value = event.detail.name;
      this.close();
    },

    /* The modal is prefilled with whatever has been typed so far, so the doctor
     * does not retype the name he just searched for. */
    typedName() {
      return this.field('patient-search')?.value || '';
    },
  }));
});
