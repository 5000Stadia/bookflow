/* Only an explicit user action opens the browser's print/save dialog. */
document.addEventListener('click', event => {
  if (event.target.closest('#report-print-dialog')) window.print();
});
