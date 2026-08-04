/* Prescription item autocomplete.
 *
 * HTMX fetches the suggestions fragment; this component owns selection state,
 * keyboard navigation, and writing the chosen entry into the row's hidden
 * inputs. Entry is done at speed during a consultation, so arrows + enter must
 * work without the mouse ever being touched.
 *
 * The markup contract is in templates/clinical/_item_row.html and
 * templates/catalog/_suggestions.html:
 *   [data-role="item-search"]   visible text box (display_name)
 *   [data-role="item-type"]     hidden MEDICATION | ADVICE
 *   [data-role="item-product"]  hidden product pk
 *   [data-role="item-advice"]   hidden advice_template pk
 *   [data-role="item-free-text"] hidden free_text_name
 *   [data-role="item-delete"]   hidden DELETE checkbox
 *   [data-result]               one selectable suggestion
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('itemRow', () => ({
    open: false,
    activeIndex: -1,
    itemType: 'MEDICATION',
    removed: false,
    justSelected: false,

    init() {
      this.itemType = this.field('item-type')?.value || 'MEDICATION';
    },

    /* $root, never $el: Alpine sets $el to whichever element the expression is
     * evaluated on, so inside @input="onInput()" it is the search box itself and
     * a querySelector under it finds nothing. $root is always the row. */
    field(role) {
      return this.$root.querySelector(`[data-role="${role}"]`);
    },

    get typeLabel() {
      return this.itemType === 'ADVICE' ? 'Advice' : '';
    },

    isAdvice() {
      return this.itemType === 'ADVICE';
    },

    /* Everything the arrow keys can land on: catalog entries first, then the
     * quick-add offers. Quick-add has to be reachable by keyboard too — it is
     * the only option when nothing matched, which is exactly when a
     * practitioner is typing something the catalog has never seen. */
    options() {
      if (!this.$refs.results) return [];
      return Array.from(
        this.$refs.results.querySelectorAll('[data-result], [data-quick-add]')
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
      // alone would hide the panel on a no-match query and make quick-add —
      // the only useful action at that moment — unreachable.
      this.open = this.$refs.results.children.length > 0;
      this.activeIndex = this.options().length > 0 ? 0 : -1;
      this.highlight();
    },

    /* Typing invalidates any previous pick: the row falls back to free text. */
    onInput() {
      this.field('item-product').value = '';
      this.field('item-advice').value = '';
      this.field('item-free-text').value = this.field('item-search').value;
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
      if (active.matches('[data-quick-add]')) {
        // Let HTMX post it; the created entry comes back auto-selecting.
        active.click();
        return;
      }
      this.select(active);
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
      this.prefill('frequency', option.dataset.frequency);
      this.prefill('duration', option.dataset.duration);
      this.prefill('instructions', option.dataset.instructions);
      this.justSelected = true;
      if (type === 'ADVICE') {
        const dosage = this.$root.querySelector('[name$="-dosage"]');
        if (dosage) dosage.value = '';
      }
      this.close();
    },

    prefill(suffix, value) {
      if (!value) return;
      const input = this.$root.querySelector(`[name$="-${suffix}"]`);
      if (input && !input.value) input.value = value;
    },

    close() {
      this.open = false;
      this.activeIndex = -1;
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
});
