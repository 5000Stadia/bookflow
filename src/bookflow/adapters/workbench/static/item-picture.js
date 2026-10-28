// The item picture (R114 increment 1): take or choose a photo, scale it to 1600 px on its long edge and make a
// 320 px thumbnail in the browser, then post both through htmx. A file the browser cannot read (a HEIC photo, say)
// goes up as it is, so the server can say what to do instead.
(function () {
  const LONG = 1600, THUMB = 320;
  function scaled(bitmap, edge) {
    const ratio = Math.min(1, edge / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
    canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
    const g = canvas.getContext('2d');
    g.fillStyle = '#fff'; g.fillRect(0, 0, canvas.width, canvas.height);
    g.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return new Promise((ok, fail) => canvas.toBlob(b => b ? ok(b) : fail(Error('encode')), 'image/jpeg', 0.85));
  }
  function put(input, blob, name) {
    const files = new DataTransfer();
    files.items.add(new File([blob], name, {type: blob.type || 'application/octet-stream'}));
    input.files = files.files;
  }
  document.addEventListener('change', async event => {
    const chooser = event.target.closest('[data-picture-choose]');
    if (!chooser || !chooser.files.length) return;
    const form = chooser.closest('[data-item-picture-form]');
    const status = form.parentElement.querySelector('[data-picture-status]');
    const file = chooser.files[0];
    const base = (file.name || 'photo').replace(/\.[^.]*$/, '') || 'photo';
    status.textContent = 'Preparing the picture…';
    const out = form.querySelector('[data-picture-file]'), thumb = form.querySelector('[data-picture-thumbnail]');
    try {
      const bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
      put(out, await scaled(bitmap, LONG), base + '.jpg');
      // A photo no larger than a thumbnail is its own thumbnail: none is sent.
      if (Math.max(bitmap.width, bitmap.height) > THUMB) put(thumb, await scaled(bitmap, THUMB), base + '-thumbnail.jpg');
      else thumb.value = '';
      bitmap.close && bitmap.close();
    } catch (_) {
      put(out, file, file.name || 'photo');
      thumb.value = '';
    }
    chooser.value = '';
    status.textContent = 'Saving the picture…';
    htmx.trigger(form, 'picture-ready');
  });
  // A post htmx does not swap (refused, too large, a server fault, no network) still says what happened.
  function failed(event) {
    const form = event.target.closest && event.target.closest('[data-item-picture] form');
    if (!form) return;
    const side = form.closest('.item-picture-side');
    const status = side.querySelector('[data-picture-status]');
    const xhr = event.detail && event.detail.xhr;
    const code = xhr ? xhr.status : 0;
    status.textContent = '';
    let alert = side.querySelector('[data-picture-error]');
    if (!alert) {
      alert = document.createElement('p');
      alert.setAttribute('role', 'alert'); alert.className = 'error'; alert.setAttribute('data-picture-error', '');
      side.appendChild(alert);
    }
    alert.textContent = code === 413 ? 'The picture is too large.' :
      code === 403 ? 'You may not change this picture.' :
      code ? 'The picture was not saved (the server answered ' + code + '). Try again.' :
      'The picture was not saved: the connection failed. Try again.';
  }
  document.addEventListener('htmx:responseError', failed);
  document.addEventListener('htmx:sendError', failed);
})();
