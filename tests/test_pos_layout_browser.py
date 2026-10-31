"""R114 increment 6: shared layouts and four styles in real Chrome, with pictures of both viewports."""
import base64
import json
from pathlib import Path

import pytest

from tests.test_item_pictures_browser import _fits, _give, _png
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos, _lines, SEALANT, COUPLING
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _click(b, selector):
    b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')


def _saved(b, version):
    b.wait_for(f'window.bookflowPos.layout().version > {version}', timeout=20)


def _shot(b, name, width):
    folder = Path('/tmp/m4-shots/pos6')
    folder.mkdir(parents=True, exist_ok=True)
    b.evaluate('document.activeElement && document.activeElement.blur()')
    (folder / f'{name}-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png'})['data']))


@pytest.mark.parametrize('width', [390, 1440])
def test_four_styles_saved_on_home_and_group_and_read_on_another_device(browser_site, tmp_path, width):
    site = browser_site
    b = _signed_in(tmp_path, site, width, 900)
    other = None
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        # Persist the same category layout the preceding increment computed.
        version = b.evaluate('window.bookflowPos.layout().version')
        _click(b, '[data-pos-seed="categories"]')
        _saved(b, version)
        assert b.evaluate('document.querySelector("[data-pos-seed-options]").hidden')
        for style, columns in [('block', 2 if width == 390 else 5), ('list', 1),
                               ('compact', 3 if width == 390 else 6), ('photo', 1 if width == 390 else 3)]:
            version = b.evaluate('window.bookflowPos.layout().version')
            b.evaluate('document.querySelector(".pos-styles details").open = true')
            _click(b, f'[data-pos-style="{style}"]')
            _saved(b, version)
            b.evaluate('document.querySelector(".pos-styles details").open = false')
            assert b.evaluate('getComputedStyle(document.querySelector("[data-pos-boxes]")).gridTemplateColumns.split(" ").length') == columns
            _fits(b)
            _shot(b, f'home-{style}', width)
        _click(b, '[data-pos-box-group="Plumbing Parts"]')
        group = b.evaluate('document.querySelector("[data-pos-crumbs] [aria-current]").textContent')
        assert group == 'Plumbing Parts'
        for style in ['block', 'list', 'compact', 'photo']:
            version = b.evaluate('window.bookflowPos.layout().version')
            b.evaluate('document.querySelector(".pos-styles details").open = true')
            _click(b, f'[data-pos-style="{style}"]')
            _saved(b, version)
            b.evaluate('document.querySelector(".pos-styles details").open = false')
            _fits(b)
            _shot(b, f'group-{style}', width)
            if style == 'compact':
                assert b.evaluate('getComputedStyle(document.querySelector("[data-pos-boxes] .pos-tile")).display') == 'none'
            if style == 'list':
                assert b.evaluate('document.querySelector("[data-pos-boxes] .pos-onhand").textContent.includes("on hand")')
                assert b.evaluate('document.querySelector("[data-pos-boxes] .pos-box").getBoundingClientRect().height') >= 56
        version = b.evaluate('window.bookflowPos.layout().version')
        _click(b, '[data-pos-style="list"]')
        _saved(b, version)
        other = _signed_in(tmp_path / 'another-device', site, width, 900)
        _open_pos(other, site)
        assert other.evaluate('document.querySelector("[data-pos-boxes]").classList.contains("pos-view-photo")')
        _click(other, '[data-pos-box-group="Plumbing Parts"]')
        assert other.evaluate('document.querySelector("[data-pos-boxes]").classList.contains("pos-view-list")')
        # A saved List is still an instant till, and the item picture comes from the same item data.
        elapsed = other.evaluate('''(() => { const start = performance.now(); document.querySelector('[data-pos-box-item]').click();
          return performance.now() - start; })()''')
        assert len(_lines(other)) == 1 and elapsed < 100
        # A stale device cannot silently overwrite the other device's newer choice.
        version = other.evaluate('window.bookflowPos.layout().version')
        other.evaluate('document.querySelector(".pos-styles details").open = true')
        _click(other, '[data-pos-style="compact"]')
        _saved(other, version)
        b.evaluate('''(() => { const real = window.fetch; window.fetch = async (url, init) => {
          const answer = await real(url, init);
          if (String(url).endsWith('/pos-box.list')) {
            window.__recoveryWaiting = true;
            await new Promise(resolve => { window.__releaseRecovery = resolve; });
          }
          return answer;
        }; })()''')
        b.evaluate('document.querySelector(".pos-styles details").open = true')
        _click(b, '[data-pos-style="photo"]')
        b.wait_for('window.__recoveryWaiting === true')
        # A second choice waits for the conflict read; its eventual save cannot be replaced by that older read.
        _click(b, '[data-pos-style="list"]')
        b.evaluate('window.__releaseRecovery()')
        b.wait_for(f'window.bookflowPos.layout().version > {version + 1}')
        assert b.evaluate('document.querySelector("[data-pos-boxes]").classList.contains("pos-view-list")')
        assert b.evaluate('document.querySelector("[data-pos-layout-status]").textContent') == 'Saved.'
    finally:
        if other:
            other.close()
        b.close()


def test_editor_adds_nested_groups_duplicate_item_moves_and_removes_only_boxes(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 390, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        version = b.evaluate('window.bookflowPos.layout().version')
        _click(b, '[data-pos-seed="empty"]')
        _saved(b, version)
        _click(b, '[data-pos-edit]')
        for label in ['Drinks', 'Hot drinks']:
            version = b.evaluate('window.bookflowPos.layout().version')
            _click(b, '[data-pos-add-group]')
            b.evaluate(f'document.querySelector("[data-pos-box-label]").value = {json.dumps(label)}')
            b.evaluate('document.querySelector("[data-pos-box-editor]").requestSubmit()')
            _saved(b, version)
            b.wait_for('!document.querySelector("[data-pos-box-dialog]").open')
            _click(b, f'[data-pos-box-group="{label}"]')
        # Lookup in edit mode adds a box without ringing up a sale.
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate('''(() => { const i = document.querySelector('[data-scan-input]'); i.value = 'Brass Shutoff Valve';
          i.dispatchEvent(new Event('input', {bubbles: true})); })()''')
        _click(b, '[data-pos-result]')
        _saved(b, version)
        assert _lines(b) == []
        item = b.evaluate('document.querySelector("[data-pos-box-item]").dataset.posBoxItem')
        _click(b, '[data-pos-crumb="0"]')
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate('''(() => { const i = document.querySelector('[data-scan-input]'); i.value = 'Brass Shutoff Valve';
          i.dispatchEvent(new Event('input', {bubbles: true})); })()''')
        _click(b, '[data-pos-result]')
        _saved(b, version)
        assert b.evaluate(f'window.bookflowPos.layout().items.filter(x => x.item === {json.dumps(item)}).length') == 2
        ordered_ids = 'Array.from(document.querySelectorAll("[data-pos-boxes] [data-pos-box-id]"), b => b.dataset.posBoxId)'
        before_drop = b.evaluate(ordered_ids)
        assert len(before_drop) == 2
        # Drag reorder in the same group uses one versioned move, without touching the item.
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate('''(() => { const boxes = document.querySelectorAll('[data-pos-boxes] [data-pos-box-id]');
          const transfer = new DataTransfer();
          boxes[1].dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: transfer}));
          boxes[0].dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer}));
        })()''')
        _saved(b, version)
        assert b.evaluate(ordered_ids) == [before_drop[1], before_drop[0]]
        # Accessible move: position one in Home, followed by removing this box.
        box_id = b.evaluate('document.querySelector("[data-pos-box-item]").closest("[data-pos-box-id]").dataset.posBoxId')
        _click(b, f'[data-pos-arrange="{box_id}"]')
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate('document.querySelector("[data-pos-box-parent]").value = ""; document.querySelector("[data-pos-box-position]").value = "1"; document.querySelector("[data-pos-box-editor]").requestSubmit()')
        b.wait_for('!document.querySelector("[data-pos-box-dialog]").open')
        _saved(b, version)
        _click(b, '[data-pos-crumb="-1"]')
        assert b.evaluate('document.querySelector("[data-pos-box-id]").dataset.posBoxId') == box_id
        _shot(b, 'editor', 390)
        _fits(b)
        _click(b, f'[data-pos-arrange="{box_id}"]')
        version = b.evaluate('window.bookflowPos.layout().version')
        _click(b, '[data-pos-box-remove]')
        _saved(b, version)
        assert b.evaluate(f'window.bookflowPos.layout().items.filter(x => x.item === {json.dumps(item)}).length') == 1
        assert _command(b, site, 'item.show', {'item': item})['id'] == item
        b.wait_for('!document.querySelector("[data-pos-box-dialog]").open')
        # USB/keyboard Enter and the camera hand-off both add boxes while editing, never sale lines.
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate(f'''(() => {{ const i = document.querySelector('[data-scan-input]'); i.value = '2*{SEALANT}';
          i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); }})()''')
        _saved(b, version)
        assert _lines(b) == []
        sealant = _command(b, site, 'item.show', {'item': 'Pipe Sealant'})['id']
        assert b.evaluate(f'window.bookflowPos.layout().items.filter(x => x.item === {json.dumps(sealant)}).length') == 1
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate(f'window.bookflowScan.scan(document.querySelector("[data-scan][data-scan-mode=pos]"), "{COUPLING}")')
        _saved(b, version)
        assert _lines(b) == []
        # A lookup-only discount cannot become a box through the scanner hand-off either.
        before = b.evaluate('window.bookflowPos.layout().version')
        assert b.evaluate('''(() => { const item = JSON.parse(document.querySelector('#pos-data').textContent).items.find(i => !i.box);
          return window.bookflowPos.scanned({item_id: item.id, item_name: item.name}, {quantity: '1'}); })()''', await_promise=True) is False
        assert b.evaluate('window.bookflowPos.layout().version') == before
        assert _lines(b) == []
    finally:
        b.close()


def test_group_picture_upload_colour_and_clear_through_htmx(browser_site, tmp_path):
    site = browser_site
    b = _signed_in(tmp_path, site, 390, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        _click(b, '[data-pos-edit]')
        box_id = b.evaluate('document.querySelector("[data-pos-box-group]").closest("[data-pos-box-id]").dataset.posBoxId')
        _click(b, f'[data-pos-arrange="{box_id}"]')
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate('document.querySelector("[data-pos-box-colour]").value = "#123456"; document.querySelector("[data-pos-box-editor]").requestSubmit()')
        _saved(b, version)
        b.wait_for('!document.querySelector("[data-pos-box-dialog]").open')
        assert b.evaluate(f'getComputedStyle(document.querySelector({json.dumps(f'[data-pos-box-id="{box_id}"] .pos-tile')})).backgroundColor') == 'rgb(18, 52, 86)'
        _click(b, f'[data-pos-arrange="{box_id}"]')
        picture = tmp_path / 'group.png'
        picture.write_bytes(_png(120, 80))
        _give(b, '[data-pos-picture-file]', picture)
        version = b.evaluate('window.bookflowPos.layout().version')
        b.evaluate('document.querySelector("[data-pos-picture-form]").requestSubmit()')
        _saved(b, version)
        b.wait_for('!document.querySelector("[data-pos-box-dialog]").open')
        b.wait_for(f'(() => {{ const i = document.querySelector({json.dumps(f'[data-pos-box-id="{box_id}"] img')}); return i && i.complete && i.naturalWidth > 0; }})()')
        url = b.evaluate(f'window.bookflowPos.layout().items.find(x => x.id === "{box_id}").picture.url')
        assert b.evaluate(f'fetch({json.dumps(url)}).then(r => [r.status, r.headers.get("cache-control"), r.headers.get("content-type")])', await_promise=True) == [200, 'private, max-age=31536000, immutable', 'image/png']
        _shot(b, 'group-picture', 390)
        _click(b, f'[data-pos-arrange="{box_id}"]')
        version = b.evaluate('window.bookflowPos.layout().version')
        _click(b, '[data-pos-picture-clear]')
        _saved(b, version)
        assert b.evaluate(f'window.bookflowPos.layout().items.find(x => x.id === "{box_id}").picture') is None
    finally:
        b.close()


@pytest.mark.parametrize('transition', ['item', 'category', 'merge'])
def test_item_transitions_keep_editor_boxes_and_skip_inactive_at_till(browser_site, tmp_path, transition):
    site = browser_site
    b = _signed_in(tmp_path, site, 390, 900)
    try:
        _pos_on(b, site)
        _open_pos(b, site)
        layout = _command(b, site, 'pos-box.list', {})
        layout = _command(b, site, 'pos-box.seed', {'mode': 'empty', 'expected_version': layout['version']})
        item = _command(b, site, 'item.show', {'item': 'Payment Example Labor' if transition == 'merge' else 'Pipe Sealant'})
        if transition == 'category':
            category = _command(b, site, 'item-category.show', {'item_category': 'Plumbing Parts'})
            item = _command(b, site, 'item.update', {'item': item['id'], 'expected_version': item['version'],
                                                   'category_id': category['id']})
        layout = _command(b, site, 'pos-box.create', {'kind': 'item', 'item': item['id'],
                                                   'expected_version': layout['version']})
        box = layout['items'][0]['id']
        if transition == 'item':
            _command(b, site, 'item.deactivate', {'item': item['id'], 'expected_version': item['version']})
        elif transition == 'category':
            category = _command(b, site, 'item-category.show', {'item_category': item['category_id']})
            _command(b, site, 'item-category.deactivate', {'item_category': category['id'],
                                                         'expected_version': category['version']})
        else:
            # A fresh matching item has no inventory cost history to block its merge.
            dup = _command(b, site, 'item.create', {'name': 'Layout duplicate sealant', 'type': item['type'],
                'description': 'Duplicate', 'price': '4.00', 'income_account_id': item['income_account_id']})
            layout = _command(b, site, 'pos-box.list', {})
            layout = _command(b, site, 'pos-box.create', {'kind': 'item', 'item': dup['id'],
                                                       'expected_version': layout['version']})
            box = next(x['id'] for x in layout['items'] if x['item'] == dup['id'])
            _command(b, site, 'item.merge', {'merged': dup['id'], 'into': item['id']})
        _open_pos(b, site)
        selector = f'[data-pos-box-id="{box}"]'
        if transition == 'merge':
            assert b.evaluate(f'document.querySelector({json.dumps(selector + " [data-pos-box-item]")}).dataset.posBoxItem') == item['id']
        else:
            assert not b.evaluate(f'!!document.querySelector({json.dumps(selector)})')
        _click(b, '[data-pos-edit]')
        if transition != 'merge':
            assert 'inactive item — remove or replace' in b.evaluate(f'document.querySelector({json.dumps(selector)}).textContent')
        _click(b, f'[data-pos-arrange="{box}"]')
        version = b.evaluate('window.bookflowPos.layout().version')
        _click(b, '[data-pos-box-remove]')
        _saved(b, version)
        assert not b.evaluate(f'window.bookflowPos.layout().items.some(x => x.id === {json.dumps(box)})')
    finally:
        b.close()
