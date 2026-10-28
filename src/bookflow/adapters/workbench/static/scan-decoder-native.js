// Decoder adapter "native" (R190, design/point-of-sale.md §7): the browser's own BarcodeDetector. Android Chrome has
// it; iOS Safari, desktop Linux and Windows Chrome and Firefox do not, and there available() is false so the next
// decoder in the order runs. Nothing to download.
(() => {
  const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code', 'data_matrix'];
  let detector = null;
  const supported = async () => {
    try { return await window.BarcodeDetector.getSupportedFormats(); } catch (error) { return []; }
  };
  const decoder = {
    name: 'native',
    formats: FORMATS,
    async available() {
      if (typeof window.BarcodeDetector !== 'function') return false;
      const formats = await supported();
      return formats.includes('ean_13') || formats.includes('code_128');
    },
    async load() {
      if (detector) return;
      const formats = await supported();
      detector = new window.BarcodeDetector({formats: FORMATS.filter(f => formats.includes(f))});
    },
    async detect(source) {
      const found = await detector.detect(source);
      return found.filter(code => code.rawValue).map(code => ({text: code.rawValue, format: code.format}));
    },
  };
  if (window.bookflowDecoders) window.bookflowDecoders.register(decoder);
  else (window.bookflowDecoderPending = window.bookflowDecoderPending || []).push(decoder);
})();
