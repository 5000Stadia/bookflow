"""Partial operation visibility never licenses its free-text explanation."""
import pytest
from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.execution import run_hosted
from tests.test_audit_projection_activity import world,customer
from tests.test_audit_projection_reference_masking import vendor,linked,hosted,denied


@pytest.fixture(scope='module')
def updated(world,linked,customer):
    return world['client'].customer.update(customer=customer['id'],expected_version=2,
        name='Visible renamed customer',company='Demo Plumbing Co',reason='Discussed confidential linked vendor')


def test_hidden_unchanged_reference_suppresses_reason_and_stales_old_response(updated,hosted,customer,request):
    host,cid,_,_,credential=hosted
    command=registry.get('audit list');ctx=Context.new('http','Explanation permission')
    fields={'record_type':'customer','record_id':customer['id'],'limit':1}
    before=run_hosted(host,command,fields,ctx,credential,cid,'option',False)
    assert before['items'][0]['explanation']['reason']=='Discussed confidential linked vendor'
    activity_command=registry.get('activity')
    activity_fields={**fields,'limit':200}
    activity_before=run_hosted(host,activity_command,activity_fields,ctx,credential,cid,'option',False)
    def matching(result):return next(x for x in result['items'] if x['event_id']==before['items'][0]['id'])
    assert matching(activity_before)['explanation']['reason']=='Discussed confidential linked vendor'
    current=request.getfixturevalue('denied')
    with pytest.raises(BookflowError) as caught:before.check()
    assert caught.value.code=='E_PERMISSION'
    after=run_hosted(host,command,fields,ctx,current,cid,'option',False)
    assert after['items'][0]['id']==before['items'][0]['id']
    assert after['items'][0]['explanation'] is None
    activity_after=run_hosted(host,activity_command,activity_fields,ctx,current,cid,'option',False)
    assert matching(activity_after)['explanation'] is None
    with pytest.raises(BookflowError) as activity_error:activity_before.check()
    assert activity_error.value.code=='E_PERMISSION'
    activity_after.check()
    after.check()
    assert host._readers_attached==0
