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
  window.bookflowSales = {configure, invalidate, initialize};
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
