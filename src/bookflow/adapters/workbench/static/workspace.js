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
  const navigation = document.getElementById('workspace-navigation');
  if (!navigation) return;
  const trigger = navigation.querySelector('summary');
  const desktop = window.matchMedia('(min-width: 1100px)');
  const sync = () => {
    const focused = document.activeElement;
    const leavingLinks = !desktop.matches && navigation.querySelector('nav').contains(focused);
    const leavingTrigger = desktop.matches && focused === trigger;
    navigation.open = desktop.matches;
    trigger.setAttribute('aria-expanded', String(navigation.open));
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
  desktop.addEventListener('change', layout);
  layout();
  navigation.addEventListener('toggle', () => trigger.setAttribute('aria-expanded', String(navigation.open)));
  desktop.addEventListener('change', sync);
  sync();
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !desktop.matches && navigation.open) {
      navigation.open = false;
      trigger.focus();
    }
  });
  document.addEventListener('click', event => {
    if (!desktop.matches && navigation.open && !navigation.contains(event.target)) navigation.open = false;
  });
  const path = window.location.pathname.replace(/\/$/, '');
  const sections = {
    customer:'customers', invoice:'customers', estimate:'customers', payment:'customers',
    'receive-payments':'customers', 'sales-receipt':'customers', 'credit-memo':'customers',
    'customer-refund':'customers', vendor:'vendors', bill:'vendors', 'purchase-order':'vendors',
    'item-receipt':'vendors', 'vendor-credit':'vendors', 'pay-bills':'vendors',
    employee:'employees', item:'items', inventory:'items', deposit:'banking', register:'banking',
    reconcile:'banking', account:'accounting', journal:'accounting', report:'reports',
    company:'company', audit:'audit'
  };
  const noun = path.split('/')[3];
  for (const link of navigation.querySelectorAll('a')) {
    if (link.getAttribute('href').replace(/\/$/, '') === path || (noun !== '_group' && sections[noun] && link.dataset.section === sections[noun])) {
      link.setAttribute('aria-current', 'page');
    }
  }
})();
