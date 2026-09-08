"""Current filtered-list corruption behavior, kept explicit before scan optimization."""
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


def test_older_unrelated_authorized_capture_still_validated(world):
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
        with pytest.raises(BookflowError) as caught:
            run_hosted(host,registry.get('audit list'),{'record_type':'customer','record_id':target['id'],'limit':20},
                       Context.new('http','Filtered corruption witness'),cred,info['company_id'],'option',False)
        assert caught.value.code=='E_VALIDATION'
        assert caught.value.details=={'reason':'audit_format'}
        assert host._readers_attached==0
    finally:
        host.stop()
