/* Bill line autocomplete and the running total.
 *
 * Same shape as static/js/item-autocomplete.js — HTMX fetches the suggestions
 * fragment, Alpine owns selection, keyboard navigation, and writing the chosen
 * product into the row's hidden inputs. It is a separate component rather than
 * a shared one because the two rows have different fields and different rules:
 * a bill line has a price and no dosage, and never offers quick-add.
 *
 * The markup contract is in templates/billing/_line_row.html and
 * templates/catalog/_suggestions.html:
 *   [data-role="line-search"]   visible text box (display_name)
 *   [data-role="line-type"]     hidden CONSULTATION | PRODUCT | OTHER
 *   [data-role="line-product"]  hidden product pk
 *   [data-role="line-delete"]   hidden DELETE checkbox
 *   [data-result]               one selectable suggestion
 *
 * Totals are read straight off the inputs rather than tracked in state: rows
 * arrive from the server mid-page (the add-row button), and a DOM sweep needs
 * no registration step for them.
 */
document.addEventListener('alpine:init', () => {
  const money = (input) => {
    const value = parseFloat(input && input.value);
    return Number.isFinite(value) ? value : 0;
  };

  Alpine.data('invoiceLine', () => ({
    open: false,
    activeIndex: -1,
    removed: false,
    justSelected: false,
    /* Plain state, not a getter over the inputs: Alpine tracks its own
     * reactive properties, and an input's .value changing is invisible to it,
     * so a computed row total would render once and then sit there stale. */
    lineTotal: 0,

    init() {
      this.recalcLine();
    },

    field(role) {
      // $root, never $el: inside @input the latter is the search box itself.
      return this.$root.querySelector(`[data-role="${role}"]`);
    },

    input(suffix) {
      return this.$root.querySelector(`[name$="-${suffix}"]`);
    },

    /* The line's own money, shown next to the row and summed by the footer. */
    recalcLine() {
      const gross = money(this.input('quantity')) * money(this.input('unit_price'));
      this.lineTotal = Math.max(gross - money(this.input('discount')), 0);
    },

    options() {
      if (!this.$refs.results) return [];
      return Array.from(this.$refs.results.querySelectorAll('[data-result]'));
    },

    /* HTMX just swapped new suggestions in. */
    onResults() {
      // A request already in flight when the user picked something must not
      // reopen the list behind them.
      if (this.justSelected) {
        this.justSelected = false;
        this.close();
        return;
      }
      this.open = this.options().length > 0;
      this.activeIndex = this.open ? 0 : -1;
      this.highlight();
    },

    /* Typing invalidates the previous pick: the line falls back to typed text. */
    onInput() {
      this.field('line-product').value = '';
      if (this.field('line-type').value !== 'CONSULTATION') {
        this.field('line-type').value = 'OTHER';
      }
      this.changed();
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
      if (active) this.select(active);
    },

    select(option) {
      this.field('line-type').value = 'PRODUCT';
      this.field('line-product').value = option.dataset.id || '';
      this.field('line-search').value = option.dataset.name || '';

      // The catalog price is a starting point, not a rule: it fills an empty
      // box and never overwrites a price already typed for this patient.
      const price = this.input('unit_price');
      if (price && !parseFloat(price.value)) price.value = option.dataset.price || '';
      const quantity = this.input('quantity');
      if (quantity && !parseFloat(quantity.value)) quantity.value = '1';

      this.justSelected = true;
      this.close();
      this.changed();
    },

    close() {
      this.open = false;
      this.activeIndex = -1;
    },

    /* Re-add this row, then tell the footer to re-add everything up. */
    changed() {
      this.recalcLine();
      window.dispatchEvent(new CustomEvent('bill-changed'));
    },

    /* Remove this row. A saved row must survive as markup so the formset can
     * delete it server-side; an unsaved one can simply go. Same reasoning, and
     * the same TOTAL_FORMS caveat, as the prescription row. */
    remove() {
      const idInput = this.$root.querySelector('[name$="-id"]');
      if (idInput && idInput.value) {
        this.field('line-delete').checked = true;
        this.removed = true;
        this.changed();
        return;
      }
      const total = document.getElementById('id_items-TOTAL_FORMS');
      const match = /items-(\d+)-/.exec(this.field('line-search')?.name || '');
      const index = match ? parseInt(match[1], 10) : -1;
      if (total && index === parseInt(total.value, 10) - 1) {
        total.value = index;
      }
      this.$root.remove();
      this.changed();
    },
  }));

  /* Footer total. Sums the rows that are still live, so the practitioner sees
   * what the patient will be asked for before saving anything. */
  Alpine.data('invoiceTotals', () => ({
    total: 0,

    init() {
      this.recalc();
    },

    recalc() {
      let sum = 0;
      document.querySelectorAll('[data-line-row]').forEach((row) => {
        const deleted = row.querySelector('[data-role="line-delete"]');
        if (deleted && deleted.checked) return;
        const value = (suffix) => {
          const input = row.querySelector(`[name$="-${suffix}"]`);
          const parsed = parseFloat(input && input.value);
          return Number.isFinite(parsed) ? parsed : 0;
        };
        sum += Math.max(value('quantity') * value('unit_price') - value('discount'), 0);
      });
      this.total = sum;
    },

    get formatted() {
      return this.total.toFixed(2);
    },
  }));
});
