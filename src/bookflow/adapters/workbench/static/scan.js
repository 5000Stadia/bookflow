// The Scan field (R104 increment 3). A scanner in keyboard mode types the code and Enter; here Enter
// looks the code up and never submits the form. See templates/scan_field.html for the three modes.
(() => {
  // Loaded once per page; a swapped-in page that carries the script again keeps the first install.
  if (window.bookflowScan) return;
  const QUANTITY = /^\s*(\d+(?:\.\d+)?)\s*\*\s*(.+)$/;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  function parse(text) {
    const m = QUANTITY.exec(text);
    if (m && !(Number(m[1]) > 0)) return {code: m[2].trim(), quantity: m[1], set: true, refused: true};
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
    // The entry posts and the page is swapped for the answer (R104 increment 2). The next scan in the
    // queue waits for the new page, and then finds the new form rather than the one that was replaced.
    const swapped = new Promise(resolve => {
      const done = () => { document.removeEventListener('htmx:afterSettle', done); clearTimeout(timer); resolve(); };
      const timer = setTimeout(done, 15000);
      document.addEventListener('htmx:afterSettle', done);
    });
    form.requestSubmit(form.querySelector('[type="submit"]') || undefined);
    return swapped.then(() => {
      // A camera scan leaves the focus alone: on a phone, focusing the field would open the keyboard over the camera.
      if (!want.camera) document.querySelector('[data-scan][data-scan-mode="count"] [data-scan-input]')?.focus();
      return want.quantity;
    });
  }

  // ---- "pick": the pick screen (R181 increment 3). The code itself is entered: the list finds the next unfilled
  // line for the item in the current bin, picks a scanned serial number's unit, and says when the item is not on it.
  function intoPick(field, answer, want) {
    const form = field.closest('form');
    const code = form.querySelector('[name="code"]');
    const q = form.querySelector('[name="quantity"]');
    const mode = form.querySelector('[name="mode"][value="set"]');
    if (!code || !q) return false;
    code.value = want.code;
    q.value = want.quantity;
    if (mode) mode.checked = want.set;
    const swapped = new Promise(resolve => {
      const done = () => { document.removeEventListener('htmx:afterSettle', done); clearTimeout(timer); resolve(); };
      const timer = setTimeout(done, 15000);
      document.addEventListener('htmx:afterSettle', done);
    });
    form.requestSubmit(form.querySelector('[type="submit"]') || undefined);
    return swapped.then(() => {
      if (!want.camera) document.querySelector('[data-scan][data-scan-mode="pick"] [data-scan-input]')?.focus();
      // The list refused it (an item not on it, a number held elsewhere): the page says why; nothing was added.
      return document.querySelector('[data-pick] [role=alert]') ? null : want.quantity;
    });
  }

  // ---- "pos": the POS screen (R114 increment 5). The cart is the page's (pos.js): a known code rings the item up,
  // adding a line or one more to its last line; "6*" first rings up six.
  function intoPos(field, answer, want) {
    if (!window.bookflowPos) return false;
    return window.bookflowPos.scanned(answer, want);
  }

  const MODES = {lines: intoLines, rows: intoRows, count: intoCount, pick: intoPick, pos: intoPos};

  // The current bin: the form's Bin field (the count screen), else every empty bin field the next lines use.
  function setBin(field, answer) {
    field.dataset.bin = answer.bin;
    const form = field.closest('form') || document;
    const own = form.querySelector('[data-scan-bin]');
    if (own) { own.value = answer.bin; fire(own); }
  }

  // One scan at a time, in the order they were typed: a scanner can send the next code before the
  // last look-up has answered, and each must land on its own line.
  const queues = new WeakMap();
  function enqueue(field, input, text, camera) {
    const previous = queues.get(field) || Promise.resolve();
    const next = previous.then(() => look(field, input, text, camera)).catch(() => {});
    queues.set(field, next);
    return next;
  }

  // `camera` is true for a code the camera read (camera-scan.js): the same look-up and placing, without moving focus.
  async function look(field, input, text, camera) {
    if (text === undefined) { text = input.value; input.value = ''; }
    if (!field.isConnected) {
      // The page was swapped while this scan waited its turn (a count entry): use the field that replaced it.
      const current = document.querySelector(`[data-scan][data-scan-mode="${field.dataset.scanMode || 'lines'}"]`);
      if (current) { field = current; input = current.querySelector('[data-scan-input]'); }
    }
    if (!text.trim()) return;
    const want = parse(text);
    want.camera = !!camera;
    const keepTyped = () => {
      // Nothing typed is lost: the code goes back in the field unless something new is already there.
      if (!input.value) input.value = text;
      if (document.activeElement === input) input.select();
      // A camera code is selected without focusing (no phone keyboard), so the next scan typed there replaces it.
      else if (camera && input.value === text) { try { input.setSelectionRange(0, text.length); } catch (error) { /* not focusable */ } }
    };
    if (want.refused) {
      say(field, 'A quantity of ' + escape(want.quantity) + ' adds nothing. Type a quantity above 0 before the *.', 'error');
      keepTyped();
      return;
    }
    say(field, 'Looking up ' + escape(want.code) + '…', 'busy');
    let answer;
    try {
      const response = await fetch(field.dataset.scanLookup + '?code=' + encodeURIComponent(want.code),
                                   {headers: {Accept: 'application/json'}, credentials: 'same-origin'});
      answer = await response.json();
    } catch (error) {
      say(field, 'The look-up could not be done. Nothing was changed; try again.', 'error');
      keepTyped();
      return;
    }
    if (!answer.found) {
      const assign = answer.assign_url
        ? ` <a href="${escape(answer.assign_url)}" target="_blank" rel="noopener" data-scan-assign>Assign to an item</a> (open the item, Edit, and enter ${escape(want.code)} as its Barcode), then scan again.`
        : '';
      say(field, escape(answer.message || 'Unknown code.') + assign, 'unknown');
      keepTyped();
      return;
    }
    if (answer.kind === 'bin') {
      // A bin label sets the current bin: the next scans count (or go) there (R104 increment 5).
      setBin(field, answer);
      say(field, 'Current bin: ' + escape(answer.bin) + ' at ' + escape(answer.site) + '.', 'ok');
      return;
    }
    if (field.dataset.scanMode === 'pick' && answer.lot && !answer.serial_numbers) {
      // A lot label on the pick screen sets the lot for the next scans.
      const lot = (field.closest('form') || document).querySelector('[data-scan-lot]');
      if (lot) { lot.value = answer.lot; fire(lot); }
      say(field, 'Lot ' + escape(answer.lot) + ' of ' + escape(answer.item_name) + ' for the next scans.', 'ok');
      return;
    }
    let placed = MODES[field.dataset.scanMode || 'lines'](field, answer, want);
    if (placed && typeof placed.then === 'function') {
      placed = await placed;
      if (!field.isConnected) {
        field = document.querySelector(`[data-scan][data-scan-mode="${field.dataset.scanMode || 'count'}"]`) || field;
        if (placed === null) {
          say(field, 'Nothing was added: ' + escape(document.querySelector('[role=alert]')?.innerText.split('\n')[0] || 'refused.'), 'error');
          return;
        }
        if (answer.message || answer.item_name) say(field, escape(answer.message || answer.item_name) + ' Quantity ' + escape(placed) + '.', 'ok');
        return;
      }
    }
    if (placed === false) {
      say(field, 'There is no free line for ' + escape(answer.item_name) + '.', 'error');
      keepTyped();
      return;
    }
    say(field, escape(answer.message || answer.item_name) + ' Quantity ' + escape(placed) + '.', 'ok');
  }

  // Enter in the Scan field is a look-up. Caught before anything else sees the key, so the form's
  // implicit submission never happens. The typed text is taken and the field cleared at once, so the
  // next scan starts clean while this one is looked up.
  document.addEventListener('keydown', event => {
    const input = event.target.closest?.('[data-scan-input]');
    if (!input || event.key !== 'Enter') return;
    event.preventDefault();
    event.stopPropagation();
    const text = input.value;
    input.value = '';
    if (text.trim()) enqueue(input.closest('[data-scan]'), input, text);
  }, true);
  // Belt and braces: a submit started from the Scan field is refused.
  document.addEventListener('submit', event => {
    if (document.activeElement?.matches?.('[data-scan-input]') && !event.submitter) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  window.bookflowScan = {parse, look: (field, input) => { const text = input.value; input.value = ''; return enqueue(field, input, text); },
    // A code from the camera, queued behind any typed ones and handled exactly as if it had been typed and entered.
    scan: (field, text) => enqueue(field, field.querySelector('[data-scan-input]'), text, true)};
})();
