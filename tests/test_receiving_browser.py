"""Received goods and linked bills through the generated desktop/phone GUI."""
import base64
from pathlib import Path
import pytest
from tests.test_money_out_browser import register_browser, browser_site, _books, _pick, _act, _click
from tests.test_service_sales_browser import _fill
from tests.test_check_card_items_browser import field


@pytest.mark.parametrize('width', [1280,390])
def test_receive_from_po_then_bill_received_goods(register_browser, width):
    env,b=register_browser,register_browser.browser
    b.viewport(width,900)
    world=_books(b,env.site,'Receiving '+str(width));run=world['run']
    income=run('account.create',dict(name='Receiving sales '+str(width),type='income'))['id']
    cogs=run('account.create',dict(name='Receiving cost '+str(width),type='cost_of_goods_sold'))['id']
    name='Received copper '+str(width)
    item=run('item.create',dict(name=name,type='inventory_part',income_account_id=income,cogs_account_id=cogs,
        description='Copper',purchase_description='Copper received',price='15.00',cost='10.00'))['id']
    po=run('purchase-order.post',dict(vendor=world['vendor'],date='2026-03-01',lines=[dict(item=item,quantity='10',rate='10.00')]))
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/item-receipt/post')
    b.wait_for('!!document.querySelector("[data-order-chooser]")')
    b.evaluate('document.querySelector("[data-order-chooser]").open=true')
    b.evaluate('document.querySelector("[data-order-chooser] a").click()')
    b.wait_for('!!document.querySelector("[data-collection-path=items] [data-collection-item]")')
    _fill(b,'f:date','2026-03-05')
    _fill(b,field(b,'items','quantity'),'6')
    _act(b,'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    _click(b,'submit')
    b.wait_for('location.pathname.includes("/item-receipt/") && !location.pathname.endsWith("/post")')
    receipt_id=b.evaluate('location.pathname').rsplit('/',1)[-1]
    receipt=run('item-receipt.show',dict(receipt=receipt_id))
    assert receipt['total']['minor_units']==6000
    assert receipt['items'][0]['quantity_microunits']==6000000
    b.wait_for('!!document.querySelector("[aria-label=\\"Received goods\\"]")')
    assert b.evaluate('getComputedStyle(document.querySelector(".receipt-lines td:nth-child(3)")).whiteSpace') == 'nowrap'
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/item-receipt/{receipt_id}/update')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _fill(b,'f:memo','Dock confirmation')
    _act(b,'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    _click(b,'submit')
    b.wait_for('location.pathname.endsWith(' + repr('/item-receipt/'+receipt_id) + ')')
    edited=run('item-receipt.show',dict(receipt=receipt_id))
    assert edited['items']==receipt['items']
    assert edited['financial_revision_id']==receipt['financial_revision_id']
    folder=Path('notes/receiving-screenshots');folder.mkdir(parents=True,exist_ok=True)
    (folder/f'receipt-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'captureBeyondViewport':True,'fromSurface':True})['data']))
    b.evaluate('document.querySelector("a[href*=\\"bill/post?receipt=\\"]").click()')
    b.wait_for('!!document.querySelector("[data-collection-path=receipts] [data-collection-item]")')
    assert b.evaluate('document.querySelector("[data-collection-path=receipts] [data-collection-item]").offsetParent !== null')
    b.evaluate('document.querySelector("[data-receipt-date]").click()')
    assert b.evaluate('document.getElementsByName("f:date")[0].value') == '2026-03-05'
    _fill(b,'f:date','2026-03-15')
    _fill(b,field(b,'receipts','quantity'),'4')
    _fill(b,field(b,'receipts','unit_cost'),'11.00')
    _act(b,'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    b.evaluate('document.querySelector("[data-collection-path=receipts]").scrollIntoView()')
    (folder/f'linked-bill-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'captureBeyondViewport':True,'fromSurface':True})['data']))
    _click(b,'submit')
    b.wait_for('location.pathname.includes("/bill/") && !location.pathname.endsWith("/post")')
    bill=run('bill.show',dict(bill=b.evaluate('location.pathname').rsplit('/',1)[-1]))
    assert bill['total_minor_units']==4400
    assert len(bill['revision']['receipts'])==1
    assert run('item-receipt.show',dict(receipt=receipt_id))['items'][0]['unbilled_quantity_microunits']==2000000
    assert run('purchase-order.show',dict(purchase_order=po['id']))['receiving'][0]['remaining_quantity_microunits']==4000000
