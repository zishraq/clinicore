/* One row of a growable case-record table.
 *
 * A separate component from ``itemRow`` in item-autocomplete.js, and not a
 * refactor of it: that one is bound to the ``items-`` prefix because the visit
 * form has exactly one formset, and this page has three. The prefix is passed
 * in rather than parsed out of the DOM so a row knows which table it is in even
 * before its inputs are named.
 *
 * There is no autocomplete here on purpose. §14's candidate is free text rather
 * than a catalog link — the analysis is a scratchpad that names candidates the
 * clinic does not stock, and an FK would PROTECT a product because somebody
 * once considered it (ADR 0020 §2).
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('caseRow', (prefix) => ({
    removed: false,

    remove() {
      const del = this.$root.querySelector('[data-role="item-delete"]');
      const idInput = this.$root.querySelector('[name$="-id"]');

      /* A saved row cannot simply leave the DOM: the formset deletes it only if
       * its DELETE box comes back ticked. Hide it and let the POST do the work. */
      if (idInput && idInput.value) {
        if (del) del.checked = true;
        this.removed = true;
        return;
      }

      /* An unsaved row is dropped outright. If it was the last one, TOTAL_FORMS
       * comes back down with it — otherwise the formset expects an index that
       * posts nothing and reads the gap as a half-filled row. */
      const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
      const named = this.$root.querySelector(`[name^="${prefix}-"]`);
      const match = named && new RegExp(`${prefix}-(\\d+)-`).exec(named.name);
      const index = match ? parseInt(match[1], 10) : -1;
      if (total && index === parseInt(total.value, 10) - 1) {
        total.value = index;
      }
      this.$root.remove();
    },
  }));
});
