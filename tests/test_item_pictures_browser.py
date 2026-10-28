"""R114 increment 1, the item picture in real headless Chrome at 1440 and 390 pixels.

A person opens an item, taps Take photo (a camera file input) and gives it a large photo: the browser scales it to
1600 px on its long edge, makes a 320 px thumbnail, and posts both through htmx. The picture shows beside the item's
name, served as image/jpeg with an immutable cache header; the item list shows its thumbnail; the pick screen shows
it on the item's line. A HEIC photo is refused with what to do instead; Remove takes the picture away. Nothing scrolls
sideways on a phone. With BF_PICTURE_SHOTS set to a folder, the screens are saved there.
"""
import base64
import os
import struct
import zlib
from pathlib import Path

import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _png(width, height):
    row = b'\x00' + b''.join(bytes(((x * 7) % 256, 120, 200 - (x % 150))) for x in range(width))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(row * height)) + chunk(b'IEND', b''))


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    folder = os.environ.get('BF_PICTURE_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


def _give(b, selector, path):
    root = b.call('DOM.getDocument', {'depth': 0})['root']['nodeId']
    node = b.call('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
    b.call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(path)]})


def _image_loaded(selector):
    """Some image the selector names has loaded (a phone's list shows cards, its hidden table's images wait)."""
    return f'[...document.querySelectorAll({selector!r})].some(i => i.complete && i.naturalWidth > 0)'


@pytest.mark.parametrize('width', (1440, 390))
def test_take_a_photo_and_see_it_on_the_item_list_and_pick_screen(register_browser, tmp_path, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    item = run('item.show', {'item': 'Pipe Sealant'})
    assert item['picture'] is None
    photo = tmp_path / 'IMG_0001.png'
    photo.write_bytes(_png(2400, 1800))
    heic = tmp_path / 'IMG_0002.HEIC'
    heic.write_bytes(b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic' + b'\x00' * 200)

    b.navigate(f'{base}/item/{item["id"]}')
    b.wait_for('!!document.querySelector("[data-item-picture-form]")', timeout=30)
    camera = 'input[type=file][capture=environment][accept="image/*"]'
    assert b.evaluate(f'!!document.querySelector({camera!r})')

    # A HEIC photo the browser cannot read goes up as it is, and the answer says what to do.
    _give(b, '[data-picture-choose]:not([capture])', heic)
    b.wait_for('!!document.querySelector("[data-picture-error]")', timeout=30)
    assert 'Most Compatible' in b.evaluate('document.querySelector("[data-picture-error]").innerText')

    # Take photo: scaled and thumbnailed in the browser, posted through htmx.
    _give(b, camera, photo)
    b.wait_for('document.querySelector("[data-picture-status]").innerText.includes("Picture saved")', timeout=30)
    b.wait_for(_image_loaded('[data-item-picture-image]'), timeout=30)
    shown = run('item.show', {'item': item['id']})
    picture, thumbnail = shown['picture'], shown['thumbnail']
    assert picture['chosen'] and picture['media_type'] == 'image/jpeg' and thumbnail['media_type'] == 'image/jpeg'
    assert thumbnail['size_bytes'] < picture['size_bytes']
    sizes = b.evaluate(f'''Promise.all([{picture['url']!r}, {thumbnail['url']!r}].map(async u => {{
        const r = await fetch(u); const bmp = await createImageBitmap(await r.blob());
        return [bmp.width, bmp.height, r.headers.get('content-type'), r.headers.get('cache-control'),
                r.headers.get('x-content-type-options')]; }}))''', await_promise=True)
    assert sizes[0][:2] == [1600, 1200] and sizes[1][:2] == [320, 240], sizes
    assert sizes[0][2] == 'image/jpeg' and 'immutable' in sizes[0][3] and sizes[0][4] == 'nosniff', sizes
    # A wrong address for the bytes is not served.
    assert b.evaluate(f'fetch({picture["url"][:-4] + "0000"!r}).then(r => r.status)', await_promise=True) == 404
    _shot(b, width, 'item-page')
    if width == 390:
        _fits(b)

    # The item list shows the thumbnail.
    b.navigate(f'{base}/item')
    b.wait_for(_image_loaded('img.item-thumb'), timeout=30)
    assert b.evaluate(f'[...document.querySelectorAll("img.item-thumb")].some(i => i.src.endsWith({thumbnail["url"]!r}))')
    # Only the item with a picture has one; the column shows because one does.
    assert b.evaluate('document.querySelectorAll("#master-results img.item-thumb").length') == 1
    assert b.evaluate('document.querySelectorAll(".list-cards img.item-thumb").length') == 1
    _shot(b, width, 'item-list')
    if width == 390:
        _fits(b)

    # The pick screen shows it on the item's line.
    write('company.update', {'pick_and_pack_enabled': True})
    customer = run('sales-order.show', {'sales_order': 'DEMO-SO-1'})['revision']['facts']['profile']['customer']['id']
    write('sales-order.create', {'customer': customer, 'date': DEMO_AS_OF, 'number': f'PIC-{width}', 'lines': [
        {'item': 'Pipe Sealant', 'quantity': '2'}]})
    pick = write('pick-list.create', {'orders': [f'PIC-{width}'], 'date': DEMO_AS_OF, 'picker': 'Sam'})
    b.navigate(f'{base}/inventory/pick-lists/{pick["number"]}')
    b.wait_for(_image_loaded('#pick-list img.item-thumb'), timeout=30)
    _shot(b, width, 'pick-screen')
    if width == 390:
        _fits(b)

    # Remove takes the picture away: it is unlinked from the item, and nothing else was attached.
    b.navigate(f'{base}/item/{item["id"]}')
    b.wait_for('!!document.querySelector("[data-picture-remove]")', timeout=30)
    b.evaluate('window.confirm = () => true; document.querySelector("[data-picture-remove]").click()')
    b.wait_for('document.querySelector("[data-picture-status]").innerText.includes("Picture removed")', timeout=30)
    assert run('item.show', {'item': item['id']})['picture'] is None
    assert not b.evaluate('!!document.querySelector("[data-item-picture-image]")')


def _exif_rotated(jpeg, orientation=6):
    """The JPEG with an EXIF APP1 segment saying how to turn it (6: a quarter turn clockwise), as a phone writes it."""
    entry = b'\x01\x12\x00\x03\x00\x00\x00\x01' + orientation.to_bytes(2, 'big') + b'\x00\x00'
    payload = b'Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x01' + entry + b'\x00\x00\x00\x00'
    return jpeg[:2] + b'\xff\xe1' + (len(payload) + 2).to_bytes(2, 'big') + payload + jpeg[2:]


def test_a_rotated_photo_shows_upright_and_failures_say_so(register_browser, tmp_path):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(390, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    item = run('item.show', {'item': 'Pipe Sealant'})
    b.navigate(f'{base}/item/{item["id"]}')
    b.wait_for('!!document.querySelector("[data-item-picture-form]")', timeout=30)
    # A phone photo stored sideways (900 x 600, red on top) with EXIF orientation 6.
    drawn = b.evaluate('''(async () => { const c = document.createElement('canvas'); c.width = 900; c.height = 600;
        const g = c.getContext('2d'); g.fillStyle = '#f00'; g.fillRect(0, 0, 900, 300);
        g.fillStyle = '#00f'; g.fillRect(0, 300, 900, 300);
        const blob = await new Promise(ok => c.toBlob(ok, 'image/jpeg', 0.95));
        const bytes = new Uint8Array(await blob.arrayBuffer()); let s = '';
        for (const x of bytes) s += String.fromCharCode(x); return btoa(s); })()''', await_promise=True)
    photo = tmp_path / 'IMG_0003.jpg'
    photo.write_bytes(_exif_rotated(base64.b64decode(drawn)))
    _give(b, 'input[type=file][capture=environment]', photo)
    b.wait_for('document.querySelector("[data-picture-status]").innerText.includes("Picture saved")', timeout=30)
    shown = run('item.show', {'item': item['id']})
    upright = b.evaluate(f'''Promise.all([{shown['picture']['url']!r}, {shown['thumbnail']['url']!r}].map(async u => {{
        const bmp = await createImageBitmap(await (await fetch(u)).blob());
        const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height;
        const g = c.getContext('2d'); g.drawImage(bmp, 0, 0);
        const px = (x, y) => Array.from(g.getImageData(x, y, 1, 1).data.slice(0, 3));
        return [bmp.width, bmp.height, px(bmp.width - 10, bmp.height / 2), px(10, bmp.height / 2)]; }}))''',
                         await_promise=True)
    # Turned a quarter clockwise: portrait, the red top now on the right, the blue on the left.
    assert [u[:2] for u in upright] == [[600, 900], [213, 320]], upright
    for width, height, right, left in upright:
        assert right[0] > 200 and right[2] < 60 and left[2] > 200 and left[0] < 60, upright

    # A picture that will not load shows "No picture", not an empty frame.
    b.evaluate('(() => { const i = document.querySelector("[data-item-picture-image]"); '
               'i.src = i.src.slice(0, -4) + "0000"; })()')
    b.wait_for('!!document.querySelector("[data-picture-none]")', timeout=30)
    assert 'No picture' in b.evaluate('document.querySelector(".item-picture-frame").innerText')

    # A post htmx does not swap (here the server answers 404) still says what happened.
    b.evaluate('document.body.addEventListener("htmx:configRequest", e => { e.detail.path += "/missing"; }, '
               '{once: true})')
    _give(b, '[data-picture-choose]:not([capture])', photo)
    b.wait_for('!!document.querySelector("[data-picture-error]")', timeout=30)
    assert 'not saved' in b.evaluate('document.querySelector("[data-picture-error]").innerText')
    assert 'Saving' not in b.evaluate('document.querySelector("[data-picture-status]").innerText')
