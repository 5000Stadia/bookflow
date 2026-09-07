"""F1 complete consumed-operation ownership before counts and after read windows."""
import json
import sqlite3
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import payment_authority, payment_recovery, schema as c
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_work_billing_lifecycle import accepted,bill
from tests.test_payment_receipts import method
from tests.test_payment_recovery import setup,declaration,seal_compare,confirm,call,raw_books
from tests.test_row3_host import hosted as host_fixture

@pytest.fixture
def consumed_graph(client,sale):
    draft,first,second=setup(client,sale)
    entries=[dict(invoice_id=second['id'],observed_invoice_version=1,action='remove')]
    begin=declaration(draft,entries);identifier,comparison=seal_compare(client,begin,entries);confirm(client,identifier,begin,comparison)
    saved=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    paid=client.run('payment receive',dict(customer=sale['customer'],date=saved['context']['date'],amount='2.00',payment_method=method(client),operation_key='recovery-owned-later-credit',applications=dict(mode='selection',selection=draft['id'],expected_version=saved['version'])),company=COMPANY)
    work=bill(client,accepted(client,sale))
    control=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02'),company=COMPANY)
    control_recovery=call(client,'begin',declaration(control,[]))['original_receipt']['recovery_id']
    return dict(selection=draft['id'],recovery=identifier,payment=paid['id'],work=work['id'],control=control['id'],control_recovery=control_recovery)


def later_work(client,graph):
    result=client.run('payment apply',dict(payment=graph['payment'],expected_version=1,date='2026-06-02',operation_key='later-work-apply',applications=dict(mode='inline',items=[dict(invoice=graph['work'],expected_version=1,amount='0.50')])),company=COMPANY)
    client.run('payment unapply',dict(payment=graph['payment'],expected_version=2,operation_key='later-work-unapply',applications=[dict(application_id=result['effect']['applications'][0]['application_id'],invoice_expected_version=2)]),company=COMPANY,reason='Retain historical authority after correction')


def deny_work(monkeypatch):
    original=payment_authority.require_resource
    def deny(s,resource,role):
        if resource=='customer-work':raise BookflowError('E_PERMISSION')
        return original(s,resource,role)
    monkeypatch.setattr(payment_authority,'require_resource',deny)
    return original,deny


def test_later_applied_then_unapplied_work_is_hidden_before_every_count(client,sale,root,consumed_graph,monkeypatch):
    graph=consumed_graph;later_work(client,graph)
    totals={name:client.run(name,{},company=COMPANY)['total_count'] for name in ('payment selection query','payment recovery query')}
    deny_work(monkeypatch);before=raw_books(root)
    for name,inp in [('payment selection show',dict(selection=graph['selection'])),('payment recovery show',dict(recovery_id=graph['recovery']))]:
        with pytest.raises(BookflowError) as caught:client.run(name,inp,company=COMPANY)
        assert caught.value.code=='E_PERMISSION'
    for name in totals:
        seen=[];cursor=None
        while True:
            page=client.run(name,dict(limit=1,**({'cursor':cursor} if cursor else {})),company=COMPANY)
            assert page['total_count']==totals[name]-1
            seen.extend(row['id'] for row in page['items']);cursor=page['next_cursor']
            if not cursor:break
        protected=graph['selection'] if name=='payment selection query' else graph['recovery']
        control=graph['control'] if name=='payment selection query' else graph['control_recovery']
        assert protected not in seen and control in seen and len(seen)==totals[name]-1
    assert call(client,'query',dict(selection=graph['selection']))['total_count']==0
    assert raw_books(root)==before


@pytest.fixture
def hosted(root,consumed_graph):yield from host_fixture.__wrapped__(root)


@pytest.mark.parametrize('command',['payment selection query','payment recovery query'])
def test_off_page_later_work_change_denies_publication_window(client,hosted,consumed_graph,command,monkeypatch):
    from bookflow.adapters.mcp.runtime import Runtime
    from bookflow.core import registry
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import PublicationPermit
    from tests.test_mcp_runtime import credential
    from types import SimpleNamespace
    host_client=SimpleNamespace(run=lambda name,inp,**kw:hosted.ok(name.replace(' ','.'),inp,company=hosted.company_id,headers={'X-Bookflow-Reason':kw['reason']} if kw.get('reason') else {}))
    graph=consumed_graph;cred=credential(hosted,monkeypatch)
    original,deny=deny_work(monkeypatch)
    first=host_client.run(command,dict(limit=1),company=COMPANY)
    assert first['items'][0]['id']==graph['control' if command=='payment selection query' else 'control_recovery']
    runtime=Runtime.for_host(hosted.handle.host);cmd=registry.get(command)
    intent=runtime.admit(cmd,Context.new(Interface.mcp,'recovery-offpage-count'),cred,hosted.company_id,'option',False)
    runtime.prepare_json(intent,dict(limit=1),cred)
    check=PublicationPermit.check;changed=[]
    def window(permit,host,credential,**kwargs):
        if permit.cmd.name==command and permit.execution_succeeded and 'payment_roots' in permit.projection and not changed:
            changed.append(True)
            assert any(root[0]=='payment_selection_query_epoch' for root in permit.projection['payment_roots'])
            monkeypatch.setattr(payment_authority,'require_resource',original)
            try:later_work(host_client,graph)
            finally:monkeypatch.setattr(payment_authority,'require_resource',deny)
        return check(permit,host,credential,**kwargs)
    monkeypatch.setattr(PublicationPermit,'check',window)
    with pytest.raises(BookflowError) as caught:runtime.execute_json(intent,cred)
    assert caught.value.code=='E_PERMISSION' and changed
    current=host_client.run(command,dict(limit=1),company=COMPANY)
    assert current['total_count']==first['total_count']-1


def test_unresolved_consumed_evidence_is_not_visible_in_synthetic_sql_relation(monkeypatch):
    # A pure relational fixture models missing/malformed evidence without editing
    # or dropping any guard in a Bookflow company database.
    engine=sa.create_engine('sqlite://')
    monkeypatch.setattr(payment_authority,'require_resource',lambda *args:None)
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE payment_selections(id TEXT,consumed_operation_id TEXT)')
        db.exec_driver_sql('CREATE TABLE payment_operations(id TEXT,request_snapshot TEXT)')
        db.exec_driver_sql('CREATE TABLE transactions(id TEXT)')
        db.exec_driver_sql("INSERT INTO transactions VALUES ('known')")
        for index,snapshot in enumerate((None,'{}','{"resolved_transaction_ids":null}','{"resolved_transaction_ids":[123]}','{"resolved_transaction_ids":["absent"]}','{"resolved_transaction_ids":["known"]}')):
            db.exec_driver_sql('INSERT INTO payment_selections VALUES (?,?)',(str(index),str(index)))
            if snapshot is not None:db.exec_driver_sql('INSERT INTO payment_operations VALUES (?,?)',(str(index),snapshot))
        rows=db.execute(sa.select(c.payment_selections.c.id).where(payment_recovery.readable_selection(None,c.payment_selections.c.id))).scalars().all()
        assert rows==['5']
