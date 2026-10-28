/* R107 increment 6: the grid edit page. A cell is sent only once it differs from what was shown (it gets its form
   name then), so a long grid posts what was typed; the column operation shows only the inputs its kind takes. */
(function () {
  function changed(el) {
    if (el.tagName === 'SELECT') {
      return Array.prototype.some.call(el.options, function (o) { return o.selected !== o.defaultSelected; });
    }
    return el.value !== el.defaultValue;
  }
  function mark(el) {
    if (!el.dataset || !el.dataset.name) return;
    if (changed(el)) { el.name = el.dataset.name; el.closest('td').classList.add('is-edited'); }
    else { el.removeAttribute('name'); el.closest('td').classList.remove('is-edited'); }
  }
  function showOp(root) {
    var kind = root.querySelector('[data-bulk-op-kind]');
    if (!kind) return;
    root.querySelectorAll('[data-op-for]').forEach(function (label) {
      var on = label.dataset.opFor.split(' ').indexOf(kind.value) >= 0;
      label.hidden = !on;
      label.querySelectorAll('input,select').forEach(function (i) { i.disabled = !on; });
    });
  }
  function init(root) {
    showOp(root);
    var pick = root.querySelector('[data-bulk-pick]');
    if (pick) {
      pick.addEventListener('submit', function () {
        var ticked = Array.prototype.map.call(pick.querySelectorAll('[data-bulk-column]:checked'),
                                              function (c) { return c.value; });
        pick.querySelector('[data-bulk-columns]').value = ticked.join(',');
        pick.querySelectorAll('[data-bulk-column]').forEach(function (c) { c.disabled = true; });
      });
    }
  }
  document.addEventListener('input', function (e) { mark(e.target); });
  document.addEventListener('change', function (e) {
    mark(e.target);
    if (e.target.matches && e.target.matches('[data-bulk-op-kind]')) showOp(e.target.closest('form'));
  });
  document.addEventListener('DOMContentLoaded', function () { init(document); });
  document.addEventListener('htmx:afterSettle', function () { init(document); });
})();
