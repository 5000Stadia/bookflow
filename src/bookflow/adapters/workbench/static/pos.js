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
  const fresh = () => ({ticket: newTicket(), customer: null, lines: [], pending: null, ticket_version: null, mutation: null});
  let state = store.get(cartKey()) || fresh();
  let server = null;      // the last preview answer for the cart as it stands
  let offline = false;    // the last request found no host
  let seq = 0, timer = null;
  const save = () => store.set(cartKey(), state);
  const locked = () => !!(state.pending || state.mutation || state.stale_ticket);
  let mutationBusy = false, heldTickets = data.held || [], lastSale = null;

  // ---- talking to the host: the command API, as the signed-in person
  async function api(name, body, options = {}) {
    let response;
    try {
      response = await fetch(`/companies/${encodeURIComponent(data.company)}/commands/${name.replaceAll(' ', '.')}`, {
        method: 'POST', credentials: 'same-origin', body: JSON.stringify(body),
        headers: {'Content-Type': 'application/json', 'X-Bookflow-Workbench': '1', 'X-Bookflow-Client-Name': 'bookflow-workbench',
          ...(options.key ? {'Idempotency-Key': options.key} : {}),
          ...(options.reason ? {'X-Bookflow-Reason': options.reason} : {})}});
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
    if (line.serial) out.serial_numbers = line.serial.split('\n').map(value => value.trim()).filter(Boolean);
    return out;
  };
  const cartInput = () => ({pos_register: register.id, customer: state.customer ? state.customer.id : null,
                            lines: state.lines.map(lineInput)});

  function schedule() {
    server = null;
    seq++;
    save();
    render();
    clearTimeout(timer);
    if (state.lines.length && !locked()) timer = setTimeout(preview, 250);
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
    if (locked()) return null;
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

  // ---- shared layout; item data stays local so drawing and ringing up never wait on the host
  let path = [], layoutEditing = false, arranged = null, layoutTail = Promise.resolve();
  const currentGroup = () => path.length ? path[path.length - 1] : null;
  const boxById = id => data.layout.items.find(box => box.id === id);
  const children = parent => data.layout.items.filter(box => box.parent === parent).sort((a, b) => a.position - b.position);
  const groupName = box => box.label || 'Group';
  function pictureHtml(url, name, shade) {
    return url ? `<img class="pos-tile" src="${escape(url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pos-tile',textContent:''}))">`
      : `<span class="pos-tile" style="background:${/^#[0-9a-f]{6}$/i.test(shade || '') ? shade : colour(name)}">${escape(name.slice(0, 2))}</span>`;
  }
  function boxHtml(box) {
    const group = box.kind === 'group', item = group ? null : items.get(box.item);
    if (!group && (box.inactive || !item)) {
      if (!layoutEditing) return '';
      const name = box.label || (item && item.name) || box.item;
      return `<div class="pos-box-wrap" role="listitem" data-pos-box-id="${escape(box.id)}"><button type="button" class="pos-box" data-pos-arrange="${escape(box.id)}"><span class="pos-box-name">${escape(name)}</span><span>inactive item — remove or replace</span></button></div>`;
    }
    const name = box.label || (group ? 'Group' : item.name);
    const picture = group ? (box.picture && box.picture.url) : item.picture;
    const attr = group ? `data-pos-box-group="${escape(name)}" data-pos-group-id="${escape(box.id)}"` : `data-pos-box-item="${escape(item.id)}"`;
    const onhand = !group && item.on_hand != null ? `${escape(item.on_hand)} on hand` : '';
    return `<div class="pos-box-wrap" role="listitem" data-pos-box-id="${escape(box.id)}"${layoutEditing ? ' draggable="true"' : ''}><button type="button" class="pos-box${group ? ' pos-box-group' : ''}" ${attr}>${pictureHtml(picture, name, box.colour)}<span class="pos-box-name">${escape(name)}</span><span class="pos-box-price">${group ? 'Group' : item.price == null ? '' : money(item.price)}</span><span class="pos-onhand">${onhand}</span></button>${layoutEditing ? `<button type="button" class="pos-arrange" data-pos-arrange="${escape(box.id)}" aria-label="Arrange ${escape(name)}">Arrange</button>` : ''}</div>`;
  }
  function drawBoxes() {
    path = path.filter(id => boxById(id));
    const group = boxById(currentGroup()), style = group ? group.view_style : data.layout.home_view_style;
    const grid = $('[data-pos-boxes]');
    grid.className = `pos-boxes pos-view-${style || 'block'}`;
    grid.innerHTML = children(currentGroup()).map(boxHtml).join('') || '<p class="pos-empty">No sales items here yet.</p>';
    const crumbs = [`<button type="button" data-pos-crumb="-1"${path.length ? '' : ' aria-current="page"'}>Home</button>`];
    path.forEach((id, i) => crumbs.push(`<span aria-hidden="true">›</span><button type="button" data-pos-crumb="${i}"${i === path.length - 1 ? ' aria-current="page"' : ''}>${escape(groupName(boxById(id)))}</button>`));
    if (path.length) crumbs.push(`<button type="button" class="pos-back" data-pos-crumb="${path.length - 2}">Back</button>`);
    $('[data-pos-crumbs]').innerHTML = crumbs.join('');
    root.querySelectorAll('[data-pos-style]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.posStyle === (style || 'block'))));
    if (data.can_layout) {
      $('[data-pos-edit]').setAttribute('aria-pressed', String(layoutEditing));
      $('[data-pos-edit]').textContent = layoutEditing ? 'Done editing' : 'Edit layout';
      $('[data-pos-editor-tools]').hidden = !layoutEditing;
      $('[data-pos-seed-options]').hidden = data.layout.initialized;
      root.classList.toggle('pos-editing', layoutEditing);
    }
  }
  function acceptLayout(answer) {
    // A delayed read or picture response must never replace a newer saved revision.
    if (answer.version < data.layout.version) return;
    data.layout = answer;
    drawBoxes();
  }
  function layoutStatus(message) {
    if ($('[data-pos-layout-status]')) $('[data-pos-layout-status]').textContent = message;
    if ($('[data-pos-box-status]')) $('[data-pos-box-status]').textContent = message;
  }
  // One in-flight change, with the revision received from its predecessor. A refusal reloads server state;
  // the person sees it and chooses again instead of overwriting another device's changes.
  function layoutWrite(command, body) {
    const work = layoutTail.catch(() => {}).then(() => new Promise((resolve, reject) => {
      const form = $('[data-pos-layout-form]');
      layoutStatus('Saving…');
      form.elements.command.value = command;
      form.elements.body.value = JSON.stringify({...body, expected_version: data.layout.version});
      async function finished(event) {
        if (event.target !== form) return;
        form.removeEventListener('htmx:afterRequest', finished);
        let answer;
        try { answer = JSON.parse(event.detail.xhr.responseText); } catch (_) {}
        if (event.detail.successful && answer && Array.isArray(answer.items)) {
          acceptLayout(answer); layoutStatus('Saved.'); resolve(answer);
        } else {
          const message = answer && (answer.message || answer.error && answer.error.message) || 'The layout was not saved. Check your connection and try again.';
          try { acceptLayout(await api('pos-box list', {})); } catch (_) { /* keep the last known layout */ }
          layoutStatus(message); reject(new Error(message));
        }
      }
      form.addEventListener('htmx:afterRequest', finished);
      form.requestSubmit();
    }));
    layoutTail = work;
    return work;
  }
  function addBoxItem(item) {
    if (!item.box) { layoutStatus('This item can be rung up from lookup, but cannot be a tap box.'); return Promise.resolve(false); }
    return layoutWrite('create', {kind: 'item', parent: currentGroup(), item: item.id}).then(() => '1', () => false);
  }
  function openBox(box) {
    arranged = box || null;
    const dialog = $('[data-pos-box-dialog]');
    $('[data-pos-box-title]').textContent = box ? 'Arrange box' : 'Add group';
    $('[data-pos-box-label]').value = box ? box.label || (items.get(box.item) || {}).name || '' : '';
    $('[data-pos-box-colour]').value = box && box.colour || '#2e7d6b';
    $('[data-pos-colour-row]').hidden = !!box && box.kind !== 'group';
    const descendants = new Set(box ? [box.id] : []);
    let changed = true;
    while (changed) { changed = false; data.layout.items.forEach(b => { if (descendants.has(b.parent) && !descendants.has(b.id)) { descendants.add(b.id); changed = true; } }); }
    function fullName(b) {
      const names = [groupName(b)]; let parent = boxById(b.parent);
      while (parent) { names.unshift(groupName(parent)); parent = boxById(parent.parent); }
      return names.join(' › ');
    }
    const pick = $('[data-pos-box-parent]');
    pick.innerHTML = '<option value="">Home</option>' + data.layout.items.filter(b => b.kind === 'group' && !descendants.has(b.id))
      .map(b => `<option value="${escape(b.id)}">${escape(fullName(b))}</option>`).join('');
    pick.value = (box ? box.parent : currentGroup()) || '';
    $('[data-pos-box-position]').value = box ? box.position + 1 : children(currentGroup()).length + 1;
    $('[data-pos-box-remove]').hidden = !box;
    const pictureForm = $('[data-pos-picture-form]');
    pictureForm.hidden = !box || box.kind !== 'group';
    pictureForm.elements.pos_box.value = box ? box.id : '';
    pictureForm.elements.expected_version.value = data.layout.version;
    pictureForm.elements.picture.value = '';
    $('[data-pos-picture-clear]').hidden = !box || !box.picture;
    $('[data-pos-box-status]').textContent = '';
    dialog.showModal();
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
    const pending = locked();
    const confirmed = !!server && state.lines.length > 0;
    const waiting = state.lines.length && !server ? (offline ? 'not confirmed' : 'updating') : '—';
    $('[data-pos-subtotal]').textContent = confirmed ? money(server.subtotal.amount) : waiting;
    $('[data-pos-tax]').textContent = confirmed ? money(server.tax.amount) : waiting;
    $('[data-pos-total]').textContent = confirmed ? money(server.total.amount) : waiting;
    $('[data-pos-total]').dataset.state = confirmed ? 'confirmed' : (state.lines.length ? (offline ? 'offline' : 'updating') : 'empty');
    $('[data-pos-charge]').disabled = !confirmed || pending;
    $('[data-pos-hold]').disabled = !state.lines.length || pending;
    $('[data-pos-clear]').disabled = pending;
    const registerSelect = $('[data-pos-register-select]');
    if (registerSelect) registerSelect.disabled = pending;
    const count = units();
    $('[data-pos-bar-count]').textContent = `${count} ${count === 1 ? 'item' : 'items'}`;
    $('[data-pos-bar-total]').textContent = confirmed ? money(server.total.amount) : (state.lines.length ? waiting : money(0));
    $('[data-pos-customer]').textContent = state.customer ? state.customer.name : 'No customer';
    $('[data-pos-customer]').classList.toggle('chosen', !!state.customer);
    if (offline) say('No connection to Bookflow. This sale is kept here; finish it when the connection returns.', true);
    else if ($('[data-pos-connection]').dataset.kind === 'error' && confirmed) say('');
    const banner = $('[data-pos-pending]');
    banner.hidden = !pending;
    if (state.stale_ticket) banner.innerHTML = `This ticket changed on the host. This device’s copy cannot change it. <button type="button" data-pos-forget>Forget this device copy</button> keeps the host’s ticket unchanged.`;
    else if (state.mutation) banner.innerHTML = `This ticket change has not been confirmed. <button type="button" data-pos-ticket-retry>Finish ticket change</button>. The cart is kept here until Bookflow answers.`;
    else if (pending) banner.innerHTML = `This sale was sent to be completed and no answer came back. <button type="button" data-pos-retry>Finish the sale</button> sends it again; it is never posted twice.`;
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
    if (!server || !state.lines.length || locked()) return;
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
      if (['E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'].includes(error.code)) state.stale_ticket = true;
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
    lastSale = sale;
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
    const voidButton = $('[data-pos-void-last]');
    if (voidButton) voidButton.disabled = false;
    screen.hidden = false;
    screen.querySelector('[data-pos-new-sale]').focus();
  }
  function retry() {
    if (state.pending) complete(state.pending);
  }

  // ---- company-held tickets; uncertain writes keep their exact request and retry key on this device
  function drawHeld() {
    $('[data-pos-held]').textContent = `Held (${heldTickets.length})`;
    $('[data-pos-held-list]').innerHTML = heldTickets.map(t => `<li><b>${escape(t.label)}</b><span>${escape(t.pos_register.name)} · ${escape(t.clerk_name || t.clerk_id)} · ${money(t.total.amount)}</span><small>${escape(new Date(t.updated_at || t.created_at).toLocaleString())}</small><div><button type="button" data-pos-resume="${escape(t.id)}">Resume</button><button type="button" data-pos-discard="${escape(t.id)}">Discard</button></div></li>`).join('') || '<li>No held sales.</li>';
  }
  async function refreshHeld() {
    $('[data-pos-held-status]').textContent = 'Refreshing…';
    try {
      heldTickets = (await api('pos ticket list', {})).items;
      drawHeld();
      $('[data-pos-held-status]').textContent = '';
    } catch (error) { $('[data-pos-held-status]').textContent = error.message; }
  }
  function resumeTicket(id) {
    const ticket = heldTickets.find(t => t.id === id);
    if (!ticket || locked()) return;
    const destination = data.registers.find(r => r.id === ticket.pos_register.id);
    if (!destination) { $('[data-pos-held-status]').textContent = 'Reload to use this ticket’s register.'; return; }
    const other = store.get(`bookflow.pos.${data.company}.${destination.id}.cart`);
    if (state.lines.length || state.ticket_version || (destination.id !== register.id && other && (other.lines.length || other.ticket_version || other.pending || other.mutation))) {
      $('[data-pos-held-status]').textContent = 'Hold or clear the sale on this device and the ticket’s register before resuming.';
      return;
    }
    ticketMutation('resume', {ticket: id, expected_version: ticket.version});
  }
  function ticketMutation(action, body, extra = {}) {
    if (locked()) return;
    state.mutation = {action, body, key: newTicket(), ...extra};
    save();
    render();
    finishMutation();
  }
  async function finishMutation() {
    if (!state.mutation || mutationBusy) return;
    mutationBusy = true;
    const request = state.mutation;
    try {
      const answer = await api('pos ticket ' + request.action, request.body, {key: request.key});
      if (!answer || !answer.id || !answer.status) {
        const error = new Error('Bookflow did not confirm the ticket change.'); error.offline = true; throw error;
      }
      state.mutation = null;
      if (request.action === 'resume') {
        save();
        register = data.registers.find(r => r.id === answer.pos_register.id) || register;
        store.set(registerKey, register.id);
        const select = $('[data-pos-register-select]');
        if (select) select.value = register.id;
        state = {...fresh(), ticket: answer.id, ticket_version: answer.version, label: answer.label,
          customer: answer.customer, lines: answer.lines.map(line => {
            const item = items.get(line.item) || {};
            return {key: newTicket(), item_id: line.item, name: item.name || line.item, price: item.price,
              quantity: line.quantity, unit_price: line.unit_price, serial: (line.serial_numbers || []).join('\n') || null,
              tracking: item.tracking || 'none', attributes: line.attributes || {}};
          })};
        $('[data-pos-held-dialog]').close();
        root.classList.add('pos-sheet-open');
      } else if (request.action === 'hold' || request.clear) {
        state = fresh();
        $('[data-pos-hold-dialog]').close();
        root.classList.remove('pos-sheet-open');
      }
      offline = false;
      schedule();
      refreshHeld();
    } catch (error) {
      if (!error.offline) state.mutation = null;
      if (request.body.ticket === state.ticket && ['E_VERSION_CONFLICT', 'E_WORK_DEPENDENCY'].includes(error.code)) state.stale_ticket = true;
      save();
      render();
      const message = error.offline ? 'No confirmation yet. Your cart is kept here; use Finish ticket change to retry.' : error.message;
      $('[data-pos-hold-status]').textContent = message;
      $('[data-pos-held-status]').textContent = message;
      say(message, true);
    } finally { mutationBusy = false; }
  }
  $('[data-pos-hold-form]').addEventListener('submit', event => {
    event.preventDefault();
    if (locked() || !state.lines.length) return;
    ticketMutation('hold', {...cartInput(), ticket: state.ticket, label: $('[data-pos-hold-label]').value.trim() || null,
      ...(state.ticket_version ? {expected_version: state.ticket_version} : {})});
  });

  // ---- voids use the same receipt reversal, with an explicit reason and an explicit retry after any conflict
  let voidVersion = 1, voidSelector = '', voidBusy = false, voidKey = null;
  function openVoid(sale) {
    voidVersion = 1; voidSelector = sale ? sale.sales_receipt.name : ''; voidKey = null;
    $('[data-pos-void-sale]').value = voidSelector;
    $('[data-pos-void-reason]').value = '';
    $('[data-pos-void-status]').textContent = '';
    $('[data-pos-void-dialog]').showModal();
  }
  if (data.can_void) $('[data-pos-void-form]').addEventListener('submit', async event => {
    event.preventDefault();
    if (voidBusy) return;
    const sale = $('[data-pos-void-sale]').value.trim(), reason = $('[data-pos-void-reason]').value.trim();
    if (!sale || !reason) return;
    if (sale !== voidSelector) { voidVersion = 1; voidSelector = sale; voidKey = null; }
    voidBusy = true; $('[data-pos-void-submit]').disabled = true;
    const status = $('[data-pos-void-status]');
    status.textContent = 'Voiding…';
    try {
      await api('pos sale void', {sale, expected_version: voidVersion}, {reason, key: voidKey || (voidKey = newTicket())});
      status.textContent = `Sale ${sale} voided. Its reversal and reason are recorded.`;
      if (lastSale && sale === lastSale.sales_receipt.name) {
        $('[data-pos-void-last]').disabled = true;
        $('[data-pos-done-summary]').textContent = `Sales receipt ${sale} — voided`;
      }
    } catch (error) {
      status.textContent = error.message;
      if (error.code === 'E_VERSION_CONFLICT' && error.details && error.details.current_version) {
        voidVersion = error.details.current_version;
        status.textContent += ' Review the changed receipt before choosing Void sale again.';
      }
      if (!error.offline) voidKey = null;
    } finally { voidBusy = false; $('[data-pos-void-submit]').disabled = false; }
  });

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
    if (d.posBoxItem) { const item = items.get(d.posBoxItem); if (layoutEditing) openBox(boxById(target.closest('[data-pos-box-id]').dataset.posBoxId)); else if (item) add(item, '1'); }
    else if (d.posBoxGroup) { path.push(d.posGroupId); drawBoxes(); }
    else if (d.posCrumb !== undefined) { path = path.slice(0, Number(d.posCrumb) + 1); drawBoxes(); }
    else if (d.posResult) {
      const item = items.get(d.posResult);
      const quantity = (/^(\d+(?:\.\d+)?)\s*\*/.exec(scanInput.value) || [])[1] || '1';
      if (item) { if (layoutEditing) addBoxItem(item); else add(item, quantity); }
      scanInput.value = '';
      lookup();
    }
    else if (d.posStyle) { layoutWrite('view', {pos_box: currentGroup(), style: d.posStyle}).catch(() => {}); }
    else if (target.matches('[data-pos-edit]')) { layoutEditing = !layoutEditing; drawBoxes(); }
    else if (d.posSeed) { layoutWrite('seed', {mode: d.posSeed}).catch(() => {}); }
    else if (d.posArrange) openBox(boxById(d.posArrange));
    else if (target.matches('[data-pos-add-group]')) openBox(null);
    else if (target.matches('[data-pos-add-item]')) { scanInput.focus(); layoutStatus('Look up an item above, then tap it to add its box here.'); }
    else if (target.matches('[data-pos-box-cancel]')) $('[data-pos-box-dialog]').close();
    else if (target.matches('[data-pos-box-remove]') && arranged) {
      const box = arranged;
      if (box.kind === 'group' && children(box.id).length && !confirm('Remove this group and its boxes? Inventory items will be kept.')) return;
      layoutWrite('remove', {pos_box: box.id}).then(() => $('[data-pos-box-dialog]').close()).catch(() => {});
    }
    else if (target.matches('[data-pos-picture-clear]') && arranged) {
      layoutWrite('update', {pos_box: arranged.id, picture_attachment: null}).then(() => {
        $('[data-pos-box-dialog]').close(); openBox(boxById(arranged.id));
      }).catch(() => {});
    }
    else if (d.posLine !== undefined) { if (!locked()) openLine(state.lines[Number(d.posLine)]); }
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
    else if (target.matches('[data-pos-clear]')) {
      if (locked() || (!state.lines.length && !state.ticket_version)) return;
      if (state.ticket_version) {
        if (confirm('Discard this resumed sale? Nothing has been posted.')) ticketMutation('discard', {ticket: state.ticket, expected_version: state.ticket_version}, {clear: true});
      } else { state = fresh(); schedule(); }
    }
    else if (target.matches('[data-pos-hold]')) {
      if (locked() || !state.lines.length) return;
      $('[data-pos-hold-label]').value = state.label || '';
      $('[data-pos-hold-status]').textContent = '';
      $('[data-pos-hold-dialog]').showModal();
    }
    else if (target.matches('[data-pos-held]')) { $('[data-pos-held-dialog]').showModal(); refreshHeld(); }
    else if (target.matches('[data-pos-held-refresh]')) refreshHeld();
    else if (d.posResume) resumeTicket(d.posResume);
    else if (d.posDiscard) {
      const ticket = heldTickets.find(t => t.id === d.posDiscard);
      if (ticket && !locked() && confirm(`Discard “${ticket.label}”? Nothing has been posted.`))
        ticketMutation('discard', {ticket: ticket.id, expected_version: ticket.version});
    }
    else if (target.matches('[data-pos-ticket-retry]')) finishMutation();
    else if (target.matches('[data-pos-forget]')) {
      if (state.stale_ticket && confirm('Forget only this device’s copy? The ticket on the host will stay unchanged.')) { state = fresh(); schedule(); }
    }
    else if (d.posClose) $('[data-pos-' + d.posClose + '-dialog]').close();
    else if (target.matches('[data-pos-void-open]')) openVoid(null);
    else if (target.matches('[data-pos-void-last]')) openVoid(lastSale);
    else if (target.matches('[data-pos-charge]')) openTender();
    else if (target.matches('[data-pos-bar]')) root.classList.toggle('pos-sheet-open');
    else if (target.matches('[data-pos-sheet-close]')) root.classList.remove('pos-sheet-open');
    else if (target.matches('[data-pos-customer]')) {
      if (locked()) return;
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
      complete({...cartInput(), ticket: state.ticket, tender, expected_total: server.total.amount,
                ...(state.ticket_version ? {ticket_version: state.ticket_version} : {})});
    }
    else if (target.matches('[data-pos-retry]')) retry();
    else if (target.matches('[data-pos-new-sale]')) { $('[data-pos-done]').hidden = true; scanInput.focus(); }
  });

  if (data.can_layout) {
    $('[data-pos-box-editor]').addEventListener('submit', async event => {
      event.preventDefault();
      const box = arranged, label = $('[data-pos-box-label]').value.trim();
      const parent = $('[data-pos-box-parent]').value || null;
      const position = Math.max(0, Number($('[data-pos-box-position]').value) - 1);
      const shade = $('[data-pos-box-colour]').value;
      try {
        if (!box) await layoutWrite('create', {kind: 'group', label, colour: shade, parent, position});
        else {
          await layoutWrite('update', {pos_box: box.id, label, ...(box.kind === 'group' ? {colour: shade} : {})});
          if (box.parent !== parent || box.position !== position) await layoutWrite('move', {pos_box: box.id, parent, position});
        }
        $('[data-pos-box-dialog]').close();
      } catch (_) { /* the visible status carries the refusal */ }
    });
    const pictureForm = $('[data-pos-picture-form]');
    pictureForm.addEventListener('htmx:configRequest', event => {
      event.detail.parameters.expected_version = data.layout.version;
      layoutStatus('Saving picture…');
    });
    pictureForm.addEventListener('htmx:afterRequest', event => {
      if (event.target !== pictureForm) return;
      let answer;
      try { answer = JSON.parse(event.detail.xhr.responseText); } catch (_) {}
      if (event.detail.successful && answer && Array.isArray(answer.items)) {
        acceptLayout(answer); layoutStatus('Picture saved.');
        $('[data-pos-box-dialog]').close();
      } else layoutStatus(answer && (answer.message || answer.error && answer.error.message) || 'The picture was not saved. Try again.');
    });
    let dragged = null;
    root.addEventListener('dragstart', event => {
      const box = event.target.closest('[data-pos-box-id]');
      if (!layoutEditing || !box) return;
      dragged = box.dataset.posBoxId;
      event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', dragged);
    });
    root.addEventListener('dragover', event => { if (layoutEditing && event.target.closest('[data-pos-box-id]')) event.preventDefault(); });
    root.addEventListener('drop', event => {
      const target = event.target.closest('[data-pos-box-id]');
      if (!layoutEditing || !target || !dragged) return;
      event.preventDefault();
      const to = boxById(target.dataset.posBoxId), from = dragged;
      dragged = null;
      if (to.id !== from) layoutWrite('move', {pos_box: from, parent: currentGroup(), position: to.position}).catch(() => {});
    });
  }

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
      if (locked()) return false;
      const known = items.get(answer.item_id) || {id: answer.item_id, name: answer.item_name, price: null, tracking: 'none'};
      if (layoutEditing) {
        scanInput.value = '';
        lookup();
        return addBoxItem(known);
      }
      const serial = Array.isArray(answer.serial_numbers) ? answer.serial_numbers[0] : null;
      const line = add(known, want.quantity, serial);
      scanInput.value = '';
      lookup();
      return line.quantity;
    },
    state: () => JSON.parse(JSON.stringify(state)),
    server: () => server,
    layout: () => JSON.parse(JSON.stringify(data.layout)),
  };

  drawBoxes();
  drawHeld();
  render();
  if (state.lines.length && !locked()) schedule();
})();
