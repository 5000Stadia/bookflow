"""Filtered scans exclude noncandidate events, never corrupt candidates."""
from pathlib import Path

import pytest

from bookflow.core import registry
from bookflow.core.config import os_login
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.execution import run_hosted
from bookflow.storage.engine import open_database
from tests.test_audit_projection_activity import world


def test_noncandidate_corruption_is_excluded_but_remains_detectable(world):
    client=world['client']
    company='Demo Plumbing Co'
    older=client.customer.create(name='Unrelated corruption witness',company=company)
    target=client.customer.create(name='Filtered valid target',company=company)
    info=client.company.show(company=company)
    # The newest endpoint is valid. Corruption is only in the older, unrelated
    # record, so unfiltered endpoint validation alone cannot explain the error.
    with open_database(Path(info['path'])/'company.db',writable=True) as db:
        changed=db.raw.execute('UPDATE audit_entries SET after=? WHERE record_type=? AND record_id=?',
                               (b'broken capture','customer',older['id']))
        assert changed.rowcount==1
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        result=run_hosted(host,registry.get('audit list'),
            {'record_type':'customer','record_id':target['id'],'limit':20},
            Context.new('http','Filtered corruption witness'),cred,info['company_id'],'option',False)
        assert result['count']==1
        result.check()
        for filters in ({'record_type':'customer','record_id':older['id']},{}):
            with pytest.raises(BookflowError) as caught:
                run_hosted(host,registry.get('audit list'),{**filters,'limit':20},
                           Context.new('http','Corruption detection witness'),cred,info['company_id'],'option',False)
            assert caught.value.code=='E_VALIDATION'
            assert caught.value.details=={'reason':'audit_format'}
        assert host._readers_attached==0
    finally:
        host.stop()
    # Move the corrupt capture into the matching event in this disposable DB.
    # The candidate unit is an event, not just its matching entry. Preserve a
    # newer valid endpoint so this specifically tests the selected scan.
    with open_database(Path(info['path'])/'company.db',writable=True) as db:
        event=db.raw.execute('SELECT event_id FROM audit_entries WHERE record_id=?',(target['id'],)).fetchone()[0]
        db.raw.execute('UPDATE audit_entries SET event_id=? WHERE record_id=?',(event,older['id']))
    client.customer.create(name='Newer valid corruption endpoint',company=company)
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        with pytest.raises(BookflowError) as caught:
            run_hosted(host,registry.get('audit list'),
                {'record_type':'customer','record_id':target['id'],'limit':20},
                Context.new('http','Co-resident corruption witness'),cred,info['company_id'],'option',False)
        assert caught.value.details=={'reason':'audit_format'}
        assert host._readers_attached==0
    finally:
        host.stop()
