"""Actual hosted execution owns both opaque history replies and rejected inputs."""
import pytest
from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core import registry
from bookflow.adapters.http.execution import run_history
from bookflow.hub.audit_projection import HistorySelection
from tests.test_history_cursors import world
from tests.test_audit_projection_publication import credential


def run(world, selection, bookmark=None):
    registry.load_all()
    host,*_=world
    return run_history(host,selection,Context.new('http','History continuation').model_copy(update={'request_id':'REQUEST'}),
                       credential(host,'R'),wire=True,bookmark=bookmark)


def test_hosted_wire_resume_retains_exact_publication(world):
    host,_,first,second=world
    selection=HistorySelection(mode='list',limit=1)
    page=run(world,selection)
    assert page['projection_version']==2 and page['items'][0]['id']==second
    page.check()
    following=run(world,selection,page['next_before'])
    assert following['items'][0]['id']==first
    following.check()
    assert host._readers_attached==0


def test_invalid_bookmark_errors_are_owned_and_revalidated(world):
    host,*_=world
    selection=HistorySelection(mode='list',limit=1)
    for token in (7,'7','a.b','x'*4097):
        with pytest.raises(BookflowError) as caught:
            run(world,selection,token)
        error=caught.value
        assert error.code=='E_VALIDATION'
        assert error.details=={'reason':'invalid_cursor'}
        assert error.message=='Restart the history query without a bookmark.'
        # Releasing the error must recheck its actual rejected request, not
        # rerun a successful first-page query and lose the response certificate.
        error.publication_document.check()
        assert host._readers_attached==0


def test_stale_bookmark_keeps_its_fixed_error_through_delivery(world):
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.test_audit_projection_publication import apply
    host,path,*_=world
    selection=HistorySelection(mode='list',limit=1)
    page=run(world,selection)
    apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(3),'readonly'),'A')
    with pytest.raises(BookflowError) as caught:
        run(world,selection,page['next_before'])
    assert caught.value.code=='E_PERMISSION'
    assert caught.value.details=={'reason':'authority_changed'}
    caught.value.publication_document.check()
    assert run(world,selection)['items']
    assert host._readers_attached==0
