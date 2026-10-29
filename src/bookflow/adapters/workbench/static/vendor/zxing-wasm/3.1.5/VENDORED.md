# zxing-wasm 3.1.5 (vendored)

The camera scanner's `zxing-wasm` decoder (design/point-of-sale.md §7): zxing-cpp compiled to WebAssembly, reader
build only. Loaded by `static/scan-decoder-zxing-wasm.js` the first time the camera opens with this decoder chosen;
never from a CDN, so scanning works on a LAN with no internet.

- **Source:** npm package `zxing-wasm`, version 3.1.5,
  <https://registry.npmjs.org/zxing-wasm/-/zxing-wasm-3.1.5.tgz>
  (project <https://github.com/Sec-ant/zxing-wasm>; zxing-cpp commit 2ecec3f5be0ee803f6e14a5a2c7028c0cfe525b4).
- **Tarball integrity** (the registry's `dist.integrity`, checked against the downloaded file on 2026-10-28):
  `sha512-jmxXvTCR/qZxMz2XwR6V+nL2BlDTD1aux7ZWSH9BV77TG9u4k0VyHba/NCCHBHJ60Wg6VV0QL/SMlJczKhJI4w==`
- **Licence:** MIT (`LICENSE`, copied unchanged from the package). zxing-cpp itself is Apache-2.0.

| File here | From the package | SHA-256 |
|---|---|---|
| `zxing-reader.js` | `dist/iife/reader/index.js` | `228e6d8ccb841c544386eeafd5f18294d3e4aa0e7aa20419bc51a0009b9d3c17` |
| `zxing_reader.wasm` | `dist/reader/zxing_reader.wasm` | `aecc1876de036c62c8419f67a5e1a16b1698a325bcd190aa84810d516e263931` |
| `LICENSE` | `LICENSE` | `fb506e4ade12d7a9efa67c9d76a9a28c8e15d347ca49a69e48e29b40b34ad2ab` |

`tests/test_camera_scan_vendored.py` checks these hashes. The reader declares the wasm's own SHA-256
(`ZXING_WASM_SHA256`), which equals the one above.

**Safety pass (2026-10-28).** The tarball was fetched into an empty scratch directory and its integrity checked before
extraction. The package has no install scripts that run on use (its `scripts` are the maintainer's build tooling) and
two type-only dependencies (`type-fest`, `@types/emscripten`), neither shipped here. In `zxing-reader.js` the only
URL is the default `locateFile`, which points the `.wasm` at `fastly.jsdelivr.net`; the adapter replaces the module's
`locateFile` and its `instantiateWasm`, so the wasm is fetched from this folder only. Its other network calls are the
Emscripten loader's `fetch(url, {credentials: 'same-origin'})` and a synchronous `XMLHttpRequest` for the same wasm
URL (its synchronous loading path); it has no `sendBeacon`, WebSocket, worker or `importScripts` use, no `eval`, and reads
nothing from `navigator`. It defines one global, `ZXingWASM`.

To change version: fetch the new tarball the same way, add a sibling folder named for the version with its own
`VENDORED.md`, point the adapter's `BASE` at it, and update the hash test.
