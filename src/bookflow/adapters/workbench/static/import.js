// R107 increment 3: the import page's small helpers. A pasted table shows at once as a table; a file can be
// dropped on the drop area; the template link follows the chosen kind. Every post itself goes through htmx.
(() => {
  const MAX_ROWS = 6;
  function cells(line, delimiter) {
    return line.split(delimiter).map(cell => cell.replace(/^"(.*)"$/s, '$1').replaceAll('""', '"'));
  }
  function showPaste(area) {
    const box = area.closest('form')?.querySelector('[data-paste-preview]');
    if (!box) return;
    const lines = area.value.split(/\r\n|\r|\n/).filter(line => line.trim());
    box.replaceChildren();
    if (!lines.length) { box.hidden = true; return; }
    const first = lines[0];
    const delimiter = first.includes('\t') ? '\t' : (first.split(';').length > first.split(',').length ? ';' : ',');
    const table = document.createElement('table');
    const caption = table.createCaption();
    caption.textContent = `${lines.length - 1} row${lines.length === 2 ? '' : 's'} pasted` +
      (lines.length - 1 > MAX_ROWS - 1 ? `; the first ${MAX_ROWS - 1} shown` : '');
    const head = table.createTHead().insertRow();
    cells(first, delimiter).forEach(text => { const th = document.createElement('th'); th.textContent = text; head.append(th); });
    const body = table.createTBody();
    lines.slice(1, MAX_ROWS).forEach(line => {
      const row = body.insertRow();
      cells(line, delimiter).forEach(text => { row.insertCell().textContent = text; });
    });
    box.append(table);
    box.hidden = false;
  }
  function wire(root) {
    root.querySelectorAll('[data-import-paste]:not([data-wired])').forEach(area => {
      area.dataset.wired = '';
      area.addEventListener('input', () => showPaste(area));
      if (area.value.trim()) showPaste(area);
    });
    root.querySelectorAll('[data-import-kind]:not([data-wired])').forEach(select => {
      select.dataset.wired = '';
      select.addEventListener('change', () => {
        const link = select.closest('form').querySelector('[data-import-template]');
        if (link) link.href = link.href.replace(/template\/[^/]+\.csv$/, `template/${encodeURIComponent(select.value)}.csv`);
      });
    });
    root.querySelectorAll('[data-import-drop]:not([data-wired])').forEach(zone => {
      zone.dataset.wired = '';
      const input = zone.querySelector('input[type=file]');
      ['dragenter', 'dragover'].forEach(name => zone.addEventListener(name, event => {
        event.preventDefault(); zone.classList.add('is-over');
      }));
      ['dragleave', 'drop'].forEach(name => zone.addEventListener(name, () => zone.classList.remove('is-over')));
      zone.addEventListener('drop', event => {
        event.preventDefault();
        if (input && event.dataTransfer?.files?.length) input.files = event.dataTransfer.files;
      });
    });
  }
  document.addEventListener('DOMContentLoaded', () => wire(document));
  document.addEventListener('htmx:afterSettle', event => wire(event.target.ownerDocument || document));
})();
