"""Real OS-bound local and hosted readers share company activity continuation."""
import pytest
from bookflow.core import history_offline, registry
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.core.config import os_login
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from bookflow.hub.audit_projection import HistorySelection
from bookflow.adapters.http.execution import run_history
from tests.test_audit_projection_activity import world, customer, note, edited, storage


def context():return Context.new('python','Local history witness')


def test_same_company_wire_and_bookmark_across_hosted_and_offline(world,customer,edited):
    registry.load_all()
    row=world['client'].company.show(company='Demo Plumbing Co')
    from pathlib import Path
    path=Path(row['path'])/'company.db';before=storage(path)
    selection=HistorySelection(mode='activity',company=row['company_id'],record_type='customer',record_id=customer['id'],limit=1)
    local=history_offline.read(world['root'],selection,context())
    assert local['projection_version']==2 and local['count']==1 and local['next_cursor']
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        hosted=run_history(host,selection,context(),cred,wire=True)
        assert hosted==local
        following=run_history(host,selection,context(),cred,wire=True,bookmark=local['next_cursor'])
        following.check()
        assert host._readers_attached==0
    finally:host.stop()
    resumed=history_offline.read(world['root'],selection,context(),bookmark=local['next_cursor'])
    assert resumed==following
    assert resumed['items'][0]['entry_id']!=local['items'][0]['entry_id']
    assert storage(path)==before


def test_local_invalid_bookmark_has_same_actionable_error(world,customer,edited):
    row=world['client'].company.show(company='Demo Plumbing Co')
    selection=HistorySelection(mode='list',company=row['company_id'])
    with pytest.raises(BookflowError) as caught:
        history_offline.read(world['root'],selection,context(),bookmark=4)
    assert caught.value.code=='E_VALIDATION'
    assert caught.value.details=={'reason':'invalid_cursor'}
    assert caught.value.message=='Restart the history query without a bookmark.'
    # A second read proves the rejected path released the root lock/resources.
    assert history_offline.read(world['root'],selection,context())['items']
