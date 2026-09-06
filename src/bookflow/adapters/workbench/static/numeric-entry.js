/* Numeric entry assistance. Only normalized decimal values reach commands. */
(() => {
  'use strict';
  function gcd(a, b) { a = a < 0n ? -a : a; while (b) [a, b] = [b, a % b]; return a; }
  function rational(n, d = 1n) {
    if (!d) throw Error('Cannot divide by zero.');
    if (d < 0n) { n = -n; d = -d; }
    if (n.toString().length > 513 || d.toString().length > 512) throw Error('Calculation is too large.');
    const g = gcd(n, d); return {n: n / g, d: d / g};
  }
  function decimal(n, scale) {
    const negative = n < 0n; if (negative) n = -n;
    let s = n.toString().padStart(scale + 1, '0');
    if (scale) s = (s.slice(0, -scale) + '.' + s.slice(-scale)).replace(/0+$/, '').replace(/\.$/, '');
    return (negative && n ? '-' : '') + s;
  }
  function evaluate(source, scale, {integer = scale === 0} = {}) {
    if (typeof source !== 'string' || source.length > 256) throw Error('Use at most 256 characters.');
    if (scale !== null && (!Number.isInteger(scale) || scale < 0 || scale > 18)) throw Error('Field precision is unavailable.');
    const text = source.trim().replace(/=$/, '').replace(/×/g, '*').replace(/÷/g, '/');
    const tokens = []; let cursor = 0;
    while (cursor < text.length) {
      if (/\s/.test(text[cursor])) { cursor++; continue; }
      const match = /^(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[+*/()%\-])/.exec(text.slice(cursor));
      if (!match) throw Error('Use numbers, +, -, *, /, % and parentheses.');
      tokens.push(match[0]); cursor += match[0].length;
      if (tokens.length > 128) throw Error('Calculation has too many terms.');
    }
    let at = 0;
    function primary(depth) {
      if (depth > 24) throw Error('Calculation is nested too deeply.');
      let result;
      const token = tokens[at++];
      if (token === '+' || token === '-') {
        result = primary(depth + 1);
        return token === '-' ? rational(-result.n, result.d) : result;
      }
      if (token === '(') {
        result = expression(depth + 1);
        if (tokens[at++] !== ')') throw Error('Complete the parentheses.');
      } else if (token && /^[0-9.]/.test(token)) {
        const [whole, fraction = ''] = token.split('.');
        result = rational(BigInt((whole || '0') + fraction), 10n ** BigInt(fraction.length));
      } else throw Error('Complete the calculation.');
      if (tokens[at] === '%') { at++; result = rational(result.n, result.d * 100n); }
      return result;
    }
    function term(depth) {
      let a = primary(depth);
      while (tokens[at] === '*' || tokens[at] === '/') {
        const op = tokens[at++], b = primary(depth);
        a = op === '*' ? rational(a.n * b.n, a.d * b.d) : rational(a.n * b.d, a.d * b.n);
      }
      return a;
    }
    function expression(depth) {
      let a = term(depth);
      while (tokens[at] === '+' || tokens[at] === '-') {
        const op = tokens[at++], b = term(depth);
        a = rational(a.n * b.d + (op === '+' ? b.n : -b.n) * a.d, a.d * b.d);
      }
      return a;
    }
    const result = expression(0);
    if (at !== tokens.length) throw Error('Separate terms with an operator.');
    const fraction = result.d === 1n ? result.n.toString() : `${result.n}/${result.d}`;
    // Derive the shortest finite decimal, bounded by supported field precision.
    let remainder = result.d, twos = 0, fives = 0;
    while (remainder % 2n === 0n) { remainder /= 2n; twos++; }
    while (remainder % 5n === 0n) { remainder /= 5n; fives++; }
    const places = Math.max(twos, fives);
    const exact = remainder === 1n && places <= (scale === null ? 0 : scale);
    if (exact) return {fraction, value: decimal(result.n * (10n ** BigInt(places)) / result.d, places), needsRounding: false};
    if (integer) throw Error('This field requires a whole-number result.');
    if (scale === null) return {fraction, value: null, needsRounding: true};
    const sign = result.n < 0n ? -1n : 1n, numerator = (result.n * sign) * 10n ** BigInt(scale);
    let rounded = numerator / result.d; const rest = numerator % result.d;
    if (rest * 2n > result.d || (rest * 2n === result.d && rounded % 2n)) rounded++;
    return {fraction, value: decimal(sign * rounded, scale), needsRounding: true};
  }
  const isExpression = value => /[+*/()%×÷=]/.test(value) || value.trim().slice(1).includes('-');
  const api = {evaluate, isExpression};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  if (window.bookflowMath) return;
  const selector = 'input[data-math-scale], input[data-math-currency]';
  const states = new WeakMap(); let sequence = 0;
  function active(input) {
    if (!input.matches(selector) || input.disabled || input.readOnly || input.type === 'hidden') return false;
    return window.bookflowMathContext?.active ? window.bookflowMathContext.active(input) : true;
  }
  function context(input) {
    if (window.bookflowMathContext?.resolve) return window.bookflowMathContext.resolve(input);
    const currency = input.dataset.mathCurrency;
    if (currency !== undefined) {
      const config = document.querySelector('[data-math-config]');
      const currencies = config ? JSON.parse(config.textContent) : {};
      return {scale: currencies[currency] ?? null, currency};
    }
    return {scale: Number(input.dataset.mathScale)};
  }
  function state(input) {
    let s = states.get(input);
    if (s) return s;
    const box = document.createElement('span'), output = document.createElement('span');
    box.className = 'numeric-calculation'; box.hidden = true;
    output.id = `calculation-${++sequence}`; output.setAttribute('role', 'status'); output.setAttribute('aria-live', 'polite');
    box.append(output); input.after(box);
    input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), output.id].filter(Boolean).join(' '));
    input.title = [input.title, 'Calculate with +, -, *, / and parentheses. Enter or Tab uses the result.'].filter(Boolean).join(' ');
    s = {box, output, offer: null, invalid: false}; states.set(input, s);
    return s;
  }
  function clear(input) {
    const s = states.get(input); if (!s) return;
    if (s.invalid) input.setCustomValidity('');
    s.invalid = false; s.offer = null; s.box.hidden = true;
  }
  function inspect(input) {
    if (!active(input) || !isExpression(input.value)) { clear(input); return null; }
    const s = state(input); s.box.hidden = false;
    try {
      let ctx = context(input), expression = input.value, suffix = '';
      // Existing currency-valued controls may carry a literal ISO suffix (e.g. JPY).
      const match = input.hasAttribute('data-math-currency') && /\s*([A-Z]{3})\s*$/.exec(expression);
      if (match) {
        const currencies = JSON.parse(document.getElementById('math-currencies')?.textContent || '{}');
        const currency = match[1];
        ctx = {...ctx, currency, scale: Object.hasOwn(currencies, currency) ? currencies[currency] : null};
        expression = expression.slice(0, match.index); suffix = ' ' + currency;
      }
      const result = evaluate(expression, ctx.scale, {integer: ctx.scale === 0 && !ctx.currency});
      if (result.value !== null) result.value += suffix;
      const key = JSON.stringify([input.value, ctx]);
      s.output.textContent = result.value === null ? `= ${result.fraction}. Choose a currency to finish the calculation.` : result.needsRounding ? `≈ ${result.value} (rounded to ${ctx.scale} decimal places)` : `= ${result.value}`;
      input.setCustomValidity(result.value === null ? 'Choose a currency to finish the calculation.' : '');
      s.invalid = result.value === null;
      s.offer = {key, result}; return s.offer;
    } catch (error) {
      s.output.textContent = error.message; s.offer = null;
      input.setCustomValidity(error.message); s.invalid = true; return null;
    }
  }
  function commit(input, value) {
    input.value = value; clear(input);
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  }
  function resolve(input) {
    if (!active(input)) { clear(input); return true; }
    if (!isExpression(input.value)) { clear(input); return true; }
    const current = inspect(input);
    if (!current || current.result.value === null) return false;
    commit(input, current.result.value); return true;
  }
  function prepare(root) {
    if (!root) return true;
    let first = null;
    for (const input of root.querySelectorAll(selector)) if (!resolve(input)) first ||= input;
    if (first) { first.focus(); first.reportValidity(); }
    return !first;
  }
  function refresh() {
    document.querySelectorAll(selector).forEach(input => {
      if (input.type === 'number') input.type = 'text';
      state(input);
      if (states.get(input).offer || states.get(input).invalid) inspect(input);
    });
  }
  Object.assign(api, {active, context, prepare, refresh}); window.bookflowMath = api;
  document.addEventListener('input', event => {
    if (event.isComposing) return;
    if (event.target.matches?.(selector)) inspect(event.target);
  });
  document.addEventListener('change', refresh);
  document.addEventListener('focusin', event => { if (event.target.matches?.(selector)) state(event.target); });
  document.addEventListener('blur', event => { if (event.target.matches?.(selector)) resolve(event.target); }, true);
  document.addEventListener('keydown', event => {
    if (!event.isComposing && event.key === 'Enter' && event.target.matches?.(selector) && active(event.target)) {
      event.preventDefault(); event.stopImmediatePropagation(); resolve(event.target);
    }
  }, true);
  document.addEventListener('click', event => {
    const button = event.target.closest?.('button, input[type=submit]');
    if (!button || button.type !== 'submit' || !button.form) return;
    if (!prepare(button.form)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('submit', event => {
    if (!prepare(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('htmx:configRequest', event => {
    const form = event.detail.elt?.closest('form');
    // Parameters are already collected. Never normalize only the DOM this late.
    if (form && [...form.querySelectorAll(selector)].some(input => active(input) && isExpression(input.value))) {
      event.preventDefault(); event.stopImmediatePropagation(); prepare(form);
    }
  }, true);
  document.addEventListener('DOMContentLoaded', refresh);
  document.addEventListener('htmx:afterSwap', refresh);
  const observer = new MutationObserver(records => {
    if (records.some(r => [...r.addedNodes].some(n => n.nodeType === 1 && (n.matches?.(selector) || n.querySelector?.(selector))))) refresh();
  });
  observer.observe(document.documentElement, {childList: true, subtree: true});
  refresh();
})();
