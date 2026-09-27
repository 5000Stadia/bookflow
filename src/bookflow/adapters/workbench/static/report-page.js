/* After a report is run from its form, land on the result rather than wherever the form was. */
(() => {
  const heading = document.querySelector('[data-report-focus]');
  if (!heading) return;
  requestAnimationFrame(() => {
    if (!heading.isConnected) return;
    heading.focus({preventScroll: true});
    heading.scrollIntoView({block: 'start'});
  });
})();
