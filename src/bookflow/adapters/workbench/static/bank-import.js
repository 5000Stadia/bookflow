/* File selection stays ordinary multipart form input, including on phones. */
(function () {
  function wire(root) {
    const drop = root.querySelector('[data-bank-drop]');
    const file = root.querySelector('[data-bank-file]');
    if (!drop || !file || drop.dataset.wired) return;
    drop.dataset.wired = '1';
    const name = root.querySelector('[data-bank-file-name]');
    file.addEventListener('change', () => { if (file.files.length) name.textContent = file.files[0].name; });
    drop.addEventListener('dragover', event => event.preventDefault());
    drop.addEventListener('drop', event => {
      event.preventDefault();
      if (event.dataTransfer.files.length) {
        const transfer = new DataTransfer(); transfer.items.add(event.dataTransfer.files[0]);
        file.files = transfer.files; file.dispatchEvent(new Event('change'));
      }
    });
  }
  document.addEventListener('DOMContentLoaded', () => wire(document));
  document.body.addEventListener('htmx:afterSwap', () => wire(document));
})();
