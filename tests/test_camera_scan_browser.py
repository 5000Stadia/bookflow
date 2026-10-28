"""R190 (POS increment 2): the camera in the Scan field, in real headless Chrome with a fake camera.

Chrome's fake camera plays a video of barcodes made at test time (--use-fake-device-for-media-stream,
--use-file-for-fake-video-capture). On the pick screen (http://127.0.0.1 is a secure page, so the camera runs live) the
camera reads the bin label and the sealant three times, in order, each through the Scan field's own look-up, and the
list advances with a beep each and no taps between. On the count screen, with the page made insecure as plain LAN http
is, the Camera button takes one photo per tap and the overlay says why; a photo of an unknown code buzzes twice and
changes nothing. The decoder follows the host's scan_decoders setting with no code change, and nothing is fetched from
the internet (Chrome resolves no other host). The keyboard-scanner tests are test_barcode_scan_browser.py,
test_pick_list_browser.py and test_inventory_count_browser.py.
"""
import json
import time

import pytest

from bookflow.company.barcodes import ean13_check_digit
from tests.barcode_images import photo, y4m
from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

A2 = '2000000001012'
SEALANT = '4006381334020'
UNKNOWN = '5901234123457'
WHY = 'Live scanning needs a secure connection. Until Bookflow is set up with HTTPS, each tap takes one photo.'
OFFLINE = '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1'


def _login(b, site):
    b.navigate(site.base_url + '/login')
    b.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value = {json.dumps(site.login)};
      document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit(); }})()""")
    b.wait_for("!!document.querySelector('.nav-group')", timeout=30)


def _chrome(tmp_path, site, *args, before=None):
    b = _Cdp(tmp_path / 'camera-chrome', extra_args=(OFFLINE, '--autoplay-policy=no-user-gesture-required', *args))
    if before:
        b.call('Page.enable')
        b.call('Page.addScriptToEvaluateOnNewDocument', {'source': before})
    _login(b, site)
    return b


def _write(b, site, name, payload):
    return _command(b, site, name, payload, **{'X-Bookflow-Reason': 'Camera test'})


def _same_origin_only(b):
    """Every resource the page loaded came from the host itself."""
    urls = b.evaluate('performance.getEntriesByType("resource").map(e => e.name).concat([location.href])')
    origin = b.evaluate('location.origin')
    assert all(url.startswith(origin) for url in urls), [u for u in urls if not u.startswith(origin)]


def _pick_list(b, site):
    _write(b, site, 'company.update', {'pick_and_pack_enabled': True})
    _write(b, site, 'sales-order.create', {
        'customer': 'Riverside Apartments', 'date': DEMO_AS_OF, 'number': 'CAM-1',
        'lines': [{'item': 'Pipe Sealant', 'quantity': '3'}]})
    made = _write(b, site, 'pick-list.create', {'orders': ['CAM-1'], 'date': DEMO_AS_OF, 'picker': 'Sam'})
    return made.get('number') or made['pick_list']['number']


def test_the_camera_scans_into_the_pick_screen_in_order(browser_site, tmp_path):
    site = browser_site
    # The bin label, then the sealant three times with a blank gap between (the code leaves the frame), then a long
    # blank tail; the fake camera loops the file, so the test closes the camera once the third is picked.
    video = y4m(tmp_path / 'scans.y4m', [(A2, 4), (None, 4), (SEALANT, 4), (None, 4), (SEALANT, 4), (None, 4),
                                         (SEALANT, 4), (None, 30)])
    b = _chrome(tmp_path, site, '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                f'--use-file-for-fake-video-capture={video}')
    try:
        number = _pick_list(b, site)
        base = f'{site.base_url}/c/{site.company_id}'
        b.viewport(390, 844)
        b.navigate(f'{base}/inventory/pick-lists/{number}')
        b.wait_for('!!window.bookflowScan && !!window.bookflowCamera && !!document.querySelector("[data-scan-mode=pick] [data-scan-camera]")',
                   timeout=30)
        assert b.evaluate('document.querySelector("meta[name=bookflow-scan-decoders]").content') == 'native,zxing-wasm'
        assert b.evaluate('window.isSecureContext') is True
        assert not b.evaluate('document.querySelector("[data-scan-mode=pick] [data-scan-camera]").hidden')
        b.evaluate('document.querySelector("[data-scan-mode=pick] [data-scan-camera]").click()')
        b.wait_for('!document.querySelector("[data-camera-overlay]").hidden', timeout=10)
        assert b.evaluate('window.bookflowCamera.mode') == 'live'
        # Headless Linux Chrome has no BarcodeDetector, so the order falls through to zxing-wasm.
        b.wait_for('window.bookflowDecoders.active === "zxing-wasm"', timeout=30)
        deadline = time.monotonic() + 60
        picked = '0'
        while time.monotonic() < deadline:
            shown = _command(b, site, 'pick-list.show', {'pick_list': number})
            picked = shown['lines'][0]['picked']
            if picked == '3':
                break
            time.sleep(0.25)
        if picked == '3':   # the beep follows the page's answer
            b.wait_for('window.bookflowCamera.signals.length >= 4', timeout=15)
        b.evaluate('document.querySelector("[data-camera-close]").click()')
        assert picked == '3', (picked, b.evaluate('JSON.stringify(window.bookflowCamera.signals)'),
                               b.evaluate('document.body.innerText').strip()[-800:])
        signals = b.evaluate('window.bookflowCamera.signals.map(s => s.kind + ":" + s.code)')
        assert signals[:4] == [f'found:{A2}', f'found:{SEALANT}', f'found:{SEALANT}', f'found:{SEALANT}'], signals
        assert b.evaluate('window.bookflowCamera.signals.every(s => s.sound && s.decoder === "zxing-wasm")')
        shown = _command(b, site, 'pick-list.show', {'pick_list': number})
        assert [(line['bin'], line['picked']) for line in shown['lines']] == [('A2', '3')], shown['lines']
        # The page under the overlay moved on by itself; the camera stopped on Close.
        assert 'picked 3 of 3' in b.evaluate('document.querySelector("[data-pick-result]").innerText')
        assert b.evaluate('document.querySelector("[data-camera-overlay]").hidden')
        assert b.evaluate('!document.querySelector("[data-camera-video]").srcObject')
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        # The wasm came from the vendored folder as application/wasm, compiled while streaming; nothing else was asked.
        assert b.evaluate('document.querySelector("[data-camera-decoder]").textContent') == 'Using zxing-wasm'
        wasm = b.evaluate('''fetch("/static/vendor/zxing-wasm/3.1.5/zxing_reader.wasm").then(r => r.headers.get("content-type"))''',
                          await_promise=True)
        assert wasm == 'application/wasm'
        _same_origin_only(b)
        assert any('/static/vendor/zxing-wasm/3.1.5/zxing_reader.wasm' in url for url in b.evaluate(
            'performance.getEntriesByType("resource").map(e => e.name)'))
    finally:
        b.close()


def test_one_photo_per_tap_on_plain_http_and_an_unknown_code_changes_nothing(browser_site, tmp_path):
    site = browser_site
    # Plain http on the LAN is not a secure page; this test makes 127.0.0.1 look the same.
    b = _chrome(tmp_path, site, before="Object.defineProperty(window, 'isSecureContext', {get: () => false});")
    try:
        code = '29000000005' + '0'
        code += ean13_check_digit(code)
        _write(b, site, 'item.update', {'item': 'Brass Shutoff Valve', 'barcode': code})
        made = _write(b, site, 'inventory-count.create', {'site': 'Van 1', 'date': '2026-12-20',
                                                          'items': ['Brass Shutoff Valve']})
        number = made.get('number') or made['count']['number']
        base = f'{site.base_url}/c/{site.company_id}'
        b.viewport(390, 844)
        b.navigate(f'{base}/inventory/counts/{number}')
        b.wait_for('!!window.bookflowCamera && !!document.querySelector("[data-scan-mode=count] [data-scan-camera]")',
                   timeout=30)
        b.call('Page.setInterceptFileChooserDialog', {'enabled': True})
        b.evaluate('document.querySelector("[data-scan-mode=count] [data-scan-camera]").click()')
        b.wait_for('!document.querySelector("[data-camera-overlay]").hidden', timeout=10)
        assert b.evaluate('window.bookflowCamera.mode') == 'photo'
        assert b.evaluate('document.querySelector("[data-camera-why]").innerText') == WHY
        assert not b.evaluate('document.querySelector("[data-camera-photo]").hidden')

        def give(path):
            root = b.call('DOM.getDocument', {'depth': 0})['root']['nodeId']
            node = b.call('DOM.querySelector', {'nodeId': root, 'selector': '[data-camera-file]'})['nodeId']
            b.call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(path)]})

        before = len(b.evaluate('window.bookflowCamera.signals'))
        give(photo(tmp_path / 'valve.png', code))
        b.wait_for(f'window.bookflowCamera.signals.length > {before}', timeout=30)
        assert b.evaluate('window.bookflowCamera.signals.at(-1).kind') == 'found', b.evaluate(
            'document.querySelector("[data-camera-flash]").innerText')
        b.wait_for('(document.querySelector("[data-count-result]")?.innerText || "").includes("counted 1")', timeout=30)
        counted = _command(b, site, 'inventory-count.show', {'count': number})
        # An unknown code: two low beeps, the field's own "Assign to an item", and nothing changes.
        before = len(b.evaluate('window.bookflowCamera.signals'))
        give(photo(tmp_path / 'unknown.png', UNKNOWN))
        b.wait_for(f'window.bookflowCamera.signals.length > {before}', timeout=30)
        assert b.evaluate('window.bookflowCamera.signals.at(-1)') == {
            'kind': 'unknown', 'code': UNKNOWN, 'sound': True, 'decoder': 'zxing-wasm'}
        status = b.evaluate('document.querySelector("[data-scan-mode=count] [data-scan-status]").innerText')
        assert f'No item has the code {UNKNOWN}' in status and 'Assign to an item' in status, status
        assert _command(b, site, 'inventory-count.show', {'count': number}) == counted
        # The reason shows once a session: the next tap does not repeat it.
        b.evaluate('document.querySelector("[data-camera-close]").click()')
        b.evaluate('document.querySelector("[data-scan-mode=count] [data-scan-camera]").click()')
        b.wait_for('!document.querySelector("[data-camera-overlay]").hidden', timeout=10)
        assert b.evaluate('document.querySelector("[data-camera-why]").hidden')
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        _same_origin_only(b)
    finally:
        b.close()


# A stand-in for the browser's BarcodeDetector (headless Linux Chrome has none), so "native" is available.
_NATIVE = """window.BarcodeDetector = class { static async getSupportedFormats() { return ['ean_13', 'code_128']; }
  constructor() {} async detect() { return []; } };"""


def test_the_decoder_follows_the_host_setting(browser_site, tmp_path):
    site = browser_site
    b = _chrome(tmp_path, site, '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', before=_NATIVE)
    try:
        number = _pick_list(b, site)
        url = f'{site.base_url}/c/{site.company_id}/inventory/pick-lists/{number}'

        def decoder_on_open():
            b.navigate(url)
            b.wait_for('!!window.bookflowCamera && !!document.querySelector("[data-scan-camera]")', timeout=30)
            b.evaluate('document.querySelector("[data-scan-mode=pick] [data-scan-camera]").click()')
            b.wait_for('!!window.bookflowDecoders.active', timeout=30)
            name = b.evaluate('window.bookflowDecoders.active')
            shown = b.evaluate('document.querySelector("[data-camera-decoder]").textContent')
            menu = b.evaluate('document.querySelector("[data-camera-choice] option").textContent')
            b.evaluate('document.querySelector("[data-camera-close]").click()')
            return name, shown, menu

        assert site.app.state.scan_decoders == 'native,zxing-wasm'
        assert decoder_on_open() == ('native', 'Using native', 'Automatic (native, zxing-wasm, from the host)')
        # `bookflow serve --scan-decoders zxing-wasm` (or BOOKFLOW_SCAN_DECODERS): the same pages, another decoder.
        site.app.state.scan_decoders = 'zxing-wasm'
        assert decoder_on_open() == ('zxing-wasm', 'Using zxing-wasm', 'Automatic (zxing-wasm, from the host)')
        assert b.evaluate('document.querySelector("meta[name=bookflow-scan-decoders]").content') == 'zxing-wasm'
        assert b.evaluate('[...document.querySelectorAll("[data-camera-choice] option")].map(o => o.value)') == [
            '', 'native', 'zxing-wasm']
        # This device's override from the Decoder menu wins over the host, and is kept across pages.
        b.evaluate('''(() => { const s = document.querySelector("[data-camera-choice]"); s.value = "native";
            s.dispatchEvent(new Event("change")); })()''')
        assert decoder_on_open()[0] == 'native'
        b.evaluate('localStorage.removeItem("bookflow.scan.decoders")')
        assert decoder_on_open()[0] == 'zxing-wasm'
        _same_origin_only(b)
    finally:
        b.close()
