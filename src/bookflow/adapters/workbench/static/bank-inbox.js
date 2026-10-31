/* Selection and focus only: all sorting and exact amount checks are commands. */
(function () {
  function wire() {
    const root = document.querySelector('#bank-workspace');
    if (!root || root.dataset.wired) return;
    root.dataset.wired = '1';
    const bar = root.querySelector('.bank-bulk');
    function selection() {
      const n = root.querySelectorAll('[name=selected]:checked').length;
      if (!bar) return;
      bar.classList.toggle('has-selection', n > 0);
      bar.querySelector('summary').textContent = n ? n + ' selected — sort together' : 'Actions for selected lines';
      if (n) bar.open = true;
    }
    root.addEventListener('change', event => { if (event.target.name === 'selected') selection(); });
    const sheet = root.querySelector('.bank-sheet');
    if (sheet) {
      const close = sheet.querySelector('.bank-close'); close.focus();
      sheet.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close.click(); }
        if (event.key !== 'Tab') return;
        const items = [...sheet.querySelectorAll('a[href],button,input,select,textarea,summary')]
          .filter(e => !e.disabled && e.offsetParent !== null);
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
    }
    selection();
  }
  document.addEventListener('DOMContentLoaded', wire);
  document.body.addEventListener('htmx:afterSwap', wire);
})();
