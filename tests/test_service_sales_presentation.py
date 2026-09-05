"""Captured sales views and generic-form edit origin boundary."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from bookflow.adapters.workbench.sales import detail_context, editable_values, preserve_line_origins
from bookflow.adapters.workbench import forms
from bookflow.company.sales_models import InvoiceUpdateInput
from tests.test_service_sales_lifecycle import sale, post, COMPANY  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401

TEMPLATES = Path(__file__).resolve().parents[1] / 'src/bookflow/adapters/workbench/templates'


def test_owned_shipping_address_lookup_and_history_paging(hosted):
    import html
    import re
    from tests.test_row5_workbench_forms import _browser
    browser = _browser(hosted)
    cid = hosted.company_id
    customer = hosted.ok('customer.create', dict(name='Address picker customer', shipping_addresses=[
        dict(label='<Dock>', is_default=True, line1='10 Dock Road'), dict(label='Other site', line1='20 Dock Road')]), company=cid)
    path = f'/c/{cid}/_references/invoice/shipping_address_id'
    choices = browser.get(path, params={'q': 'Dock', 'f:customer': customer['id']})
    assert choices.status_code == 200
    assert '&lt;Dock&gt;' in choices.text and '<Dock>' not in choices.text
    assert all(address['id'] in choices.text for address in customer['shipping_addresses'])
    assert browser.get(path, params={'q': 'Dock'}).text == ''
    other = hosted.ok('customer.create', dict(name='No shipping address customer'), company=cid)
    assert browser.get(path, params={'q': 'Dock', 'f:customer': other['id']}).text == ''
    invoice = hosted.ok('invoice.show', {'invoice': 'DEMO-SALE-INV-ACTIVE'}, company=cid)
    history = browser.get(f'/c/{cid}/invoice/{invoice["id"]}/history?limit=1')
    assert history.status_code == 200 and 'Revision history' in history.text
    assert f'?revision_number=1' in history.text and f'?revision_number=2' not in history.text
    next_url = html.unescape(re.search(r'href="([^"]+)" rel="next"', history.text)[1])
    following = browser.get(next_url)
    assert following.status_code == 200 and '?revision_number=2' in following.text
    assert 'Next revisions' not in following.text


def render(record, preview=False):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape())
    return env.get_template('sales_detail.html').render(sale=detail_context(record, 'company', preview=preview))


def test_public_sale_render_and_quantity_edit_preserve_captured_origins(client, sale):
    first = post(client, sale)
    baseline = editable_values(first)
    assert baseline['lines'][0]['unit_price'] == '12.34'
    assert baseline['lines'][0]['quantity'] == '2.5'
    html = render(first)
    assert 'Sale witness customer' in html and 'Service labor' in html and '30.85' in html
    assert 'Accounting history for this revision' in html
    changed = deepcopy(baseline['lines'])
    changed[0]['quantity'] = '3'
    patch = preserve_line_origins({'lines': changed}, baseline)
    assert patch['lines'] == [{'line_id': baseline['lines'][0]['line_id'], 'item': sale['item'], 'quantity': '3'}]
    saved = client.run('invoice update', dict(invoice=first['id'], expected_version=1, **patch), company=COMPANY)
    before = first['revision']['lines'][0]['item_snapshot']
    after = saved['revision']['lines'][0]['item_snapshot']
    assert after == before
    assert saved['total_minor_units'] == 3702


def test_generic_collection_translation_then_origin_projection(client, sale):
    first = post(client, sale)
    baseline = editable_values(first)
    form = {'collection:lines': '1', 'f:invoice': first['id']}
    for index, row in enumerate(baseline['lines']):
        for key, value in row.items():
            if value is not None:
                form[f'c:lines:{index}:{key}'] = str(value)
    form['c:lines:0:quantity'] = '4'
    raw, _, _ = forms.translate(SimpleNamespace(input_model=InvoiceUpdateInput, name='invoice update'), form, baseline)
    patch = preserve_line_origins(raw, baseline)
    assert patch['lines'][0] == dict(line_id=baseline['lines'][0]['line_id'], item=sale['item'], quantity='4')


def test_generated_line_clear_is_explicit_and_preserves_other_origins(client, sale):
    first = post(client, sale)
    baseline = editable_values(first)
    form = {'collection:lines': '1', 'f:invoice': first['id'], 'f:expected_version': '1'}
    for key, value in baseline['lines'][0].items():
        if value is not None:
            form['c:lines:row0:' + key] = str(value)
    form['clear:c:lines:row0:description'] = '1'
    raw, _, _ = forms.translate(SimpleNamespace(input_model=InvoiceUpdateInput, name='invoice update'), form, baseline)
    updated = client.run('invoice update', preserve_line_origins(raw, baseline), company=COMPANY)
    line = updated['revision']['lines'][0]
    assert line['description'] is None
    assert line['item_snapshot']['origins']['description']['kind'] == 'explicit'
    assert line['item_snapshot']['origins']['unit_price'] == first['revision']['lines'][0]['item_snapshot']['origins']['unit_price']


def test_xss_custom_falsy_addresses_and_revision_links(client, sale):
    record = post(client, sale)
    dangerous = '<script>alert("stored")</script>'
    record['revision']['profile']['customer']['label'] = dangerous
    record['revision']['profile']['billing_address'] = dict(line1=dangerous, city='Saved city')
    record['revision']['custom_fields'] = [dict(definition_id=str(i), name=dangerous, kind=kind, value=value)
        for i, (kind, value) in enumerate([('bool', False), ('number', 0), ('text', '')])]
    record['revision']['revision_number'] = 2
    record['current_revision_id'] = 'later'
    record['id'] = 'x?next=<script>'
    baseline = editable_values(record)
    assert baseline['billing_address']['line1'] == dangerous
    assert baseline['custom_fields'] == {'0': False, '1': 0, '2': ''}
    html = render(record)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<dd>false</dd>' in html and '<dd>0</dd>' in html and '<dd></dd>' in html
    assert 'Saved city' in html and 'Previous revision' in html and 'Current revision' in html
    assert '/x%3Fnext%3D%3Cscript%3E?revision_number=1' in html
    preview = render(record, preview=True)
    assert 'Preview — nothing written' in preview and 'Previous revision' not in preview


def test_origin_projection_reordering_new_rows_clears_and_refresh():
    baseline = {'lines': [dict(line_id='a', item='one', unit_price='0.00', description='', class_id='old'),
                          dict(line_id='b', item='two', unit_price='4.00', description=None)]}
    raw = {'lines': [dict(baseline['lines'][1], refresh_defaults=True),
                     dict(baseline['lines'][0], class_id=None, use_defaults=['unit_price']),
                     dict(item='new', unit_price='0.00', description='')]}
    result = preserve_line_origins(raw, baseline)
    assert result['lines'] == [dict(line_id='b', item='two', refresh_defaults=True),
                               dict(line_id='a', item='one', class_id=None, use_defaults=['unit_price']),
                               dict(item='new', unit_price='0.00', description='')]
    assert raw['lines'][0]['unit_price'] == '4.00'


def test_browser_fingerprint_and_payload_specific_retry_hooks(tmp_path):
    import json
    import pytest
    from tests.test_row5_browser_acceptance import CHROME, _Cdp
    if not CHROME.exists():
        pytest.skip('Chrome unavailable')
    browser = _Cdp(tmp_path / 'sales-chrome')
    try:
        markup = '''<form data-sales-form data-sales-scope="actor-1" action="/c/company/invoice/post">
          <input name="f:expected_facts_fingerprint" type="hidden" value="''' + 'a' * 64 + '''">
          <input name="f:memo" value="before"><input name="ctx:idempotency_key">
          <span data-sales-preview-status></span><button type="button" data-collection-add>Add</button></form>'''
        browser.evaluate('document.body.innerHTML = ' + json.dumps(markup))
        browser.evaluate((TEMPLATES.parent / 'static/sales.js').read_text())
        browser.evaluate('''window.requestSale = (action='submit') => {
          const form=document.querySelector('form');
          const event={target:form, detail:{elt:form,parameters:Object.fromEntries(new FormData(form)),headers:{}},
            prevented:false, preventDefault(){this.prevented=true;}};
          event.detail.parameters.action=action;
          const handled=bookflowSales.configure(event);
          return {handled, prevented:event.prevented, key:event.detail.headers['Idempotency-Key'],
            fingerprint:event.detail.parameters['f:expected_facts_fingerprint']};
        }''')
        first = browser.evaluate('requestSale()')
        assert first['handled'] and not first['prevented'] and first['key'].startswith('sale-')
        assert browser.evaluate('requestSale()')['key'] == first['key']
        # Full body error response reuses the exact same request key.
        browser.evaluate('document.body.innerHTML = ' + json.dumps(markup))
        assert browser.evaluate('requestSale()')['key'] == first['key']
        browser.evaluate('''document.querySelector('[name="f:memo"]').value='after';
          document.querySelector('[name="f:memo"]').dispatchEvent(new Event('input',{bubbles:true}));''')
        assert browser.evaluate('requestSale()')['prevented']
        assert browser.evaluate("requestSale('preview')").get('fingerprint') is None
        # A successful preview renders the edited input and new fingerprint.
        browser.evaluate('document.body.innerHTML = ' + json.dumps(markup.replace('before', 'after').replace('a' * 64, 'b' * 64)))
        second = browser.evaluate('requestSale()')
        assert not second['prevented'] and second['key'] != first['key']
        assert browser.evaluate('requestSale()')['key'] == second['key']
        browser.evaluate("document.querySelector('[data-collection-add]').click()")
        assert browser.evaluate('requestSale()')['prevented']
    finally:
        browser.close()


def test_receipt_projection_and_commercial_view_at_desktop_phone(client, sale, tmp_path):
    import json
    import pytest
    from tests.test_row5_browser_acceptance import CHROME, _Cdp
    bank = client.account.create(name='Presentation deposit', type='bank', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Presentation cash', kind='cash'), company=COMPANY)['id']
    receipt = client.run('sales-receipt post', dict(date='2026-01-12', customer=sale['customer'],
        deposit_to=bank, payment_method=method, payment_reference='Paid at counter',
        lines=[dict(item=sale['item'], description='Very long description ' * 80)]), company=COMPANY)
    baseline = editable_values(receipt)
    assert baseline['payment_method'] == method and baseline['deposit_to'] == bank
    assert 'terms' not in baseline and 'due_date' not in baseline and 'ar_account' not in baseline
    markup = render(receipt)
    assert 'Presentation cash' in markup and 'Paid at counter' in markup and 'Sales receipt' in markup
    if not CHROME.exists():
        pytest.skip('Chrome unavailable')
    browser = _Cdp(tmp_path / 'sales-layout-chrome')
    try:
        style = '\n'.join((TEMPLATES.parent / 'static' / name).read_text() for name in ('style.css', 'sales.css'))
        browser.evaluate('document.head.innerHTML = ' + json.dumps('<meta name="viewport" content="width=device-width, initial-scale=1"><style>' + style + '</style>'))
        browser.evaluate('document.body.innerHTML = ' + json.dumps('<main>' + markup + '</main>'))
        for width in (1280, 390):
            browser.viewport(width, 900)
            assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert browser.evaluate("document.querySelector('.sales-document').innerText.includes('12.34')")
    finally:
        browser.close()
