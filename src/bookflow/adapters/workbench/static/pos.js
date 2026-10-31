// The POS screen (R114 increment 5, design/point-of-sale.md §3, §11, §12).
// The cart lives in the page and is mirrored to localStorage per register on every change, so a reload, a dropped
// connection or a locked phone loses nothing. A tap or a scan adds its line at once from the item data on the page;
// the total shown is always the host's (`pos sale preview`, 250 ms after the last change), so what posts is what was
// shown. Complete sends the ticket's key as the operation key: a retry after a lost answer finds the sale, never a
// second one.
(() => {
  const root = document.querySelector('[data-pos]');
  if (!root || window.bookflowPos) return;
  const data = JSON.parse(document.getElementById('pos-data').textContent);
  const $ = selector => root.querySelector(selector);
  const items = new Map(data.items.map(item => [item.id, item]));
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  let format;
  try { format = new Intl.NumberFormat(undefined, {style: 'currency', currency: data.currency}); }
  catch (error) { format = {format: n => n.toFixed(2)}; }
  const money = amount => amount == null || amount === '' ? '—' : format.format(Number(amount));
  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (error) { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* private window */ } },
  };
  const COLOURS = ['#2e7d6b', '#3b6fb6', '#8a5a9e', '#b5652a', '#5f7d2e', '#a8445b', '#3f7f8c', '#7a6a2b'];
  const colour = text => COLOURS[[...String(text)].reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) >>> 0, 7) % COLOURS.length];

  // ---- the register and its cart
  const registerKey = `bookflow.pos.${data.company}.register`;
  let register = data.registers.find(r => r.id === store.get(registerKey)) || data.registers[0];
  const cartKey = () => `bookflow.pos.${data.company}.${register.id}.cart`;
  const newTicket = () => (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
    : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  const fresh = () => ({ticket: newTicket(), customer: null, lines: [], pending: null});
  let state = store.get(cartKey()) || fresh();
  let server = null;      // the last preview answer for the cart as it stands
  let offline = false;    // the last request found no host
  let seq = 0, timer = null;
  const save = () => store.set(cartKey(), state);

  // ---- talking to the host: the command API, as the signed-in person
  async function api(name, body) {
    let response;
    try {
      response = await fetch(`/companies/${encodeURIComponent(data.company)}/commands/${name.replaceAll(' ', '.')}`, {
        method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
        headers: {'Content-Type': 'application/json', 'X-Bookflow-Workbench': '1', 'X-Bookflow-Client-Name': 'bookflow-workbench'}});
    } catch (error) {
      const failure = new Error('No connection to Bookflow.');
      failure.offline = true;
      throw failure;
    }
    let out = null;
    try { out = await response.json(); } catch (error) { /* not JSON */ }
    const code = out && (out.code || (out.error && out.error.code));
    if (!out || !response.ok || (code && String(code).startsWith('E_'))) {
      const failure = new Error((out && (out.message || (out.error && out.error.message))) || `The host answered ${response.status}.`);
      failure.code = code;
      failure.details = out && (out.details || (out.error && out.error.details));
      failure.offline = !response.status || response.status >= 500 || !out;
      throw failure;
    }
    return out;
  }

  const lineInput = line => {
    const out = {item: line.item_id, quantity: line.quantity};
    if (line.unit_price != null && line.unit_price !== '') out.unit_price = line.unit_price;
    if (line.serial) out.serial_numbers = [line.serial];
    return out;
  };
  const cartInput = () => ({pos_register: register.id, customer: state.customer ? state.customer.id : null,
                            lines: state.lines.map(lineInput)});

  function schedule() {
    server = null;
    save();
    render();
    clearTimeout(timer);
    if (state.lines.length) timer = setTimeout(preview, 250);
  }

  async function preview() {
    const mine = ++seq;
    try {
      const answer = await api('pos sale preview', cartInput());
      if (mine !== seq) return;
      offline = false;
      server = answer;
    } catch (error) {
      if (mine !== seq) return;
      offline = !!error.offline;
      server = null;
      if (!error.offline) say(error.message, true);
    }
    render();
  }

  // ---- ringing up
  function add(item, quantity, serial) {
    quantity = String(quantity || '1');
    const tracked = item.tracking === 'serial';
    let line = tracked ? null : [...state.lines].reverse().find(l => l.item_id === item.id && !l.unit_price && !l.serial);
    if (line) {
      const sum = Number(line.quantity) + Number(quantity);
      line.quantity = Number.isInteger(sum) ? String(sum) : String(Math.round(sum * 1e6) / 1e6);
    } else {
      line = {key: newTicket(), item_id: item.id, name: item.name, price: item.price, quantity, unit_price: null,
              serial: serial || null, tracking: item.tracking || 'none', attributes: {}};
      state.lines.push(line);
    }
    afterAdd(line);   // the seam R189's commission picker would prompt from (design §3)
    schedule();
    if (unpriced(item) && !line.unit_price) openLine(line);
    else if (tracked && !line.serial) openLine(line);
    return line;
  }
  function afterAdd(line) { return line; }
  // An item with no price (0.00) takes one typed at the till; a discount or subtotal has none to type.
  const unpriced = item => item.box !== false && item.price != null && Number(item.price) === 0;

  function say(message, error) {
    const bar = $('[data-pos-connection]');
    bar.hidden = !message;
    bar.textContent = message || '';
    bar.dataset.kind = error ? 'error' : '';
  }

  // ---- drawing
  let path = [];   // the group paths walked into, from the home
  function boxHtml(box) {
    if (box.kind === 'group') {
      return `<button type="button" class="pos-box pos-box-group" role="listitem" data-pos-box-group="${escape(box.path)}"><span class="pos-tile" style="background:${colour(box.name)}">${escape(box.name.slice(0, 2))}</span><span class="pos-box-name">${escape(box.name)}</span><span class="pos-box-price">Group</span></button>`;
    }
    const item = items.get(box.id);
    if (!item) return '';
    const picture = item.picture
      ? `<img class="pos-tile" src="${escape(item.picture)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pos-tile',textContent:''}))">`
      : `<span class="pos-tile" style="background:${colour(item.name)}">${escape(item.name.slice(0, 2))}</span>`;
    return `<button type="button" class="pos-box" role="listitem" data-pos-box-item="${escape(item.id)}">${picture}<span class="pos-box-name">${escape(item.name)}</span><span class="pos-box-price">${item.price == null ? '' : money(item.price)}</span></button>`;
  }
  function drawBoxes() {
    const here = path.length ? (data.layout.groups[path[path.length - 1]] || {boxes: []}).boxes : data.layout.home;
    $('[data-pos-boxes]').innerHTML = here.map(boxHtml).join('') || '<p class="pos-empty">No sales items here yet.</p>';
    const crumbs = [`<button type="button" data-pos-crumb="-1"${path.length ? '' : ' aria-current="page"'}>Home</button>`];
    path.forEach((p, i) => crumbs.push(`<span aria-hidden="true">›</span><button type="button" data-pos-crumb="${i}"${i === path.length - 1 ? ' aria-current="page"' : ''}>${escape(data.layout.groups[p] ? data.layout.groups[p].name : p)}</button>`));
    if (path.length) crumbs.push(`<button type="button" class="pos-back" data-pos-crumb="${path.length - 2}">Back</button>`);
    $('[data-pos-crumbs]').innerHTML = crumbs.join('');
  }

  const units = () => state.lines.reduce((n, l) => n + (Number(l.quantity) || 0), 0);
  function render() {
    const lines = $('[data-pos-lines]');
    const nets = server && server.lines.length === state.lines.length ? server.lines.map(l => l.net.amount) : null;
    lines.innerHTML = state.lines.map((line, i) => {
      const price = line.unit_price != null && line.unit_price !== '' ? line.unit_price : line.price;
      const amount = nets ? nets[i] : (price == null ? null : String(Number(price) * Number(line.quantity)));
      return `<li><button type="button" class="pos-line" data-pos-line="${i}" data-pos-line-item="${escape(line.item_id)}"><span class="pos-line-name">${escape(line.name)}${line.serial ? ` <small>SN ${escape(line.serial)}</small>` : ''}</span><span class="pos-line-qty" data-pos-line-quantity>${escape(line.quantity)} × ${price == null ? '…' : money(price)}</span><span class="pos-line-amount">${amount == null ? '…' : money(amount)}</span></button></li>`;
    }).join('');
    $('[data-pos-empty]').hidden = state.lines.length > 0;
    const pending = !!state.pending;
    const confirmed = !!server && state.lines.length > 0;
    const waiting = state.lines.length && !server ? (offline ? 'not confirmed' : 'updating') : '—';
    $('[data-pos-subtotal]').textContent = confirmed ? money(server.subtotal.amount) : waiting;
    $('[data-pos-tax]').textContent = confirmed ? money(server.tax.amount) : waiting;
    $('[data-pos-total]').textContent = confirmed ? money(server.total.amount) : waiting;
    $('[data-pos-total]').dataset.state = confirmed ? 'confirmed' : (state.lines.length ? (offline ? 'offline' : 'updating') : 'empty');
    $('[data-pos-charge]').disabled = !confirmed || pending;
    const count = units();
    $('[data-pos-bar-count]').textContent = `${count} ${count === 1 ? 'item' : 'items'}`;
    $('[data-pos-bar-total]').textContent = confirmed ? money(server.total.amount) : (state.lines.length ? waiting : money(0));
    $('[data-pos-customer]').textContent = state.customer ? state.customer.name : 'No customer';
    $('[data-pos-customer]').classList.toggle('chosen', !!state.customer);
    if (offline) say('No connection to Bookflow. This sale is kept here; finish it when the connection returns.', true);
    else if ($('[data-pos-connection]').dataset.kind === 'error' && confirmed) say('');
    const banner = $('[data-pos-pending]');
    banner.hidden = !pending;
    if (pending) banner.innerHTML = `This sale was sent to be completed and no answer came back. <button type="button" data-pos-retry>Finish the sale</button> sends it again; it is never posted twice.`;
    root.classList.toggle('pos-locked', pending);
  }

  // ---- the line keypad
  let editing = null;
  function openLine(line) {
    editing = line;
    const dialog = $('[data-pos-line-dialog]');
    dialog.querySelector('[data-pos-line-name]').textContent = line.name;
    dialog.querySelector('[data-pos-line-qty]').value = line.quantity;
    const priced = line.price != null && Number(line.price) !== 0;
    const priceRow = dialog.querySelector('[data-pos-line-price-row]');
    priceRow.hidden = priced && !data.can_override;
    dialog.querySelector('[data-pos-line-price]').value = line.unit_price || (priced ? '' : '');
    dialog.querySelector('[data-pos-line-price]').placeholder = priced ? money(line.price) : 'Price';
    dialog.querySelector('[data-pos-line-serial-row]').hidden = line.tracking !== 'serial';
    dialog.querySelector('[data-pos-line-serial]').value = line.serial || '';
    if (!dialog.open) dialog.showModal();
    dialog.querySelector(priced || priceRow.hidden ? '[data-pos-line-qty]' : '[data-pos-line-price]').select();
  }
  function closeLine(keep) {
    const dialog = $('[data-pos-line-dialog]');
    if (editing && keep) {
      const q = dialog.querySelector('[data-pos-line-qty]').value.trim();
      if (Number(q) > 0) editing.quantity = q;
      const price = dialog.querySelector('[data-pos-line-price]').value.trim();
      editing.unit_price = /^\d+(\.\d{1,2})?$/.test(price) ? price : null;
      editing.serial = dialog.querySelector('[data-pos-line-serial]').value.trim() || null;
    }
    editing = null;
    if (dialog.open) dialog.close();
    schedule();
  }

  // ---- the customer chip
  let customerTimer = null;
  async function searchCustomers() {
    const q = $('[data-pos-customer-q]').value.trim();
    const list = $('[data-pos-customer-results]');
    if (q.length < 3) { list.innerHTML = '<li class="pos-empty">Type at least 3 characters.</li>'; return; }
    try {
      const found = await api('pos customer search', {q});
      list.innerHTML = found.items.map(c => `<li><button type="button" data-pos-choose="${escape(c.id)}" data-name="${escape(c.name)}"><b>${escape(c.name)}</b>${c.phone ? ` · ${escape(c.phone)}` : ''}${c.email ? ` · ${escape(c.email)}` : ''}</button></li>`).join('')
        || '<li class="pos-empty">No customer matches.</li>';
    } catch (error) { $('[data-pos-customer-status]').textContent = error.message; }
  }
  function choose(customer) {
    state.customer = customer;
    $('[data-pos-customer-dialog]').close();
    schedule();   // re-priced with their price level and tax code
  }

  // ---- taking payment
  let method = null, tendered = '';
  const places = () => ((server.total.amount.split('.')[1]) || '').length;
  const minor = text => Math.round(Number(text || 0) * 10 ** places());
  const amountText = cents => (cents / 10 ** places()).toFixed(places());
  function quickAmounts(total) {
    const cents = minor(total), unit = 10 ** places();
    const out = [cents, Math.ceil(cents / unit) * unit];
    for (const step of [5, 10, 20, 50, 100]) out.push((Math.floor(cents / (step * unit)) + 1) * step * unit);
    return [...new Set(out)].filter(x => x >= cents).sort((a, b) => a - b);
  }
  function openTender() {
    if (!server || !state.lines.length || state.pending) return;
    const dialog = $('[data-pos-tender]');
    dialog.querySelector('[data-pos-tender-total]').textContent = money(server.total.amount);
    dialog.querySelector('[data-pos-methods]').innerHTML = register.methods.map(m => `<button type="button" data-pos-method="${escape(m.id)}" data-kind="${escape(m.kind)}">${escape(m.name)}</button>`).join('');
    dialog.querySelector('[data-pos-quick]').innerHTML = quickAmounts(server.total.amount).map((cents, i) => `<button type="button" data-pos-quick-amount="${amountText(cents)}">${i === 0 ? 'Exact' : money(amountText(cents))}</button>`).join('');
    dialog.querySelector('[data-pos-tender-status]').textContent = '';
    pick(register.methods[0]);
    if (!dialog.open) dialog.showModal();
  }
  function pick(m) {
    method = m || null;
    const dialog = $('[data-pos-tender]');
    dialog.querySelectorAll('[data-pos-method]').forEach(b => b.setAttribute('aria-pressed', String(!!m && b.dataset.posMethod === m.id)));
    const cash = !!m && m.kind === 'cash';
    dialog.querySelector('[data-pos-cash]').hidden = !cash;
    dialog.querySelector('[data-pos-reference-row]').hidden = !m || cash;
    tendered = '';
    dialog.querySelector('[data-pos-tendered]').value = '';
    change();
  }
  function change() {
    const dialog = $('[data-pos-tender]');
    const cash = method && method.kind === 'cash';
    const due = minor(server.total.amount), given = minor(tendered);
    const covers = !cash || (tendered !== '' && given >= due);
    dialog.querySelector('[data-pos-change-due]').textContent = cash && tendered !== '' && given >= due ? money(amountText(given - due)) : '—';
    dialog.querySelector('[data-pos-complete]').disabled = !method || !covers;
  }
  async function complete(body) {
    const dialog = $('[data-pos-tender]');
    const status = dialog.querySelector('[data-pos-tender-status]');
    state.pending = body;
    save();
    render();
    status.textContent = 'Completing…';
    let sale;
    try {
      sale = await api('pos sale complete', body);
      if (!sale || !sale.sales_receipt || !sale.tenders || !sale.total) {
        const failure = new Error('The host did not confirm this sale.');
        failure.offline = true;
        throw failure;
      }
    } catch (error) {
      if (error.offline) {
        offline = true;
        status.textContent = 'No connection to Bookflow. This sale is kept here; finish it when the connection returns.';
        render();
        return;
      }
      state.pending = null;
      save();
      status.textContent = error.message;
      if (error.code === 'E_PREVIEW_STALE') schedule();
      render();
      return;
    }
    offline = false;
    if (dialog.open) dialog.close();
    done(sale);
  }
  function done(sale) {
    state = fresh();
    server = null;
    save();
    render();
    root.classList.remove('pos-sheet-open');
    const screen = $('[data-pos-done]');
    screen.querySelector('[data-pos-done-change]').textContent = money(sale.change.amount);
    const paid = sale.tenders.map(t => `${escape(t.payment_method.name)} ${money(t.amount.amount)}${t.reference ? ` (${escape(t.reference)})` : ''}`).join(', ');
    screen.querySelector('[data-pos-done-summary]').innerHTML = `Sales receipt <b data-pos-done-number>${escape(sale.sales_receipt.name)}</b> · Total ${money(sale.total.amount)} · Paid ${paid}${sale.customer ? ` · ${escape(sale.customer.name)}` : ''}${sale.replayed ? ' · already completed' : ''}`;
    screen.dataset.sale = sale.id;
    screen.hidden = false;
    screen.querySelector('[data-pos-new-sale]').focus();
  }
  function retry() {
    if (state.pending) complete(state.pending);
  }

  // ---- lookup: every active sales item, by name, SKU, barcode and description
  const scanInput = root.querySelector('[data-scan-input]');
  function lookup() {
    const q = scanInput.value.trim().toLocaleLowerCase();
    const list = $('[data-pos-results]');
    if (!q || /^\d+(\.\d+)?\s*\*\s*$/.test(q)) { list.hidden = true; list.innerHTML = ''; return; }
    const text = q.replace(/^\d+(\.\d+)?\s*\*\s*/, '');
    const found = data.items.filter(item => [item.name, item.sku, item.barcode, item.description]
      .some(v => v && v.toLocaleLowerCase().includes(text))).slice(0, 30);
    list.innerHTML = found.map(item => `<li><button type="button" data-pos-result="${escape(item.id)}">${item.picture ? `<img src="${escape(item.picture)}" alt="" loading="lazy">` : `<span class="pos-tile" style="background:${colour(item.name)}"></span>`}<span class="pos-box-name">${escape(item.name)}</span><span class="pos-box-price">${item.price == null ? '' : money(item.price)}</span><span class="pos-onhand">${item.on_hand == null ? '' : escape(item.on_hand) + ' on hand'}</span></button></li>`).join('')
      || '<li class="pos-empty">No sales item matches.</li>';
    list.hidden = false;
  }

  // ---- events
  root.addEventListener('click', event => {
    const target = event.target.closest('button, [data-pos-box-item]');
    if (!target) return;
    const d = target.dataset;
    if (d.posBoxItem) { const item = items.get(d.posBoxItem); if (item) add(item, '1'); }
    else if (d.posBoxGroup) { path.push(d.posBoxGroup); drawBoxes(); }
    else if (d.posCrumb !== undefined) { path = path.slice(0, Number(d.posCrumb) + 1); drawBoxes(); }
    else if (d.posResult) {
      const item = items.get(d.posResult);
      const quantity = (/^(\d+(?:\.\d+)?)\s*\*/.exec(scanInput.value) || [])[1] || '1';
      if (item) add(item, quantity);
      scanInput.value = '';
      lookup();
    }
    else if (d.posLine !== undefined) { if (!state.pending) openLine(state.lines[Number(d.posLine)]); }
    else if (target.matches('[data-pos-qty-step]')) {
      const input = $('[data-pos-line-qty]');
      input.value = String(Math.max(1, (Number(input.value) || 0) + Number(d.posQtyStep)));
    }
    else if (target.matches('[data-pos-line-remove]')) {
      if (editing) state.lines.splice(state.lines.indexOf(editing), 1);
      editing = null;
      $('[data-pos-line-dialog]').close();
      schedule();
    }
    else if (target.matches('[data-pos-line-done]')) { event.preventDefault(); closeLine(true); }
    else if (target.matches('[data-pos-clear]')) { if (!state.pending && state.lines.length) { state = fresh(); schedule(); } }
    else if (target.matches('[data-pos-charge]')) openTender();
    else if (target.matches('[data-pos-bar]')) root.classList.toggle('pos-sheet-open');
    else if (target.matches('[data-pos-sheet-close]')) root.classList.remove('pos-sheet-open');
    else if (target.matches('[data-pos-customer]')) {
      if (state.pending) return;
      const dialog = $('[data-pos-customer-dialog]');
      $('[data-pos-customer-status]').textContent = '';
      dialog.showModal();
      $('[data-pos-customer-q]').focus();
    }
    else if (d.posChoose) choose({id: d.posChoose, name: d.name});
    else if (target.matches('[data-pos-customer-clear]')) choose(null);
    else if (target.matches('[data-pos-customer-add]')) {
      const name = $('[data-pos-new-name]').value.trim();
      if (!name) { $('[data-pos-customer-status]').textContent = 'Type the customer’s name.'; return; }
      api('pos customer add', {name, phone: $('[data-pos-new-phone]').value.trim() || null,
                               email: $('[data-pos-new-email]').value.trim() || null})
        .then(made => choose({id: made.id, name: made.name}))
        .catch(error => { $('[data-pos-customer-status]').textContent = error.message; });
    }
    else if (d.posMethod) pick(register.methods.find(m => m.id === d.posMethod));
    else if (d.posQuickAmount) { tendered = d.posQuickAmount; $('[data-pos-tendered]').value = tendered; change(); }
    else if (d.posKey) {
      const input = $('[data-pos-tendered]');
      input.value = d.posKey === '⌫' ? input.value.slice(0, -1) : input.value + d.posKey;
      tendered = input.value; change();
    }
    else if (target.matches('[data-pos-complete]')) {
      if (!server || !method) return;
      const tender = {payment_method: method.id};
      if (method.kind === 'cash') tender.amount = amountText(minor(tendered));
      const reference = $('[data-pos-reference]').value.trim();
      if (method.kind !== 'cash' && reference) tender.reference = reference;
      complete({...cartInput(), ticket: state.ticket, tender, expected_total: server.total.amount});
    }
    else if (target.matches('[data-pos-retry]')) retry();
    else if (target.matches('[data-pos-new-sale]')) { $('[data-pos-done]').hidden = true; scanInput.focus(); }
  });
  $('[data-pos-tendered]').addEventListener('input', event => { tendered = event.target.value.trim(); change(); });
  $('[data-pos-customer-q]').addEventListener('input', () => { clearTimeout(customerTimer); customerTimer = setTimeout(searchCustomers, 200); });
  $('[data-pos-line-dialog]').addEventListener('close', () => { if (editing) closeLine(false); });
  scanInput.addEventListener('input', lookup);
  scanInput.addEventListener('keydown', event => { if (event.key === 'Escape') { scanInput.value = ''; lookup(); } });
  const select = root.querySelector('[data-pos-register-select]');
  if (select) {
    select.value = register.id;
    select.addEventListener('change', () => {
      save();
      register = data.registers.find(r => r.id === select.value) || register;
      store.set(registerKey, register.id);
      state = store.get(cartKey()) || fresh();
      schedule();
    });
  }
  window.addEventListener('online', () => { if (offline) { offline = false; schedule(); } });

  // The Scan field's "pos" mode (scan.js) rings a found code up here: the same path for a scanner and the camera.
  window.bookflowPos = {
    scanned(answer, want) {
      if (state.pending) return false;
      const known = items.get(answer.item_id) || {id: answer.item_id, name: answer.item_name, price: null, tracking: 'none'};
      const serial = Array.isArray(answer.serial_numbers) ? answer.serial_numbers[0] : null;
      const line = add(known, want.quantity, serial);
      scanInput.value = '';
      lookup();
      return line.quantity;
    },
    state: () => JSON.parse(JSON.stringify(state)),
    server: () => server,
  };

  drawBoxes();
  render();
  if (state.lines.length && !state.pending) schedule();
})();
