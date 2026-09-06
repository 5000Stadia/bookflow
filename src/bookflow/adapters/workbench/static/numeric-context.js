/* Command-form meaning for arithmetic entry; command validation stays authoritative. */
(() => {
  'use strict';
  const named = (form, name) => name && form?.querySelector(`[name="${CSS.escape(name)}"]`);
  function active(input) {
    if (!input.isConnected || input.matches(':disabled') || input.readOnly || input.type === 'hidden') return false;
    const form = input.form || input.closest('form');
    if (named(form, input.dataset.mathClear)?.checked) return false;
    for (let collection = input.closest('[data-collection]'); collection; collection = collection.parentElement?.closest('[data-collection]')) {
      if (named(form, 'clear:' + collection.dataset.collectionPath)?.checked) return false;
    }
    if (input.dataset.mathActive) {
      let conditions;
      try { conditions = JSON.parse(input.dataset.mathActive); } catch (_) { return false; }
      for (const condition of conditions) {
        const control = named(form, condition.name);
        if (!control || control.matches(':disabled')) return false;
        if (condition.values && !condition.values.includes(control.value)) return false;
        if ('checked' in condition && control.checked !== condition.checked) return false;
      }
    }
    const field = input.dataset.mathLineField;
    const mode = named(form, input.dataset.mathPriceMode)?.value;
    if (mode && field) {
      const selected = {manual: 'unit_price', markup: 'markup_percent', amount: 'net_amount', catalog: null}[mode];
      if (['unit_price', 'markup_percent', 'net_amount'].includes(field) && field !== selected) return false;
      if (['markup', 'amount'].includes(mode) && field === 'price_basis_amount') return false;
    }
    const prefix = input.dataset.mathDefaultsPrefix;
    if (prefix && field && [...form.querySelectorAll('[name]')].some(control =>
      !control.matches(':disabled') && control.name.startsWith(prefix) && control.name.endsWith(':value') && control.value === field)) return false;
    // Hidden panels can still contribute values (notably closed register splits).
    return true;
  }
  function resolve(input) {
    const form = input.form || input.closest('form');
    const mode = named(form, input.dataset.mathModeField)?.value;
    if (mode === 'quantity' || mode === 'percent') return {scale: 6, mode};
    if (input.dataset.mathModeField && mode !== 'net_amount') return {scale: null, mode};
    if (input.hasAttribute('data-math-currency')) {
      const company = input.closest('[data-math-company-currency]')?.dataset.mathCompanyCurrency || '';
      const base = input.dataset.mathCurrency === 'company' ? company : input.dataset.mathCurrency;
      const override = named(form, input.dataset.mathCurrencyField);
      const currency = (override && !override.matches(':disabled') && override.value.trim()) || base;
      let currencies;
      try { currencies = JSON.parse(document.getElementById('math-currencies')?.textContent || '{}'); } catch (_) { currencies = {}; }
      const scale = Object.hasOwn(currencies, currency) ? currencies[currency] : null;
      return {scale, currency, mode};
    }
    const value = input.dataset.mathScale;
    return {scale: /^(?:0|[1-9][0-9]*)$/.test(value || '') ? Number(value) : null, mode};
  }
  window.bookflowMathContext = {active, resolve};
})();
