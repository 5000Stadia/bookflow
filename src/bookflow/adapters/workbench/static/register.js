/* Register presentation only: commands own money, compatibility and authority. */
(() => {
  'use strict';
  const configNode = document.getElementById('register-config');
  if (!configNode) return;
  const c = JSON.parse(configNode.textContent);
  const $ = id => document.getElementById(id);
  const root = $('register-workspace'), form = $('register-form');
  const field = name => form.elements.namedItem(name);
  const money = value => value ? `${value.amount} ${value.currency}` : '';
  const base = `/c/${encodeURIComponent(c.company)}`;
  const path = `${base}/account/${encodeURIComponent(c.account)}/register`;
  const storageKey = 'bookflow-register-pending-v1';
  const maxBytes = 1024 * 1024, retention = 30 * 24 * 60 * 60 * 1000;
  let pending = null, sending = false, storageBlocked = false, dirty = false;
  let sequence = 0, splits = [], splitMode = false, edit = null, initial = c.edit;
  let nextCursor = null, queryEpoch = 0;
  const precommit = new Set(['E_VALIDATION', 'E_USAGE', 'E_VERSION_CONFLICT', 'E_PERIOD_CLOSED',
    'E_PERMISSION', 'E_INACTIVE_REFERENCE', 'E_UNBALANCED_ENTRY', 'E_DUPLICATE_NUMBER',
    'E_AMOUNT_PRECISION', 'E_VALUE_RANGE', 'E_REASON_REQUIRED', 'E_RECORD_NOT_FOUND',
    'E_CAPABILITY', 'E_FEATURE_DISABLED', 'E_UNAUTHENTICATED', 'E_WORKBENCH_HEADER']);
  function node(tag, text, cls) {
    const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n;
  }
  function button(text, action) { const n = node('button', text); n.type = 'button'; n.addEventListener('click', action); return n; }
  function link(text, url) { const n = node('a', text); n.href = url; return n; }
  function errorText(error) {
    return `${error.code ? error.code + ': ' : ''}${error.message || 'Request could not be completed.'}` +
      (error.details ? '\n' + JSON.stringify(error.details) : '');
  }
  function error(error) { if ($('register-error')) { $('register-error').textContent = errorText(error); $('register-error').focus(); } }
  async function command(name, payload, context = {}, key = null, company = c.company, wire = null) {
    const headers = {'Content-Type': 'application/json', 'X-Bookflow-Workbench': '1', 'X-Bookflow-Client-Name': 'bookflow-workbench', ...context};
    if (key) headers['Idempotency-Key'] = key;
    try {
      for (const name of ['X-Bookflow-Reason', 'X-Bookflow-Source-Ref', 'X-Bookflow-Directive',
                          'Idempotency-Key', 'X-Bookflow-Client-Name', 'X-Bookflow-Client-Version']) {
        if (headers[name] != null) headers[name] = encodeURIComponent(headers[name]);
      }
      headers['X-Bookflow-Context-Encoding'] = 'percent-utf8';
      new Headers(headers);  // Local construction failures cannot have posted.
    } catch (_) { throw {code: 'E_VALIDATION', message: 'Not sent: attribution contains invalid text.'}; }
    let response;
    try { response = await fetch(`/companies/${encodeURIComponent(company)}/commands/${name}`, {
      method: 'POST', credentials: 'same-origin', headers, body: wire || JSON.stringify(payload),
    }); } catch (_) { throw {code: 'TRANSPORT', message: 'No response. The save may have committed.'}; }
    let body;
    try { body = await response.json(); } catch (_) { throw {code: 'INVALID_RESPONSE', message: 'Unreadable response. The save may have committed.'}; }
    if (!response.ok || body.error) throw (body.error || (body.code ? body : {code: 'UNKNOWN', message: 'Unexpected server response.'}));
    return body;
  }
  // Bounded reference command queries expose stable IDs and server-formatted labels.
  function picker(container, title, target, value = '', label = '') {
    const wrapper = node('div', null, 'register-picker');
    const labelNode = node('label', title), input = node('input');
    input.type = 'text'; input.autocomplete = 'off'; input.maxLength = 200;
    input.id = 'register-picker-' + (++sequence); input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list'); input.setAttribute('aria-expanded', 'false');
    const options = node('div', null, 'register-options'); options.id = input.id + '-options'; options.role = 'listbox'; options.hidden = true;
    input.setAttribute('aria-controls', options.id); labelNode.append(input);
    const status = node('small'); status.role = 'status';
    let selected = value, selectedLabel = label, version = 0, timer, choices = [], active = -1;
    function close() { options.hidden = true; active = -1; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); }
    function set(id = '', name = '') { version++; clearTimeout(timer); selected = id; selectedLabel = name; input.value = name; input.setCustomValidity(''); close(); status.textContent = id ? 'Selected: ' + name : ''; }
    function choose(index) { const row = choices[index]; if (!row) return; set(row.id, row.label); if (form?.contains(wrapper)) dirty = true; input.focus(); }
    async function search() {
      const requestVersion = ++version, query = input.value.trim();
      if (!query) { close(); return; }
      status.textContent = 'Searching…';
      try {
        const result = await command((typeof target === 'function' ? target() : target) + '.query', {query, limit: 25, projection: 'reference'});
        if (version !== requestVersion || !wrapper.isConnected) return;
        choices = result.items; options.replaceChildren(); active = -1;
        choices.forEach((row, index) => { const b = button(row.label, () => choose(index)); b.role = 'option'; b.tabIndex = -1; b.id = options.id + '-' + index; options.append(b); });
        options.hidden = !choices.length; input.setAttribute('aria-expanded', String(!!choices.length));
        status.textContent = choices.length ? 'Use arrows then Enter to choose.' : 'No matching records.';
      } catch (e) { if (version === requestVersion) { close(); status.textContent = errorText(e); } }
    }
    input.addEventListener('input', () => { version++; selected = ''; selectedLabel = ''; close(); clearTimeout(timer); input.setCustomValidity(input.value ? 'Choose a matching name or Clear.' : ''); timer = setTimeout(search, 180); if (form?.contains(wrapper)) dirty = true; });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); version++; clearTimeout(timer); close(); return; }
      if (e.key === 'Tab') { close(); return; }
      if (e.key === 'Enter') { e.preventDefault(); if (!options.hidden && active >= 0) choose(active); return; }
      if (!['ArrowDown', 'ArrowUp'].includes(e.key)) return;
      e.preventDefault(); if (options.hidden) { search(); return; }
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length;
      [...options.children].forEach((n, i) => n.setAttribute('aria-selected', String(i === active)));
      input.setAttribute('aria-activedescendant', options.children[active].id); options.children[active].scrollIntoView({block: 'nearest'});
    });
    const clear = button('Clear ' + title, () => { set(); if (form?.contains(wrapper)) dirty = true; input.focus(); });
    wrapper.append(labelNode, clear, options, status); container.append(wrapper); set(value, label);
    return {input, wrapper, set, value: () => { if (input.value && !selected) throw {code: 'E_VALIDATION', message: `Choose ${title} by name or Clear.`}; return selected; }, label: () => selectedLabel};
  }
  function partyPicker(container, title, value, label) {
    const wrapper = node('div'), typeLabel = node('label', title + ' type'), type = node('select');
    for (const [value, label] of [['customer', 'Customer / job'], ['vendor', 'Vendor'], ['employee', 'Employee'], ['other_name', 'Other name']]) {
      const option = node('option', label); option.value = value; type.append(option);
    }
    typeLabel.append(type); wrapper.append(typeLabel); container.append(wrapper);
    type.value = value?.name_type || 'vendor';
    const p = picker(wrapper, title, () => type.value.replaceAll('_', '-'), value?.name_id, label);
    type.addEventListener('change', () => p.set());
    return {...p, type, value: () => { const id = p.value(); return id ? {name_type: type.value, name_id: id} : null; }, set: (value, label) => { type.value = value?.name_type || 'vendor'; p.set(value?.name_id, label); }};
  }
  const accountChooser = picker($('register-account-chooser'), 'Find account', 'account');
  $('register-account-chooser').append(button('Open register', () => {
    try { const id = accountChooser.value(); if (id) location.href = `${base}/account/${encodeURIComponent(id)}/register`; } catch (e) { accountChooser.input.reportValidity(); }
  }));
  let payee, category, rowClass;
  function dateFocus() { if (form && !$('register-fields').disabled) { field('date').focus(); field('date').select(); } }
  function textControl(container, title, value = '', decimal = false) {
    const label = node('label', title), input = node('input'); input.value = value || ''; if (decimal) input.inputMode = 'decimal'; else input.maxLength = 2000;
    label.append(input); container.append(label); return input;
  }
  function addSplit(value = {}, labels = {}) {
    if (splits.length >= 199) { error({message: 'At most 199 allocations are allowed.'}); return; }
    const box = node('fieldset'), legend = node('legend', 'Allocation'), fields = node('div', null, 'register-split-fields'); box.append(legend, fields);
    const a = picker(fields, 'Split account', 'account', value.account, labels.account);
    const amount = textControl(fields, 'Split amount', value.amount, true);
    amount.dataset.mathCurrency = c.currency;
    const directionLabel = node('label', 'Split movement'), direction = node('select');
    for (const [v, text] of [['', 'Same as main entry'], ...c.directions]) { const o = node('option', text); o.value = v; direction.append(o); }
    direction.value = value.direction || ''; directionLabel.append(direction); fields.append(directionLabel);
    const memo = textControl(fields, 'Split memo', value.memo);
    const party = partyPicker(fields, 'Split party', value.party, labels.party);
    const classLabel = node('label', 'Split class mode'), classMode = node('select');
    for (const [v, text] of [['inherit', 'Inherit row class'], ['none', 'No class'], ['value', 'Choose class']]) { const o = node('option', text); o.value = v; classMode.append(o); }
    classMode.value = value.class_mode || 'inherit'; classLabel.append(classMode); fields.append(classLabel);
    const klass = picker(fields, 'Split class', 'class', value.class_id, labels.class_id);
    const classVisibility = () => { klass.wrapper.hidden = classMode.value !== 'value'; };
    classMode.addEventListener('change', () => { classVisibility(); if (classMode.value !== 'value') klass.set(); }); classVisibility();
    const row = {box, a, amount, direction, memo, party, classMode, klass, originalMemo: value.memo, line_id: value.line_id};
    box.append(button('Remove split', () => { const i = splits.indexOf(row); splits.splice(i, 1); box.remove(); dirty = true; (splits[i]?.a.input || $('register-split-add')).focus(); }));
    $('register-allocations').append(box); splits.push(row); return row;
  }
  function allocations() {
    if (!splits.length) throw {code: 'E_VALIDATION', message: 'Add at least one split, or Clear splits to use Category.'};
    return splits.map(s => {
      const value = {account: s.a.value(), amount: s.amount.value.trim(), memo: s.memo.value === (s.originalMemo ?? '') ? (s.originalMemo ?? null) : (s.memo.value || null),
        party: s.party.value(), class_mode: s.classMode.value};
      if (s.direction.value) value.direction = s.direction.value;
      if (s.classMode.value === 'value') value.class_id = s.klass.value();
      if (s.line_id) value.line_id = s.line_id;
      return value;
    });
  }
  let customState = {};
  function customLoad(projection) {
    const container = $('register-custom-fields');
    if (!container) return;
    container.replaceChildren(node('legend', 'Custom fields'));
    const p = projection?.payload || {}, captured = projection?.custom_fields || [];
    const facts = Object.fromEntries(captured.map(f => [f.definition_id, f]));
    const controls = new Map((c.custom_fields || []).map(f => [f.definition_id, f]));
    customState = structuredClone(projection?.customState || {});
    for (const [id, value] of Object.entries(p.custom_fields || {})) {
      if (!customState[id]) customState[id] = {state: value === null ? 'clear' : 'set', value,
        kind: typeof value === 'boolean' ? 'bool' : controls.get(id)?.wire_kind || 'text', label: id};
    }
    const ids = new Set([...controls.keys(), ...Object.keys(customState)]);
    for (const id of ids) {
      const d = controls.get(id), fact = facts[id];
      const saved = customState[id];
      const kind = saved?.kind || d?.wire_kind || 'text';
      const value = saved ? saved.value : fact ? fact.value : p.journal ? null : d?.creation_default;
      const state = customState[id] = {state: saved?.state || 'keep', value, kind, label: saved?.label || d?.label || id, choice_id: saved?.choice_id || fact?.choice_id || null};
      const box = node('div'); box.style.cssText = 'min-width:0;max-width:100%;margin-block:1rem';
      const label = node('label', state.label + (d?.required ? ' *' : ''));
      label.style.overflowWrap = 'anywhere';
      const input = node(['bool', 'choice'].includes(kind) ? 'select' : 'input');
      input.id = 'register-custom-' + id; label.htmlFor = input.id;
      input.dataset.customField = id; input.style.cssText = 'width:100%;max-width:100%;box-sizing:border-box';
      const text = v => v == null ? '' : String(v);
      if (input.tagName === 'SELECT') {
        const options = kind === 'bool' ? ['true', 'false'] : (d?.choices || []);
        // Stable choice identity retains the selection through spelling-only
        // changes while the original canonical value remains the wire value.
        const selected = Object.entries(d?.choice_ids || {}).find(([, choiceId]) => choiceId === state.choice_id)?.[0]
          ?? (!saved && fact && d?.selected != null ? d.selected : text(value));
        if (!options.includes(selected)) {
          const o = node('option', value == null ? '(choose)' : text(value) + ' — unavailable choice'); o.value = text(value); input.append(o);
        }
        for (const option of options) { const o = node('option', option); o.value = option === selected && value != null ? text(value) : option; if (d?.choice_ids?.[option]) o.dataset.choiceId = d.choice_ids[option]; input.append(o); }
        input.value = text(value);
        state.choice_id = input.selectedOptions[0]?.dataset.choiceId || state.choice_id;
      } else {
        const date = new Date(text(value) + 'T12:00:00Z');
        const validDate = value == null || value === '' || (!Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value);
        input.type = kind === 'date' && validDate ? 'date' : 'text';
        if (kind === 'number') {
          input.inputMode = 'decimal'; input.dataset.mathScale = '9';
          input.dataset.mathActive = JSON.stringify([{name: 'register-custom-action:' + id, values: ['set']}]);
        }
        input.value = text(value);
      }
      const actionLabel = node('label', 'Action for ' + state.label), action = node('select');
      action.dataset.customAction = id;
      action.name = 'register-custom-action:' + id;
      action.style.cssText = 'max-width:100%';
      for (const [v, title] of [['keep', p.journal ? 'Keep stored value' : 'Use creation default / omit'], ['set', 'Set value (including empty text)'], ['clear', 'Clear']]) {
        const option = node('option', title); option.value = v; action.append(option);
      }
      action.value = state.state; actionLabel.append(action);
      input.addEventListener('input', () => {
        state.value = kind === 'bool' && ['true', 'false'].includes(input.value) ? input.value === 'true' : input.value;
        state.choice_id = input.selectedOptions?.[0]?.dataset.choiceId || null;
        state.state = 'set'; action.value = 'set'; dirty = true;
      });
      action.addEventListener('change', () => {
        state.state = action.value;
        if (state.state === 'set') state.value = kind === 'bool' && ['true', 'false'].includes(input.value) ? input.value === 'true' : input.value;
        dirty = true;
      });
      box.append(label, input, actionLabel);
      if (!d || kind !== d.wire_kind) box.append(node('strong', 'Unavailable field attempt — change or clear explicitly.'));
      if (d && kind !== d.wire_kind) {
        const adopt = node('button', `Use ${d.wire_kind} type`); adopt.type = 'button'; adopt.dataset.customAdopt = id;
        adopt.addEventListener('click', () => {
          state.kind = d.wire_kind; state.state = 'set';
          if (state.kind === 'bool' && ['true', 'false'].includes(String(state.value))) state.value = String(state.value) === 'true';
          else if (state.kind !== 'bool') state.value = state.value == null ? '' : String(state.value);
          customLoad({payload: p, customState: structuredClone(customState), custom_fields: captured}); dirty = true;
        });
        box.append(adopt);
      }
      if (!p.journal && d?.creation_default != null) box.append(node('small', ' Default: ' + text(d.creation_default)));
      container.append(box);
    }
    for (const fact of captured) {
      const value = fact.kind === 'choice' ? fact.choice_label : fact.value;
      container.append(node('p', `${fact.name} (${fact.kind}, captured): ${String(value)}`));
    }
  }
  function customPatch() {
    const patch = {};
    for (const [id, field] of Object.entries(customState)) {
      if (field.state === 'clear') patch[id] = null;
      else if (field.state === 'set') patch[id] = field.value;
    }
    return patch;
  }
  function payload() {
    const value = {account: c.account, date: field('date').value.trim(), direction: field('direction').value,
      amount: field('amount').value.trim(), memo: field('memo').value === (edit?.memo ?? '') ? (edit?.memo ?? null) : (field('memo').value || null), payee: payee.value(), class_id: rowClass.value() || null};
    value.custom_fields = customPatch();
    value.custom_field_kinds = Object.fromEntries(Object.entries(customState)
      .filter(([, f]) => f.state === 'set').map(([id, f]) => [id, f.kind]));
    if (field('number').value.trim()) value.number = field('number').value.trim();
    if (splitMode) value.allocations = allocations(); else value.category = category.value();
    if (edit) { value.journal = edit.journal; value.expected_version = edit.expected_version; value.selected_line_id = edit.selected_line_id;
      if (!splitMode && edit.category_line_id) value.category_line_id = edit.category_line_id; }
    return value;
  }
  function displayLabels() { return {payee: payee.label(), category: category.label(), class_id: rowClass.label(),
    allocations: splits.map(s => ({account: s.a.label(), party: s.party.label(), class_id: s.klass.label()}))}; }
  function load(projection) {
    const p = projection?.payload || {}, labels = projection?.labels || {};
    edit = p.journal ? p : null;
    customLoad(projection);
    field('date').value = p.date || c.today; field('direction').value = p.direction || c.directions[0][0];
    for (const name of ['number', 'memo', 'amount']) field(name).value = p[name] || '';
    payee.set(p.payee, labels.payee); category.set(p.category, labels.category); rowClass.set(p.class_id, labels.class_id);
    splits = []; $('register-allocations').replaceChildren(); splitMode = !!p.allocations;
    (p.allocations || []).forEach((a, i) => addSplit(a, labels.allocations?.[i]));
    $('register-splits').hidden = !splitMode; $('register-category').hidden = splitMode;
    $('register-entry-title').textContent = edit ? `Edit journal ${p.number} · version ${p.expected_version}` : 'New journal entry';
    $('register-new').hidden = !edit; $('register-error').textContent = ''; dirty = false;
  }
  function pendingView() {
    $('register-pending').hidden = !pending && !storageBlocked;
    const samePlace = pending?.company === c.company && pending?.account === c.account;
    const expired = pending && Date.now() - pending.time >= retention;
    $('register-retry').hidden = !pending || !c.writable || !samePlace;
    $('register-retry').disabled = sending || expired || storageBlocked;
    if ($('register-fields')) $('register-fields').disabled = !!pending || sending || storageBlocked;
    $('register-pending-message').textContent = storageBlocked ? 'Save recovery storage is unavailable. No new save will be sent. Restore storage access and reload.' :
      pending ? `Unresolved ${pending.command}. Request key: ${pending.key}. ` + (expired ? 'The 30-day retry window has expired. Review the journal and audit trail and reconcile this request before any replacement.' : 'The exact request is retained in this tab. Retry same save to retrieve its result. Restore cannot discard it.') : '';
    $('register-review').hidden = !pending;
    $('register-reconciliation').hidden = !expired || !samePlace || !c.writable;
    if (pending) $('register-review').href = `/c/${encodeURIComponent(pending.company)}/audit?` + new URLSearchParams({command: pending.command.replace('.', ' ')}) + '#idempotency-key=' + encodeURIComponent(pending.key);
    $('register-pending-location').hidden = !pending || samePlace;
    if (pending) $('register-pending-location').href = `/c/${encodeURIComponent(pending.company)}/account/${encodeURIComponent(pending.account)}/register`;
  }
  const identityChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('bookflow-register-identity') : null;
  identityChannel?.addEventListener('message', () => { clearProtected(); storageBlocked = true; pendingView(); });
  function logout() { identityChannel?.postMessage('logout'); clearProtected(); }
  function clearProtected() { try { sessionStorage.removeItem(storageKey); } catch (_) { storageBlocked = true; } pending = null; initial = null; c.edit = null; c.custom_fields = []; customState = {}; configNode.textContent = '{}';
    $('register-receipt').replaceChildren(); $('register-receipt').hidden = true;
    if (form) { load(null); for (const name of ['reason', 'source_ref', 'directive_id']) field(name).value = ''; }
  }
  function intentKey() {
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    // getRandomValues is available on plain HTTP LAN origins as well. Preserve
    // UUID v4 entropy and version/variant bits; never substitute Math.random.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function persist(intent) {
    const encoded = JSON.stringify(intent);
    if (new Blob([encoded]).size > maxBytes) throw new Error('Pending request exceeds the 1 MiB recovery limit.');
    sessionStorage.setItem(storageKey, encoded);
    if (sessionStorage.getItem(storageKey) !== encoded) throw new Error('Recovery storage verification failed.');
  }
  function forgetResolved() {
    // If cleanup fails, retain the old exact intent rather than risk a second write.
    sessionStorage.removeItem(storageKey); pending = null; pendingView();
  }
  async function verifyActor() {
    // Re-read authenticated identity before exposing/retrying a stored write.
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store'});
    if (response.status === 401 || (response.redirected && new URL(response.url).pathname === '/login')) {
      clearProtected(); storageBlocked = true; throw {code: 'IDENTITY_CHANGED', message: 'Session ended. Protected draft cleared; reload to sign in.'};
    }
    if (!response.ok) throw {code: 'TRANSPORT', message: 'Could not verify identity. No write sent; pending request retained.'};
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const config = page.getElementById('register-config');
    if (!config) throw {code: 'INVALID_RESPONSE', message: 'Identity response unreadable. Pending request retained.'};
    const current = JSON.parse(config.textContent);
    if (current.actor !== c.actor) {
      clearProtected(); storageBlocked = true; pendingView();
      throw {code: 'IDENTITY_CHANGED', message: 'Authentication changed. Protected draft cleared; reload to continue.'};
    }
    c.custom_fields = current.custom_fields || [];
  }
  async function sendPending() {
    if (!pending || sending || storageBlocked || !c.writable || pending.actor !== c.actor || pending.company !== c.company || pending.account !== c.account || Date.now() - pending.time >= retention) return;
    // Missing flags on older persisted intents mean a previous send is possible.
    // A retry rejection before idempotency lookup cannot settle that earlier send.
    const firstAttempt = pending.possibly_sent === false;
    let commandStarted = false;
    sending = true; pendingView();
    try {
      await verifyActor();
      if (!pending || storageBlocked) return;  // Logout may have cleared it during the read.
      pending = {...pending, possibly_sent: true};
      try { persist(pending); } catch (_) {
        storageBlocked = true;
        throw {code: 'STORAGE', message: 'Not sent: could not persist the attempt state. The pending request is retained.'};
      }
      commandStarted = true;
      const result = await command(pending.command, null, pending.context, pending.key, pending.company, pending.wire);
      if (!pending || storageBlocked) return;  // Do not reveal a receipt after identity clearance.
      if (!result.receipt || result.receipt.account_id !== c.account || !result.receipt.amount?.amount || !result.id) throw {code: 'INVALID_RESPONSE', message: 'No authoritative receipt returned. Retry the same save.'};
      const receipt = $('register-receipt'), saved = node('strong', `${result.changed === false ? 'Saved (unchanged)' : 'Saved'} journal ${result.number} · ${result.receipt.direction} `);
      saved.append(node('span', money(result.receipt.amount), 'register-money')); receipt.replaceChildren(saved);
      receipt.append(node('p', `Accounting date ${result.date}. This receipt is the saved account movement; All entries is a separate balance snapshot.`), link('Open saved journal', `${base}/journal/${encodeURIComponent(result.id)}`));
      if (result.warnings?.length) receipt.append(node('p', result.warnings.join(' ')));
      receipt.hidden = false;
      const date = pending.payload.date;
      try { forgetResolved(); } catch (_) { storageBlocked = true; throw {code: 'STORAGE', message: 'Saved. Recovery cleanup failed; reload after restoring storage access.'}; }
      initial = null; load({payload: {date}}); field('reason').value = ''; field('source_ref').value = ''; field('directive_id').value = '';
      refresh();
    } catch (e) {
      if (e.code === 'E_UNAUTHENTICATED') { clearProtected(); storageBlocked = true; }
      else if (firstAttempt && commandStarted && precommit.has(e.code)) {
        try { forgetResolved(); } catch (_) { storageBlocked = true; }
        if (!storageBlocked) customLoad({payload: edit || {}, customState: structuredClone(customState), custom_fields: initial?.custom_fields || []});
      }
      error(e);
    } finally { sending = false; pendingView(); if (!pending && !storageBlocked && !$('register-error')?.textContent) dateFocus(); }
  }
  async function record() {
    if (pending || sending || storageBlocked) { pendingView(); return; }
    try {
      if (!window.bookflowMath.prepare(form)) return;
      const p = payload(); if (!form.reportValidity()) return;
      const context = {};
      for (const [name, header] of [['reason', 'X-Bookflow-Reason'], ['source_ref', 'X-Bookflow-Source-Ref'], ['directive_id', 'X-Bookflow-Directive']]) if (field(name).value) context[header] = field(name).value;
      const intent = {actor: c.actor, company: c.company, account: c.account, command: edit ? 'register.update' : 'register.post',
        payload: p, wire: JSON.stringify(p), labels: displayLabels(), customState: structuredClone(customState), custom_fields: initial?.custom_fields || [], context, key: intentKey(), time: Date.now(), possibly_sent: false};
      try { persist(intent); } catch (e) { error({message: `Not sent: ${e.message}`}); return; }
      pending = intent; $('register-error').textContent = ''; await sendPending();
    } catch (e) { error(e); }
  }
  function metadata(value) { return value ? `Generated ${value.generation_time} · audit watermark ${value.audit_watermark}` : ''; }
  function appendRows(rows) {
    const body = $('register-history').tBodies[0];
    for (const row of rows) {
      const tr = node('tr'); tr.dataset.kind = row.kind;
      if (row.kind !== 'posting') {
        const label = node('th', row.kind === 'opening' ? 'Period opening' : 'Period closing'); label.colSpan = 8;
        tr.append(label, node('td', money(row.running_balance), 'register-money'), node('td')); body.append(tr); continue;
      }
      const document = {
        journal_entry: {noun: 'journal', label: 'Journal'},
        invoice: {noun: 'invoice', label: 'Invoice'},
        sales_receipt: {noun: 'sales-receipt', label: 'Sales receipt'},
        // A deposit has no registered history command, so its link opens the
        // deposit detail page instead. Opening it is an attempt: a reader
        // admitted here may still not be admitted to the whole deposit.
        deposit: {noun: 'deposit', label: 'Deposit', action: 'Deposit details'},
      }[row.transaction_type];
      const values = [row.effective_date + '\n' + row.recorded_at,
        `${row.transaction_number} · ${document?.label || row.transaction_type || ''} · ${row.batch_kind}`, row.party_name, row.category_label,
        [row.memo, row.description !== row.memo ? row.description : null].filter(Boolean).join(' / '),
        row.class_summary, money(row.increase), money(row.decrease), money(row.running_balance)];
      values.forEach((value, i) => tr.append(node('td', value || '', i >= 6 ? 'register-money' : '')));
      const actions = node('td'), journal = encodeURIComponent(row.transaction_id);
      if (document) actions.append(link(document.action || 'History', `${base}/${document.noun}/${journal}?` + new URLSearchParams(row.revision_number ? {revision_number: row.revision_number} : {})));
      if (c.writable && row.transaction_type === 'journal_entry') {
        actions.append(' ', link('Edit current', path + '?' + new URLSearchParams({edit: row.transaction_id})), ' ', link('Void current', `${base}/journal/${journal}/void`));
      }
      tr.append(actions); body.append(tr);
    }
  }
  async function refresh(more = false) {
    if (!c.supported) return;
    const epoch = ++queryEpoch, period = $('register-period');
    const input = {account: c.account, date_from: period.elements.date_from.value, date_to: period.elements.date_to.value, limit: 50};
    if (more && nextCursor) input.cursor = nextCursor;
    $('register-next').disabled = true;
    try {
      const result = await command('register.query', input);
      if (epoch !== queryEpoch) return;
      if (!more) $('register-history').tBodies[0].replaceChildren();
      appendRows(result.rows); nextCursor = result.next_cursor;
      $('register-next').hidden = !nextCursor;
      $('register-current').replaceChildren('All entries: ', node('span', money(result.current_balance.balance), 'register-money'), ' (includes future-dated entries)');
      $('register-current-metadata').textContent = metadata(result.current_balance);
      $('register-period-totals').replaceChildren(`Selected period ${result.metadata.period.date_from} through ${result.metadata.period.date_to}: `);
      for (const [key, label] of [['opening', 'Opening'], ['increases', 'Increases'], ['decreases', 'Decreases'], ['closing', 'Closing']]) {
        $('register-period-totals').append(label + ' ', node('span', money(result.totals[key]), 'register-money'), key === 'closing' ? '' : ' · ');
      }
      $('register-metadata').textContent = `Period snapshot: ${metadata(result.metadata)} · ${result.metadata.currency} · ${result.metadata.basis} · report ${result.metadata.report_version} · schema ${result.metadata.schema_revision}`;
      $('register-query-error').textContent = result.rows.length ? '' : 'No movements in this period.';
    } catch (e) {
      if (epoch !== queryEpoch) return;
      if (more && e.code === 'E_QUERY_STALE') {
        nextCursor = null; $('register-history').tBodies[0].replaceChildren(); $('register-next').hidden = true;
        $('register-query-error').textContent = 'History changed. Restarting the selected period; your draft is retained.'; return refresh();
      }
      $('register-query-error').textContent = 'Balance/history refresh failed. Saved receipts and your draft are retained. Use Refresh. ' + errorText(e);
    } finally { if (epoch === queryEpoch) $('register-next').disabled = false; }
  }
  if (form) {
    payee = partyPicker($('register-payee'), 'Payee'); category = picker($('register-category'), 'Category / transfer account', 'account'); rowClass = picker($('register-class'), 'Row class', 'class');
    load(initial);
    form.addEventListener('input', () => { dirty = true; });
    form.addEventListener('change', () => { dirty = true; });
    form.addEventListener('submit', e => { e.preventDefault(); });
    form.addEventListener('keydown', e => {
      // Includes closed/empty pickers and native controls. Buttons retain their
      // explicit keyboard actions; only the Record button calls the write path.
      if (e.key === 'Enter' && !e.target.matches('button')) e.preventDefault();
      if (e.key === 'Escape' && !$('register-splits').hidden && !e.target.closest('.register-picker')) { e.preventDefault(); $('register-splits').hidden = true; $('register-splits-open').focus(); }
    });
    function dateShortcut(key) {
      if (!['t', 'T', '+', '-', '−'].includes(key)) return false;
      const input = field('date'), value = input.value;
      const wholeSelected = input.selectionStart === 0 && input.selectionEnd === value.length;
      const atEnd = input.selectionStart === value.length && input.selectionEnd === value.length;
      // A hyphen typed inside a date is a separator, not a decrement. This also
      // preserves ordinary partial-date entry on keyboards emitting beforeinput.
      if (['-', '−'].includes(key) && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || (!wholeSelected && !atEnd))) return false;
      if (key.toLowerCase() === 't') input.value = c.today;
      else {
        const source = value || c.today;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(source)) return false;
        const date = new Date(source + 'T12:00:00Z');
        if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== source) return false;
        date.setUTCDate(date.getUTCDate() + (['-', '−'].includes(key) ? -1 : 1));
        const shifted = date.toISOString().slice(0, 10);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(shifted)) return false;
        input.value = shifted;
      }
      dirty = true; input.select(); return true;
    }
    field('date').addEventListener('keydown', e => {
      if (!e.isComposing && !e.ctrlKey && !e.metaKey && !e.altKey && dateShortcut(e.key)) e.preventDefault();
    });
    field('date').addEventListener('beforeinput', e => {
      if (!e.isComposing && e.inputType === 'insertText' && dateShortcut(e.data)) e.preventDefault();
    });
    $('register-calendar-open').addEventListener('click', () => { $('register-calendar-label').hidden = false; $('register-calendar').value = field('date').value; $('register-calendar').focus(); $('register-calendar').showPicker?.(); });
    $('register-calendar').addEventListener('change', () => { field('date').value = $('register-calendar').value; $('register-calendar-label').hidden = true; dirty = true; dateFocus(); });
    $('register-splits-open').addEventListener('click', () => {
      if (!splitMode) { splitMode = true; const value = {}; try { value.account = category.value(); } catch (_) {} value.amount = field('amount').value; value.memo = field('memo').value === (edit?.memo ?? '') ? (edit?.memo ?? null) : (field('memo').value || null); try { value.party = payee.value(); } catch (_) {} if (edit?.category_line_id) value.line_id = edit.category_line_id;
        addSplit(value, {account: category.label(), party: payee.label()}); dirty = true; }
      $('register-category').hidden = true; $('register-splits').hidden = false; splits[0]?.a.input.focus();
    });
    $('register-split-add').addEventListener('click', () => { dirty = true; addSplit()?.a.input.focus(); });
    $('register-split-clear').addEventListener('click', () => { splits = []; $('register-allocations').replaceChildren(); splitMode = false; $('register-splits').hidden = true; $('register-category').hidden = false; dirty = true; category.input.focus(); });
    $('register-split-close').addEventListener('click', () => { $('register-splits').hidden = true; $('register-splits-open').focus(); });
    $('register-recalculate').addEventListener('click', async () => {
      if (pending || sending || storageBlocked) { pendingView(); return; }
      if (!window.bookflowMath.prepare($('register-allocations'))) return;
      try { const rows = allocations().map(({line_id, ...a}) => a); const result = await command('register.calculate', {account: c.account, direction: field('direction').value, allocations: rows});
        field('amount').value = result.amount.amount; window.bookflowMath.refresh(); dirty = true; $('register-error').textContent = ''; $('register-recalculate').focus();
      } catch (e) { error(e); }
    });
    $('register-record').addEventListener('click', record);
    $('register-restore').addEventListener('click', () => { if (pending) { pendingView(); return; } load(initial); dateFocus(); });
    $('register-new').addEventListener('click', () => { if (pending || (dirty && !confirm('Discard the changed draft and begin a new journal?'))) return; initial = null; load(null); dateFocus(); });
  }
  try {
    const encoded = sessionStorage.getItem(storageKey);
    if (encoded) {
      if (new Blob([encoded]).size > maxBytes) throw new Error('Recovery state exceeds its limit.');
      const value = JSON.parse(encoded);
      if (value.actor !== c.actor) sessionStorage.removeItem(storageKey);
      else {
        if (!value.key || !Number.isFinite(value.time) || !['register.post', 'register.update'].includes(value.command) || JSON.stringify(value.payload) !== value.wire) throw new Error('Recovery state is invalid.');
        pending = value;
        if (form && value.company === c.company && value.account === c.account) {
          load({payload: value.payload, labels: value.labels, customState: value.customState, custom_fields: value.custom_fields});
          for (const [name, header] of [['reason', 'X-Bookflow-Reason'], ['source_ref', 'X-Bookflow-Source-Ref'], ['directive_id', 'X-Bookflow-Directive']]) field(name).value = value.context[header] || '';
        }
      }
    }
  } catch (_) { storageBlocked = true; }
  pendingView(); $('register-retry').addEventListener('click', sendPending);
  $('register-reconciled').addEventListener('change', () => { $('register-finish-reconciliation').disabled = !$('register-reconciled').checked; });
  $('register-finish-reconciliation').addEventListener('click', () => {
    if (!pending || !$('register-reconciled').checked || Date.now() - pending.time < retention || sending) return;
    if (!confirm(`Finish reconciliation for request ${pending.key}? This clears its protected payload. Any new entry will be a separate write.`)) return;
    try { forgetResolved(); initial = null; load(null); dateFocus(); } catch (_) { storageBlocked = true; pendingView(); }
  });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') verifyActor().catch(error); });
  window.addEventListener('pageshow', e => { if (e.persisted) verifyActor().catch(error); });
  document.addEventListener('submit', e => { if (e.target.getAttribute('action') === '/logout') logout(); }, true);
  document.addEventListener('htmx:beforeRequest', e => { if (e.detail.elt?.closest('form[action="/logout"]')) logout(); });
  window.addEventListener('beforeunload', e => { if (dirty && !pending) { e.preventDefault(); e.returnValue = ''; } });
  root.addEventListener('focusin', e => { setTimeout(() => e.target.scrollIntoView({block: 'nearest', inline: 'nearest'}), 0); });
  window.visualViewport?.addEventListener('resize', () => { if (root.contains(document.activeElement)) document.activeElement.scrollIntoView({block: 'nearest', inline: 'nearest'}); });
  if (c.supported) { $('register-period').addEventListener('submit', e => { e.preventDefault(); refresh(); }); $('register-next').addEventListener('click', () => refresh(true)); refresh(); }
  dateFocus();
})();
