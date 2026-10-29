"""R190 (POS increment 2): the camera in the Scan field, in real headless Chrome with a fake camera.

Chrome's fake camera plays a video of barcodes made at test time (--use-fake-device-for-media-stream,
--use-file-for-fake-video-capture). http://127.0.0.1 is a secure page, so the camera runs live:
  - on the pick screen it reads the bin label and the sealant three times, in order, each through the Scan field's own
    look-up; the list fills its first line and moves to the second with no taps between, and each scan plays a tone;
  - on the count screen two labels in one frame are each counted once, however long both stay in view;
  - with the page made insecure, as plain LAN http is, each tap takes one photo (here on the pick screen), the overlay
    says why once a session, and a photo of an unknown code buzzes twice and changes nothing;
  - the decoder follows the host's scan_decoders setting from BOOKFLOW_SCAN_DECODERS to the page, with no code change;
    a decoder that will not load, or keeps failing, is passed over for the next;
  - a camera started twice, or closed while starting, leaves no track running once Close returns, and a code read
    before Close is never delivered after it; a keyboard-mode scanner still enters codes while the overlay is open.
Nothing is fetched from the internet (Chrome resolves no host but 127.0.0.1). The keyboard-scanner tests are
test_barcode_scan_browser.py, test_pick_list_browser.py, test_inventory_count_browser.py and test_pack_browser.py.
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
VALVE = '290000000050' + ean13_check_digit('290000000050')
PIPE = '290000000051' + ean13_check_digit('290000000051')
WHY = 'Live scanning needs a secure connection. Until Bookflow is set up with HTTPS, each tap takes one photo.'
OFFLINE = '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1'
FAKE_CAMERA = ('--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream')
# Every tone the page starts, by frequency; every camera stream it is handed.
WATCH = """
window.__tones = [];
const __start = OscillatorNode.prototype.start;
OscillatorNode.prototype.start = function (...args) { window.__tones.push(this.frequency.value); return __start.apply(this, args); };
window.__streams = []; window.__asked = 0;
if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
  const __gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async constraints => {
    window.__asked += 1; const stream = await __gum(constraints); window.__streams.push(stream); return stream; };
}
"""
INSECURE = "Object.defineProperty(window, 'isSecureContext', {get: () => false});"


def _chrome(tmp_path, site, *args, before=''):
    b = _Cdp(tmp_path / 'camera-chrome', extra_args=(OFFLINE, '--autoplay-policy=no-user-gesture-required', *args))
    b.call('Page.enable')
    b.call('Page.addScriptToEvaluateOnNewDocument', {'source': before + WATCH})
    b.navigate(site.base_url + '/login')
    b.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value = {json.dumps(site.login)};
      document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit(); }})()""")
    b.wait_for("!!document.querySelector('.nav-group')", timeout=30)
    return b


def _write(b, site, name, payload):
    return _command(b, site, name, payload, **{'X-Bookflow-Reason': 'Camera test'})


def _same_origin_only(b):
    """Every resource the page loaded came from the host itself."""
    urls = b.evaluate('performance.getEntriesByType("resource").map(e => e.name).concat([location.href])')
    origin = b.evaluate('location.origin')
    assert all(url.startswith(origin) for url in urls), [u for u in urls if not u.startswith(origin)]


def _pick_list(b, site, lines):
    _write(b, site, 'company.update', {'pick_and_pack_enabled': True})
    _write(b, site, 'sales-order.create', {'customer': 'Riverside Apartments', 'date': DEMO_AS_OF, 'number': 'CAM-1',
                                           'lines': [{'item': 'Pipe Sealant', 'quantity': q} for q in lines]})
    return _write(b, site, 'pick-list.create', {'orders': ['CAM-1'], 'date': DEMO_AS_OF, 'picker': 'Sam'})['number']


def _open_camera(b, mode):
    b.wait_for(f'!!window.bookflowScan && !!window.bookflowCamera && !!document.querySelector("[data-scan-mode={mode}] [data-scan-camera]")',
               timeout=30)
    assert not b.evaluate(f'document.querySelector("[data-scan-mode={mode}] [data-scan-camera]").hidden')
    b.evaluate(f'document.querySelector("[data-scan-mode={mode}] [data-scan-camera]").click()')
    b.wait_for('!document.querySelector("[data-camera-overlay]").hidden', timeout=10)


def _close_and_check_tracks(b):
    b.evaluate('document.querySelector("[data-camera-close]").click()')
    assert b.evaluate('document.querySelector("[data-camera-overlay]").hidden')
    states = b.evaluate('window.__streams.flatMap(s => s.getTracks().map(t => t.readyState))')
    assert states and all(state == 'ended' for state in states), states


def _signals(b):
    return b.evaluate('window.bookflowCamera.signals.map(s => s.kind + ":" + s.code)')


def test_the_camera_scans_into_the_pick_screen_line_after_line(browser_site, tmp_path):
    site = browser_site
    # The bin label, then the sealant three times with a blank gap between (the code leaves the frame), then a long
    # blank tail; the fake camera loops the file, so the test closes the camera once the third is picked.
    video = y4m(tmp_path / 'scans.y4m', [(A2, 4), (None, 6), (SEALANT, 4), (None, 6), (SEALANT, 4), (None, 6),
                                         (SEALANT, 4), (None, 30)])
    b = _chrome(tmp_path, site, *FAKE_CAMERA, f'--use-file-for-fake-video-capture={video}')
    try:
        number = _pick_list(b, site, ['1', '2'])
        b.viewport(390, 844)
        b.navigate(f'{site.base_url}/c/{site.company_id}/inventory/pick-lists/{number}')
        _open_camera(b, 'pick')
        assert b.evaluate('document.querySelector("meta[name=bookflow-scan-decoders]").content') == 'native,zxing-wasm'
        assert b.evaluate('window.isSecureContext') is True and b.evaluate('window.bookflowCamera.mode') == 'live'
        # Headless Linux Chrome has no BarcodeDetector, so the order falls through to zxing-wasm.
        b.wait_for('window.bookflowDecoders.active === "zxing-wasm"', timeout=30)
        assert b.evaluate('document.documentElement.classList.contains("camera-open")')
        assert b.evaluate('parseFloat(getComputedStyle(document.body).paddingBottom) >= document.querySelector("[data-camera-overlay]").offsetHeight')
        states = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            lines = _command(b, site, 'pick-list.show', {'pick_list': number})['lines']
            state = tuple(line['picked'] for line in lines)
            if not states or states[-1] != state:
                states.append(state)
            if state == ('1', '2'):
                break
            time.sleep(0.2)
        assert states[-1] == ('1', '2'), (states, _signals(b), b.evaluate('document.body.innerText')[-800:])
        # The first line filled before the list moved on to the second.
        assert ('1', '0') in states and states.index(('1', '0')) < states.index(('1', '2')), states
        b.wait_for('window.bookflowCamera.signals.length >= 4', timeout=15)   # each beep follows the page's answer
        _close_and_check_tracks(b)
        assert _signals(b)[:4] == [f'found:{A2}'] + [f'found:{SEALANT}'] * 3, _signals(b)
        # A tone was played for each: one 1,000 Hz beep a scan.
        assert b.evaluate('window.__tones.filter(f => f === 1000).length') >= 4
        assert all(line['bin'] == 'A2' for line in _command(b, site, 'pick-list.show', {'pick_list': number})['lines'])
        assert b.evaluate('!document.querySelector("[data-camera-video]").srcObject')
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        # The wasm came from the vendored folder as application/wasm; nothing outside the host was asked for.
        assert b.evaluate('document.querySelector("[data-camera-decoder]").textContent') == 'Using zxing-wasm'
        wasm = b.evaluate('fetch("/static/vendor/zxing-wasm/3.1.5/zxing_reader.wasm").then(r => r.headers.get("content-type"))',
                          await_promise=True)
        assert wasm == 'application/wasm'
        _same_origin_only(b)
    finally:
        b.close()


def test_two_labels_in_one_frame_are_each_counted_once_on_the_count_screen(browser_site, tmp_path):
    site = browser_site
    # Both labels stay in view for 4 s: each is one scan, however many decodes see it.
    video = y4m(tmp_path / 'two.y4m', [(None, 2), ((VALVE, PIPE), 20), (None, 40)])
    b = _chrome(tmp_path, site, *FAKE_CAMERA, f'--use-file-for-fake-video-capture={video}')
    try:
        _write(b, site, 'item.update', {'item': 'Brass Shutoff Valve', 'barcode': VALVE})
        _write(b, site, 'item.update', {'item': 'Copper Pipe', 'barcode': PIPE})
        number = _write(b, site, 'inventory-count.create', {'site': 'Van 1', 'date': '2026-12-20',
                                                            'items': ['Brass Shutoff Valve', 'Copper Pipe']})['number']
        b.viewport(390, 844)
        b.navigate(f'{site.base_url}/c/{site.company_id}/inventory/counts/{number}')
        _open_camera(b, 'count')
        assert b.evaluate('window.bookflowCamera.mode') == 'live'
        b.wait_for('window.bookflowCamera.signals.length >= 2', timeout=60)
        time.sleep(3)    # both still in view for the rest of the 4 s
        signals = _signals(b)
        _close_and_check_tracks(b)
        assert sorted(signals) == sorted([f'found:{VALVE}', f'found:{PIPE}']), signals
        lines = {line['item_name']: line['counted'] for line in
                 _command(b, site, 'inventory-count.show', {'count': number})['lines']}
        assert lines.get('Brass Shutoff Valve') == '1' and lines.get('Copper Pipe') == '1', lines
        assert b.evaluate('window.__tones.filter(f => f === 1000).length') == 2
    finally:
        b.close()


def _give(b, path):
    root = b.call('DOM.getDocument', {'depth': 0})['root']['nodeId']
    node = b.call('DOM.querySelector', {'nodeId': root, 'selector': '[data-camera-file]'})['nodeId']
    b.call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(path)]})


def _photo(b, path):
    before = len(b.evaluate('window.bookflowCamera.signals'))
    _give(b, path)
    b.wait_for(f'window.bookflowCamera.signals.length > {before}', timeout=30)
    return b.evaluate('window.bookflowCamera.signals.at(-1)')


def test_one_photo_per_tap_on_plain_http_and_an_unknown_code_changes_nothing(browser_site, tmp_path):
    site = browser_site
    b = _chrome(tmp_path, site, before=INSECURE)
    try:
        number = _pick_list(b, site, ['2'])
        b.viewport(390, 844)
        b.navigate(f'{site.base_url}/c/{site.company_id}/inventory/pick-lists/{number}')
        b.call('Page.setInterceptFileChooserDialog', {'enabled': True})
        _open_camera(b, 'pick')
        assert b.evaluate('window.bookflowCamera.mode') == 'photo'
        assert b.evaluate('document.querySelector("[data-camera-why]").innerText') == WHY
        assert not b.evaluate('document.querySelector("[data-camera-photo]").hidden')
        assert _photo(b, photo(tmp_path / 'a2.png', A2))['kind'] == 'found'
        assert _photo(b, photo(tmp_path / 'sealant.png', SEALANT))['kind'] == 'found'
        b.wait_for('(document.querySelector("[data-pick-result]")?.innerText || "").includes("picked 1 of 2")', timeout=30)
        picked = _command(b, site, 'pick-list.show', {'pick_list': number})
        # An unknown code: two low beeps, the field's own "Assign to an item", nothing changes, and the code waits in
        # the field selected, so the next scan replaces it.
        tones = b.evaluate('window.__tones.length')
        assert _photo(b, photo(tmp_path / 'unknown.png', UNKNOWN)) == {
            'kind': 'unknown', 'code': UNKNOWN, 'sound': True, 'decoder': 'zxing-wasm'}
        assert b.evaluate(f'window.__tones.slice({tones})') == [330, 330]
        status = b.evaluate('document.querySelector("[data-scan-mode=pick] [data-scan-status]").innerText')
        assert f'No item has the code {UNKNOWN}' in status and 'Assign to an item' in status, status
        assert b.evaluate('''(() => { const i = document.querySelector("[data-scan-mode=pick] [data-scan-input]");
            return [i.value, i.selectionStart, i.selectionEnd, document.activeElement === i]; })()''') == [
            UNKNOWN, 0, len(UNKNOWN), False]
        assert _command(b, site, 'pick-list.show', {'pick_list': number}) == picked
        # The reason shows once a session: the next tap does not repeat it.
        b.evaluate('document.querySelector("[data-camera-close]").click()')
        b.evaluate('document.querySelector("[data-scan-mode=pick] [data-scan-camera]").click()')
        b.wait_for('!document.querySelector("[data-camera-overlay]").hidden', timeout=10)
        assert b.evaluate('document.querySelector("[data-camera-why]").hidden')
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        _same_origin_only(b)
    finally:
        b.close()


# A stand-in for the browser's BarcodeDetector (headless Linux Chrome has none), so "native" is available.
_NATIVE = """window.BarcodeDetector = class { static async getSupportedFormats() { return ['ean_13', 'code_128']; }
  constructor() {} async detect() { return []; } };"""


@pytest.mark.parametrize('setting, expected', ((None, 'native'), ('zxing-wasm', 'zxing-wasm')))
def test_the_decoder_follows_the_host_setting(request, monkeypatch, tmp_path, setting, expected):
    # BOOKFLOW_SCAN_DECODERS as `bookflow serve` reads it when the host starts (--scan-decoders takes the same path).
    if setting:
        monkeypatch.setenv('BOOKFLOW_SCAN_DECODERS', setting)
    else:
        monkeypatch.delenv('BOOKFLOW_SCAN_DECODERS', raising=False)
    site = request.getfixturevalue('browser_site')
    b = _chrome(tmp_path, site, *FAKE_CAMERA, before=_NATIVE)
    try:
        number = _pick_list(b, site, ['1'])
        url = f'{site.base_url}/c/{site.company_id}/inventory/pick-lists/{number}'

        def decoder_on_open():
            b.navigate(url)
            _open_camera(b, 'pick')
            b.wait_for('!!window.bookflowDecoders.active && !!document.querySelector("[data-camera-video]").srcObject', timeout=30)
            found = (b.evaluate('window.bookflowDecoders.active'),
                     b.evaluate('document.querySelector("[data-camera-decoder]").textContent'),
                     b.evaluate('document.querySelector("[data-camera-choice] option").textContent'))
            _close_and_check_tracks(b)
            return found

        order = setting or 'native,zxing-wasm'
        assert decoder_on_open() == (expected, f'Using {expected}',
                                     f'Automatic ({order.replace(",", ", ")}, from the host)')
        assert b.evaluate('document.querySelector("meta[name=bookflow-scan-decoders]").content') == order
        assert b.evaluate('[...document.querySelectorAll("[data-camera-choice] option")].map(o => o.value)') == [
            '', 'native', 'zxing-wasm']
        # This device's override from the Decoder menu wins over the host, and is kept across pages.
        other = 'zxing-wasm' if expected == 'native' else 'native'
        b.evaluate(f'''(() => {{ const s = document.querySelector("[data-camera-choice]"); s.value = "{other}";
            s.dispatchEvent(new Event("change")); }})()''')
        assert decoder_on_open()[0] == other
        b.evaluate('localStorage.removeItem("bookflow.scan.decoders")')
        assert decoder_on_open()[0] == expected
        _same_origin_only(b)
    finally:
        b.close()


# A "native" decoder that will not load, or whose detect() always throws, registered before the real adapter.
_FAILING = """window.bookflowDecoderPending = [{name: 'native', formats: ['ean_13'],
  async available() { return true; },
  async load() { if (window.__nativeMode === 'load') throw new Error('broken on purpose'); },
  async detect() { window.__nativeDetects = (window.__nativeDetects || 0) + 1; throw new Error('detect broken'); }}];
window.__nativeMode = sessionStorage.getItem('nativeMode') || 'load';"""


def test_camera_lifecycle_failing_decoders_and_a_keyboard_scanner(browser_site, tmp_path):
    site = browser_site
    b = _chrome(tmp_path, site, *FAKE_CAMERA, before=_FAILING)
    try:
        number = _pick_list(b, site, ['2'])
        url = f'{site.base_url}/c/{site.company_id}/inventory/pick-lists/{number}'
        b.navigate(url)
        # Closed while the decoder is still loading: the camera is never asked for.
        b.wait_for('!!window.bookflowCamera && !!document.querySelector("[data-scan-camera]")', timeout=30)
        b.evaluate('''(() => { document.querySelector("[data-scan-mode=pick] [data-scan-camera]").click();
            document.querySelector("[data-camera-close]").click(); })()''')
        time.sleep(3)
        assert b.evaluate('window.__asked') == 0
        # A decoder that will not load is passed over for the next.
        _open_camera(b, 'pick')
        b.wait_for('window.bookflowDecoders.active === "zxing-wasm" && window.__streams.length > 0', timeout=30)
        # Started twice (a double tap), then closed and reopened while starting: one camera, and Close ends it all.
        b.evaluate('''(() => { const button = document.querySelector("[data-scan-mode=pick] [data-scan-camera]");
            button.click(); button.click(); document.querySelector("[data-camera-close]").click(); button.click(); })()''')
        b.wait_for('window.__streams.filter(s => s.getTracks().some(t => t.readyState === "live")).length === 1 && '
                   '!!document.querySelector("[data-camera-video]").srcObject', timeout=30)
        time.sleep(1)
        assert b.evaluate('window.__streams.filter(s => s.getTracks().some(t => t.readyState === "live")).length') == 1
        # A keyboard-mode scanner while the overlay is open: its keys reach the Scan field, without focusing it.
        b.evaluate('document.activeElement.blur()')
        for key in A2:
            b.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': key, 'text': key})
            b.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': key})
        b.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
        b.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
        b.wait_for('document.querySelector("[data-scan-bin]").value === "A2"', timeout=30)
        assert not b.evaluate('document.activeElement.matches("[data-scan-input]")')
        # Escape closes the overlay, and every track the page opened has ended.
        b.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Escape', 'code': 'Escape', 'windowsVirtualKeyCode': 27})
        b.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Escape', 'code': 'Escape', 'windowsVirtualKeyCode': 27})
        assert b.evaluate('document.querySelector("[data-camera-overlay]").hidden')
        states = b.evaluate('window.__streams.flatMap(s => s.getTracks().map(t => t.readyState))')
        assert len(states) >= 2 and all(state == 'ended' for state in states), states
        # A code read before Close is not delivered after it.
        _open_camera(b, 'pick')
        b.wait_for('!!document.querySelector("[data-camera-video]").srcObject', timeout=30)
        signals = len(b.evaluate('window.bookflowCamera.signals'))
        b.evaluate(f'''(() => {{ window.bookflowCamera.take("{UNKNOWN}"); window.bookflowCamera.take("{A2}");
            document.querySelector("[data-camera-close]").click(); }})()''')
        time.sleep(2)
        assert len(b.evaluate('window.bookflowCamera.signals')) == signals
        assert all(s == 'ended' for s in b.evaluate('window.__streams.flatMap(s => s.getTracks().map(t => t.readyState))'))
        # A decoder whose detect() keeps failing is passed over after ten tries, with a message.
        b.evaluate('sessionStorage.setItem("nativeMode", "detect")')
        b.navigate(url)
        _open_camera(b, 'pick')
        b.wait_for('window.__nativeDetects >= 10 && window.bookflowDecoders.active === "zxing-wasm" && '
                   '!!document.querySelector("[data-camera-video]").srcObject', timeout=30)
        assert b.evaluate('window.__nativeDetects') == 10
        _close_and_check_tracks(b)
        _same_origin_only(b)
    finally:
        b.close()
