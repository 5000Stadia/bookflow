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
  const FRAME_MS = 125;            // about 8 decodes a second
  const LEFT_FRAME_DECODES = 3;    // a code not read in 3 decodes in a row has left the frame
  const SAME_CODE_MS = 1500;       // ... or, once missed at least once, may be taken again after 1.5 s
  const IDLE_MS = 60000;           // a minute with no code stops the camera
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
    const key = wanted.join(',');
    if (chosen && chosen.key === key) return chosen.decoder;
    chosen = null;
    const tried = [];
    for (const name of wanted) {
      const decoder = decoders.get(name);
      if (!decoder) { tried.push(name + ' is not installed'); continue; }
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
  for (const decoder of window.bookflowDecoderPending || []) window.bookflowDecoders.register(decoder);
  window.bookflowDecoderPending = [];

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
  const state = {
    overlay: null, target: null, mode: null, stream: null, running: false, timer: null, idle: null,
    decoder: null, memory: null, chain: Promise.resolve(), audio: null, busy: false,
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
    overlay.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
    refreshMenu();
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
    if (state.overlay) state.overlay.hidden = true;
    document.documentElement.classList.remove('camera-open');
  }

  function stop() {
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

  async function startLive() {
    stop();
    part('restart').hidden = true;
    flash('Starting the camera…', 'busy');
    try {
      state.decoder = await choose();
    } catch (error) {
      refreshMenu();
      flash(error.message, 'error');
      return;
    }
    refreshMenu();
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: {facingMode: {ideal: 'environment'}, width: {ideal: 1280}, height: {ideal: 720}}});
    } catch (error) {
      flash('The camera did not open (' + (error.name || error) + '). Allow the camera for this site, or type the code.', 'error');
      part('restart').hidden = false;
      return;
    }
    if (state.overlay.hidden) { stop(); return; }   // closed while the camera was asked for
    const video = part('video');
    video.srcObject = state.stream;
    try { await video.play(); } catch (error) { /* autoplay with muted video is allowed; ignore */ }
    const track = state.stream.getVideoTracks()[0];
    const caps = track && track.getCapabilities ? track.getCapabilities() : {};
    part('torch').hidden = !caps.torch;
    part('torch').setAttribute('aria-pressed', 'false');
    state.memory = null;
    state.running = true;
    flash('Point the camera at a barcode.', '');
    idle();
    tick();
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

  async function tick() {
    if (!state.running) return;
    const started = performance.now();
    const video = part('video');
    let codes = [];
    if (video.readyState >= 2 && video.videoWidth) {
      try { codes = await state.decoder.detect(video); } catch (error) { codes = []; }
    }
    if (!state.running) return;
    see(codes.map(code => code.text));
    state.timer = setTimeout(tick, Math.max(0, FRAME_MS - (performance.now() - started)));
  }

  // De-duplication: a code still in the frame is not taken again. It is taken again once it has left the frame (not
  // read in LEFT_FRAME_DECODES decodes in a row), or once SAME_CODE_MS have passed since it was taken and it has been
  // missed at least once since; so holding a can still is one scan, and scanning it again adds one.
  function see(texts) {
    const now = performance.now();
    const memory = state.memory;
    let again = false;
    if (memory) {
      const present = texts.includes(memory.code);
      again = present && (memory.streak >= LEFT_FRAME_DECODES || (memory.missed && now - memory.takenAt >= SAME_CODE_MS));
      if (present) memory.streak = 0;
      else { memory.streak += 1; memory.missed = true; }
    }
    const text = texts.find(t => !memory || t !== memory.code || again);
    if (text === undefined) return;
    state.memory = {code: text, takenAt: now, streak: 0, missed: false};
    idle();
    take(text);
  }

  // A code read (live or from a photo) goes the way a typed one goes, one at a time, in the order read.
  function take(text) {
    state.chain = state.chain.then(() => deliver(text)).catch(() => {});
    return state.chain;
  }

  function currentScanField() {
    const target = state.target;
    if (!target || target.kind !== 'scan') return null;
    return document.querySelector(`[data-scan][data-scan-mode="${target.mode}"]`);
  }

  async function deliver(text) {
    const target = state.target;
    let found = false;
    let label = text;
    if (target.kind === 'scan') {
      const field = currentScanField();
      if (!field || !window.bookflowScan || !window.bookflowScan.scan) return;
      await window.bookflowScan.scan(field, text);
      const status = (currentScanField() || field).querySelector('[data-scan-status]');
      found = !!status && status.dataset.kind === 'ok';
      label = status ? status.innerText : text;
    } else {
      // A plain scan input (the pack screen): the code is typed into it and its form submitted, as Enter would.
      const input = document.querySelector(target.selector);
      const form = input && input.closest('form');
      if (!form) return;
      input.value = text;
      const swapped = new Promise(resolve => {
        const done = () => { document.removeEventListener('htmx:afterSettle', done); clearTimeout(timer); resolve(); };
        const timer = setTimeout(done, 15000);
        document.addEventListener('htmx:afterSettle', done);
      });
      form.requestSubmit(form.querySelector('[type="submit"]') || undefined);
      await swapped;
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
})();
