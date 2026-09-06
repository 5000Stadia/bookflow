/* Presentation state only. Commands validate identities, authority and writes. */
(() => {
  if (window.bookflowWorkflow) return;
  window.bookflowWorkflow = true;
  let sequence = 0;
  const requests = new WeakMap();
  const normalize = value => (value || '').replaceAll('_', '-');
  const named = (form, name) => form?.querySelector(`[name="${CSS.escape(name)}"]`);
  const wire = picker => picker.querySelector('[data-ref-value]');
  const search = picker => picker.querySelector('[data-ref-search]');
  const status = (picker, text) => { picker.querySelector('[data-ref-status]').textContent = text; };
  function close(picker) {
    picker.querySelector('[data-ref-options]').hidden = true;
    search(picker).setAttribute('aria-expanded', 'false');
    search(picker).removeAttribute('aria-activedescendant');
  }
  function clear(picker, message = '') {
    clearTimeout(picker.searchTimer);
    requests.get(picker)?.abort();
    const previous = wire(picker).value;
    wire(picker).value = '';
    search(picker).value = '';
    search(picker).setCustomValidity('');
    picker.querySelector('[data-ref-state]').value = 'clear';
    const checkbox = named(picker.closest('form'), 'clear:' + wire(picker).name.replace(/^f:/, ''));
    if (checkbox) checkbox.checked = true;
    status(picker, message);
    close(picker);
    syncVersions(picker, null);
    if (previous) invalidateUnit(picker);
  }
  function syncVersions(picker, option) {
    const form = picker.closest('form');
    for (const [field, value] of [[picker.dataset.refVersionField, option?.dataset.version], [picker.dataset.refLinkVersionField, option?.dataset.linkVersion]]) {
      const input = field && named(form, 'f:' + field);
      if (input) input.value = value || '';
    }
  }
  function invalidateUnit(picker) {
    if (!wire(picker).name.endsWith(':component_item_id') && !wire(picker).name.endsWith(':item')) return;
    const unit = picker.closest('[data-collection-item]')?.querySelector('[data-ref-component]');
    if (unit) clear(unit, 'Component changed. Choose a unit for the selected component, if needed.');
  }
  function choose(picker, option) {
    clearTimeout(picker.searchTimer);
    requests.get(picker)?.abort();
    const changed = wire(picker).value !== option.value;
    wire(picker).value = option.value;
    search(picker).value = option.label;
    picker.querySelector('[data-ref-state]').value = 'selected';
    search(picker).setCustomValidity('');
    const checkbox = named(picker.closest('form'), 'clear:' + wire(picker).name.replace(/^f:/, ''));
    if (checkbox) checkbox.checked = false;
    syncVersions(picker, option);
    status(picker, 'Selected: ' + option.label);
    close(picker);
    if (changed) invalidateUnit(picker);
    search(picker).focus();
  }
  async function suggestions(picker) {
    const input = search(picker);
    requests.get(picker)?.abort();
    const controller = new AbortController();
    requests.set(picker, controller);
    if (!input.value.trim()) { close(picker); return; }
    const url = new URL(picker.dataset.suggestions, location.origin);
    url.searchParams.set('q', input.value.trim());
    const form = picker.closest('form');
    for (const field of ['f:name_type', 'f:customer']) {
      const control = named(form, field);
      if (control) url.searchParams.set(field, control.value);
    }
    if (picker.dataset.refDiscriminator) {
      const kind = normalize(named(form, 'f:' + picker.dataset.refDiscriminator)?.value);
      if (!kind) { status(picker, 'Choose the record type first.'); return; }
      url.searchParams.set('target', kind);
    }
    if (picker.dataset.refComponent) {
      const component = picker.closest('[data-collection-item]')?.querySelector('input[name$=":component_item_id"], input[name$=":item"]');
      if (!component?.value) { status(picker, 'Choose a component first.'); return; }
      url.searchParams.set(component.name, component.value);
    }
    status(picker, 'Searching…');
    try {
      const response = await fetch(url, {credentials: 'same-origin', signal: controller.signal});
      if (!response.ok) throw new Error('Lookup unavailable. Try again.');
      const markup = await response.text();
      if (controller.signal.aborted || !picker.isConnected) return;
      const data = picker.querySelector('[data-ref-data]');
      data.innerHTML = markup;
      const options = picker.querySelector('[data-ref-options]');
      options.replaceChildren();
      [...data.options].forEach(option => {
        const button = document.createElement('button');
        button.type = 'button'; button.role = 'option'; button.tabIndex = -1;
        button.id = 'reference-choice-' + (++sequence);
        button.textContent = option.label;
        button.addEventListener('click', () => choose(picker, option));
        options.append(button);
      });
      options.hidden = !data.options.length;
      input.setAttribute('aria-expanded', String(!!data.options.length));
      status(picker, data.options.length ? 'Choose a match. Use ↓ and Enter, or click a name.' : 'No matching records. Refine the name or add a new record.');
    } catch (error) {
      if (error.name !== 'AbortError') { close(picker); status(picker, error.message); }
    }
  }
  function refresh(form) {
    form.querySelectorAll('[data-definition-default]').forEach(wrapper => {
      const previous = wrapper.firstElementChild;
      const boolean = named(form, 'f:kind')?.value === 'bool';
      if ((previous.tagName === 'SELECT') === boolean) return;
      const control = document.createElement(boolean ? 'select' : 'input');
      control.name = previous.name; control.id = previous.id;
      if (boolean) {
        for (const [value, label] of [['', '(no default)'], ['true', 'true'], ['false', 'false']]) {
          control.add(new Option(label, value));
        }
        control.value = ['true', 'false'].includes(previous.value) ? previous.value : '';
      } else {
        control.value = previous.value;
        control.dataset.mathScale = '9';
        control.dataset.mathActive = JSON.stringify([{name:'f:kind', values:['number']}]);
      }
      previous.replaceWith(control);
    });
    form.querySelectorAll('[data-visibility-cases]').forEach(row => {
      const shown = JSON.parse(row.dataset.visibilityCases).some(conditions =>
        conditions.every(condition => condition.values.includes(named(form, 'f:' + condition.field)?.value)));
      row.hidden = !shown;
      row.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = !shown; });
    });
    form.querySelectorAll('[data-when-field]').forEach(row => {
      const control = named(form, 'f:' + row.dataset.whenField);
      const shown = control && JSON.parse(row.dataset.whenValues).includes(control.value);
      row.hidden = !shown;
      row.querySelectorAll('input,select,textarea').forEach(input => { input.disabled = !shown; });
    });
    form.querySelectorAll('[data-ref-discriminator]').forEach(picker => {
      const selected = normalize(named(form, 'f:' + picker.dataset.refDiscriminator)?.value);
      picker.querySelectorAll('[data-ref-add-target]').forEach(link => { link.hidden = link.dataset.refAddTarget !== selected; });
    });
    form.querySelectorAll('[data-ref-state]').forEach(state => {
      if (state.value === 'pending') search(state.closest('[data-reference]')).setCustomValidity('Choose a matching record or use Clear.');
    });
  }
  function initialize() {
    // A full-body preview/error response replaces form nodes. Rebind the
    // transport before the next user action, as well as presentation state.
    if (window.htmx && document.body) window.htmx.process(document.body);
    document.querySelectorAll('[data-generated-form]').forEach(refresh);
  }
  document.addEventListener('DOMContentLoaded', initialize);
  document.addEventListener('htmx:afterSwap', initialize);
  document.addEventListener('input', event => {
    if (!event.target.matches('[data-ref-search]')) return;
    const picker = event.target.closest('[data-reference]');
    requests.get(picker)?.abort();
    wire(picker).value = '';
    picker.querySelector('[data-ref-state]').value = 'pending';
    event.target.setCustomValidity('Choose a matching record or use Clear.');
    syncVersions(picker, null);
    invalidateUnit(picker);
    close(picker);
    clearTimeout(picker.searchTimer);
    picker.searchTimer = setTimeout(() => suggestions(picker), 200);
  });
  document.addEventListener('keydown', event => {
    const picker = event.target.closest('[data-reference]');
    if (!picker || !event.target.matches('[data-ref-search],[role="option"]')) return;
    const options = picker.querySelector('[data-ref-options]');
    if (event.key === 'Escape') { close(picker); search(picker).focus(); return; }
    if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
    if (event.key === 'Enter' && event.target.matches('[role="option"]')) { event.preventDefault(); event.target.click(); return; }
    if (options.hidden) { if (event.key !== 'Enter') { event.preventDefault(); suggestions(picker); } return; }
    const buttons = [...options.querySelectorAll('button')];
    if (event.key === 'Enter') { event.preventDefault(); return; }
    event.preventDefault();
    const current = buttons.indexOf(document.activeElement);
    const next = (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-custom-adopt]');
    if (!button) return;
    const form = button.closest('[data-generated-form]'), id = button.dataset.customAdopt;
    named(form, 'cf-kind:' + id).value = button.dataset.currentKind;
    named(form, 'cf-state:' + id).value = 'set';
    form.querySelector('button[value="preview"]').click();
  });
  document.addEventListener('input', event => {
    if (!event.target.name?.startsWith('cf:')) return;
    const form = event.target.closest('[data-generated-form]');
    if (form) named(form, 'cf-state:' + event.target.name.slice(3)).value = 'set';
  });
  document.addEventListener('change', event => {
    const form = event.target.closest('[data-generated-form]');
    if (!form) return;
    form.querySelectorAll('[data-ref-discriminator]').forEach(picker => {
      if (event.target.name === 'f:' + picker.dataset.refDiscriminator) clear(picker, 'Record type changed. Choose a matching record.');
    });
    if (event.target.name?.startsWith('clear:') && event.target.checked) {
      const value = named(form, 'f:' + event.target.name.slice(6));
      if (value?.matches('[data-ref-value]')) clear(value.closest('[data-reference]'));
    }
    refresh(form);
  });
  document.addEventListener('click', event => {
    const form = event.target.closest('[data-generated-form]');
    if (!form) return;
    if (event.target.closest('[data-ref-clear]')) { clear(event.target.closest('[data-reference]')); return; }
    const add = event.target.closest('[data-collection-add]');
    if (add) {
      const collection = add.closest('[data-collection]');
      const template = collection.querySelector(':scope > [data-collection-template]');
      const index = `new${Date.now()}${sequence++}`;
      const items = collection.querySelector(':scope > [data-collection-items]');
      items.insertAdjacentHTML('beforeend', template.innerHTML.replaceAll(template.dataset.indexToken, index));
      if (window.htmx) window.htmx.process(items.lastElementChild);
      refresh(form); return;
    }
    const item = event.target.closest('[data-collection-item]');
    if (!item) return;
    if (event.target.closest('[data-collection-remove]')) item.remove();
    else if (event.target.closest('[data-collection-up]') && item.previousElementSibling) item.parentElement.insertBefore(item, item.previousElementSibling);
    else if (event.target.closest('[data-collection-down]') && item.nextElementSibling) item.parentElement.insertBefore(item.nextElementSibling, item);
  });
  document.addEventListener('invalid', event => { event.target.closest('details')?.setAttribute('open', ''); }, true);
  addEventListener('message', event => {
    if (event.origin !== location.origin || !event.data || event.data.type !== 'bookflow-reference-created') return;
    const link = document.querySelector(`[data-return-token="${CSS.escape(event.data.token || '')}"]`);
    const picker = link?.closest('[data-reference]');
    if (!picker || link.hidden || link.dataset.refAddTarget !== event.data.target || typeof event.data.id !== 'string' || typeof event.data.label !== 'string') return;
    const option = document.createElement('option');
    option.value = event.data.id; option.label = event.data.label;
    if (Number.isInteger(event.data.version)) option.dataset.version = String(event.data.version);
    choose(picker, option);
  });
  initialize();
})();


// A pending register intent is private to the signed-in person in this tab.
(() => {
  let cleared = false;
  function clearRegisterIntent() {
    if (cleared) return;
    cleared = true;
    try { sessionStorage.removeItem('bookflow-register-pending-v1'); } catch (_) {}
    if (typeof BroadcastChannel === 'function') {
      const channel = new BroadcastChannel('bookflow-register-identity');
      channel.postMessage('logout'); channel.close();
    }
  }
  document.addEventListener('submit', event => {
    if (event.target.matches('form[action="/logout"]')) clearRegisterIntent();
  }, true);
  document.addEventListener('htmx:beforeRequest', event => {
    if (event.detail.elt?.closest('form[action="/logout"]')) clearRegisterIntent();
  });
})();
