/* A formset row that can only be removed.
 *
 * The prescription and bill rows carry richer components — autocomplete, live
 * totals — and provide their own remove(). The goods receipt row needs nothing
 * else, so it gets this rather than a copy of one of those.
 *
 * Receipts are created and never edited, so there is no DELETE checkbox to tick
 * and no saved row to keep in the DOM: removing means dropping the node. The
 * form count is only decremented when the row removed was the last one —
 * lowering it past a row that still exists would make Django stop reading that
 * row's fields, which is the same rule the other two follow.
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('formsetRow', (prefix = 'items') => ({
    removed: false,

    remove() {
      const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
      const named = this.$root.querySelector(`[name^="${prefix}-"]`);
      const match = new RegExp(`${prefix}-(\\d+)-`).exec(named ? named.name : '');
      const index = match ? parseInt(match[1], 10) : -1;
      if (total && index === parseInt(total.value, 10) - 1) {
        total.value = index;
      }
      this.$root.remove();
    },
  }));
});
