// Refunds keep their exact pending request until the host confirms it, including after reload.
(() => {
  const root = document.querySelector('[data-pos]');
  const dialog = root && root.querySelector('[data-pos-refund-dialog]');
  if (!dialog) return;
  const $ = selector => root.querySelector(selector);
  const data = JSON.parse($('#pos-data').textContent);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => value == null ? '—' : new Intl.NumberFormat(undefined, {style:'currency',currency:data.currency}).format(Number(value.amount ?? value));
  const key = `bookflow.pos.${data.company}.refund`;
  const freshKey = () => crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2);
  let source = null, lines = [], customer = null, preview = null, pending = null, busy = false, generation = 0;
  let operation = freshKey(), customerTimer;
  try { pending = JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) {}
  const status = text => { $('[data-pos-refund-status]').textContent = text; };
  function keepPending(value) {
    pending = value;
    try { if (value) localStorage.setItem(key, JSON.stringify(value)); else localStorage.removeItem(key); } catch (_) {}
  }
  async function api(name, body, dry = false) {
    let response, out;
    try {
      response = await fetch(`/companies/${encodeURIComponent(data.company)}/commands/${name.replaceAll(' ', '.')}${dry ? '?dry_run=true' : ''}`, {
        method:'POST', credentials:'same-origin', body:JSON.stringify(body),
        headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1','X-Bookflow-Client-Name':'bookflow-workbench',...(name === 'pos refund' ? {'X-Bookflow-Reason':'POS refund'} : {})}});
      out = await response.json();
    } catch (_) { const e = new Error('No confirmation from Bookflow. Retry this refund when the connection returns.'); e.uncertain = true; throw e; }
    if (out === null || typeof out !== 'object' || Array.isArray(out)) {
      const e = new Error('The host did not confirm this refund. Retry the saved request.');
      e.uncertain = true; throw e;
    }
    const code = out.code || out.error?.code;
    if (!response.ok || (typeof code === 'string' && code.startsWith('E_'))) {
      const e = new Error(out.message || out.error?.message || 'The refund was refused.'); e.uncertain = response.status >= 500; throw e;
    }
    return out;
  }
  function invalidate() {
    generation++; preview = null;
    $('[data-pos-refund-total]').textContent = '—';
    $('[data-pos-refund-subtotal]').textContent = '—'; $('[data-pos-refund-tax]').textContent = '—';
    $('[data-pos-refund-complete]').disabled = true;
  }
  function lock() {
    $('[data-pos-refund-editor]').disabled = busy || !!pending;
    $('[data-pos-refund-preview]').disabled = busy || !!pending;
    $('[data-pos-refund-back]').disabled = busy || !!pending;
    $('[data-pos-refund-complete]').disabled = busy || (!pending && !preview);
    $('[data-pos-refund-complete]').textContent = pending ? 'Retry this refund' : 'Complete refund';
  }
  function draw() {
    $('[data-pos-refund-lines]').innerHTML = lines.map((l, i) => `<li><b>${escape(l.item_name)}</b>${source ? `<small>Sold ${escape(l.quantity)} · Available to return ${escape(l.available_quantity)}</small>` : ''}<div class="pos-refund-line-fields"><label>Return quantity <input type="number" min="0" step="any" ${source ? `max="${escape(l.available_quantity)}"` : ''} data-refund-quantity="${i}" value="${escape(l.return_quantity || '0')}" inputmode="decimal"></label>${source ? '' : `<label>Unit price <input data-refund-price="${i}" value="${escape(l.price || '')}" placeholder="Current price" inputmode="decimal"></label>`}</div>${l.tracking === 'serial' ? `<label>Serial numbers <textarea data-refund-serial="${i}" placeholder="One per line">${escape(l.serial || '')}</textarea></label>` : ''}</li>`).join('');
    $('[data-pos-refund-source]').textContent = source ? `Receipt ${source.receipt_number} · ${source.customer?.name || 'No customer'}` : 'Return without a receipt';
    $('[data-pos-refund-unlinked]').hidden = !!source;
    $('[data-pos-refund-customer]').textContent = customer?.name || 'No customer';
    const original = $('[data-pos-refund-settlement] option[value=original]');
    original.disabled = !source;
    original.textContent = source ? `Original payment method (${source.payment_method.name})` : 'Original payment method';
    if (!source && $('[data-pos-refund-settlement]').value === 'original') $('[data-pos-refund-settlement]').value = 'cash';
  }
  function register() {
    const chosen = $('[data-pos-register-select]')?.value;
    return source?.pos_register.id || chosen || data.registers[0].id;
  }
  function payload() {
    const body = {operation_key:operation,pos_register:register(),settlement:$('[data-pos-refund-settlement]').value,
      reference:$('[data-pos-refund-reference]').value.trim() || null,
      lines:lines.filter(l => Number(l.return_quantity) > 0).map(l => {
        const row = {quantity:l.return_quantity};
        if (source) row.source_line = l.source_line;
        else { row.item = l.item_id; if (l.price) row.unit_price = l.price; }
        if (l.serial) row.serial_numbers = l.serial.split('\n').map(x => x.trim()).filter(Boolean);
        return row;
      })};
    if (source) body.sale = source.sale_id;
    else if (customer) body.customer = customer.id;
    return body;
  }
  function open(sale) {
    if (!pending) {
      operation = freshKey(); source = null; lines = []; customer = null; invalidate();
      $('[data-pos-refund-sale]').value = sale || ''; $('[data-pos-refund-reference]').value = '';
      $('[data-pos-refund-settlement]').value = 'cash'; draw(); status('Scan or type a receipt number, or choose No receipt.');
    } else {
      status('This device has a refund awaiting confirmation. Retry it before starting another.');
      $('[data-pos-refund-total]').textContent = money(pending.expected_total);
      $('[data-pos-refund-source]').textContent = pending.sale ? 'Return for receipt ' + pending.sale : 'Return without a receipt';
    }
    lock(); if (!dialog.open) dialog.showModal();
    if (sale && !pending) find(sale);
  }
  async function find(sale) {
    if (busy || pending) return;
    busy = true; source = null; lines = []; customer = null; invalidate(); draw(); lock(); status('Finding receipt…');
    try { source = await api('pos refund lines', {sale}); $('[data-pos-refund-sale]').value = source.receipt_number; lines = source.lines.map(l => ({...l, tracking:l.tracking || data.items.find(i => i.id === l.item_id)?.tracking, return_quantity:'0'})); customer = source.customer; draw(); status('Enter quantities for the lines being returned, then review the total.'); }
    catch (e) { status(e.message); }
    finally { busy = false; lock(); }
  }
  async function review() {
    if (busy || pending) return;
    invalidate(); const mine = generation; const body = payload();
    if (!body.lines.length) { status('Enter a return quantity for at least one line.'); return; }
    busy = true; lock(); status('Calculating refund…');
    try {
      const out = await api('pos refund', body, true);
      if (generation !== mine) return;
      preview = out;
      $('[data-pos-refund-total]').textContent = money(out.total); $('[data-pos-refund-subtotal]').textContent = money(out.subtotal); $('[data-pos-refund-tax]').textContent = money(out.tax);
      status(body.settlement === 'store_credit' ? 'This credit will remain on the customer’s account.' : 'Check the refund total before paying the customer.');
    } catch (e) { status(e.message); }
    finally { busy = false; lock(); }
  }
  async function complete() {
    if (busy || (!pending && !preview)) return;
    if (!pending) keepPending({...payload(),expected_total:preview.total.amount});
    busy = true; lock(); status('Completing refund…');
    try {
      const out = await api('pos refund', pending);
      if (typeof out.id !== 'string' || typeof out.credit_memo?.name !== 'string' ||
          typeof out.total?.amount !== 'string' || !['cash','original','store_credit'].includes(out.settlement)) {
        const failure = new Error('The host did not confirm this refund. Retry the saved request.');
        failure.uncertain = true; throw failure;
      }
      keepPending(null); dialog.close();
      $('[data-pos-done]').hidden = true;
      $('[data-pos-refund-done-label]').textContent = out.settlement === 'store_credit' ? 'Store credit issued' : 'Refund paid';
      $('[data-pos-refund-done-amount]').textContent = money(out.total);
      $('[data-pos-refund-done-summary]').textContent = `Credit memo ${out.credit_memo.name}${out.payment_method ? ' · ' + out.payment_method.name : ''}${out.customer ? ' · ' + out.customer.name : ''}`;
      $('[data-pos-refund-print]').href = `/c/${encodeURIComponent(data.company)}/pos/receipt/${encodeURIComponent(out.id)}?refund=true`;
      $('[data-pos-refund-done]').hidden = false;
    } catch (e) {
      if (!e.uncertain) { keepPending(null); invalidate(); }
      status(e.message);
    } finally { busy = false; lock(); }
  }
  $('[data-pos-refund-find]').addEventListener('submit', e => { e.preventDefault(); find($('[data-pos-refund-sale]').value.trim()); });
  dialog.addEventListener('cancel', e => { if (busy || pending) e.preventDefault(); });
  function receiptChanged(e) {
    if (e.target.matches('[data-pos-refund-sale]') && source) {
      source = null; lines = []; customer = null; draw(); invalidate();
      status('Find the new receipt before choosing lines.');
    }
  }
  // Receipt Scan emits change; typing emits input. Both discard a previously selected sale.
  dialog.addEventListener('change', receiptChanged);
  dialog.addEventListener('input', e => {
    receiptChanged(e);
    if (e.target.matches('[data-refund-quantity]')) lines[Number(e.target.dataset.refundQuantity)].return_quantity = e.target.value;
    if (e.target.matches('[data-refund-price]')) lines[Number(e.target.dataset.refundPrice)].price = e.target.value.trim();
    if (e.target.matches('[data-refund-serial]')) lines[Number(e.target.dataset.refundSerial)].serial = e.target.value;
    invalidate();
  });
  $('[data-pos-refund-item-search]').addEventListener('input', e => {
    const q = e.target.value.trim().toLowerCase();
    const found = q ? data.items.filter(i => i.box && i.type !== 'group' && [i.name,i.sku,i.barcode,i.description].some(v => (v || '').toLowerCase().includes(q))).slice(0,20) : [];
    $('[data-pos-refund-items]').innerHTML = found.map(i => `<li><button type="button" data-refund-add="${escape(i.id)}">${escape(i.name)} · ${money(i.price)}</button></li>`).join('');
  });
  $('[data-pos-refund-customer-search]').addEventListener('input', e => {
    clearTimeout(customerTimer); const q = e.target.value.trim();
    $('[data-pos-refund-customers]').innerHTML = '';
    if (q.length < 3) return;
    customerTimer = setTimeout(async () => {
      try {
        const out = await api('pos customer search',{q});
        if ($('[data-pos-refund-customer-search]').value.trim() !== q) return;
        $('[data-pos-refund-customers]').innerHTML = out.items.map(c => `<li><button type="button" data-refund-customer="${escape(c.id)}" data-name="${escape(c.name)}">${escape(c.name)}</button></li>`).join('');
      } catch (e) { status(e.message); }
    },200);
  });
  root.addEventListener('click', e => {
    const target = e.target.closest('button'); if (!target) return;
    if (target.matches('[data-pos-refund-open]')) open(null);
    else if (target.matches('[data-pos-refund-last]')) open($('[data-pos-done]').dataset.sale);
    else if (target.matches('[data-pos-refund-no-receipt]') && !busy && !pending) { source = null; lines = []; customer = null; invalidate(); draw(); status('Choose the returned items and quantities.'); }
    else if (target.dataset.refundAdd) {
      const item = data.items.find(i => i.id === target.dataset.refundAdd);
      lines.push({item_id:item.id,item_name:item.name,return_quantity:'1',price:'',tracking:item.tracking}); invalidate(); draw();
      $('[data-pos-refund-item-search]').value = ''; $('[data-pos-refund-items]').innerHTML = '';
    } else if (target.dataset.refundCustomer) { customer = {id:target.dataset.refundCustomer,name:target.dataset.name}; invalidate(); draw(); $('[data-pos-refund-customers]').innerHTML = ''; }
    else if (target.matches('[data-pos-refund-customer-clear]')) { customer = null; invalidate(); draw(); }
    else if (target.matches('[data-pos-refund-preview]')) review();
    else if (target.matches('[data-pos-refund-complete]')) complete();
    else if (target.matches('[data-pos-refund-back]') && !busy && !pending) dialog.close();
    else if (target.matches('[data-pos-refund-finish]')) $('[data-pos-refund-done]').hidden = true;
  });
  if (pending) open(null);
})();
