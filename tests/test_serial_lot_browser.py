"""R104 increment 4: serial numbers in real headless Chrome, at 1440 and at 390 pixels.

A person opens the demo water heater's page, sees its serial numbers by site, follows Assign numbers,
adds the serial number in the generated form and submits it through htmx, as the
server requires of a cookie session. The unit's number comes off and the page then shows it unassigned; the
invoice form shows the Serial numbers and Lot columns, because the company tracks numbers. At a phone's width
the page fits without sideways scrolling.
"""
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


@pytest.mark.parametrize('width', (1440, 390))
def test_a_serial_number_taken_off_from_the_item_page(register_browser, width):
    env, b = register_browser, register_browser.browser
    company = env.site.company_id
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    heater = run('item.show', {'item': 'Water Heater'})
    page = f'{env.site.base_url}/c/{company}/item/{heater["id"]}'
    b.navigate(page)
    b.wait_for('!!document.querySelector("#item-numbers")')
    assert b.evaluate('!!document.querySelector("#item-numbers")')
    assign = f'/c/{company}/inventory/assign?item={heater["id"]}'
    assert assign in b.evaluate('[...document.querySelectorAll("#item-numbers a")].map(a => a.getAttribute("href"))')
    if width == 390:
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')

    b.navigate(env.site.base_url + assign)
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate('!!document.querySelector("[data-generated-form][hx-post], [data-generated-form] [hx-post]")'
                      ' || !!document.querySelector("form[hx-post]")')
    # Serial numbers are a list: Add entry gives one box per number.
    b.evaluate('document.querySelector("[data-collection-path=serial_numbers] [data-collection-add]").click()')
    b.wait_for('!!document.querySelector("[data-collection-path=serial_numbers] [data-collection-items] input:not([type=hidden])")')
    b.evaluate('''(() => {const e=document.querySelector("[data-collection-path=serial_numbers] [data-collection-items] input:not([type=hidden])");
        e.value="WH-1002"; e.dispatchEvent(new Event("input",{bubbles:true}));})()''')
    for name, value in (('site', 'Warehouse'), ('date', DEMO_AS_OF),
                        ('direction', 'unassign')):
        b.evaluate(f'''(() => {{const e=document.getElementsByName("f:{name}")[0]; e.value="{value}";
            e.dispatchEvent(new Event("input",{{bubbles:true}})); e.dispatchEvent(new Event("change",{{bubbles:true}}));}})()''')
    b.evaluate('void (window.assignForm = document.querySelector("[data-generated-form]"))')
    b.evaluate('document.querySelector("button[value=submit]").click()')
    b.wait_for('!window.assignForm || !window.assignForm.isConnected', timeout=30)
    b.wait_for('document.readyState === "complete"')
    shown = run('serial-number.show', {'number': 'WH-1002', 'as_of': DEMO_AS_OF})
    assert shown['on_hand'] == '0', _text(b)[-1500:]
    stock = run('report.serial-lot-stock', {'item': 'Water Heater', 'as_of': DEMO_AS_OF})
    assert [(r['number'], r['site_name'], r['quantity']) for r in stock['rows'] if r['number'] is None] == [
        (None, 'Warehouse', '1')]

    assert run('item.list', {'filter': ['tracking=serial']})['items']
    b.navigate(f'{env.site.base_url}/c/{company}/invoice/post')
    b.wait_for('document.readyState === "complete"')
    # The grid's Serial numbers and Lot columns (at a phone's width the row stacks, the heads still in the page).
    heads = b.evaluate('document.body.innerHTML')
    assert 'Serial numbers' in heads and '>Lot<' in heads
