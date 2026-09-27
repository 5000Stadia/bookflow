/* Integration (no competing transport handler):
 * Mark post/update forms data-sales-form and data-sales-scope=<session identity>.
 * Render hidden f:expected_facts_fingerprint only from a successful preview;
 * omit its generic visible leaf. Add [data-sales-preview-status] aria-live=polite.
 * Load once with workflow.js. In the existing htmx:configRequest handler call
 * bookflowSales.configure(event) BEFORE assigning generic idempotency; when it
 * returns true, skip only generic idempotency assignment, not auth/CSRF headers.
 * On E_PREVIEW_STALE render an empty fingerprint, retaining attempted content.
 * Form comparisons must remain the same through Preview->Save and retries.
 */
(() => {
  if (window.bookflowSales) return;
  const states = new WeakMap(), keys = new Map();
  const fingerprintName = 'f:expected_facts_fingerprint';
  const keyName = 'ctx:idempotency_key';
  const field = (form, name) => form.elements.namedItem(name);
  const canonical = entries => JSON.stringify([...entries].filter(([k]) => k !== keyName)
    .sort((a, b) => a[0].localeCompare(b[0])));
  const snapshot = form => canonical([...new FormData(form)].filter(([k]) => k !== fingerprintName));
  function initialize() {
    document.querySelectorAll('[data-sales-form]').forEach(form => {
      if (!states.has(form)) states.set(form, {snapshot: snapshot(form)});
    });
  }
  function invalidate(form) {
    if (!form) return;
    const control = field(form, fingerprintName);
    if (control) control.value = '';
    const status = form.querySelector('[data-sales-preview-status]');
    if (status) status.textContent = 'Values changed. Preview again before saving.';
  }
  function configure(event) {
    const form = event.detail.elt?.closest('[data-sales-form]') || event.target.closest?.('[data-sales-form]');
    if (!form) return false;
    initialize();
    const parameters = event.detail.parameters;
    const state = states.get(form);
    if (snapshot(form) !== state.snapshot) invalidate(form);
    const fingerprint = field(form, fingerprintName)?.value || '';
    if (parameters.action === 'preview') {
      delete parameters[fingerprintName];
      delete parameters[keyName];
      delete event.detail.headers['Idempotency-Key'];
      return true;
    }
    if (!/^[0-9a-f]{64}$/.test(fingerprint)) {
      invalidate(form);
      event.preventDefault();
      return true;
    }
    parameters[fingerprintName] = fingerprint;
    const payload = JSON.stringify([form.dataset.salesScope, form.action,
      canonical(Object.entries(parameters))]);
    if (!keys.has(payload)) {
      const bytes = crypto.getRandomValues(new Uint8Array(24));
      keys.set(payload, 'sale-' + [...bytes].map(b => b.toString(16).padStart(2, '0')).join(''));
    }
    const key = keys.get(payload);
    parameters[keyName] = key;
    event.detail.headers['Idempotency-Key'] = key;
    if (field(form, keyName)) field(form, keyName).value = key;
    return true;
  }
  for (const type of ['input', 'change']) document.addEventListener(type, event => {
    invalidate(event.target.closest('[data-sales-form]'));
  });
  // Reference selection and collection changes set hidden values without input events.
  document.addEventListener('click', event => {
    const recovery = event.target.closest('[data-billing-recovery-adopt]');
    if (recovery) {
      const amount = recovery.closest('.billing-line').querySelector('[data-billing-recovery-amount]');
      amount.value = recovery.dataset.billingRecoveryAdopt;
      invalidate(recovery.closest('[data-sales-form]'));
    }
    if (event.target.closest('[data-ref-clear],[role="option"],[data-collection-add],[data-collection-remove],[data-collection-up],[data-collection-down],[data-custom-adopt]'))
      invalidate(event.target.closest('[data-sales-form]'));
  }, true);
  // Add-new reference windows update controls in workflow.js; compare after it runs.
  addEventListener('message', () => queueMicrotask(() => {
    document.querySelectorAll('[data-sales-form]').forEach(form => {
      if (states.has(form) && snapshot(form) !== states.get(form).snapshot) invalidate(form);
    });
  }));
  document.addEventListener('DOMContentLoaded', initialize);
  document.addEventListener('htmx:configRequest', configure);
  document.addEventListener('htmx:afterSwap', initialize);
  document.addEventListener('submit', event => {
    if (event.target.matches('form[action="/logout"]')) keys.clear();
  });
  // A background preview that answered exactly what the form still holds carries the same
  // fingerprint the Preview button would have: Save then saves those checked values.
  function adopt(form, fingerprint, sent) {
    const control = field(form, fingerprintName);
    if (!control || !/^[0-9a-f]{64}$/.test(fingerprint) || snapshot(form) !== sent) return false;
    control.value = fingerprint;
    states.set(form, {snapshot: sent});
    const status = form.querySelector('[data-sales-preview-status]');
    if (status) status.textContent = 'Preview checked. Save these values.';
    return true;
  }
  window.bookflowSales = {configure, invalidate, initialize, snapshot, adopt};
  initialize();
})();


/* The document window's line tabs: two grids in one band, one of them showing.
 *
 * A bill is entered on the accounts a person types or on the things they buy, and the
 * command takes both collections at once. So the panel that is not showing stays in the
 * form and still submits — hiding a tab must never drop what the other grid holds, which
 * is exactly what a correction opened on one tab would otherwise do to the other.
 *
 * Without script both panels are simply visible and both grids are enterable; the tab
 * strip is an affordance over a page that already works.
 */
(() => {
  if (window.bookflowLineTabs) return;
  const BAND = '[data-line-bands]';
  const tabsOf = band => [...band.querySelectorAll(':scope > .line-tabs > [data-line-tab]')];
  const panelsOf = band => [...band.querySelectorAll(':scope > [data-line-panel]')];
  const rows = panel => panel.querySelectorAll(
    ':scope > .line-grid > [data-collection-items] > [data-collection-item]').length;
  // The tab a person last chose, so a preview that swaps the whole form in place comes
  // back on the grid they were working on rather than on the first one.
  let chosen = null;
  function show(band, key) {
    tabsOf(band).forEach(tab => tab.setAttribute(
      'aria-selected', tab.dataset.lineTab === key ? 'true' : 'false'));
    panelsOf(band).forEach(panel => { panel.hidden = panel.dataset.linePanel !== key; });
  }
  function initialize() {
    document.querySelectorAll(BAND).forEach(band => {
      const panels = panelsOf(band);
      if (tabsOf(band).length < 2 || panels.length < 2) return;
      const keys = panels.map(panel => panel.dataset.linePanel);
      if (chosen && keys.includes(chosen)) { show(band, chosen); return; }
      // Otherwise open on the grid that has lines: a bill bought wholly on the Items tab
      // reopens on Items rather than on an empty Expenses grid. Lines on both, or on
      // neither, and the first tab is where a person starts.
      const entered = panels.filter(panel => rows(panel) > 0);
      show(band, entered.length === 1 ? entered[0].dataset.linePanel : keys[0]);
    });
  }
  document.addEventListener('click', event => {
    const tab = event.target.closest('[data-line-tab]');
    const band = tab && tab.closest(BAND);
    if (!band) return;
    chosen = tab.dataset.lineTab;
    show(band, chosen);
  });
  document.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-line-tab]');
    const band = tab && tab.closest(BAND);
    if (!band) return;
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    const tabs = tabsOf(band);
    const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
    event.preventDefault();
    chosen = next.dataset.lineTab;
    show(band, chosen);
    next.focus();
  });
  document.addEventListener('DOMContentLoaded', initialize);
  document.addEventListener('htmx:afterSwap', initialize);
  window.bookflowLineTabs = {initialize};
  initialize();
})();


/* Live totals: the server's own preview, run in the background as the person edits.
 *
 * Nothing here computes money. It sends the form exactly as the Preview button would
 * (action=preview, no fingerprint, no retry key; a preview writes nothing), reads the
 * totals, line amounts and terms-derived due date out of the page the server answers with,
 * and puts only those into this page. No input is touched, so what the person is typing,
 * where the cursor is and what has focus all stay as they were. A preview that is refused
 * leaves the totals saying so quietly beside them; the Preview button keeps working.
 *
 * When: on a committed change (leaving a field, choosing from a list, changing a select),
 * after a line is removed or moved, and while typing a number in a line after a pause.
 * A preview measured about 0.5 s for one line and 0.8 s for twenty, so a pause is enough.
 */
(() => {
  if (window.bookflowLiveTotals) return;
  const QUIET = 400, TYPING = 900;
  const runs = new WeakMap();
  const region = form => form?.querySelector('[data-live-totals]');
  const statusOf = form => form.querySelector('[data-live-totals-status]');
  function schedule(form, wait = QUIET) {
    if (!form || !region(form) || !form.querySelector('button[name="action"][value="preview"]')) return;
    const run = runs.get(form) || {seq: 0};
    runs.set(form, run);
    clearTimeout(run.timer);
    run.timer = setTimeout(() => refresh(form), wait);
  }
  function problem(page) {
    const error = page.querySelector('[data-submit-error-top]');
    if (!error) return '';
    const fields = [...error.querySelectorAll('li')].map(li => li.textContent.trim());
    const said = fields.length ? fields.join('; ') : error.textContent.replace(/^\s*E_[A-Z_]+\s*/, '');
    return said.replace(/\s+/g, ' ').trim();
  }
  async function refresh(form) {
    const run = runs.get(form);
    run.controller?.abort();
    const controller = new AbortController();
    run.controller = controller;
    const seq = ++run.seq;
    const sent = window.bookflowSales?.snapshot(form);
    const body = new FormData(form);
    body.delete('f:expected_facts_fingerprint');
    body.delete('ctx:idempotency_key');
    body.set('action', 'preview');
    const status = statusOf(form), totals = region(form);
    totals.setAttribute('aria-busy', 'true');
    if (status) { status.textContent = 'Updating…'; status.dataset.state = 'busy'; }
    try {
      // getAttribute: the form has controls named "action", which shadow form.action.
      const response = await fetch(form.getAttribute('action'), {method: 'POST', body, credentials: 'same-origin',
        signal: controller.signal, headers: {'X-Bookflow-Workbench': '1', 'HX-Request': 'true'}});
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (seq !== run.seq || !form.isConnected) return;
      const answer = page.querySelector('[data-live-totals]');
      if (answer) totals.innerHTML = answer.innerHTML;
      // Line amounts by position: the answer renders the lines in the order they were sent.
      const mine = [...form.querySelectorAll('.line-row .line-amount')];
      const theirs = [...page.querySelectorAll('[data-generated-form] .line-row .line-amount')];
      if (mine.length === theirs.length) mine.forEach((cell, index) => { cell.textContent = theirs[index].textContent; });
      const refused = problem(page);
      if (!refused && form.matches('[data-sales-form]')) {
        const print = page.querySelector('[name="f:expected_facts_fingerprint"]')?.value || '';
        if (sent !== undefined) window.bookflowSales.adopt(form, print, sent);
      }
      if (status) {
        status.textContent = refused ? 'Totals are not up to date: ' + refused : '';
        status.dataset.state = refused ? 'refused' : '';
      }
    } catch (error) {
      if (error.name === 'AbortError' || seq !== run.seq) return;
      if (status) { status.textContent = 'Totals could not be updated just now. Preview still checks them.'; status.dataset.state = 'refused'; }
    } finally {
      if (seq === run.seq) totals.removeAttribute('aria-busy');
    }
  }
  const lineNumber = target => target.matches?.('.line-row input[inputmode="decimal"]');
  document.addEventListener('change', event => {
    if (event.target.matches?.('[data-ref-search]')) return; // a pending name is not a choice yet
    schedule(event.target.closest?.('[data-generated-form]'));
  });
  document.addEventListener('input', event => {
    if (lineNumber(event.target)) schedule(event.target.closest('[data-generated-form]'), TYPING);
  });
  // Choosing from a list, clearing a choice and moving or removing a line set values
  // without a change event; let workflow.js finish, then look again.
  document.addEventListener('click', event => {
    if (!event.target.closest?.('[role="option"]:not(.reference-add-option),[data-ref-clear],[data-collection-remove],[data-collection-up],[data-collection-down]')) return;
    // Captured before workflow.js acts: a removed line no longer knows its form.
    const form = event.target.closest('[data-generated-form]');
    setTimeout(() => schedule(form));
  }, true);
  addEventListener('message', event => {
    if (event.origin === location.origin && event.data?.type === 'bookflow-reference-created')
      setTimeout(() => document.querySelectorAll('[data-generated-form]').forEach(form => schedule(form)));
  });
  window.bookflowLiveTotals = {schedule, refresh};
})();
