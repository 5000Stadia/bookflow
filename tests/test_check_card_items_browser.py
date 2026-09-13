"""First-six entry affordances through the registered workbench commands."""
import base64
import json
from pathlib import Path
import pytest
from tests.test_money_out_browser import register_browser, browser_site, _pick, _act, _save, _books
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
