"""R154: a person converts a non-inventory part to an inventory part from the item's own page.

The page offers Convert only on a convertible item; the form opens with the item pinned, says plainly
that the change cannot be undone, previews the accounts it will carry, and saving it leaves an
inventory part with nothing on hand and no Convert action.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _links(b):
    return b.evaluate('[...document.querySelectorAll("a")].map(a => a.getAttribute("href") || "")')


def _text(b):
    return b.evaluate('document.body.innerText')


def test_convert_from_the_item_page(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    income = run('account.create', {'name': 'Fitting sales', 'type': 'income'})['id']
    cogs = run('account.create', {'name': 'Fitting cost', 'type': 'cost_of_goods_sold'})['id']
    item = run('item.create', {'name': 'Brass tee', 'type': 'non_inventory_part', 'description': 'Brass tee',
                               'price': '7.00', 'income_account_id': income, 'purchase_enabled': True,
                               'purchase_description': 'Brass tee', 'cost': '3.00', 'expense_account_id': cogs})['id']
    page = f'{env.site.base_url}/c/{env.site.company_id}/item/{item}'
    b.navigate(page)
    b.wait_for('document.readyState === "complete"')
    assert f'/c/{env.site.company_id}/item/{item}/convert' in _links(b)

    b.navigate(page + '/convert')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert 'This cannot be undone.' in _text(b)
    assert b.evaluate('document.getElementsByName("f:to")[0].value') == 'inventory'
    assert not b.evaluate('!!document.querySelector("[name^=\\"cf:\\"]")')  # it takes no custom fields

    def act(action):
        b.evaluate('''(() => {const e=document.querySelector("input[name$=reason]");
            e.value="We stock tees now"; e.dispatchEvent(new Event("input",{bubbles:true}));})()''')
        b.evaluate('void (window.convertForm = document.querySelector("[data-generated-form]"))')
        b.evaluate(f'document.querySelector("button[value={action}]").click()')
        b.wait_for('!window.convertForm || !window.convertForm.isConnected', timeout=20)
        b.wait_for('document.readyState === "complete"')

    act('preview')
    text = _text(b)
    assert 'Preview' in text and 'Fitting cost' in text and 'Inventory Asset' in text, text
    assert run('item.show', {'item': item})['type'] == 'non_inventory_part'

    act('submit')
    b.wait_for('document.body.innerText.includes("inventory_part")', timeout=20)
    assert f'/c/{env.site.company_id}/item/{item}/convert' not in _links(b)
    shown = run('item.show', {'item': item})
    assert shown['type'] == 'inventory_part' and shown['cogs_account_id'] == cogs and shown['quantity_on_hand'] == '0'
    assert json.dumps(shown['id']) == json.dumps(item)
