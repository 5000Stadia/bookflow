/* Advisory live allocation using the shared exact expression parser. Server owns posting. */
(() => {
  if (window.bookflowPurchaseAllocation) return;
  window.bookflowPurchaseAllocation = true;
  const control = (row, suffix) => row.querySelector(`[name$=":${suffix}"]`);
  function calculate(form) {
    const status = form?.querySelector('[data-purchase-allocation]');
    if (!status || !window.bookflowMath) return;
    try {
      const header = form.elements.namedItem('f:amount');
      const scale = window.bookflowMath.context(header).scale;
      if (!Number.isInteger(scale)) throw Error('Preview to resolve currency precision.');
      const exact = (value, precision = scale) => {
        const result = window.bookflowMath.evaluate(value, precision);
        if (result.needsRounding) throw Error('Resolve the entered calculation before allocation.');
        return result.value;
      };
      let amounts = [];
      for (const grid of form.querySelectorAll('[data-collection-path="expenses"], [data-collection-path="items"]')) {
        const items = grid.dataset.collectionPath === 'items';
        for (const row of grid.querySelectorAll(':scope > [data-collection-items] > [data-collection-item]')) {
          const identity = control(row, items ? 'item' : 'account');
          const amount = control(row, 'amount')?.value?.trim();
          const cost = control(row, 'unit_cost')?.value?.trim();
          if (!identity?.value && !amount && !cost) continue;
          let value;
          if (amount && cost && items) throw Error('Enter cost or line amount, not both.');
          if (amount) value = exact(amount);
          else if (items && cost) {
            const quantity = exact(control(row, 'quantity')?.value || '1', 6);
            const unit = exact(cost);
            // Purchase extension uses half-even at the currency precision, like shared math.
            value = window.bookflowMath.evaluate(`(${quantity})*(${unit})`, scale).value;
          } else throw Error('Enter amounts/costs, or preview to resolve item defaults.');
          amounts.push(value);
          const display = row.querySelector('.line-amount');
          if (display && display.textContent !== value) display.textContent = value;
        }
      }
      const total = exact(amounts.length ? amounts.map(v => `(${v})`).join('+') : '0');
      const entered = exact(header.value || '0');
      const difference = exact(`(${entered})-(${total})`);
      const text = `Entered ${entered} · Allocated ${total} · Remaining ${difference}`;
      if (status.textContent !== text) status.textContent = text;
    } catch (error) {
      if (status.textContent !== error.message) status.textContent = error.message;
    }
  }
  const refresh = () => document.querySelectorAll('[data-purchase-allocation]').forEach(el => calculate(el.closest('form')));
  document.addEventListener('input', event => calculate(event.target.closest('form')));
  document.addEventListener('change', event => calculate(event.target.closest('form')));
  document.addEventListener('htmx:afterSwap', refresh);
  document.addEventListener('DOMContentLoaded', refresh);
  new MutationObserver(records => {
    if (records.some(record => [...record.addedNodes, ...record.removedNodes].some(node =>
      node.nodeType === 1 && (node.matches?.('[data-collection-item], [data-purchase-allocation]') || node.querySelector?.('[data-collection-item]'))))) refresh();
  }).observe(document.documentElement, {childList:true, subtree:true});
  refresh();
})();
