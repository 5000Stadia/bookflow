/* The Pay Bills window: browser presentation over the shared commands, and nothing else.

   Every fact on this page came from `bill query`, `account list`, `payment-method list` or
   `vendor query`, and the one write is `bill pay`. No amount is decided here: a row's default
   payment is the open balance the bill itself reported, and the running total is the sum of
   what is on screen in exact minor units.

   The grouping shown before the save is the command's own rule. `bill pay` writes one payment
   per (vendor, payable account, currency, funding account, method); the funding account, the
   method and the currency are one choice for this whole page, so what splits a selection is the
   vendor and the payable, read off the rows rather than guessed. Selecting two vendors is two
   payments, and the page says so before anything is written rather than after. */
(() => {
  const root = document.querySelector('#pay-bills');
  if (!root || root.dataset.ready) return;
  root.dataset.ready = 'true';
  const exact = window.BookflowExactJSON;
  const config = exact.parse(document.querySelector('#pay-bills-config').textContent);
  const $ = id => document.getElementById('pay-bills-' + id);
  const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
  const link = (text, href) => { const n = el('a', text); n.href = href; return n; };
  const scale = () => {
    const table = JSON.parse(document.querySelector('#math-currencies').textContent);
    return Object.hasOwn(table, config.currency) ? table[config.currency] : 2;
  };
  const units = value => exact.minor(value, config.currency);
  const money = value => units(value) + ' ' + config.currency;

  /* Exact decimal text to minor units. Null means "not a number this currency accepts", which
     is a refusal rather than a rounded guess: no monetary value is ever a float here. */
  function toMinor(text) {
    const trimmed = String(text ?? '').trim();
    if (!/^-?(?:\d+)(?:\.\d+)?$/.test(trimmed)) return null;
    const negative = trimmed.startsWith('-');
    const [whole, fraction = ''] = trimmed.replace('-', '').split('.');
    const places = scale();
    if (fraction.length > places) return null;
    const value = BigInt(whole + fraction.padEnd(places, '0'));
    return negative ? -value : value;
  }

  let candidates = [], selected = new Map(), nextCursor = null, busy = false;
  let vendorId = config.vendor || null, accounts = new Map(), methods = new Map();

  function note(text) { $('message').textContent = text; }

  function showError(err) {
    $('error').hidden = false;
    const message = err.code === 'E_VERSION_CONFLICT'
      ? 'A bill you selected changed while this page was open. Reload the open bills and select again.'
      : err.message;
    $('error').querySelector('p').textContent = [err.code, message].filter(Boolean).join(' — ');
    const area = $('error').querySelector('[data-problems]');
    area.replaceChildren();
    for (const field of err.details?.fields || []) area.append(el('p', `${field.field}: ${field.problem}`));
    if (err.details?.bill_number) area.append(el('p', `Bill ${err.details.bill_number}: ${err.details.next || 'this bill cannot take that amount.'}`));
    if (err.details?.available) area.append(el('p', `Still open on that bill: ${err.details.available.amount} ${err.details.available.currency}.`));
    for (const row of err.details?.changes || []) area.append(el('p', `${row.actor_id || 'Unknown actor'} at ${row.at || 'unknown time'} changed ${(row.fields || []).join(', ') || 'this record'}.`));
  }

  async function perform(fn) {
    if (busy) { note('Finishing the current action. Try that again in a moment.'); return; }
    busy = true; root.setAttribute('aria-busy', 'true');
    try { await fn(); } catch (err) { showError(err); } finally { busy = false; root.removeAttribute('aria-busy'); }
  }

  async function command(name, input = {}) {
    const headers = {'Content-Type': 'application/json', 'X-Bookflow-Workbench': '1',
                     'X-Bookflow-Client-Name': 'bookflow-workbench'};
    let response;
    try {
      response = await fetch('/companies/' + encodeURIComponent(config.company) + '/commands/'
        + name.replaceAll(' ', '.'), {method: 'POST', credentials: 'same-origin', headers,
                                      body: exact.stringify(input)});
    } catch (_) {
      throw {message: 'The connection was interrupted. Reload the open bills before paying again.'};
    }
    let out;
    try { out = exact.parse(await response.text()); }
    catch (_) { throw {message: 'The server response could not be read. Reload the open bills.'}; }
    if (!response.ok || out.code?.startsWith('E_')) throw out;
    return out;
  }

  // ------------------------------------------------------------------ what is on offer

  function fundingAccount() { return accounts.get($('funding').value) || null; }
  function method() { return methods.get($('method').value) || null; }

  function drawCheckNumber() {
    /* A check number belongs to a check written on a bank account, which is the command's own
       rule; anywhere else there is no number to write, so the field is not offered. */
    const account = fundingAccount(), chosen = method();
    const offered = !!account && account.type === 'bank' && !!chosen && chosen.kind === 'check';
    $('check-label').hidden = !offered;
    if (!offered) $('check').value = '';
  }

  // ------------------------------------------------------------------ the open bills

  async function loadBills() {
    /* Reloading the open bills never clears the receipt of a save: the panel naming what was
       just written is the only place those payment numbers appear, and a reload runs right
       after every save. It is cleared when a new piece of work starts, not when facts move. */
    selected.clear(); candidates = []; nextCursor = null;
    $('error').hidden = true;
    await morePage(null);
  }

  function clearResult() { $('result').hidden = true; }

  async function morePage(cursor) {
    let guard = 0;
    do {
      const input = {status: 'posted', direction: 'asc', limit: 50,
                     ...(vendorId ? {vendor: vendorId} : {}), ...(cursor ? {cursor} : {})};
      const out = await command('bill query', input);
      for (const row of out.items) {
        if (row.settlement_current.open_minor_units > 0) candidates.push(row);
      }
      cursor = out.next_cursor;
      guard += 1;
      // A page of fully paid bills is not an empty window: keep walking until there is
      // something to select, or the walk runs out.
    } while (cursor && candidates.length === 0 && guard < 20);
    nextCursor = cursor;
    $('more').hidden = !nextCursor;
    drawCandidates();
  }

  function drawCandidates() {
    const body = $('rows');
    body.replaceChildren();
    for (const row of candidates) {
      const open = row.settlement_current.open_minor_units;
      const tr = el('tr');
      tr.dataset.bill = row.id;
      tr.dataset.vendor = row.vendor_id;
      const check = el('input');
      check.type = 'checkbox';
      check.className = 'pay-bills-select';
      check.setAttribute('aria-label', 'Select bill ' + row.number + ' from ' + row.vendor_name);
      const input = el('input');
      input.type = 'text';
      input.inputMode = 'decimal';
      input.className = 'pay-bills-amount';
      input.dataset.mathCurrency = config.currency;
      input.setAttribute('aria-label', 'Payment for bill ' + row.number);
      check.addEventListener('change', () => {
        if (check.checked) {
          selected.set(row.id, row);
          // Selecting a row means paying it off, which is what a person ticking a Pay Bills
          // row means; typing over it is a partial payment and the remainder stays open.
          if (!input.value) input.value = units(open);
        } else {
          selected.delete(row.id);
          input.value = '';
        }
        redrawTotals();
      });
      input.addEventListener('input', redrawTotals);
      // The shared arithmetic entry offers `= 50.00` under `100/2`; committing it on change is
      // what turns that offer into the value, the way a document form's Preview does.
      input.addEventListener('change', () => { window.bookflowMath?.prepare(tr); redrawTotals(); });
      const billCell = el('span');
      billCell.append(link(row.number, '/c/' + encodeURIComponent(config.company) + '/bill/'
        + encodeURIComponent(row.id)));
      if (row.supplier_reference) billCell.append(el('small', ' · ref ' + row.supplier_reference));
      const cells = [check, el('span', row.vendor_name), billCell, el('span', row.due_date),
                     el('span', units(row.total_minor_units)),
                     el('span', units(open)), input];
      const labels = ['Select', 'Vendor', 'Bill', 'Due date', 'Original amount', 'Open balance', 'Payment'];
      cells.forEach((node, index) => {
        const td = el('td');
        td.dataset.label = labels[index];
        if (['Original amount', 'Open balance'].includes(labels[index])) td.className = 'pay-bills-money';
        const tag = el('span', labels[index]);
        tag.className = 'pay-bills-cell-label';
        tag.setAttribute('aria-hidden', 'true');
        td.append(tag, node);
        tr.append(td);
      });
      body.append(tr);
    }
    $('selection-status').textContent = candidates.length
      ? `${candidates.length} open bill${candidates.length === 1 ? '' : 's'}${vendorId ? ' for this vendor' : ''}.`
      : 'No open bills. Enter a bill, or clear the vendor filter.';
    window.bookflowMath?.refresh();
    redrawTotals();
  }

  // ------------------------------------------------------------------ totals and the split

  function entered() {
    /* Every selected row with the amount now on screen, in document order. */
    const rows = [];
    for (const tr of $('rows').querySelectorAll('tr')) {
      const box = tr.querySelector('.pay-bills-select');
      if (!box?.checked) continue;
      const row = selected.get(tr.dataset.bill);
      const text = tr.querySelector('.pay-bills-amount').value;
      rows.push({row, text, minor: toMinor(text)});
    }
    return rows;
  }

  function groupsOf(rows) {
    /* The command's own grouping, minus the parts this whole page holds constant. */
    const found = new Map();
    for (const entry of rows) {
      const key = config.group_fields.map(field => entry.row[field]).join(' ');
      if (!found.has(key)) {
        found.set(key, {vendor: entry.row.vendor_name, vendor_id: entry.row.vendor_id,
                        ap_account_id: entry.row.ap_account_id, bills: [], total: 0n});
      }
      const group = found.get(key);
      group.bills.push(entry.row.number);
      if (entry.minor !== null) group.total += entry.minor;
    }
    return [...found.values()];
  }

  function redrawTotals() {
    const rows = entered(), groups = groupsOf(rows);
    const bad = rows.filter(entry => entry.minor === null || entry.minor <= 0n);
    let total = 0n;
    for (const entry of rows) if (entry.minor !== null) total += entry.minor;
    const totals = $('totals');
    totals.replaceChildren();
    const line = el('p', `Total to be paid: ${money(total)} across ${rows.length} bill${rows.length === 1 ? '' : 's'}.`);
    line.id = 'pay-bills-total';
    line.dataset.totalMinor = total.toString();
    line.dataset.billCount = String(rows.length);
    totals.append(line);
    if (bad.length) totals.append(el('p', `${bad.length} selected bill${bad.length === 1 ? ' has' : 's have'} no usable amount. Enter an amount greater than zero, in ${config.currency}.`));

    const list = $('group-list');
    list.replaceChildren();
    const summary = $('groups-summary');
    summary.dataset.groupCount = String(groups.length);
    if (!groups.length) {
      summary.textContent = 'Select bills to see how they are paid.';
    } else {
      summary.textContent = groups.length === 1
        ? `This selection makes 1 payment.`
        : `This selection makes ${groups.length} payments, one for each payee. One save writes them all.`;
      const apNames = new Map([...accounts.values()].map(row => [row.id, row.full_name || row.name]));
      for (const group of groups) {
        const item = el('li');
        item.dataset.vendorId = group.vendor_id;
        item.dataset.apAccountId = group.ap_account_id;
        item.textContent = `${group.vendor} · ${apNames.get(group.ap_account_id) || 'Accounts Payable'} · `
          + `${money(group.total)} · ${group.bills.length} bill${group.bills.length === 1 ? '' : 's'}: `
          + group.bills.join(', ');
        list.append(item);
      }
    }
    const problems = saveProblem(rows, groups);
    $('save').disabled = !!problems;
    $('save').title = problems || '';
    if (problems) note(problems); else if (rows.length) note('Ready to save.');
  }

  function saveProblem(rows, groups) {
    if (!config.allowed.includes('bill pay')) return 'Paying bills requires ledger posting permission.';
    if (!$('date').value) return 'Choose the payment date.';
    if (!$('funding').value) return 'Choose the account the money comes out of.';
    if (!$('method').value) return 'Choose the payment method.';
    if (!rows.length) return 'Select at least one bill to pay.';
    if (rows.some(entry => entry.minor === null || entry.minor <= 0n))
      return `Enter an amount greater than zero for every selected bill, in ${config.currency}.`;
    for (const entry of rows) {
      if (entry.minor > BigInt(entry.row.settlement_current.open_minor_units))
        return `Bill ${entry.row.number} has only ${money(BigInt(entry.row.settlement_current.open_minor_units))} open. A vendor credit is a separate document.`;
    }
    if ($('check').value.trim() && !$('check-label').hidden && groups.length > 1)
      return `A check number names one check, and this selection writes ${groups.length} payments. Clear the check number, or pay one vendor at a time.`;
    return '';
  }

  // ------------------------------------------------------------------ the save

  async function save() {
    $('error').hidden = true; clearResult();
    const rows = entered(), groups = groupsOf(rows);
    const problem = saveProblem(rows, groups);
    if (problem) { note(problem); return; }
    const input = {
      date: $('date').value,
      funding_account: $('funding').value,
      method: $('method').value,
      bills: rows.map(entry => ({bill: entry.row.id, amount: units(entry.minor),
                                 expected_version: entry.row.version})),
    };
    for (const [field, id] of [['check_number', 'check'], ['reference', 'reference'], ['memo', 'memo']]) {
      const value = $(id).value.trim();
      if (value && !(field === 'check_number' && $('check-label').hidden)) input[field] = value;
    }
    const out = await command('bill pay', input);
    drawResult(out);
    await loadBills();
    note(`Saved. ${out.group_count} payment${out.group_count === 1 ? '' : 's'} written for ${out.paid.amount} ${out.paid.currency}.`);
  }

  function drawResult(out) {
    const area = $('result');
    area.hidden = false;
    area.dataset.groupCount = String(out.group_count);
    area.replaceChildren(el('h2', out.group_count === 1 ? 'Bill payment written'
      : `${out.group_count} bill payments written`));
    area.append(el('p', `${out.paid.amount} ${out.paid.currency} paid across ${out.bill_count} bill${out.bill_count === 1 ? '' : 's'}.`));
    for (const payment of out.payments) {
      const card = el('div');
      card.className = 'pay-bills-effect';
      card.dataset.payment = payment.id;
      const head = el('p');
      head.append(link('Bill payment ' + payment.number,
        '/c/' + encodeURIComponent(config.company) + '/bill-payment/' + encodeURIComponent(payment.id)));
      head.append(el('span', ` · ${payment.vendor_name} · ${payment.total.amount} ${payment.currency}`
        + ` · ${payment.payment_method_name}`
        + (payment.check_number ? ` · check ${payment.check_number}` : '')));
      card.append(head);
      const settled = el('ul');
      for (const line of payment.revision.lines) {
        const item = el('li', `Bill ${line.bill_number} · ${line.amount.amount} ${line.currency}`);
        item.dataset.bill = line.bill_id;
        settled.append(item);
      }
      card.append(settled);
      area.append(card);
    }
    area.scrollIntoView({block: 'nearest'});
  }

  // ------------------------------------------------------------------ start

  async function chooseVendor(id, label) {
    clearResult();
    vendorId = id;
    $('vendor').value = label || '';
    $('vendor-chosen').textContent = id ? 'Showing open bills for ' + label + '.' : 'Showing open bills for every vendor.';
    $('vendor-results').replaceChildren();
    await loadBills();
  }

  async function initialize() {
    $('date').value = new Date().toISOString().slice(0, 10);
    const [accountPage, methodPage] = await Promise.all([
      command('account list', {}), command('payment-method list', {})]);
    for (const row of accountPage.items) {
      accounts.set(row.id, row);
      if (row.active && (row.type === 'bank' || row.type === 'credit_card')) {
        const option = new Option((row.full_name || row.name)
          + (row.type === 'credit_card' ? ' (credit card)' : ' (bank)'), row.id);
        $('funding').append(option);
      }
    }
    for (const row of methodPage.items) {
      if (!row.active) continue;
      methods.set(row.id, row);
      $('method').append(new Option(row.name, row.id));
    }
    if (config.vendor) {
      const party = await command('vendor show', {vendor: config.vendor});
      await chooseVendor(party.id, party.full_name || party.name);
    } else {
      $('vendor-chosen').textContent = 'Showing open bills for every vendor.';
      await loadBills();
    }
    drawCheckNumber();
    if (!config.allowed.includes('bill pay')) {
      note('Read-only access: paying bills requires ledger posting permission.');
    }
    root.dataset.loaded = 'true';
  }

  $('form').addEventListener('submit', event => event.preventDefault());
  for (const id of ['date', 'reference', 'memo', 'check']) $(id).addEventListener('change', redrawTotals);
  for (const id of ['funding', 'method']) $(id).addEventListener('change', () => { drawCheckNumber(); redrawTotals(); });
  $('find-vendor').addEventListener('click', () => perform(async () => {
    const out = await command('vendor query', {query: $('vendor').value, limit: 25});
    const results = $('vendor-results');
    results.replaceChildren();
    for (const row of out.items) {
      const button = el('button', row.full_name || row.name);
      button.type = 'button';
      button.className = 'pay-bills-vendor-choice';
      button.addEventListener('click', () => perform(() => chooseVendor(row.id, row.full_name || row.name)));
      results.append(button);
    }
    if (!out.items.length) results.append(el('p', 'No matching vendors.'));
  }));
  $('all-vendors').addEventListener('click', () => perform(() => chooseVendor(null, '')));
  $('load').addEventListener('click', () => perform(async () => { clearResult(); await loadBills(); }));
  $('reload').addEventListener('click', () => perform(async () => { clearResult(); await loadBills(); }));
  $('more').addEventListener('click', () => perform(() => morePage(nextCursor)));
  $('save').addEventListener('click', () => perform(save));
  perform(initialize);
})();
