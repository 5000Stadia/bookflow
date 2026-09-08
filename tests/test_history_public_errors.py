"""Public history preserves fixed input errors and retained rejected-query proofs."""
import pytest
from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.execution import run_hosted
from tests.test_history_cursors import world
from tests.test_audit_projection_publication import credential


@pytest.mark.parametrize('value',[7,'7','a.b'])
def test_registered_history_bookmark_error_is_actionable(world,value):
    registry.load_all();host,*_=world
    ctx=Context.new('http','Public error').model_copy(update={'request_id':'REQUEST'})
    with pytest.raises(BookflowError) as caught:
        run_hosted(host,registry.get('hub audit list'),{'limit':1,'before':value},ctx,
                   credential(host,'R'),None,'none',False)
    error=caught.value
    assert error.code=='E_VALIDATION'
    assert error.details=={'reason':'invalid_cursor'}
    assert error.message=='Restart the history query without a bookmark.'
    if isinstance(value,str):
        # Shape validation precedes execution. A string reaches the retained
        # request resolver; its cryptographic rejection must own a fresh proof.
        error.publication_document.check()
    assert host._readers_attached==0


def test_registered_stale_bookmark_error_keeps_authority_change_reason(world):
    from bookflow.hub.identity_admin import PutMembership, Version
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.test_audit_projection_publication import apply
    registry.load_all();host,path,*_=world
    ctx=Context.new('http','Stale public bookmark').model_copy(update={'request_id':'REQUEST'})
    cmd=registry.get('hub audit list');cred=credential(host,'R')
    page=run_hosted(host,cmd,{'limit':1},ctx,cred,None,'none',False)
    apply(host,path,PutMembership('R',ScopeKey('organization','O'),Version(3),'readonly'),'A')
    with pytest.raises(BookflowError) as caught:
        run_hosted(host,cmd,{'limit':1,'before':page['next_before']},ctx,cred,None,'none',False)
    assert caught.value.code=='E_PERMISSION'
    assert caught.value.details=={'reason':'authority_changed'}
    caught.value.publication_document.check()
    assert host._readers_attached==0
