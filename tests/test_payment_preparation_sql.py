"""Complete read relation, historical ownership and bound predicate witnesses."""
import sqlite3
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import payment_authority, payment_selection
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_payment_gate_b_oracles import allrows
from tests.test_row8_journal import database_path
from tests.test_work_billing_lifecycle import accepted, bill


def test_complete_preparation_pages_and_retired_revisions(client, sale):
    parent = sale['customer']
    child = client.customer.create(name='SQL child', parent_id=parent, company=COMPANY)['id']
    invoices = [posted(client, child, sale['item'], str(n+10), f'SQL-{n}-Straße%_/\\') for n in range(6)]
    pm = method(client)
    paid = client.run('payment receive', dict(customer=parent, date='2026-06-02', amount='15', payment_method=pm,
        operation_key='sql-partial-and-paid', applications=dict(mode='inline', items=[
            dict(invoice=invoices[0]['id'], expected_version=1, amount='4'),
            dict(invoice=invoices[1]['id'], expected_version=1, amount='11')])), company=COMPANY)
    client.run('invoice void', dict(invoice=invoices[3]['id'], expected_version=1), company=COMPANY, reason='Cancel duplicate')
    changed = client.run('invoice update', dict(invoice=invoices[4]['id'], expected_version=1, date='2026-06-03'),
        company=COMPANY, reason='Correct commercial date')
    # The old revision remains present but must not re-enter the current cohort.
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM transaction_revisions WHERE transaction_id=?', (changed['id'],)).fetchone()[0] == 2
    client.customer.deactivate(customer=child, expected_version=1, company=COMPANY)
    request = dict(mode='new_receipt', customer=parent, date='2026-06-02', limit=1)
    before = allrows(client)
    for q, expected in [(None,3), ('STRASSE%_/\\',3), ('SQL-5-',1), ('Missing text',0)]:
        args = dict(request, **({'q':q} if q is not None else {}))
        seen=[]; cursor=None
        while True:
            page=client.run('payment invoices',dict(args,**({'cursor':cursor} if cursor else {})),company=COMPANY)
            assert page['total_count']==expected
            seen.extend(row['invoice_id'] for row in page['items']);cursor=page['next_cursor']
            if cursor is None:break
        assert len(seen)==len(set(seen))==expected
    for strategy, amount in [('none','100'),('company','100'),('exact_then_oldest','12'),('exact_then_oldest','100')]:
        args=dict(request,amount=amount,strategy=strategy);seen=[];cursor=None
        while True:
            page=client.run('payment suggest',dict(args,**({'cursor':cursor} if cursor else {})),company=COMPANY)
            seen.extend(row['invoice_id'] for row in page['items']);cursor=page['next_cursor']
            if cursor is None:break
        assert len(seen)==len(set(seen))
    assert client.run('payment invoices',dict(request,date='2026-05-31'),company=COMPANY)['total_count']==0
    assert client.run('payment invoices',dict(request,date='2026-06-03'),company=COMPANY)['total_count']==4
    assert client.run('payment invoices',dict(mode='existing_credit',payment=paid['id'],date='2026-06-02'),company=COMPANY)['total_count']==0
    assert allrows(client)==before
    # A relevant change outside the delivered first page must stale its cursor.
    first=client.run('payment invoices',request,company=COMPANY)
    target=next(i for i in (invoices[2],invoices[5]) if i['id']!=first['items'][0]['invoice_id'])
    client.run('invoice update',dict(invoice=target['id'],expected_version=1,memo='Corrected outside page'),company=COMPANY,reason='Correct note')
    before=allrows(client)
    with pytest.raises(BookflowError) as error:
        client.run('payment invoices',dict(request,cursor=first['next_cursor']),company=COMPANY)
    assert error.value.code=='E_QUERY_STALE' and allrows(client)==before


def test_preparation_authority_fragment_stays_correlated_and_bound(client, sale, monkeypatch):
    ordinary=posted(client,sale['customer'],sale['item'],'10','SQL-ORDINARY')
    protected=bill(client,accepted(client,sale))
    source=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1',payment_method=method(client),
        operation_key='sql-protected-source',applications=dict(mode='inline',items=[dict(invoice=protected['id'],expected_version=1,amount='1')])),company=COMPANY)
    client.run('payment unapply',dict(payment=source['id'],expected_version=1,operation_key='sql-historical-unapply',applications=[
        dict(application_id=source['effect']['applications'][0]['application_id'],invoice_expected_version=2)]),company=COMPANY,reason='Remove allocation')
    request=dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02',amount='1000',strategy='exact_then_oldest')
    assert client.run('payment suggest',request,company=COMPANY)['total_count']==2
    original=payment_authority.require_resource
    def deny_work(s, resource, role):
        if resource=='customer-work':raise BookflowError('E_PERMISSION',details={'resource':resource})
        return original(s,resource,role)
    monkeypatch.setattr(payment_authority,'require_resource',deny_work)
    before=allrows(client)
    result=client.run('payment suggest',request,company=COMPANY)
    assert [row['invoice_id'] for row in result['items']]==[ordinary['id']]
    with pytest.raises(BookflowError) as error:
        client.run('payment invoices',{k:v for k,v in request.items() if k not in ('amount','strategy')},company=COMPANY)
    assert error.value.code=='E_PERMISSION'  # Complete posting graph, not filtered page.
    with pytest.raises(BookflowError) as error:
        client.run('payment suggest',dict(mode='existing_credit',payment=source['id'],date='2026-06-02',amount='1'),company=COMPANY)
    assert error.value.code=='E_PERMISSION'
    assert allrows(client)==before
    owning=payment_authority.readable_predicate
    # Exercise postcompiled empty/large sets and parameter names colliding with
    # read inputs without replacing the owning historical-work policy.
    for ids, count in [([],0),([ordinary['id']],1),([ordinary['id'],*[f'absent-{n}' for n in range(200)]],1)]:
        monkeypatch.setattr(payment_authority,'readable_predicate',lambda s,t,ids=ids:sa.and_(owning(s,t),t.in_(sa.bindparam('date',value=ids,expanding=True,unique=True))))
        assert client.run('payment suggest',request,company=COMPANY)['total_count']==count
    assert allrows(client)==before


def test_preparation_ar_currency_and_funding_edges(client, sale, monkeypatch):
    invoice=posted(client,sale['customer'],sale['item'],'10','SQL-FUNDING')
    other=client.account.create(name='SQL other AR',type='accounts_receivable',company=COMPANY)['id']
    request=dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02')
    assert client.run('payment invoices',dict(request,ar_account=other),company=COMPANY)['total_count']==0
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-03',amount='20',payment_method=method(client),operation_key='sql-funding'),company=COMPANY)
    credit=dict(mode='existing_credit',payment=paid['id'],date='2026-06-02')
    assert client.run('payment suggest',dict(credit,amount='20'),company=COMPANY)['total_count']==0
    page=client.run('payment suggest',dict(credit,date='2026-06-03',amount='20',strategy='exact_then_oldest'),company=COMPANY)
    assert page['items'][0]['amount_minor_units']==1000
    client.run('payment update',dict(payment=paid['id'],expected_version=1,amount='5',operation_key='sql-funding-revised'),company=COMPANY,reason='Correct received amount')
    page=client.run('payment suggest',dict(credit,date='2026-06-03',amount='20',strategy='exact_then_oldest'),company=COMPANY)
    assert page['items'][0]['amount_minor_units']==500
    # Currency mismatch is a read-context equivalence witness, not a foreign
    # currency posting claim; production only posts the owning home currency.
    owning=payment_selection.context
    monkeypatch.setattr(payment_selection,'context',lambda s,inp:dict(owning(s,inp),currency='EUR'))
    before=allrows(client)
    assert client.run('payment invoices',request,company=COMPANY)['total_count']==0
    assert client.run('payment suggest',dict(request,amount='10'),company=COMPANY)['total_count']==0
    assert allrows(client)==before
