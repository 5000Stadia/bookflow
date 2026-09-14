"""First-six entry affordances through the registered workbench commands."""
import base64
import json
from pathlib import Path
import pytest
from tests.test_money_out_browser import register_browser, browser_site, _pick, _act, _save, _books, _click
from tests.test_service_sales_browser import _fill, _value


def field(b, grid, suffix):
    return b.evaluate(f'document.querySelector("[data-collection-path={grid}] [data-collection-item] [name$=\\":{suffix}\\"]").name')


@pytest.mark.parametrize('width,noun', [(1280,'check'), (390,'card-charge')])
def test_amount_items_live_allocation_and_registered_save(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    world = _books(b, env.site, 'Paid items '+str(width))
    run = world['run']
    income = run('account.create', dict(name='Item income '+str(width), type='income'))['id']
    cogs = run('account.create', dict(name='Item COGS '+str(width), type='cost_of_goods_sold'))['id']
    item_name = 'Fractional part '+str(width)
    item = run('item.create', dict(name=item_name, type='inventory_part', income_account_id=income,
        cogs_account_id=cogs, description='Paid receipt', price='15.00', cost='12.34', purchase_description='Paid receipt'))['id']
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/post')
    b.wait_for('document.querySelector("[data-purchase-allocation]") !== null')
    _pick(b, 'f:account', world['tag']+(' checking' if noun=='check' else ' card'))
    _fill(b, 'f:date', '2026-03-04')
    _fill(b, 'f:amount', '10.99')
    b.evaluate('document.querySelector("[data-collection-path=expenses] > [data-collection-add]").click()')
    _pick(b, field(b,'expenses','account'), world['tag']+' parts')
    _fill(b, field(b,'expenses','amount'), '4.83')
    b.evaluate('document.querySelector("[data-line-tab=items]").click()')
    b.evaluate('document.querySelector("[data-collection-path=items] > [data-collection-add]").click()')
    _pick(b, field(b,'items','item'), item_name)
    _fill(b, field(b,'items','quantity'), '1/2')
    _fill(b, field(b,'items','unit_cost'), '12.34')
    b.wait_for('document.querySelector("[data-purchase-allocation]").textContent.includes("Remaining -0.01")')
    _act(b,'preview')
    assert _value(b,'f:amount') == '10.99'
    assert b.evaluate('document.querySelector(".error") !== null')
    _fill(b,'f:amount','11.00')
    b.wait_for('document.querySelector("[data-purchase-allocation]").textContent.includes("Allocated 11")')
    _act(b,'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    b.evaluate('document.querySelector("[data-line-tab=items]").click()')
    assert b.evaluate('document.querySelector("[data-purchase-raw-result]").open') is False
    assert 'Fully allocated' in b.evaluate('document.querySelector(' + json.dumps('[aria-label="Purchase allocation summary"]') + ').textContent')
    assert b.evaluate('document.querySelector("[data-generated-form]").getBoundingClientRect().top') < 900
    dest=Path('notes/item-screenshots');dest.mkdir(exist_ok=True,parents=True)
    shot=b.call('Page.captureScreenshot', {'captureBeyondViewport':True,'fromSurface':True})
    (dest/f'{noun}-{width}.png').write_bytes(base64.b64decode(shot['data']))
    identity=_save(b)
    shown=run(noun+'.show', {('check' if noun=='check' else 'card_charge'):identity})
    assert shown['document']['items'][0]['profile']['item']['id'] == item
    assert shown['document']['item_total']['minor_units'] == 617
    b.wait_for('document.querySelector("[aria-label=\\"Purchased items\\"]") !== null')
    assert item_name in b.evaluate('document.body.innerText')


@pytest.mark.parametrize('width,noun', [(1280, 'check'), (390, 'card-charge')])
def test_saved_purchase_edit_history_and_register_owner_routes(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    world = _books(b, env.site, 'Saved purchase ' + str(width))
    run = world['run']
    selector = 'check' if noun == 'check' else 'card_charge'
    funding = world['bank'] if noun == 'check' else world['card']
    income = run('account.create', dict(name='Saved income ' + str(width), type='income'))['id']
    cogs = run('account.create', dict(name='Saved COGS ' + str(width), type='cost_of_goods_sold'))['id']
    item_name = 'Saved copper ' + str(width)
    item = run('item.create', dict(name=item_name, type='inventory_part',
        income_account_id=income, cogs_account_id=cogs, description='Copper',
        purchase_description='Captured copper', price='15.00', cost='12.34'))['id']
    posted = run(noun + '.post', dict(account=funding, date='2026-03-04', amount='11.00',
        pay_to=dict(name_type='vendor', name_id=world['vendor']),
        expenses=[dict(account=world['first'], amount='4.83', memo='Delivery')],
        items=[dict(item=item, quantity='0.5', unit_cost='12.34', class_id=world['job'])]))
    identity = posted['id']
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    owner = f'{base}/{noun}/{identity}'
    register = f'{base}/account/{funding}/register'
    b.navigate(register + '?date_from=2026-01-01&date_to=2026-12-31')
    expected = f'/c/{env.site.company_id}/{noun}/{identity}'
    b.wait_for('document.querySelector(' + json.dumps(f'#register-history a[href="{expected}/update"]') + ') !== null')
    assert b.evaluate('document.querySelector(' + json.dumps(f'#register-history a[href="{expected}/void"]') + ') !== null')
    assert b.evaluate('document.querySelector(' + json.dumps(f'#register-history a[href^="{expected}?"]') + ') !== null')
    # An old register edit URL must also reach the owning form, not the generic editor.
    b.call('Page.navigate', {'url': register + '?edit=' + identity})
    b.wait_for('document.readyState === "complete" && location.pathname === ' + json.dumps(expected + '/update'))
    assert _value(b, 'f:amount') == '11.00'
    assert _value(b, 'f:date') == '2026-03-04'
    assert _value(b, field(b, 'expenses', 'amount')) == '4.83'
    assert _value(b, field(b, 'items', 'quantity')) == '0.5'
    assert _value(b, field(b, 'items', 'unit_cost')) == '12.34'
    assert _value(b, field(b, 'items', 'amount')) == ''
    # A header edit after master changes keeps the captured grid untouched.
    run('item.update', dict(item=item, purchase_description='New master copper', cost='99.00'))
    _fill(b, 'f:memo', 'Saved header correction')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    _click(b, 'submit')
    b.wait_for('location.pathname === ' + json.dumps(expected))
    current = run(noun + '.show', {selector: identity})
    assert current['document']['items'] == posted['document']['items']
    assert 'Captured copper' in b.evaluate('document.querySelector("[data-purchase-item]").textContent')
    # Another editor wins while this form contains an attempted same-money quantity edit.
    b.navigate(owner + '/update')
    _fill(b, field(b, 'items', 'quantity'), '1')
    _fill(b, field(b, 'items', 'unit_cost'), '6.17')
    winner = run(noun + '.update', {selector: identity, 'expected_version': current['version'], 'memo': 'Other editor'})
    _act(b, 'preview')
    assert 'E_VERSION_CONFLICT' in b.evaluate('document.querySelector(".error").textContent')
    assert _value(b, field(b, 'items', 'quantity')) == '1'
    assert _value(b, field(b, 'items', 'unit_cost')) == '6.17'
    assert _value(b, 'f:expected_version') == str(current['version'])
    assert run(noun + '.show', {selector: identity})['version'] == winner['version']
    # Review current state and start a fresh correction; a mismatched header stays editable.
    b.navigate(owner + '/update')
    _fill(b, field(b, 'items', 'quantity'), '1')
    _fill(b, field(b, 'items', 'unit_cost'), '6.17')
    _fill(b, 'f:amount', '10.99')
    _act(b, 'preview')
    assert 'E_UNBALANCED_ENTRY' in b.evaluate('document.querySelector(".error").textContent')
    assert _value(b, 'f:amount') == '10.99'
    assert _value(b, field(b, 'items', 'quantity')) == '1'
    _fill(b, 'f:amount', '11.00')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    _click(b, 'submit')
    b.wait_for('location.pathname === ' + json.dumps(expected))
    corrected = run(noun + '.show', {selector: identity})
    assert corrected['document']['items'][0]['quantity'] == '1'
    assert corrected['document']['items'][0]['line_id'] == posted['document']['items'][0]['line_id']
    assert corrected['document']['expense_total']['minor_units'] == 483
    valuation = run('report.inventory-valuation', {'as_of': '2026-12-31', 'limit': 200})
    stock = next(row for row in valuation['rows'] if row['item_id'] == item)
    assert stock['quantity_on_hand'] == '1' and stock['asset_value']['minor_units'] == 617
    b.navigate(owner + '?revision_number=1')
    content = b.evaluate('document.querySelector("[data-purchase-item]").textContent')
    assert 'Quantity 0.5' in content and 'Unit cost 12.34' in content and 'Captured copper' in content
    b.navigate(owner)
    content = b.evaluate('document.querySelector("[data-purchase-item]").textContent')
    assert 'Quantity 1' in content and 'Unit cost 6.17' in content
    dest = Path('notes/lifecycle-screenshots'); dest.mkdir(parents=True, exist_ok=True)
    shot = b.call('Page.captureScreenshot', {'captureBeyondViewport': True, 'fromSurface': True})
    (dest / f'{noun}-{width}.png').write_bytes(base64.b64decode(shot['data']))
    # Journal history remains readable but its write links point to the owner.
    b.navigate(base + '/journal/' + identity)
    assert b.evaluate('document.querySelector(' + json.dumps(f'a[href="{expected}/void"]') + ') !== null')
    b.call('Page.navigate', {'url': base + '/journal/' + identity + '/void'})
    b.wait_for('document.readyState === "complete" && location.pathname === ' + json.dumps(expected + '/void'))
    _fill(b, 'ctx:reason', 'Cancel saved purchase')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    _click(b, 'submit')
    b.wait_for('location.pathname === ' + json.dumps(expected))
    assert run(noun + '.show', {selector: identity})['status'] == 'voided'
    balance = run('register.query', dict(account=funding, date_from='2026-01-01', date_to='2026-12-31'))
    assert balance['current_balance']['balance']['minor_units'] == 0
    valuation = run('report.inventory-valuation', {'as_of': '2026-12-31', 'limit': 200})
    stock = next(row for row in valuation['rows'] if row['item_id'] == item)
    assert stock['quantity_on_hand'] == '0' and stock['asset_value']['minor_units'] == 0

@pytest.mark.parametrize('width,noun', [(1280, 'check'), (390, 'card-charge')])
def test_free_purchase_generated_entry_saved_header_and_edit(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    world = _books(b, env.site, 'Free purchase '+str(width)); run=world['run']
    income=run('account.create',dict(name='Free income '+str(width),type='income'))['id']
    cogs=run('account.create',dict(name='Free COGS '+str(width),type='cost_of_goods_sold'))['id']
    name='Free stock '+str(width)
    item=run('item.create',dict(name=name,type='inventory_part',income_account_id=income,cogs_account_id=cogs,price='0.00',cost='0.00',description='Free goods',purchase_description='Free goods'))['id']
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/post')
    b.wait_for('document.querySelector("[data-purchase-allocation]") !== null')
    _pick(b,'f:account',world['tag']+(' checking' if noun=='check' else ' card'))
    _fill(b,'f:date','2017-03-03')
    _fill(b,'f:amount','0.00')
    b.evaluate('document.querySelector("[data-line-tab=items]").click()')
    b.evaluate('document.querySelector("[data-collection-path=items] > [data-collection-add]").click()')
    _pick(b,field(b,'items','item'),name)
    _fill(b,field(b,'items','quantity'),'0.5')
    _fill(b,field(b,'items','unit_cost'),'0.00')
    b.wait_for('document.querySelector("[data-purchase-allocation]").textContent.includes("Remaining 0")')
    _act(b,'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    identity=_save(b)
    b.wait_for('document.querySelector("[aria-label=\\"Recorded purchase\\"]") !== null')
    assert 'No payment was made.' in b.evaluate('document.body.innerText')
    assert world['tag']+(' checking' if noun=='check' else ' card') in b.evaluate('document.body.innerText')
    selector='check' if noun=='check' else 'card_charge'
    record=run(noun+'.show',{selector:identity})
    assert record['document']['amount']['minor_units']==0
    assert record['document']['items'][0]['profile']['item']['id']==item
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/{identity}/update')
    b.wait_for('document.querySelector("[data-generated-form]") !== null')
    assert _value(b,'f:amount')=='0.00'
    assert _value(b,field(b,'items','unit_cost'))=='0.00'
    assert _value(b,field(b,'items','quantity'))=='0.5'
    _act(b,'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    dest=Path('notes/zero-screenshots');dest.mkdir(exist_ok=True,parents=True)
    shot=b.call('Page.captureScreenshot', {'captureBeyondViewport':True,'fromSurface':True})
    (dest/f'{noun}-{width}.png').write_bytes(base64.b64decode(shot['data']))
