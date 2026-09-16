"""Actual CLI and source-bound MCP deletion, including retained historical reads."""
import asyncio
from pathlib import Path
import pytest
from tests.test_bill_item_lines import books, _inventory_part
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


# The commands this test is the designated transport witness for; the coverage ledger imports
# this set rather than restating the names beside a path it cannot check.
COMMANDS = frozenset(('invoice delete', 'sales-receipt delete'))


@pytest.mark.timeout(300)
def test_cli_and_source_bound_mcp_delete_preview_refusal_replay_and_history(books,tmp_path):
    pytest.importorskip('mcp')
    run=books['run'];item=_inventory_part(books)
    run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='4',unit_cost='8')]),reason='Receive transport stock')
    posts={}
    for noun in ('invoice','sales-receipt'):
        raw=dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='12')])
        if noun=='sales-receipt':raw.update(deposit_to=books['bank'],payment_method=books['methods']['Cash'])
        posts[noun]=run(noun+' post',raw,reason='Sell transport stock')
    async def witness():
        matrix=Matrix()
        try:
            await matrix.open(Path(books['client'].data_root),tmp_path/'surfaces')
            for surface in ('cli','http','mcp'):
                async def call(name,raw,**ctx):return await matrix.call(surface,name,raw,**ctx)
                state=await call('permission show',{})
                await call('permission activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
                rows=(await call('membership list',{'company':books['company']}))['items']
                member=next(x for x in rows if x['scope_type']=='company' and x['scope_id']==books['company'])
                await call('membership grant',dict(user=member['user_id'],company=books['company'],expected_version=member['version'],
                    grants=['transaction.invoice.delete','transaction.sales_receipt.delete'],denies=['ledger.post']))
                path=Path((await call('company show',{}))['path'])/'company.db'
                for noun,post in posts.items():
                    selector=noun.replace('-','_');raw={selector:post['id'],'expected_version':1,'operation_key':surface+'-'+noun}
                    before=database(path)
                    preview=await call(noun+' delete',raw,dry_run=True)
                    assert preview['dry_run'] and preview['cancelled_stock_movements']==1
                    refused=await call(noun+' delete',{**raw,'expected_version':999},rejected=True)
                    assert refused['code']=='E_VERSION_CONFLICT'
                    denied=await call(noun+' update',{selector:post['id'],'expected_version':1,'memo':'Denied'},rejected=True)
                    assert denied['code']=='E_PERMISSION'
                    assert database(path)==before
                    deleted=await call(noun+' delete',raw)
                    assert deleted['status']=='deleted' and deleted['version']==2
                    after=database(path)
                    assert after!=before
                    replay=await call(noun+' delete',raw)
                    assert replay['idempotent_replay'] and not replay['changed']
                    stale=await call(noun+' delete',{**raw,'operation_key':'losing-'+noun},rejected=True)
                    assert stale['code']=='E_VERSION_CONFLICT'
                    assert (await call(noun+' show',{selector:post['id']},rejected=True))['code']=='E_RECORD_NOT_FOUND'
                    shown=await call(noun+' show',{selector:post['id'],'include_deleted':True})
                    assert shown['status']=='deleted'
                    assert shown['revision']==dict(post['revision'],batches=shown['revision']['batches'])
                    assert shown['revision']['batches'][:-1]==post['revision']['batches']
                    inverse=shown['revision']['batches'][-1]
                    assert inverse['kind']=='reversal' and inverse['reverses_batch_id']==post['revision']['batches'][0]['id']
                    assert inverse['debit_minor_units']==inverse['credit_minor_units']==2000
                    history=await call(noun+' history',{selector:post['id'],'include_deleted':True})
                    assert history['status']=='deleted' and history['items'][0]['id']==post['revision']['id']
                    assert post['id'] not in {x['id'] for x in (await call(noun+' query',{}))['items']}
                    assert database(path)==after
        finally:await matrix.close()
    asyncio.run(witness())
