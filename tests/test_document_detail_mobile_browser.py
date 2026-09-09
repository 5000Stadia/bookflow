"""Saved document readback in real Chrome, using only disposable books."""
import base64
import json

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
PROSE = 'Replace the kitchen fittings and retain all inspection notes.\n' + 'ScopeReference' * 18


def _cells(b, selector):
    return b.evaluate(f'''[...document.querySelectorAll({json.dumps(selector)})].map(e=>{{
        const copy=e.cloneNode(true); copy.querySelectorAll('.document-cell-label').forEach(l=>l.remove());
        return copy.textContent.trim(); }})''')


def _layouts(b, tmp_path, name):
    """Containment checks cover each table, its wrapper and every visible cell."""
    b.viewport(390, 900)
    measured = b.evaluate('''[...document.querySelectorAll('.document-lines,.document-lines td,.document-lines tfoot th,.document-lines tbody tr,.document-detail .table-wrap')]
        .filter(e=>e.getBoundingClientRect().width).map(e=>({tag:e.tagName,
        scroll:e.scrollWidth,client:e.clientWidth,left:e.getBoundingClientRect().left,
        right:e.getBoundingClientRect().right,height:e.getBoundingClientRect().height}))''')
    (tmp_path / f'{name}-390-widths.json').write_text(json.dumps(measured, indent=2) + '\n')
    assert measured and all(m['scroll'] == m['client'] and m['left'] >= 0 and m['right'] <= 390 and m['height'] > 0 for m in measured), measured
    assert b.evaluate('document.documentElement.scrollWidth === document.documentElement.clientWidth')
    assert b.evaluate('getComputedStyle(document.querySelector(".document-cell-label")).display') == 'block'
    assert b.evaluate('''[...document.querySelectorAll('.document-lines tbody tr')].every(e=>
        getComputedStyle(e).display==='grid' && getComputedStyle(e).gridTemplateColumns.split(' ').length===2)''')
    # Real accessibility tree keeps table, row, column header and cell roles after CSS layout changes.
    ax_nodes = b.call('Accessibility.getFullAXTree', {})['nodes']
    by_id = {node['nodeId']: node for node in ax_nodes}
    document = b.call('DOM.getDocument', {})['root']['nodeId']
    tables = b.call('DOM.querySelectorAll', {'nodeId': document, 'selector': '.document-lines'})['nodeIds']
    assert tables
    for table_id in tables:
        backend_id = b.call('DOM.describeNode', {'nodeId': table_id})['node']['backendNodeId']
        root = next(node for node in ax_nodes if node.get('backendDOMNodeId') == backend_id)
        assert not root.get('ignored') and root['role']['value'] == 'table', root
        pending, roles = [root['nodeId']], set()
        while pending:
            node = by_id[pending.pop()]
            if not node.get('ignored'):
                roles.add(node.get('role', {}).get('value'))
            pending.extend(node.get('childIds', []))
        assert {'table', 'row', 'columnheader', 'cell'} <= roles, roles
    (tmp_path / f'{name}-390.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':True})['data']))
    # Disabling only this stylesheet reproduces the original table overflow.
    assert b.evaluate('''(() => {const sheet=[...document.styleSheets].find(s=>s.href?.includes('/document-detail.css'));
        sheet.disabled=true; const t=document.querySelector('.document-lines');
        const overflow=t.scrollWidth>t.parentElement.clientWidth; sheet.disabled=false; return overflow;})()''')
    for media, width in [('', 1280), ('print', 390)]:
        b.viewport(width, 900)
        b.call('Emulation.setEmulatedMedia', {'media':media})
        try:
            assert b.evaluate('''[...document.querySelectorAll('.document-lines')].every(e=>
                getComputedStyle(e).display==='table' && getComputedStyle(e.querySelector('thead')).position==='static' &&
                getComputedStyle(e.querySelector('tbody tr')).display==='table-row')''')
            assert b.evaluate('getComputedStyle(document.querySelector(".document-cell-label")).display') == 'none'
        finally:
            b.call('Emulation.setEmulatedMedia', {'media':''})


def test_saved_work_chain_phone_values_links_and_rules(register_browser, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    income = run('account.create', dict(name='Phone work income', type='income'))['id']
    customer = run('customer.create', dict(name='Phone work customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('company.update', dict(units_of_measure_mode='multiple_related_units'))
    units = run('unit-of-measure.create', dict(name='Phone units', units=[dict(name='Each', abbreviation='ea', is_base=True, base_factor='1')]))
    item = run('item.create', dict(name='Phone service', type='service', sales_enabled=True,
        income_account_id=income, description='Saved service', price='12.34', sales_tax_code_id=code, unit_of_measure_set_id=units['id']))['id']
    proposal = run('proposal.create', dict(date='2026-01-12', title='Phone scope', customer=customer,
        lines=[dict(item=item, quantity='2.5', description=PROSE, estimated_unit_cost='4.00', markup_percent='25'),
               dict(item=item, quantity='3', net_amount='10.01', description='Exact quoted amount', billable=False)]))
    estimate = run('proposal.estimate', dict(proposal=proposal['id'], expected_version=1, conversion_key='phone-estimate', date='2026-01-13'))
    estimate = run('estimate.update', dict(estimate=estimate['id'], expected_version=1, status='accepted', decision_note='Agreed'))
    order = run('estimate.work-order', dict(estimate=estimate['id'], expected_version=2, conversion_key='phone-order', date='2026-01-14'))
    order = run('work-order.complete', dict(work_order=order['id'], expected_version=1, actual_start='2026-01-14T10:00:00Z', actual_end='2026-01-14T11:00:00Z'))
    for noun, record in [('proposal', proposal), ('estimate', estimate), ('work-order', order)]:
        b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/{record["id"]}')
        b.wait_for('!!document.querySelector(".work-document .document-lines")')
        b.viewport(390, 900)
        revision = record['revision']
        assert b.evaluate('document.querySelector(".document-prose").innerText') == PROSE
        for index, line in enumerate(revision['lines'], 1):
            row = f'.document-lines tbody tr:nth-child({index})'
            for label, expected in [('Quantity',line['quantity']),('Unit', line['facts']['profile']['unit']['label'] if line['facts']['profile']['unit'] else '—'),('Unit price',line['unit_price']['amount'] if line['unit_price'] else ''),
                ('Net',line['net']['amount']),('Tax',line['tax']['amount']),('Total',line['total']['amount']),
                ('Billable','Yes' if line['facts']['billable'] else 'No')]:
                assert _cells(b, row + f' [data-label="{label}"]') == [expected]
            assert ('Quoted source scope' if line['source_line_id'] else 'Independent scope') in _cells(b, row + ' td:first-child')[0]
            if noun == 'work-order':
                assert _cells(b, row + ' [data-label="Completed"]') == [line['completed_quantity']]
        totals = b.evaluate('document.querySelector(".document-totals").innerText')
        assert totals == f'Net {revision["net"]["amount"]} · Tax {revision["tax"]["amount"]} · Total {revision["total"]["amount"]} {revision["currency"]}'
        assert 'Price mode' not in b.evaluate('document.querySelector(".document-lines").innerText')
        rules = 'document.querySelector(".work-document details:last-of-type")'
        assert not b.evaluate(rules + '.open')
        b.evaluate(rules + '.querySelector("summary").click()')
        assert 'Price mode: markup · 25%' in b.evaluate(rules + '.innerText')
        assert 'Price mode: amount' in b.evaluate(rules + '.innerText')
        b.evaluate(rules + '.querySelector("summary").click()')
        _layouts(b, tmp_path, noun)
        b.evaluate('document.querySelector(".document-lines a").click()')
        b.wait_for('!!document.querySelector("[data-annotations]") && location.search.includes("annotation_line=")')
        target = json.loads(b.evaluate('document.querySelector("[data-annotations]").dataset.annotations'))['target']
        assert target == dict(record_type='work_line', record_id=revision['lines'][0]['line_id'])


@pytest.mark.parametrize('foreign', [False, True])
def test_saved_journal_phone_exact_money_and_history(register_browser, tmp_path, foreign):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    customer = run('customer.create', dict(name='Journal customer with retained name'))['id']
    klass = run('class.create', dict(name='Journal service class'))['id']
    amount = '2345 JPY' if foreign else '1234.56'
    args = dict(date='2026-03-11', number='PHONE-JOURNAL', lines=[
        dict(account=env.bank['id'], side='debit', amount=amount, description=PROSE, name_type='customer', name_id=customer, class_id=klass),
        dict(account=env.expense['id'], side='credit', amount=amount, description='Offset')])
    if foreign:
        args['rate'] = '0.0068'
    record = run('journal.post', args)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/journal/{record["id"]}')
    b.wait_for('!!document.querySelector(".document-lines")')
    b.viewport(390, 900)
    assert b.evaluate('document.querySelector(".document-prose").innerText').endswith(PROSE)
    table = '[aria-label="Journal entry"] .document-lines'
    assert _cells(b, table + ' tbody [data-label="Name"]') == ['Journal customer with retained name', '']
    assert _cells(b, table + ' tbody [data-label="Class"]') == ['Journal service class', '']
    assert _cells(b, table + ' tbody [data-label="Account"]') == ['CDP bank', 'CDP supplies']
    expected = '15.95' if foreign else '1234.56'
    assert _cells(b, table + ' tbody [data-label="Debit"]') == [expected, '']
    assert _cells(b, table + ' tbody [data-label="Credit"]') == ['', expected]
    assert _cells(b, table + ' tfoot th') == ['Total (USD)', expected, expected]
    assert len(_cells(b, table + ' tbody [data-label="Original / rate"]')) == (2 if foreign else 0)
    if foreign:
        assert _cells(b, table + ' tbody [data-label="Original / rate"]') == ['2345 JPY · 0.0068 USD per JPY · manual'] * 2
    history = '[aria-label="Accounting history"] .document-lines'
    assert _cells(b, history + ' [data-label="Effect"]') == ['Original']
    assert _cells(b, history + ' [data-label="Debits"]') == [expected]
    assert _cells(b, history + ' [data-label="Credits"]') == [expected]
    _layouts(b, tmp_path, 'journal-foreign' if foreign else 'journal-home')
