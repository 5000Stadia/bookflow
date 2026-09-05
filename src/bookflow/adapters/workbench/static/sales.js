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
