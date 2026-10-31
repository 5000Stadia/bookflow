"""Drawer count, lost-answer recovery and deposit continuation at phone and desktop widths."""
import base64
import json
from pathlib import Path

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_pos_sale_browser import _signed_in, _pos_on, _open_pos
from tests.test_pos_ticket_browser import click
from tests.test_pos_refund_browser import fill
from tests.test_row8_register_browser import _command
from tests.test_item_pictures_browser import _fits
from tests.test_service_sales_browser import _click

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def shot(b, width, name):
    folder=Path('/tmp/m4-shots/pos10');folder.mkdir(parents=True,exist_ok=True)
    b.evaluate('document.activeElement?.blur()')
    (folder/f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png'})['data']))


@pytest.mark.parametrize('width', [390, 1440])
def test_count_shift_and_continue_to_make_deposits(browser_site, tmp_path, width):
    site=browser_site;b=_signed_in(tmp_path,site,width,900)
    try:
        _pos_on(b,site)
        reg=_command(b,site,'pos-register.show',{'pos_register':'Main'})
        _command(b,site,'pos-register.update',{'pos_register':'Main','expected_version':reg['version'],'count_shifts':True},**{'X-Bookflow-Reason':'Count drawer'})
        _open_pos(b,site)
        b.wait_for('!document.querySelector("[data-pos-shift-open]").hidden')
        click(b,'[data-pos-shift-open]')
        b.wait_for('document.querySelector("[data-pos-shift-dialog]").open')
        fill(b,'[data-pos-shift-amount]','50')
        _fits(b);shot(b,width,'opening-float')
        click(b,'[data-pos-shift-submit]')
        b.wait_for('document.querySelector("[data-pos-day-results]").textContent.includes("50.00")',timeout=30)
        click(b,'[data-pos-day-dismiss]')
        cart={'pos_register':reg['id'],'lines':[{'item':'Pipe Sealant','quantity':'1','unit_price':'10'}]}
        preview=_command(b,site,'pos.sale.preview',cart)
        sale=_command(b,site,'pos.sale.complete',dict(cart,ticket=f'day-browser-{width}',expected_total=preview['total']['amount'],tender={'payment_method':'Cash','amount':'20'}),**{'X-Bookflow-Reason':'Drawer sale'})
        report=_command(b,site,'report.pos-day',{'pos_register':reg['id']})
        expected=int(report['expected_drawer']['minor_units']);delta=-50 if width==390 else 50
        counted=f'{(expected+delta)//100}.{(expected+delta)%100:02}'
        click(b,'[data-pos-shift-close]')
        b.wait_for('document.querySelector("[data-pos-shift-dialog]").open')
        fill(b,'[data-pos-shift-amount]',counted)
        _fits(b);shot(b,width,'closing-count')
        b.evaluate('''window.realFetch=window.fetch;window.fetch=async (url,options)=>{const response=await window.realFetch(url,options);if(String(url).endsWith('/pos.shift.close'))throw new TypeError('lost answer');return response;};''')
        click(b,'[data-pos-shift-submit]')
        b.wait_for('document.querySelector("[data-pos-shift-status]").textContent.includes("No confirmation")',timeout=30)
        _open_pos(b,site)
        b.wait_for('document.querySelector("[data-pos-shift-dialog]").open')
        assert b.evaluate('document.querySelector("[data-pos-shift-amount]").disabled')
        click(b,'[data-pos-shift-submit]')
        b.wait_for('document.querySelector("[data-pos-day-results]").textContent.includes("Over / short")',timeout=30)
        assert ('-0.50' if delta<0 else '0.50') in b.evaluate('document.querySelector("[data-pos-day-results]").textContent')
        _fits(b);shot(b,width,'day-summary')
        click(b,'[data-pos-day-deposit]')
        b.wait_for('!!document.querySelector(".deposit-receipts")',timeout=30)
        b.wait_for('document.querySelectorAll(`[name^="c:document.sources:"][name$=":source"]`).length===1',timeout=30)
        if delta<0:
            assert b.evaluate('document.querySelector(`[name="f:document.cash_back.amount"]`).value')=='0.50'
        else:
            assert b.evaluate('document.querySelector(`[name="c:document.additional:0:amount"]`).value')=='0.50'
            assert json.loads(b.evaluate('document.querySelector(`[name="c:document.additional:0:received_from"]`).value'))['kind']=='customer'
        _fits(b);shot(b,width,'deposit-handoff')
        _click(b,'preview')
        b.wait_for('!!document.querySelector(`[aria-label="Deposit preview"]`)',timeout=30)
        assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
        _click(b,'submit')
        b.wait_for('!!document.querySelector(`[aria-label="Saved deposit"]`)',timeout=30)
        remaining=_command(b,site,'deposit.sources',{'date':report['date']})
        assert sale['sales_receipt']['id'] not in [source['source'] for source in remaining['items']]
    finally:
        b.close()
