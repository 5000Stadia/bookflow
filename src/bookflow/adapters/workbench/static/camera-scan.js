// Camera scanning (R190, design/point-of-sale.md §7). Two parts:
//
// 1. The decoder seam. A Decoder turns one frame into zero or more codes:
//      { name, formats: ['ean_13', 'upc_a', 'code_128', 'qr_code', ...],
//        async available() -> boolean,               // runtime check, no download
//        async load() -> void,                       // fetch and compile its files, once
//        async detect(source) -> [{text, format}] }  // HTMLVideoElement | ImageBitmap | HTMLCanvasElement
//    Adapters (scan-decoder-<name>.js) register themselves with bookflowDecoders.register(decoder). The order is a
//    list of names; the first whose available() is true is loaded, lazily, the first time the camera opens. Nothing
//    in this file knows any particular decoder. Where the order comes from, first match wins:
//      - this device: localStorage 'bookflow.scan.decoders', set from the overlay's Decoder menu (for testing);
//      - the register: bookflowDecoders.setRegisterOrder(names), for the POS screen to call with its register's
//        decoder override (§2) once registers exist; nothing calls it yet;
//      - the host: <meta name="bookflow-scan-decoders"> (bookflow serve --scan-decoders, BOOKFLOW_SCAN_DECODERS);
//      - the default, native then zxing-wasm.
//
// 2. The camera in the Scan field. Each Scan field's Camera button (scan_field.html) opens an overlay over the lower
//    half of the screen. On a secure page the rear camera runs live and about 8 frames a second are decoded; on a
//    plain-http page each tap takes one photo. A code read goes through scan.js's own look-up (bookflowScan.scan),
//    exactly as a typed code does; the overlay beeps for a found item and buzzes twice for an unknown one.
(() => {
  if (window.bookflowDecoders) return;

  const DEFAULT_ORDER = ['native', 'zxing-wasm'];
  const KEY_ORDER = 'bookflow.scan.decoders';
  const KEY_SOUND = 'bookflow.scan.sound';
  const KEY_WHY = 'bookflow.scan.photo-why-shown';
  // Tuning for the phone trial.
  const FRAME_MS = 125;            // about 8 decodes a second
  const LEFT_FRAME_DECODES = 5;    // a code unread in 5 decodes in a row (about 0.6 s) has left the frame
  const IDLE_MS = 60000;           // a minute with no code stops the camera
  const BROKEN_DETECTS = 10;       // a decoder whose detect() throws this many times in a row is passed over
  const PHOTO_WHY = 'Live scanning needs a secure connection. Until Bookflow is set up with HTTPS, each tap takes one photo.';

  const stored = {
    get(store, key) { try { return window[store].getItem(key); } catch (error) { return null; } },
    set(store, key, value) {
      try { if (value === null || value === '') window[store].removeItem(key); else window[store].setItem(key, value); }
      catch (error) { /* private mode: the setting lasts for this page only */ }
    },
  };
  const names = text => String(text || '').split(',').map(n => n.trim()).filter(Boolean);
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  // ---------------------------------------------------------------- the registry
  const decoders = new Map();
  let registerOrder = null;
  let chosen = null;     // {decoder, key}
  const broken = new Map();   // name -> why: decoders that kept failing on this page are skipped

  function order({device: useDevice = true} = {}) {
    const device = useDevice ? names(stored.get('localStorage', KEY_ORDER)) : [];
    if (device.length) return {names: device, from: 'this device'};
    if (registerOrder && registerOrder.length) return {names: registerOrder, from: 'the register'};
    const host = names(document.querySelector('meta[name="bookflow-scan-decoders"]')?.content);
    if (host.length) return {names: host, from: 'the host'};
    return {names: DEFAULT_ORDER, from: 'the default'};
  }

  async function choose() {
    const wanted = order().names;
    const key = wanted.join(',') + '|' + [...broken.keys()].join(',');
    if (chosen && chosen.key === key) return chosen.decoder;
    chosen = null;
    const tried = [];
    for (const name of wanted) {
      const decoder = decoders.get(name);
      if (!decoder) { tried.push(name + ' is not installed'); continue; }
      if (broken.has(name)) { tried.push(name + ' ' + broken.get(name)); continue; }
      try {
        if (!(await decoder.available())) { tried.push(name + ' is not available in this browser'); continue; }
        await decoder.load();
        chosen = {decoder, key};
        return decoder;
      } catch (error) {
        tried.push(name + ' did not load (' + (error && error.message || error) + ')');
      }
    }
    throw new Error('No barcode decoder works here: ' + tried.join('; ') + '. Type the code or use a scanner.');
  }

  window.bookflowDecoders = {
    register(decoder) {
      if (!decoder || !decoder.name || decoders.has(decoder.name)) return;
      decoders.set(decoder.name, decoder);
      refreshMenu();
    },
    names: () => [...decoders.keys()],
    order,
    choose,
    get active() { return chosen ? chosen.decoder.name : null; },
    // The register's decoder override (§2): the POS screen calls this with its register's list, or null.
    setRegisterOrder(list) { registerOrder = list ? names(Array.isArray(list) ? list.join(',') : list) : null; },
    // This device's override (the overlay's Decoder menu): a name list, or '' for the host's order.
    setDeviceOrder(text) { stored.set('localStorage', KEY_ORDER, names(text).join(',')); },
  };

  // ---------------------------------------------------------------- the camera button
  const hasCamera = () => !!navigator.mediaDevices || !!(window.matchMedia && matchMedia('(pointer: coarse)').matches);
  const canLive = () => !!window.isSecureContext && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

  function showButtons() {
    const show = hasCamera();
    for (const button of document.querySelectorAll('[data-scan-camera]')) button.hidden = !show;
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', showButtons);
  else showButtons();
  document.addEventListener('htmx:afterSettle', showButtons);

  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-scan-camera]');
    if (!button) return;
    event.preventDefault();
    const field = button.closest('[data-scan]');
    open(field ? {kind: 'scan', mode: field.dataset.scanMode || 'lines'}
               : {kind: 'input', selector: button.dataset.cameraInput});
  });

  // ---------------------------------------------------------------- the overlay
  // `session` changes on every open and close: a code read in one session is never delivered in another.
  // `start` changes on every start and stop of the live camera: only the newest start owns the camera.
  const state = {
    overlay: null, target: null, mode: null, stream: null, running: false, timer: null, idle: null,
    decoder: null, seen: new Map(), chain: Promise.resolve(), audio: null, session: 0, start: 0, failures: 0,
    typed: '',
  };
  const part = name => state.overlay.querySelector(`[data-camera-${name}]`);

  function build() {
    if (state.overlay) return state.overlay;
    const overlay = document.createElement('div');
    overlay.className = 'camera-overlay';
    overlay.hidden = true;
    overlay.dataset.cameraOverlay = '';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-label', 'Camera scanner');
    overlay.innerHTML = `
      <div class="camera-view" data-camera-view><video data-camera-video playsinline muted autoplay></video><div class="camera-frame" aria-hidden="true"></div></div>
      <p class="camera-flash" data-camera-flash role="status" aria-live="polite"></p>
      <p class="camera-why" data-camera-why hidden></p>
      <div class="camera-controls">
        <button type="button" data-camera-photo hidden>Take photo</button>
        <button type="button" data-camera-restart hidden>Start again</button>
        <button type="button" data-camera-torch aria-pressed="false" hidden>Torch</button>
        <label class="camera-sound"><input type="checkbox" data-camera-sound> Sound</label>
        <label class="camera-decoder">Decoder <select data-camera-choice></select></label>
        <span class="camera-using" data-camera-decoder></span>
        <button type="button" data-camera-close>Close</button>
      </div>
      <input type="file" accept="image/*" capture="environment" hidden data-camera-file>`;
    // Outside <body>: the pick and count screens swap the whole body after each entry, and the camera stays open.
    document.documentElement.appendChild(overlay);
    state.overlay = overlay;
    part('sound').checked = stored.get('localStorage', KEY_SOUND) !== 'off';
    part('sound').addEventListener('change', event => stored.set('localStorage', KEY_SOUND, event.target.checked ? null : 'off'));
    part('close').addEventListener('click', close);
    part('restart').addEventListener('click', () => { if (state.mode === 'live') startLive(); });
    part('photo').addEventListener('click', () => part('file').click());
    part('file').addEventListener('change', event => {
      const file = event.target.files && event.target.files[0];
      event.target.value = '';
      if (file) photo(file);
    });
    part('torch').addEventListener('click', torch);
    part('choice').addEventListener('change', event => {
      window.bookflowDecoders.setDeviceOrder(event.target.value);
      changeDecoder();
    });
    refreshMenu();
    // The page keeps the space the overlay covers, so its last lines can scroll clear of it.
    const room = () => document.documentElement.style.setProperty('--camera-overlay-height', overlay.offsetHeight + 'px');
    if (window.ResizeObserver) new ResizeObserver(room).observe(overlay);
    overlay.addEventListener('transitionend', room);
    return overlay;
  }

  function refreshMenu() {
    if (!state.overlay) return;
    const select = part('choice');
    const device = names(stored.get('localStorage', KEY_ORDER)).join(',');
    const fallback = order({device: false});
    select.innerHTML = `<option value="">Automatic (${escape(fallback.names.join(', '))}, from ${escape(fallback.from)})</option>`
      + [...decoders.keys()].map(name => `<option value="${escape(name)}">${escape(name)} only</option>`).join('');
    select.value = [...select.options].some(o => o.value === device) ? device : '';
    const active = window.bookflowDecoders.active;
    part('decoder').textContent = active ? 'Using ' + active : '';
    part('decoder').dataset.cameraDecoderName = active || '';
  }

  function flash(text, kind) {
    const node = part('flash');
    node.textContent = text;
    node.dataset.kind = kind || '';
  }

  function open(target) {
    build();
    state.session += 1;
    state.chain = Promise.resolve();
    state.typed = '';
    state.target = target;
    state.overlay.hidden = false;
    document.documentElement.classList.add('camera-open');
    // Audio may start only from a tap: make (or wake) the context now.
    try {
      state.audio = state.audio || new (window.AudioContext || window.webkitAudioContext)();
      if (state.audio.state === 'suspended') state.audio.resume();
    } catch (error) { state.audio = null; }
    if (canLive()) {
      state.mode = 'live';
      state.overlay.dataset.cameraMode = 'live';
      part('photo').hidden = true;
      part('why').hidden = true;
      startLive();
    } else {
      state.mode = 'photo';
      state.overlay.dataset.cameraMode = 'photo';
      part('photo').hidden = false;
      part('restart').hidden = true;
      part('torch').hidden = true;
      if (stored.get('sessionStorage', KEY_WHY) !== 'shown') {
        stored.set('sessionStorage', KEY_WHY, 'shown');
        part('why').textContent = PHOTO_WHY;
        part('why').hidden = false;
        // When the host knows its https address, say where live scanning works.
        window.bookflowSecureContext?.().then(info => {
          if (info && info.https && info.public_origin) part('why').textContent = PHOTO_WHY + ' Live scanning works at ' + info.public_origin + '.';
        });
      } else {
        part('why').hidden = true;
      }
      flash('Tap Take photo, with the barcode filling the frame.', '');
      part('file').click();   // still inside the tap: the phone opens its camera
    }
  }

  function close() {
    stop();
    state.session += 1;            // codes read before Close are dropped, not delivered after it
    state.chain = Promise.resolve();
    state.typed = '';
    if (state.overlay) state.overlay.hidden = true;
    document.documentElement.classList.remove('camera-open');
  }

  function stop() {
    state.start += 1;              // a start still waiting for its decoder or camera is now stale
    state.running = false;
    clearTimeout(state.timer);
    clearTimeout(state.idle);
    if (state.stream) for (const track of state.stream.getTracks()) track.stop();
    state.stream = null;
    if (state.overlay) {
      part('video').srcObject = null;
      part('torch').hidden = true;
    }
  }

  // Only the newest start owns the camera. A start replaced by another, or by Close, asks for no camera, stops any
  // stream it is handed late, and runs no decode loop.
  async function startLive() {
    stop();
    const mine = state.start;
    const stale = () => mine !== state.start || state.overlay.hidden;
    part('restart').hidden = true;
    flash('Starting the camera…', 'busy');
    let decoder;
    try {
      decoder = await choose();
    } catch (error) {
      if (stale()) return;
      refreshMenu();
      flash(error.message, 'error');
      return;
    }
    if (stale()) return;
    state.decoder = decoder;
    refreshMenu();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: {facingMode: {ideal: 'environment'}, width: {ideal: 1280}, height: {ideal: 720}}});
    } catch (error) {
      if (stale()) return;
      flash('The camera did not open (' + (error.name || error) + '). Allow the camera for this site, or type the code.', 'error');
      part('restart').hidden = false;
      return;
    }
    if (stale()) { for (const track of stream.getTracks()) track.stop(); return; }
    state.stream = stream;
    const video = part('video');
    video.srcObject = stream;
    try { await video.play(); } catch (error) { /* autoplay with muted video is allowed; ignore */ }
    if (stale()) return;            // stop() has ended this stream's tracks
    const track = stream.getVideoTracks()[0];
    const caps = track && track.getCapabilities ? track.getCapabilities() : {};
    part('torch').hidden = !caps.torch;
    part('torch').setAttribute('aria-pressed', 'false');
    state.seen = new Map();
    state.failures = 0;
    state.running = true;
    flash('Point the camera at a barcode.', '');
    idle();
    tick(mine);
  }

  async function changeDecoder() {
    chosen = null;
    if (state.mode === 'live' && state.running) { startLive(); return; }
    try { state.decoder = await choose(); } catch (error) { flash(error.message, 'error'); }
    refreshMenu();
  }

  function idle() {
    clearTimeout(state.idle);
    state.idle = setTimeout(() => {
      stop();
      flash('The camera stopped after a minute with no code.', '');
      part('restart').hidden = false;
    }, IDLE_MS);
  }

  async function torch() {
    const track = state.stream && state.stream.getVideoTracks()[0];
    if (!track) return;
    const on = part('torch').getAttribute('aria-pressed') !== 'true';
    try {
      await track.applyConstraints({advanced: [{torch: on}]});
      part('torch').setAttribute('aria-pressed', String(on));
    } catch (error) { part('torch').hidden = true; }
  }

  async function tick(mine) {
    if (!state.running || mine !== state.start) return;
    const started = performance.now();
    const video = part('video');
    const decoder = state.decoder;
    let codes = [];
    if (video.readyState >= 2 && video.videoWidth) {
      try {
        codes = await decoder.detect(video);
        state.failures = 0;
      } catch (error) {
        codes = [];
        state.failures += 1;
        if (state.failures >= BROKEN_DETECTS && mine === state.start) {
          // A decoder that keeps failing on this device is passed over for the next in the order.
          broken.set(decoder.name, 'kept failing (' + (error && error.message || error) + ')');
          chosen = null;
          flash(decoder.name + ' kept failing here; trying the next decoder.', 'error');
          startLive();
          return;
        }
      }
    }
    if (!state.running || mine !== state.start) return;
    see(codes.map(code => code.text));
    state.timer = setTimeout(() => tick(mine), Math.max(0, FRAME_MS - (performance.now() - started)));
  }

  // De-duplication, per code: a code in view is taken once, whatever else is in the frame, and is taken again only
  // after it has itself left the frame (unread in LEFT_FRAME_DECODES decodes in a row). There is no time-based
  // re-take while it stays in view, so holding a can still is one scan and scanning it again adds one.
  function see(texts) {
    const present = new Set(texts);
    for (const [code, entry] of state.seen) {
      if (present.has(code)) continue;
      entry.absent += 1;
      if (entry.absent >= LEFT_FRAME_DECODES) state.seen.delete(code);
    }
    for (const code of present) {
      const entry = state.seen.get(code);
      if (entry) { entry.absent = 0; continue; }
      state.seen.set(code, {absent: 0});
      idle();
      take(code);
    }
  }

  // A code read (live or from a photo) goes the way a typed one goes, one at a time, in the order read, to the field
  // it was read for, and only while the overlay that read it is still open.
  function take(text, target = state.target) {
    const session = state.session;
    state.chain = state.chain.then(() => deliver(text, target, session)).catch(() => {});
    return state.chain;
  }

  function scanField(target) {
    if (!target || target.kind !== 'scan') return null;
    return document.querySelector(`[data-scan][data-scan-mode="${target.mode}"]`);
  }

  // The field the overlay was opened for has gone (a pick list completed, a page left): say so and stop the camera.
  function fieldGone() {
    stop();
    flash('There is nothing to scan into on this page any more. The camera has stopped.', 'error');
  }

  async function deliver(text, target, session) {
    if (session !== state.session) return;
    let found = false;
    let label = text;
    if (target.kind === 'scan') {
      const field = scanField(target);
      if (!field || !window.bookflowScan || !window.bookflowScan.scan) { fieldGone(); return; }
      await window.bookflowScan.scan(field, text);
      if (session !== state.session) return;
      const status = (scanField(target) || field).querySelector('[data-scan-status]');
      found = !!status && status.dataset.kind === 'ok';
      label = status ? status.innerText : text;
    } else {
      // A plain scan input (the pack screen): the code is typed into it and its form submitted, as Enter would.
      const input = document.querySelector(target.selector);
      const form = input && input.closest('form');
      if (!form) { fieldGone(); return; }
      input.value = text;
      const swapped = new Promise(resolve => {
        const done = () => { document.removeEventListener('htmx:afterSettle', done); clearTimeout(timer); resolve(); };
        const timer = setTimeout(done, 15000);
        document.addEventListener('htmx:afterSettle', done);
      });
      form.requestSubmit(form.querySelector('[type="submit"]') || undefined);
      await swapped;
      if (session !== state.session) return;
      const alert = document.querySelector('[role=alert]');
      found = !alert;
      label = alert ? alert.innerText.split('\n')[0] : (document.querySelector('[role=status]')?.innerText || text);
    }
    signal(found, text);
    flash(label, found ? 'ok' : 'unknown');
  }

  // ---------------------------------------------------------------- photo, one per tap
  async function photo(file) {
    flash('Reading the photo…', 'busy');
    let decoder;
    try { decoder = await choose(); } catch (error) { refreshMenu(); flash(error.message, 'error'); return; }
    refreshMenu();
    let codes = [];
    try {
      const bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext('2d').drawImage(bitmap, 0, 0);
      bitmap.close?.();
      codes = await decoder.detect(canvas);
    } catch (error) {
      flash('This photo could not be read. Take it again.', 'error');
      signal(false, '');
      return;
    }
    if (!codes.length) {
      flash('No barcode was found in the photo. Take it again closer, with the code filling the frame.', 'unknown');
      signal(false, '');
      return;
    }
    await take(codes[0].text);
  }

  // ---------------------------------------------------------------- a keyboard scanner beside the camera
  // While the overlay is open, Escape closes it, and a keyboard-mode scanner's keys that land on no text field (the
  // focus is on a button after a tap) are gathered and entered in the Scan field on Enter, exactly as if typed there.
  // The field is not focused, so a phone's keyboard does not rise over the camera.
  const editable = element => element && (element.isContentEditable
    || /^(INPUT|TEXTAREA|SELECT)$/.test(element.tagName) && !/^(button|checkbox|radio|submit|file)$/i.test(element.type || ''));
  document.addEventListener('keydown', event => {
    if (!state.overlay || state.overlay.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); return; }
    if (editable(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'Enter') {
      const text = state.typed;
      state.typed = '';
      if (!text) return;
      event.preventDefault();
      typed(text, state.target);
    } else if (event.key.length === 1) {
      event.preventDefault();
      state.typed += event.key;
    }
  }, true);

  function typed(text, target) {
    if (target.kind === 'scan') {
      const field = scanField(target);
      const input = field && field.querySelector('[data-scan-input]');
      if (!input) return;
      input.value = text;
      window.bookflowScan.look(field, input);
    } else {
      const input = document.querySelector(target.selector);
      const form = input && input.closest('form');
      if (!form) return;
      input.value = text;
      form.requestSubmit(form.querySelector('[type="submit"]') || undefined);
    }
  }

  // The pack screen marks its code field autofocus, which would raise a phone's keyboard over the camera after each
  // entry; while the camera is open the swapped-in page takes no autofocus.
  document.addEventListener('htmx:beforeSwap', event => {
    if (!state.overlay || state.overlay.hidden || typeof event.detail.serverResponse !== 'string') return;
    event.detail.serverResponse = event.detail.serverResponse.replace(/(<[^>]*?)\sautofocus(?:=(?:""|''|"autofocus"))?(?=[\s/>])/gi, '$1');
  });

  // ---------------------------------------------------------------- sound and vibration
  function tone(frequency, at, length) {
    const audio = state.audio;
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = 'square';
    oscillator.frequency.value = frequency;
    gain.gain.value = 0.08;
    oscillator.connect(gain).connect(audio.destination);
    const start = audio.currentTime + at;
    oscillator.start(start);
    oscillator.stop(start + length);
  }

  // A found item: one short beep (1,000 Hz, 80 ms) and a 40 ms vibration. An unknown code: two low beeps and a
  // longer double buzz. Sound follows this device's toggle; a phone on silent still vibrates.
  function signal(found, code) {
    const sound = !state.overlay || part('sound').checked;
    if (sound && state.audio) {
      try {
        if (found) tone(1000, 0, 0.08);
        else { tone(330, 0, 0.12); tone(330, 0.2, 0.12); }
      } catch (error) { /* no audio output */ }
    }
    try { navigator.vibrate?.(found ? 40 : [120, 80, 120]); } catch (error) { /* not supported */ }
    const detail = {kind: found ? 'found' : 'unknown', code, sound, decoder: window.bookflowDecoders.active};
    window.bookflowCamera.signals.push(detail);
    document.dispatchEvent(new CustomEvent('bookflow:camera-signal', {detail}));
  }

  window.bookflowCamera = {open, close, take, signals: [], get mode() { return state.mode; }};
  // Adapters that loaded before this file queued themselves.
  for (const decoder of window.bookflowDecoderPending || []) window.bookflowDecoders.register(decoder);
  window.bookflowDecoderPending = [];
})();
