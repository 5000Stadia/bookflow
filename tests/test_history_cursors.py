"""Authenticated hub readers and real B2 history through opaque bookmarks."""
import pytest
from bookflow.core import identity_admin_binding as binding, history_cursors as cursors
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.core.errors import BookflowError
from bookflow.core.publication_audit import execute_history
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from bookflow.hub.audit_projection import HistorySelection
from tests.test_permission_runtime import path as root_fixture
from tests.test_audit_projection_publication import apply
from tests.permission_admin_support import binding as token_binding, snapshot


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    path = root_fixture.__wrapped__(tmp_path_factory.mktemp('history-cursor'))
    host = Host(path.parent,version=client_version());host.start()
    try:
        first = apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(1),'readonly'),'A')
        second = apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(2),'standard'),'A')
        yield host,path,first,second
    finally:host.stop()


def observe(world, who='R'):
    host,path,*_ = world
    return binding.hosted_reader(host,token_binding(path,who),request_id='REQUEST')


def test_visible_list_resume_and_storage_preserved(world):
    host,_,first,second = world
    selection = HistorySelection(mode='list',limit=1)
    before = host.submit(lambda:snapshot(host._hub.raw))
    with observe(world) as reader:
        page,proof = execute_history(reader,selection)
        token = cursors.issue(reader,proof)
        assert page['events'][0]['id'] == second and token
        payload = cursors.decode64(token.split('.')[0])
        assert not any(word in payload for word in (b'seq',b'generation',b'high_water'))
    with observe(world) as reader:
        resumed = cursors.resume(reader,selection,token)
        page,_ = execute_history(reader,resumed)
        assert page['events'][0]['id'] == first
    assert host.submit(lambda:snapshot(host._hub.raw)) == before
    assert host._readers_attached == 0


@pytest.mark.parametrize('token',(3,'3','broken','a.b','x'*4097))
def test_invalid_and_numeric_bookmarks_have_one_error(world,token):
    with observe(world) as reader:
        with pytest.raises(BookflowError) as caught:
            cursors.resume(reader,HistorySelection(mode='list',limit=1),token)
        assert caught.value.code == 'E_VALIDATION'
        assert caught.value.details == {'reason':'invalid_cursor'}


def test_reader_and_query_binding(world):
    selection = HistorySelection(mode='list',limit=1)
    with observe(world) as reader:
        _,proof = execute_history(reader,selection)
        token = cursors.issue(reader,proof)
        with pytest.raises(BookflowError) as caught:
            cursors.resume(reader,HistorySelection(mode='list',limit=2),token)
        assert caught.value.details == {'reason':'invalid_cursor'}
    with observe(world,'RO') as reader:
        with pytest.raises(BookflowError) as caught:cursors.resume(reader,selection,token)
        assert caught.value.details == {'reason':'invalid_cursor'}


def test_hidden_commit_preserves_entire_page_and_bookmark(world):
    host,path,*_ = world
    selection = HistorySelection(mode='list',limit=1)
    with observe(world) as reader:
        before,proof = execute_history(reader,selection)
        token = cursors.issue(reader,proof)
    apply(host,path,admin.SetUserActive('U',5,False),'H')
    with observe(world) as reader:
        after,proof = execute_history(reader,selection)
        assert after == before
        assert cursors.issue(reader,proof) == token
        cursors.resume(reader,selection,token)


def test_tail_bookmark_does_not_pin_old_endpoint(world):
    selection = HistorySelection(mode='tail',limit=1)
    with observe(world) as reader:
        _,proof = execute_history(reader,selection)
        token = cursors.issue(reader,proof)
        resumed = cursors.resume(reader,selection,token)
        assert resumed.anchor == proof.history.endpoint
        assert resumed.endpoint is None
