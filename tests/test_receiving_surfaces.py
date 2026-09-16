"""Source-bound Python, CLI, HTTP and MCP receipt/bill round trips."""
from pathlib import Path
import sqlite3
import anyio
import pytest
from tests.test_bill_item_lines import books, _inventory_part
from tests.payment_raw_evidence import database


# The commands this test is the designated four-surface witness for; the coverage ledger imports
# this set rather than restating the names beside a path it cannot check.
COMMANDS = frozenset(('item-receipt post', 'item-receipt show', 'item-receipt query',
                      'item-receipt update', 'item-receipt history', 'item-receipt void'))


@pytest.mark.timeout(300)
def test_receiving_and_linked_bill_cross_all_four_actual_transports(books,tmp_path):
    pytest.importorskip('mcp')
    from tests.mcp_matrix_support import Matrix
    item=_inventory_part(books)
    vendor=books['vendor']

    async def witness():
        matrix=Matrix()
        try:
            await matrix.open(tmp_path/'items',tmp_path/'surfaces')
            for surface in matrix.documents:
                async def call(name,raw,**ctx):
                    return await matrix.call(surface,name,raw,**ctx)
                dbpath=next(matrix.roots[surface].rglob('company.db'))
                po=await call('purchase-order post',dict(date='2017-01-01',vendor=vendor,
                    lines=[dict(item=item,quantity='10',rate='10.00')]))
                raw=dict(date='2017-01-05',vendor=vendor,shipping='12.00',purchase_order=po['id'],purchase_order_version=po['version'],
                    items=[dict(item=item,order_line_id=po['revision']['lines'][0]['line_id'],quantity='6',unit_cost='10.00')])
                before=database(dbpath)
                preview=await call('item-receipt post',raw,dry_run=True)
                assert preview['dry_run'] and preview['total']['minor_units']==7200, surface
                assert database(dbpath)==before,surface
                received=await call('item-receipt post',raw,idempotency_key='transport-receipt')
                saved=database(dbpath)
                replay=await call('item-receipt post',raw,idempotency_key='transport-receipt')
                assert replay['id']==received['id'] and replay['idempotent_replay'],surface
                assert database(dbpath)==saved,surface
                shown=await call('item-receipt show',dict(receipt=received['id']))
                assert shown['items'][0]['quantity_microunits']==6000000
                assert (await call('item-receipt query',dict(vendor=vendor)))['count']==1
                edited=await call('item-receipt update',dict(receipt=received['id'],expected_version=1,memo='Dock note'))
                assert edited['items']==received['items']
                assert len((await call('item-receipt history',dict(receipt=received['id'])))['items'])==2
                selection=dict(receipt_line=received['items'][0]['id'],expected_receipt_version=edited['version'],quantity='4',unit_cost='11.00')
                before=database(dbpath)
                refused=await call('bill post',dict(date='2017-01-15',receipts=[dict(selection,quantity='7')]),rejected=True)
                assert refused['code']=='E_WORK_DEPENDENCY',surface
                assert database(dbpath)==before,surface
                billraw=dict(date='2017-01-15',receipts=[selection])
                assert (await call('bill post',billraw,dry_run=True))['total_minor_units']==5200
                assert database(dbpath)==before,surface
                bill=await call('bill post',billraw,idempotency_key='transport-linked-bill')
                assert bill['total_minor_units']==5200
                assert bill['revision']['items'][0]['item_id']==item
                assert bill['revision']['items'][0]['quantity_microunits']==4000000
                saved=database(dbpath)
                assert (await call('bill post',billraw,idempotency_key='transport-linked-bill'))['id']==bill['id']
                assert database(dbpath)==saved,surface
                with sqlite3.connect(dbpath) as db:
                    assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone()==(6000000,7600)
                    assert db.execute("SELECT count(*) FROM inventory_movements WHERE kind='receipt'").fetchone()==(1,)
                    assert db.execute('SELECT sum(end_microunits-start_microunits),sum(original_minor_units),sum(billed_minor_units),sum(shipping_minor_units) FROM receipt_bill_claims').fetchone()==(4000000,4800,5200,800)
                await call('bill void',dict(bill=bill['id']))
                current=await call('item-receipt show',dict(receipt=received['id']))
                assert current['items'][0]['unbilled_quantity_microunits']==6000000
                voided=await call('item-receipt void',dict(receipt=received['id'],expected_version=current['version']))
                assert voided['status']=='voided'
                with sqlite3.connect(dbpath) as db:
                    assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone()==(0,0)
                remaining=await call('purchase-order show',dict(purchase_order=po['id']))
                assert remaining['receiving'][0]['remaining_quantity_microunits']==10000000
        finally:
            await matrix.close()
    anyio.run(witness)
