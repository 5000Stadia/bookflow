/* Browser transport state stays in the document, never persistent storage. */
(() => {
  const token = () => document.querySelector('meta[name="bookflow-csrf"]')?.content;
  const sameOrigin = url => new URL(url, location.href).origin === location.origin;
  document.addEventListener('htmx:configRequest', event => {
    if (token() && sameOrigin(event.detail.path)) {
      event.detail.headers['X-Bookflow-CSRF'] = token();
    }
  });
  // Existing workbench fetch consumers use the same transport policy as forms.
  const original = window.fetch;
  window.fetch = function(input, init) {
    const url = input instanceof Request ? input.url : input;
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (token() && sameOrigin(url) && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
      headers.set('X-Bookflow-CSRF', token());
      headers.set('X-Bookflow-Workbench', '1');
      init = {...init, headers};
    }
    return original.call(this, input, init);
  };
})();
