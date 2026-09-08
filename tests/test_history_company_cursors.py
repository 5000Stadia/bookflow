"""Actual company history/activity with database-owned opaque continuation."""
import pytest
from bookflow.core import identity_admin_binding as binding, history_cursors as cursors
from bookflow.core.context import Context
from bookflow.core.publication_audit import execute_history
from bookflow.hub.audit_projection import HistorySelection
from tests.test_audit_projection_activity import world, customer, note, edited, hosted, storage


@pytest.mark.parametrize('mode',('list','activity'))
def test_company_bookmarks_resume_without_skips_or_storage_changes(hosted,customer,mode):
    host,cred,cid,path = hosted
    fields = dict(mode=mode,company=cid,limit=1)
    if mode=='activity':fields.update(record_type='customer',record_id=customer['id'])
    selection = HistorySelection(**fields)
    original = storage(path)
    seen=[];token=None
    for index in range(3):
        ctx = Context.new('http','Company bookmark witness')
        with binding.hosted_reader(host,cred,request_id=ctx.request_id) as reader:
            if token is None:
                current=selection
                page,proof=execute_history(reader,current,ctx=ctx)
            else:
                current=cursors.resume(reader,selection,token,ctx=ctx)
                page,proof=execute_history(reader,current)
            value=page['items'][0] if mode=='activity' else page['events'][0]
            identity=(value['event_id'],value['entry_id']) if mode=='activity' else value['id']
            assert identity not in seen
            seen.append(identity)
            token=cursors.issue(reader,proof)
        assert host._readers_attached==0
        if mode=='activity':assert bool(token)==(index<2)
        else:assert token
    assert len(seen)==3 and storage(path)==original
