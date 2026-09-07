"""Actual shared payment draft history preserves captured origins and amounts."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.core.host import Host
from bookflow.core.config import os_login
from bookflow.core.context import Context,client_version
from bookflow.core.publication import OSBinding,PublicationPermit
from bookflow.adapters.http.execution import run_history
from bookflow.hub.audit_projection import HistorySelection
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def selected(world):
    client=world['client'];company='Demo Plumbing Co'
    customer=client.customer.create(name='Selection historical owner',company=company)
    draft=client.run('payment selection create',dict(mode='new_receipt',customer=customer['id'],
        date='2026-06-02',amount='1.50',label='Captured selection'),company=company)
    from tests.test_service_sales_lifecycle import sale
    sales=sale.__wrapped__(client)
    invoice=client.run('invoice post',dict(customer=customer['id'],date='2026-06-01',
        lines=[dict(item=sales['item'],quantity='1',unit_price='1')]),company=company)
    for version in (1,3):
        client.run('payment selection update',dict(selection=draft['id'],expected_version=version,
            set_items=[dict(invoice=invoice['id'],expected_version=1,amount='1')]),company=company)
        if version==1:
            client.run('payment selection update',dict(selection=draft['id'],expected_version=2,
                remove_invoices=[invoice['id']]),company=company)
    client.run('payment selection clear',dict(selection=draft['id'],expected_version=4),company=company)
    info=client.company.show(company=company);path=Path(info['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        events=db.execute("SELECT e.event_id FROM audit_entries e JOIN audit_events a ON a.id=e.event_id WHERE e.record_type='payment_selection' AND e.record_id=? ORDER BY a.seq",(draft['id'],)).fetchall()
        histories=[(event,db.execute('SELECT id,record_type,record_id,after FROM audit_entries WHERE event_id=?',(event,)).fetchall()) for (event,) in events]
    host=Host(world['root'],version=client_version());host.start()
    try:yield host,OSBinding.capture(host,os_login()),info['company_id'],path,histories,draft
    finally:host.stop()


@pytest.mark.parametrize('version',[1,2,3,4,5])
def test_actual_selection_history_preserves_header_and_context(selected,version):
    host,cred,cid,path,histories,draft=selected;before=storage(path)
    assert len(histories)==5
    event,rows=histories[version-1]
    item_kinds=[]
    result=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','selection-history'),cred)
    entries=result['events'][0]['entries']
    assert result['events'][0]['command'] in ('payment selection create','payment selection update','payment selection clear')
    assert {(x['id'],x['identity']['kind'],x['identity']['id']) for x in entries}=={(x[0],x[1],x[2]) for x in rows}
    actual={x['id']:x['after'] for x in entries}
    for identifier,kind,record,blob in rows:
        raw=decode_snapshot(blob)
        if kind=='payment_selection_revision':
            import json
            raw['context_snapshot']=json.loads(raw['context_snapshot'])
            raw.pop('manifest_hash')
        assert {k:v for k,v in actual[identifier].items() if k!='tag'}==raw
    revision=next(x['after'] for x in entries if x['identity']['kind']=='payment_selection_revision')
    assert revision['version']==version and revision['amount_minor_units']==150
    assert revision['amount_origin']=='entered' and revision['selection_id']==draft['id']
    assert revision['context_snapshot']['label']=='Captured selection'
    assert revision['context_snapshot']['date']=='2026-06-02'
    assert revision['item_count']==(1 if version in (2,4) else 0)
    item_kinds.extend(x['after']['kind'] for x in entries if x['identity']['kind']=='payment_selection_item')
    PublicationPermit.from_retained(result.permit.retained()).check(host,cred)
    assert item_kinds=={1:[],2:['set'],3:['remove'],4:['set'],5:['clear']}[version]
    assert storage(path)==before and host._readers_attached==0
