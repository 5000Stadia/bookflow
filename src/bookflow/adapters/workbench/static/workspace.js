/* Presentation only: preserve the same forms, URLs and accounting actions. */
(() => {
  // Optional sections must never conceal browser validation failures.
  document.addEventListener('invalid', event => {
    let section = event.target.closest('details');
    while (section) { section.open = true; section = section.parentElement.closest('details'); }
  }, true);
  const navigation = document.getElementById('workspace-navigation');
  if (!navigation) return;
  const trigger = navigation.querySelector('summary');
  const desktop = window.matchMedia('(min-width: 1100px)');
  const sync = () => {
    navigation.open = desktop.matches;
    trigger.setAttribute('aria-expanded', String(navigation.open));
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
