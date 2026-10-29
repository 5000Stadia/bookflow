// Decoder adapter "zxing-wasm" (R190, design/point-of-sale.md §7): zxing-cpp compiled to WebAssembly, vendored under
// static/vendor/zxing-wasm/3.1.5/ (see its VENDORED.md). Works in any browser with WebAssembly and needs no internet:
// the module's default would fetch the .wasm from a CDN, so locateFile and instantiateWasm are replaced and the wasm
// comes from the vendored folder only. Streaming compile first (the host serves .wasm as application/wasm); if that
// fails (a proxy that rewrites the type, an old browser) the bytes are fetched and compiled from an ArrayBuffer.
(() => {
  const BASE = '/static/vendor/zxing-wasm/3.1.5/';
  const WASM = BASE + 'zxing_reader.wasm';
  const TO_ZXING = {ean_13: 'EAN13', ean_8: 'EAN8', upc_a: 'UPCA', upc_e: 'UPCE', code_128: 'Code128',
                    code_39: 'Code39', itf: 'ITF', qr_code: 'QRCode', data_matrix: 'DataMatrix'};
  const FROM_ZXING = Object.fromEntries(Object.entries(TO_ZXING).map(([ours, theirs]) => [theirs, ours]));
  const MAX_SIDE = 1280;   // larger frames are scaled down first: decoding time grows with the pixels
  let loading = null;
  let canvas = null;

  function script(src) {
    return new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error('could not load ' + src));
      document.head.appendChild(tag);
    });
  }

  async function instantiate(imports) {
    if (typeof WebAssembly.instantiateStreaming === 'function') {
      try {
        const made = await WebAssembly.instantiateStreaming(fetch(WASM, {credentials: 'same-origin'}), imports);
        return {...made, via: 'streaming'};
      } catch (error) { /* fall through to the ArrayBuffer path */ }
    }
    const response = await fetch(WASM, {credentials: 'same-origin'});
    if (!response.ok) throw new Error('zxing_reader.wasm: HTTP ' + response.status);
    const made = await WebAssembly.instantiate(await response.arrayBuffer(), imports);
    return {...made, via: 'arraybuffer'};
  }

  const decoder = {
    name: 'zxing-wasm',
    formats: Object.keys(TO_ZXING),
    via: null,            // 'streaming' or 'arraybuffer', once loaded
    async available() {
      return typeof WebAssembly === 'object' && typeof WebAssembly.instantiate === 'function';
    },
    load() {
      loading = loading || (async () => {
        if (!window.ZXingWASM) await script(BASE + 'zxing-reader.js');
        let fail;
        const failed = new Promise((_, reject) => { fail = reject; });
        const ready = window.ZXingWASM.prepareZXingModule({
          overrides: {
            locateFile: (path, prefix) => (path.endsWith('.wasm') ? BASE + path : prefix + path),
            instantiateWasm: (imports, done) => {
              instantiate(imports).then(made => { decoder.via = made.via; done(made.instance, made.module); }, fail);
              return {};
            },
          },
          fireImmediately: true,
        });
        await Promise.race([ready, failed]);
      })();
      loading.catch(() => { loading = null; });
      return loading;
    },
    async detect(source) {
      const width = source.videoWidth || source.width;
      const height = source.videoHeight || source.height;
      if (!width || !height) return [];
      const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
      canvas = canvas || document.createElement('canvas');
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const context = canvas.getContext('2d', {willReadFrequently: true});
      context.drawImage(source, 0, 0, canvas.width, canvas.height);
      const image = context.getImageData(0, 0, canvas.width, canvas.height);
      const found = await window.ZXingWASM.readBarcodes(image, {
        formats: Object.values(TO_ZXING), tryHarder: true, maxNumberOfSymbols: 4});
      return found.filter(code => code.isValid !== false && code.text)
        .map(code => ({text: code.text, format: FROM_ZXING[code.format] || String(code.format)}));
    },
  };
  if (window.bookflowDecoders) window.bookflowDecoders.register(decoder);
  else (window.bookflowDecoderPending = window.bookflowDecoderPending || []).push(decoder);
})();
