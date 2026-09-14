"""Deletion refuses live claims even when an older public Void left the source voided."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books
from tests.test_sales_deletion import sale, service_item
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


@pytest.mark.parametrize('voided',[False,True])
def test_live_return_claim_refuses_then_supported_release_allows_delete(books,voided):
    _,post=sale(books);run=books['run']
    credit=run('credit-memo post',dict(customer=books['customer'],date='2017-01-03',
        lines=[dict(source_invoice=post['id'],source_line=post['revision']['lines'][0]['line_id'],quantity='1')]),reason='Return sold unit')
    if voided:post=run('invoice void',dict(invoice=post['id'],expected_version=1),reason='Existing void behavior')
    path=location(books);enable(books,'invoice',deny_post=False)
    raw=dict(invoice=post['id'],expected_version=post['version'],operation_key='return-dependent-delete')
    before=database(path)
    for preview in (True,False):
        with pytest.raises(BookflowError) as refused:run('invoice delete',raw,reason='Delete claimed source',dry_run=preview)
        assert refused.value.code=='E_SOURCE_CORRECTION_CONFLICT'
        assert refused.value.details['credit_memo_ids']==[credit['id']]
        assert 'credit-memo void' in refused.value.details['next']
        assert database(path)==before
    run('credit-memo void',dict(credit_memo=credit['id'],expected_version=credit['version']),reason='Release returned claim explicitly')
    before=database(path)
    result=run('invoice delete',raw,reason='Delete claimed source')
    assert result['status']=='deleted'
    assert database(path)['tables']['credit_source_claims']==before['tables']['credit_source_claims']


def test_stale_and_family_refuse_and_deleted_sale_cannot_be_edited(books):
    _,post=sale(books);run=books['run'];path=location(books)
    enable(books,'invoice',deny_post=False,grants=['transaction.invoice.delete','transaction.sales_receipt.delete'])
    before=database(path)
    with pytest.raises(BookflowError) as stale:run('invoice delete',dict(invoice=post['id'],expected_version=99),reason='Stale')
    assert stale.value.code=='E_VERSION_CONFLICT' and database(path)==before
    with pytest.raises(BookflowError) as family:run('sales-receipt delete',dict(sales_receipt=post['id'],expected_version=1),reason='Wrong family')
    assert family.value.code=='E_RECORD_NOT_FOUND' and database(path)==before
    result=run('invoice delete',dict(invoice=post['id'],expected_version=1),reason='Delete source')
    before=database(path)
    for command in ('invoice update','invoice void'):
        with pytest.raises(BookflowError) as deleted:run(command,dict(invoice=post['id'],expected_version=result['version']),reason='No resurrection')
        assert deleted.value.code=='E_VALIDATION' and database(path)==before


@pytest.mark.parametrize('blocker',['closed','payment','deposit'])
def test_owning_dependencies_refuse_preview_and_write(books,blocker):
    noun='sales-receipt' if blocker=='deposit' else 'invoice';run=books['run']
    if blocker=='deposit':
        with sqlite3.connect(location(books)) as db:
            uf=db.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]
        item=service_item(books,'Service for deposit')
        post=run('sales-receipt post',dict(customer=books['customer'],date='2017-01-02',deposit_to=uf,payment_method=books['methods']['Cash'],lines=[dict(item=item)]),reason='Undeposited receipt')
        run('deposit post',dict(operation_key='claim-sale',document=dict(mode='inline',date='2017-01-03',deposit_to=books['bank'],
            sources=[dict(source_type='sales_receipt',source=post['id'],expected_version=1)],additional=[])),reason='Claim receipt')
        post=run('sales-receipt show',dict(sales_receipt=post['id']))
    else:
        if blocker=='payment':
            item=service_item(books,'Service payment target')
            post=run('invoice post',dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=item)]),reason='Service invoice')
        else:
            _,post=sale(books)
        if blocker=='closed':run('company update',dict(closing_date='2017-01-02'),reason='Close sale date')
        else:
            run('payment receive',dict(customer=books['customer'],date='2017-01-03',amount='12',payment_method=books['methods']['Cash'],
                applications=dict(mode='inline',items=[dict(invoice=post['id'],amount='12',expected_version=1)]),operation_key='pay-sale'),reason='Pay invoice')
            post=run('invoice show',dict(invoice=post['id']))
    path=location(books);enable(books,noun);before=database(path)
    for preview in (True,False):
        with pytest.raises(BookflowError) as refused:
            run(noun+' delete',{noun.replace('-','_'):post['id'],'expected_version':post['version']},reason='Dependency refusal',dry_run=preview)
        assert refused.value.code=={'closed':'E_PERIOD_CLOSED','payment':'E_HAS_APPLICATIONS','deposit':'E_DEPOSIT_DEPENDENCY'}[blocker]
        assert database(path)==before
