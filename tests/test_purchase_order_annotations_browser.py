"""The existing purchase-order record page shares human annotations with commands."""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_purchase_order_annotations_desktop_and_phone(register_browser, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    vendor = run('vendor.create', {'name': 'Annotation supplier'})['id']
    order = run('purchase-order.post', {
        'vendor': vendor, 'date': '2026-06-01', 'number': 'PO-NOTES',
        'lines': [{'account': env.expense['id'], 'description': 'Fittings', 'amount': '75.00'}],
    })
    target = {'record_type': 'purchase_order', 'record_id': order['id']}
    run('note.add', {**target, 'body': 'Supplier will call before delivery.'})
    # Home promotion is a separate increment; exercise the existing query-to-record links.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/purchase-order')
    b.wait_for(f'[...document.querySelectorAll("a[href]")].some(a=>a.href.endsWith({json.dumps("/" + order["id"])}))')
    b.evaluate(f'[...document.querySelectorAll("a[href]")].find(a=>a.href.endsWith({json.dumps("/" + order["id"])})).click()')
    b.wait_for('!!document.querySelector("[data-note-id]")')
    config = json.loads(b.evaluate('document.querySelector("[data-annotations]").dataset.annotations'))
    assert config['target'] == target
    assert 'Supplier will call before delivery.' in b.evaluate('document.querySelector("[data-annotations]").innerText')
    for width in (1280, 390):
        b.viewport(width, 900)
        body = f'Delivery note from {width}px screen'
        b.evaluate(f'document.querySelector("#annotation-note").value={json.dumps(body)};document.querySelector("[data-note-add]").requestSubmit()')
        b.wait_for('document.querySelector("[data-note-add] [data-status]").textContent === "Note added."')
        assert body in [note['body'] for note in run('note.list', target)['items']]
        assert b.evaluate('''[...document.querySelectorAll('[data-annotations] textarea,[data-annotations] input')]
            .every(e=>{const r=e.getBoundingClientRect();return r.width>0 && r.left>=0 && r.right<=innerWidth;})''')
    source = tmp_path / 'supplier-quote.txt'
    source.write_text('Quote for fittings\n')
    root = b.call('DOM.getDocument')['root']['nodeId']
    node = b.call('DOM.querySelector', {'nodeId': root, 'selector': '#annotation-file'})['nodeId']
    b.call('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(source)]})
    b.evaluate('document.querySelector("[data-file-add]").requestSubmit()')
    b.wait_for('document.querySelector("[data-file-add] [data-status]").textContent === "File attached."')
    assert run('attachment.list', target)['items'][0]['attachment']['original_filename'] == source.name
    assert run('purchase-order.show', {'purchase_order': order['id']})['version'] == order['version']
