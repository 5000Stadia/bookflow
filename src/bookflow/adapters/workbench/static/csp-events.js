/* Declarative UI actions, without inline handlers or evaluated expressions. */
(function () {
  'use strict';

  function closest(event, selector) {
    return event.target instanceof Element ? event.target.closest(selector) : null;
  }

  document.addEventListener('htmx:beforeSwap', function (event) {
    if (closest(event, '[data-swap-validation-errors]') &&
        [400, 409].includes(event.detail.xhr.status)) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  document.addEventListener('htmx:beforeRequest', function (event) {
    if (closest(event, '[data-auth-feedback]')) {
      const error = document.getElementById('err');
      if (error) error.textContent = '';
    }
  });

  document.addEventListener('htmx:afterRequest', function (event) {
    if (closest(event, '[data-logout]')) {
      window.location = '/login';
      return;
    }
    const form = closest(event, '[data-auth-feedback]');
    if (!form) return;
    const xhr = event.detail.xhr;
    if (xhr.status !== 0 && xhr.status < 400) return;
    let message = 'Bookflow could not be reached. Check your connection and try again.';
    if (form.dataset.authFeedback === 'login') {
      if (xhr.status !== 0) message = 'That username and password did not match. Try again.';
    } else {
      try { message = JSON.parse(xhr.responseText).message || message; } catch (_) {}
    }
    const error = document.getElementById('err');
    if (error) error.textContent = message;
  });

  function requestFailed(event) {
    if (closest(event, '[data-batch-errors]')) {
      const error = document.querySelector('[data-batch-network-error]');
      if (error) error.hidden = false;
    }
    const overview = closest(event, '[data-overview-load]');
    if (overview && event.type === 'htmx:responseError') {
      const loading = overview.querySelector('.overview-loading');
      if (loading) loading.textContent = 'Figures could not be loaded just now.';
    }
  }
  document.addEventListener('htmx:sendError', requestFailed);
  document.addEventListener('htmx:responseError', requestFailed);

  document.addEventListener('click', function (event) {
    const confirmation = closest(event, '[data-confirm-action]');
    if (confirmation && !window.confirm(confirmation.dataset.confirmAction)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (closest(event, '[data-print-page]')) {
      event.preventDefault();
      window.print();
    }
    if (closest(event, '[data-history-back]') && window.history.length > 1) {
      event.preventDefault();
      window.history.back();
    }
  }, true);

  document.addEventListener('change', function (event) {
    const field = closest(event, '[data-submit-on-change]');
    if (field && field.form) field.form.submit();
  });

  function imageFailed(image) {
    if (!(image instanceof HTMLImageElement)) return;
    if (image.hasAttribute('data-hide-on-error')) image.hidden = true;
    if (image.hasAttribute('data-pos-tile-fallback')) {
      const fallback = document.createElement('span');
      fallback.className = 'pos-tile';
      image.replaceWith(fallback);
    }
    if (image.hasAttribute('data-picture-fallback')) {
      const fallback = document.createElement('span');
      fallback.className = 'muted';
      fallback.textContent = 'No picture';
      fallback.dataset.pictureNone = '';
      image.replaceWith(fallback);
    }
  }
  document.addEventListener('error', function (event) { imageFailed(event.target); }, true);

  // Images may have failed before a deferred script or swapped fragment loads.
  function checkImages() {
    document.querySelectorAll('img[data-hide-on-error], img[data-picture-fallback], img[data-pos-tile-fallback]').forEach(function (image) {
      if (image.complete && image.naturalWidth === 0) imageFailed(image);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', checkImages);
  else checkImages();
  document.addEventListener('htmx:load', checkImages);
})();
