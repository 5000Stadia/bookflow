/* Presentation only: preserve the same forms, URLs and accounting actions. */
(() => {
  // Optional sections must never conceal browser validation failures.
  document.addEventListener('invalid', event => {
    let section = event.target.closest('details');
    while (section) { section.open = true; section = section.parentElement.closest('details'); }
  }, true);
  const phone = window.matchMedia('(max-width: 700px), (max-height: 500px) and (max-width: 1099px)');
  const errorSummary = document.querySelector('[data-submit-error]');
  if (errorSummary && phone.matches) requestAnimationFrame(() => {
    errorSummary.focus({preventScroll:true});
    errorSummary.scrollIntoView({block:'center'});
  });
  for (const box of document.querySelectorAll('#payment-error, #pay-bills-error')) {
    box.tabIndex = -1;
    const reveal = () => {
      if (box.hidden || !phone.matches) return;
      requestAnimationFrame(() => {
        if (!box.isConnected || box.hidden) return;
        box.focus({preventScroll:true});
        box.scrollIntoView({block:'center'});
      });
    };
    new MutationObserver(reveal).observe(box, {attributes:true, attributeFilter:['hidden']});
    reveal();
  }
  // A lost response is not evidence that a write failed. Never resubmit here.
  if (window.bookflowSubmitFeedback) window.bookflowSubmitFeedback.abort();
  const feedback = new AbortController();
  window.bookflowSubmitFeedback = feedback;
  const connectionError = event => {
    const form = event.detail?.elt?.closest('form');
    if (!form || !form.isConnected) return;
    let box = form.querySelector('[data-connection-error]');
    if (!box) {
      box = document.createElement('section'); box.className = 'error';
      box.dataset.connectionError = ''; box.setAttribute('role','alert');
      box.tabIndex = -1;
      const actions = form.querySelector('.document-actions, .form-submit, .actions');
      if (actions) actions.before(box); else form.append(box);
    }
    box.textContent = 'The response could not be confirmed. Your entries are retained. A save may have completed; check the record before trying again.';
    if (phone.matches) { box.focus({preventScroll:true}); box.scrollIntoView({block:'center'}); }
  };
  for (const name of ['htmx:sendError', 'htmx:timeout', 'htmx:responseError']) document.addEventListener(name, connectionError, {signal:feedback.signal});
  // A form that replaces the whole page leaves focus nowhere, so the next Tab lands on the skip link
  // wherever the page happens to be scrolled. Move focus to what the submit produced instead.
  document.addEventListener('htmx:beforeSwap', event => {
    if (event.detail.target === document.body) window.bookflowPageSwapped = true;
  }, {signal:feedback.signal});
  document.addEventListener('htmx:afterSettle', () => {
    if (!window.bookflowPageSwapped) return;
    window.bookflowPageSwapped = false;
    requestAnimationFrame(() => {
      const focused = document.activeElement;
      if (focused && focused !== document.body && focused.isConnected) return;
      const main = document.getElementById('main-content');
      if (!main) return;
      const shown = element => element.getClientRects().length > 0;
      // An error first; then the first section that is a result rather than a form's wrapper.
      let target = [...main.querySelectorAll('[data-submit-error], [data-submit-error-top], [role=alert]')].find(shown);
      if (!target) {
        const section = [...main.querySelectorAll('section')].find(s => shown(s) && !s.querySelector('form') && !s.closest('nav'));
        target = section ? ([...section.querySelectorAll('h2, h3')].find(shown) || section) : main.querySelector('h1');
      }
      if (!target) return;
      if (!target.hasAttribute('tabindex')) target.tabIndex = -1;
      target.dataset.swapFocus = '';
      target.focus({preventScroll:true});
      target.scrollIntoView({block: phone.matches && target.matches('[data-submit-error]') ? 'center' : 'start'});
    });
  }, {signal:feedback.signal});
  // A section's "+ New" menu closes on Escape, on a tap outside it and when focus leaves it.
  for (const menu of document.querySelectorAll('[data-new-menu]')) {
    const summary = menu.querySelector('summary');
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.open) { event.stopPropagation(); menu.open = false; summary.focus(); }
    });
    menu.addEventListener('focusout', event => {
      if (menu.open && event.relatedTarget && !menu.contains(event.relatedTarget)) menu.open = false;
    });
    document.addEventListener('click', event => {
      // The phone sheet's backdrop is the menu's own ::before, so a click on the <details> itself is outside.
      if (menu.open && (event.target === menu || !menu.contains(event.target))) menu.open = false;
    }, {signal:feedback.signal});
  }
  // The command finder: every task and command page this reader may open, found by name.
  const finder = document.getElementById('finder');
  if (finder && finder.showModal) {
    const input = finder.querySelector('#finder-input');
    const results = finder.querySelector('#finder-results');
    const status = finder.querySelector('#finder-status');
    const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    for (const key of document.querySelectorAll('[data-finder-key]')) key.textContent = mac ? '⌘K' : 'Ctrl K';
    let index = null, loading = null, shown = [], active = -1, opener = null;
    const load = () => loading || (loading = fetch(finder.dataset.finderUrl, {credentials:'same-origin', headers:{'X-Bookflow-Workbench':'1'}})
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => { index = data.items.map((item, order) => ({...item, order,
        text: [item.label, item.section, ...item.keywords].join(' ').toLowerCase().replace(/[-_]/g, ' ')})); })
      .catch(() => { loading = null; status.textContent = 'The list of tasks could not be loaded. Use All commands below.'; }));
    const rank = {section:0, task:1, page:2};
    const score = (item, query, words) => {
      const label = item.label.toLowerCase();
      if (label === query) return 0;
      if (label.startsWith(query)) return 1;
      if (label.split(/[\s—/-]+/).some(word => word.startsWith(words[0]))) return 2;
      return label.includes(words[0]) ? 3 : 4;
    };
    const select = next => {
      const options = results.querySelectorAll('[role=option]');
      if (!options.length) { active = -1; input.removeAttribute('aria-activedescendant'); return; }
      active = (next + options.length) % options.length;
      options.forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
      input.setAttribute('aria-activedescendant', options[active].id);
      options[active].scrollIntoView({block:'nearest'});
    };
    const render = () => {
      if (!index) return;
      const query = input.value.trim().toLowerCase().replace(/[-_]/g, ' ');
      const words = query.split(/\s+/).filter(Boolean);
      if (words.length) {
        shown = index.filter(item => words.every(word => item.text.includes(word)))
          .map(item => ({item, score: score(item, query, words)}))
          .sort((a, b) => a.score - b.score || rank[a.item.kind] - rank[b.item.kind] || a.item.label.length - b.item.label.length || a.item.order - b.item.order)
          .slice(0, 50).map(entry => entry.item);
      } else {
        // Before anything is typed: the everyday tasks, then the sections.
        shown = [...index.filter(item => item.kind === 'task'), ...index.filter(item => item.kind === 'section')].slice(0, 80);
      }
      results.replaceChildren(...shown.map((item, i) => {
        const option = document.createElement('li');
        option.id = 'finder-option-' + i; option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
        const link = document.createElement('a'); link.href = item.href; link.tabIndex = -1; link.textContent = item.label;
        const where = document.createElement('span'); where.className = 'finder-where'; where.textContent = item.section;
        option.append(link, where);
        option.addEventListener('click', () => { window.location = item.href; });
        return option;
      }));
      status.textContent = words.length && !shown.length ? 'Nothing matches. Try another word, or open All commands.' : '';
      select(0);
    };
    const open = () => {
      if (finder.open) { input.focus(); input.select(); return; }
      opener = document.activeElement;
      input.value = '';
      finder.showModal();
      input.focus();
      if (index) render(); else { status.textContent = 'Loading…'; load().then(() => { if (index) { status.textContent = ''; render(); } }); }
    };
    finder.addEventListener('close', () => {
      if (opener && opener.isConnected) opener.focus();
      opener = null;
    });
    finder.addEventListener('click', event => { if (event.target === finder) finder.close(); });
    finder.querySelector('[data-finder-close]').addEventListener('click', () => finder.close());
    input.addEventListener('input', render);
    input.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); select(active + (event.key === 'ArrowDown' ? 1 : -1)); }
      else if (event.key === 'Escape') { event.preventDefault(); finder.close(); }
      else if (event.key === 'Enter') {
        event.preventDefault();
        if (active >= 0 && shown[active]) window.location = shown[active].href;
      }
    });
    for (const control of document.querySelectorAll('[data-finder-open]')) {
      control.setAttribute('role', 'button');
      control.addEventListener('click', event => { event.preventDefault(); open(); });
    }
    document.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k') { event.preventDefault(); open(); }
    }, {signal:feedback.signal});
  }
  const navigation = document.getElementById('workspace-navigation');
  const trigger = document.getElementById('menu-toggle');
  if (!navigation || !trigger) return;
  const desktop = window.matchMedia('(min-width: 1100px)');
  const isOpen = () => navigation.hasAttribute('data-open');
  // Phones and tablets open the menu from the header button; the desktop sidebar is always shown.
  const setOpen = open => {
    navigation.toggleAttribute('data-open', open);
    trigger.setAttribute('aria-expanded', String(open));
    trigger.querySelector('use').setAttribute('href', open ? '#i-close' : '#i-menu');
  };
  // Chrome may drop focus from a control the new layout hides before the breakpoint change
  // reaches us, so remember what last had focus and hand that on.
  let lastFocused = document.activeElement;
  document.addEventListener('focusin', event => { lastFocused = event.target; }, {signal:feedback.signal});
  const sync = () => {
    const active = document.activeElement;
    const focused = active && active !== document.body ? active : lastFocused;
    const leavingLinks = !desktop.matches && navigation.contains(focused);
    const leavingTrigger = desktop.matches && focused === trigger;
    setOpen(desktop.matches);
    if (leavingLinks) trigger.focus();
    if (leavingTrigger) (navigation.querySelector('a[aria-current]') || navigation.querySelector('a')).focus();
  };
  // Desktop menu layout: collapsed to icons and/or moved to a top bar, remembered on this device.
  const body = document.body;
  const collapse = navigation.querySelector('[data-nav-collapse]');
  const top = navigation.querySelector('[data-nav-top]');
  const layout = () => {
    const collapsed = 'menuCollapsed' in body.dataset, onTop = 'menuTop' in body.dataset;
    collapse.setAttribute('aria-pressed', String(collapsed));
    collapse.querySelector('.nav-label').textContent = collapsed ? 'Expand menu' : 'Collapse menu';
    collapse.querySelector('use').setAttribute('href', collapsed ? '#i-expand' : '#i-collapse');
    top.setAttribute('aria-pressed', String(onTop));
    top.querySelector('.nav-label').textContent = onTop ? 'Move menu to side' : 'Move menu to top';
    top.querySelector('use').setAttribute('href', onTop ? '#i-layout-side' : '#i-layout-top');
    // A collapsed menu shows icons only, so each one names itself on hover.
    for (const control of navigation.querySelectorAll('a[data-tip], .nav-controls button')) {
      const tip = control.dataset.tip || control.querySelector('.nav-label').textContent;
      const iconOnly = collapsed || control.tagName === 'BUTTON';
      if (iconOnly && desktop.matches) control.title = tip; else control.removeAttribute('title');
    }
    try { localStorage.setItem('bookflow.menu', JSON.stringify({collapsed, top: onTop})); } catch (e) {}
  };
  const flip = (key, control) => control.addEventListener('click', () => {
    if (key in body.dataset) delete body.dataset[key]; else body.dataset[key] = '';
    layout();
  });
  flip('menuCollapsed', collapse); flip('menuTop', top);
  desktop.addEventListener('change', layout, {signal:feedback.signal});
  layout();
  trigger.addEventListener('click', () => setOpen(!isOpen()));
  desktop.addEventListener('change', sync, {signal:feedback.signal});
  sync();
  const close = () => { setOpen(false); trigger.focus(); };
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !desktop.matches && isOpen()) close();
  }, {signal:feedback.signal});
  document.addEventListener('click', event => {
    if (!desktop.matches && isOpen() && !navigation.contains(event.target) && !trigger.contains(event.target)) setOpen(false);
  }, {signal:feedback.signal});
  // Tabbing past the last section closes the open menu rather than wandering the page beneath it.
  navigation.addEventListener('focusout', event => {
    if (!desktop.matches && isOpen() && event.relatedTarget && !navigation.contains(event.relatedTarget) && event.relatedTarget !== trigger) setOpen(false);
  });
  const path = window.location.pathname.replace(/\/$/, '');
  const sections = {
    customer:'customers', invoice:'customers', estimate:'customers', payment:'customers',
    'receive-payments':'customers', 'sales-receipt':'customers', 'credit-memo':'customers',
    'customer-refund':'customers', vendor:'vendors', bill:'vendors', 'purchase-order':'vendors',
    'item-receipt':'vendors', 'vendor-credit':'vendors', 'pay-bills':'vendors',
    employee:'employees', item:'items', inventory:'items', deposit:'banking', register:'banking', _registers:'banking',
    reconcile:'banking', account:'accounting', journal:'accounting', report:'reports',
    company:'company', audit:'audit', transfer:'banking', check:'vendors', 'card-charge':'vendors', 'card-credit':'vendors', 'bill-payment':'vendors'
  };
  // The profile lists sit under Settings, where the anchor keeps them.
  for (const list of ['customer-type', 'vendor-type', 'job-type', 'term', 'payment-method', 'sales-rep', 'ship-method',
    'customer-message', 'price-level', 'unit-of-measure', 'item-category', 'sales-tax-code', 'class', 'custom-field']) sections[list] = 'settings';
  // An account register belongs to Banking, wherever its account sits in the chart.
  const noun = /^\/c\/[^/]+\/account\/[^/]+\/register$/.test(path) ? 'register' : path.split('/')[3];
  for (const link of navigation.querySelectorAll('a')) {
    if (link.getAttribute('href').replace(/\/$/, '') === path || (noun !== '_group' && sections[noun] && link.dataset.section === sections[noun])) {
      link.setAttribute('aria-current', 'page');
    }
  }
})();

// A text field that offers "choose a file": read the chosen file as text into it, so a
// statement downloaded from the bank goes in with its line breaks and is never uploaded as a file.
document.addEventListener('change', (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== 'file' || !input.dataset.textFileInto) return;
  const target = document.getElementById(input.dataset.textFileInto);
  const file = input.files && input.files[0];
  if (!target || !file) return;
  file.text().then((text) => {
    target.value = text;
    target.dispatchEvent(new Event('input', { bubbles: true }));
  });
});
