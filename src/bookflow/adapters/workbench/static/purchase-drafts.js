/* Expense cards retain text exactly; amounts are calculated by the shared command. */
(function () {
  if (window.bookflowDraftLines) return;
  window.bookflowDraftLines = true;
  document.addEventListener('click', function (event) {
    const add = event.target.closest('[data-draft-add]');
    if (add) {
      const rows = add.closest('form').querySelector('[data-draft-lines]');
      const cards = [...rows.querySelectorAll('[data-draft-line]')];
      if (cards.length >= 200) return;
      const next = Math.max(...cards.map(e => Number(e.dataset.draftLine))) + 1;
      const card = cards[0].cloneNode(true);
      card.dataset.draftLine = next;
      card.querySelector('legend').textContent = 'Expense ' + (next + 1);
      card.querySelectorAll('[name]').forEach(e => {
        e.name = e.name.replace(/^expense:\d+:/, 'expense:' + next + ':');
        if (e.type === 'checkbox') e.checked = false;
        else if (e.tagName === 'SELECT') e.value = 'inherit';
        else e.value = '';
      });
      rows.appendChild(card);
      card.querySelector('input').focus();
    }
    const remove = event.target.closest('[data-draft-remove]');
    if (remove && remove.closest('[data-draft-lines]').children.length > 1) {
      remove.closest('[data-draft-line]').remove();
    }
  });
})();
