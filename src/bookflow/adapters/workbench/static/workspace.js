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
