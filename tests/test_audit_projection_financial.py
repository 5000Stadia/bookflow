"""Real seeded financial history through authenticated company projection."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.host import Host
from bookflow.core.context import Context,client_version
from bookflow.core.config import os_login
from bookflow.core.publication import OSBinding,PublicationPermit
from bookflow.adapters.http.execution import run_history
from bookflow.hub.audit_projection import HistorySelection
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def hosted(world):
    row=world['client'].company.show(company='Demo Plumbing Co');path=Path(row['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        event=db.execute("SELECT id FROM audit_events WHERE command='invoice post' ORDER BY seq LIMIT 1").fetchone()[0]
    host=Host(world['root'],version=client_version());host.start()
    try:yield host,OSBinding.capture(host,os_login()),row['company_id'],path,event
    finally:host.stop()


@pytest.fixture(scope='module')
def receipt(hosted):
    host,cred,cid,path,event=hosted;before=storage(path)
    out=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','financial-projection'),cred)
    assert storage(path)==before and host._readers_attached==0
    return out


def test_actual_invoice_all_entry_identities_and_exact_posting_amounts(hosted,receipt):
    host,cred,cid,path,event=hosted
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT id,record_type,record_id,after FROM audit_entries WHERE event_id=? ORDER BY id',(event,)).fetchall()
    assert {(x['id'],x['identity']['kind'],x['identity']['id']) for x in receipt['events'][0]['entries']}=={(x[0],x[1],x[2]) for x in rows}
    from bookflow.core.audit import decode_snapshot
    expected={identifier:decode_snapshot(blob) for identifier,kind,_,blob in rows if kind=='posting_line'}
    actual={x['id']:x['after'] for x in receipt['events'][0]['entries'] if x['identity']['kind']=='posting_line'}
    assert expected and actual.keys()==expected.keys()
    for identifier,raw in expected.items():
        assert actual[identifier]['debit_minor_units']==raw['debit_minor_units']
        assert actual[identifier]['credit_minor_units']==raw['credit_minor_units']
        assert actual[identifier]['account_id']==raw['account_id']
    assert sum(x['debit_minor_units'] for x in actual.values())==sum(x['credit_minor_units'] for x in actual.values())>0


def test_financial_history_fresh_publication(hosted,receipt):
    host,cred,_,path,_=hosted;before=storage(path)
    PublicationPermit.from_retained(receipt.permit.retained()).check(host,cred)
    assert storage(path)==before and host._readers_attached==0
