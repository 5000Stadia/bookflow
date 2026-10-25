// The Scan field (R104 increment 3). A scanner in keyboard mode types the code and Enter; here Enter
// looks the code up and never submits the form. See templates/scan_field.html for the three modes.
(() => {
  const QUANTITY = /^\s*(\d+(?:\.\d+)?)\s*\*\s*(.+)$/;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function parse(text) {
    const m = QUANTITY.exec(text);
    return m ? {code: m[2].trim(), quantity: m[1], set: true} : {code: text.trim(), quantity: '1', set: false};
  }
  function say(field, html, kind) {
    const status = field.querySelector('[data-scan-status]');
    status.innerHTML = html;
    status.dataset.kind = kind || '';
  }
  function fire(element) {
    for (const type of ['input', 'change']) element.dispatchEvent(new Event(type, {bubbles: true}));
  }
  function add(a, b) {
    const sum = Number(a || 0) + Number(b || 0);
    return Number.isInteger(sum) ? String(sum) : String(Math.round(sum * 1e6) / 1e6);
  }

  // ---- "lines": a document grid whose rows hold an item reference picker and a quantity
  function grid(field) {
    const form = field.closest('form') || document;
    if (field.dataset.scanTarget) return form.querySelector(field.dataset.scanTarget);
    return [...form.querySelectorAll('[data-collection]')].find(c =>
      c.querySelector(':scope > template[data-collection-template]')?.innerHTML.match(/name="c:[^"]*:item"/));
  }
  const rowItem = row => row.querySelector('[data-ref-value][name$=":item"]');
  const rowQuantity = row => row.querySelector('[name$=":quantity"]');
  function intoLines(field, answer, want) {
    const lines = grid(field);
    if (!lines) return false;
    const rows = [...lines.querySelectorAll(':scope > [data-collection-items] > [data-collection-item]')].filter(rowItem);
    let row = rows.filter(r => rowItem(r).value === answer.item_id).pop();
    if (row) {
      const q = rowQuantity(row);
      q.value = want.set ? want.quantity : add(q.value || '1', want.quantity);
      fire(q);
      return q.value;
    }
    row = rows.find(r => !rowItem(r).value);
    if (!row) {
      lines.querySelector(':scope > [data-collection-add]').click();
      row = [...lines.querySelectorAll(':scope > [data-collection-items] > [data-collection-item]')].pop();
    }
    const picker = rowItem(row).closest('[data-reference]');
    rowItem(row).value = answer.item_id;
    picker.querySelector('[data-ref-state]').value = 'selected';
    const search = picker.querySelector('[data-ref-search]');
    search.value = answer.item_name;
    search.setCustomValidity('');
    picker.setAttribute('data-has-value', '');
    const status = picker.querySelector('[data-ref-status]');
    if (status) status.textContent = 'Selected: ' + answer.item_name;
    const q = rowQuantity(row);
    if (q) { q.value = want.quantity; fire(q); }
    fire(rowItem(row));
    return want.quantity;
  }

  // ---- "rows": fixed rows named item_N / quantity_N (the transfer form)
  function intoRows(field, answer, want) {
    const form = field.closest('form');
    const items = [...form.querySelectorAll('input[name^="item_"]')];
    const qty = input => form.querySelector(`[name="quantity_${input.name.slice(5)}"]`);
    const same = items.filter(i => i.value.trim().toLowerCase() === answer.item_name.toLowerCase()).pop();
    const target = same || items.find(i => !i.value.trim());
    if (!target) return false;
    const q = qty(target);
    target.value = answer.item_name;
    q.value = same && !want.set ? add(q.value || '1', want.quantity) : want.quantity;
    fire(target); fire(q);
    return q.value;
  }

  // ---- "count": the count screen's entry form (item, quantity, "Add to it")
  function intoCount(field, answer, want) {
    const form = field.closest('form');
    const item = form.querySelector('[name="scan"],[name="item"]');
    const q = form.querySelector('[name="quantity"]');
    const mode = form.querySelector('[name="mode"][value="add"]');
    if (!item || !q) return false;
    item.value = answer.item_name;
    q.value = want.quantity;
    if (mode) mode.checked = !want.set;
    form.requestSubmit(form.querySelector('[type="submit"]') || undefined);
    return want.quantity;
  }

  const MODES = {lines: intoLines, rows: intoRows, count: intoCount};

  async function look(field, input) {
    const text = input.value;
    if (!text.trim()) return;
    const want = parse(text);
    say(field, 'Looking up ' + escape(want.code) + '…', 'busy');
    let answer;
    try {
      const response = await fetch(field.dataset.scanLookup + '?code=' + encodeURIComponent(want.code),
                                   {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
      answer = await response.json();
    } catch (error) {
      say(field, 'The look-up could not be done. Nothing was changed; try again.', 'error');
      return;
    }
    if (!answer.found) {
      const assign = answer.assign_url
        ? ` <a href="${escape(answer.assign_url)}" target="_blank" rel="noopener" data-scan-assign>Assign to an item</a> (open the item, Edit, and enter ${escape(want.code)} as its Barcode), then scan again.`
        : '';
      say(field, escape(answer.message || 'Unknown code.') + assign, 'unknown');
      input.select();
      return;
    }
    const placed = MODES[field.dataset.scanMode || 'lines'](field, answer, want);
    if (placed === false) {
      say(field, 'There is no free line for ' + escape(answer.item_name) + '.', 'error');
      return;
    }
    say(field, escape(answer.item_name) + ' · quantity ' + escape(placed), 'ok');
    input.value = '';
    input.focus();
  }

  // Enter in the Scan field is a look-up. Caught before anything else sees the key, so the form's
  // implicit submission never happens.
  document.addEventListener('keydown', event => {
    const input = event.target.closest?.('[data-scan-input]');
    if (!input || event.key !== 'Enter') return;
    event.preventDefault();
    event.stopPropagation();
    look(input.closest('[data-scan]'), input);
  }, true);
  // Belt and braces: a submit started from the Scan field is refused.
  document.addEventListener('submit', event => {
    if (document.activeElement?.matches?.('[data-scan-input]') && !event.submitter) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  window.bookflowScan = {parse, look};
})();
