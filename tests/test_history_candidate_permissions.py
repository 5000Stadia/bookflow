"""Raw candidate matches cannot disclose entries hidden by current authority."""
import pytest
from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.execution import run_hosted
from bookflow.hub import audit_projection as projection
from tests.test_audit_projection_activity import world,customer
from tests.test_audit_projection_reference_masking import vendor,linked,hosted,denied


def test_permission_reduction_hides_matching_event_and_invalidates_old_page(
        hosted,customer,request,monkeypatch):
    host,cid,_,event,credential=hosted
    ctx=Context.new('http','Candidate permission witness')
    command=registry.get('audit list')
    fields={'record_type':'customer','record_id':customer['id'],'limit':1}
    before=run_hosted(host,command,fields,ctx,credential,cid,'option',False)
    assert before['items'][0]['id']==event and before['next_before']
    current=request.getfixturevalue('denied')
    with pytest.raises(BookflowError):before.check()
    after=run_hosted(host,command,fields,ctx,current,cid,'option',False)
    assert event not in {item['id'] for item in after['items']}
    after.check()
    original=projection._visible_events
    def full(*args,**kwargs):
        kwargs.pop('candidate',None)
        return original(*args,**kwargs)
    monkeypatch.setattr(projection,'_visible_events',full)
    baseline=run_hosted(host,command,fields,ctx,current,cid,'option',False)
    assert after==baseline
    assert host._readers_attached==0
