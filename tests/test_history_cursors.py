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


def test_tampering_cannot_change_visible_anchor_or_signature(world):
    import json
    selection = HistorySelection(mode='list',limit=1)
    with observe(world) as reader:
        _,proof = execute_history(reader,selection)
        token = cursors.issue(reader,proof)
        body,signature = token.split('.')
        payload = json.loads(cursors.decode64(body))
        payload['anchor'] = 'not-the-authenticated-anchor'
        tampered = cursors.encode64(cursors.canonical(payload))+'.'+signature
        changed_mac = body+'.'+('A' if signature[0]!='A' else 'B')+signature[1:]
        for value in (tampered,changed_mac,body+'=.'+signature):
            with pytest.raises(BookflowError) as caught:cursors.resume(reader,selection,value)
            assert caught.value.code == 'E_VALIDATION'
            assert caught.value.details == {'reason':'invalid_cursor'}


def test_changed_current_authority_rejects_old_bookmark(world):
    host,path,*_ = world
    selection = HistorySelection(mode='list',limit=1)
    with observe(world) as reader:
        _,proof = execute_history(reader,selection)
        token = cursors.issue(reader,proof)
    apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(3),'readonly'),'A')
    with observe(world) as reader:
        with pytest.raises(BookflowError) as caught:cursors.resume(reader,selection,token)
        assert caught.value.code == 'E_PERMISSION'
        assert caught.value.details == {'reason':'authority_changed'}
        # Current read access remains; the old authority-bound bookmark is stale.
        page,proof = execute_history(reader,selection)
        assert page['events']
        fresh = cursors.issue(reader,proof)
        assert fresh != token
        cursors.resume(reader,selection,fresh)


def test_agent_bookmark_is_bound_to_fixed_human_principal(world):
    host,path,*_ = world
    selection=HistorySelection(mode='tail',limit=1)
    first=admin.TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
    second=admin.TokenBinding('secret-GQ-live','GQ-live','G','session','Q',path,'REQUEST')
    with binding.hosted_reader(host,first,request_id='REQUEST') as reader:
        _,proof=execute_history(reader,selection)
        assert proof.identity.actor=='G' and proof.identity.principal=='P'
        token=cursors.issue(reader,proof)
        assert token
        cursors.resume(reader,selection,token)
    with binding.hosted_reader(host,second,request_id='REQUEST') as reader:
        assert reader.authenticate().principal=='Q'
        with pytest.raises(BookflowError) as caught:cursors.resume(reader,selection,token)
        assert caught.value.details=={'reason':'invalid_cursor'}
    assert host._readers_attached==0


def test_copied_database_key_does_not_make_bookmark_valid_in_another_root(world,tmp_path):
    import sqlite3
    import shutil
    host,path,*_=world
    selection=HistorySelection(mode='list',limit=1)
    with observe(world) as reader:
        _,proof=execute_history(reader,selection)
        token=cursors.issue(reader,proof)
    target=tmp_path/'copy';target.mkdir()
    def backup():
        with sqlite3.connect(target/'hub.db') as destination:host._hub.raw.backup(destination)
    host.submit(backup)
    shutil.copyfile(path.parent/'config.toml',target/'config.toml')
    copied=Host(target,version=client_version());copied.start()
    try:
        admitted=token_binding(target/'hub.db','R')
        with binding.hosted_reader(copied,admitted,request_id='REQUEST') as reader:
            with pytest.raises(BookflowError) as caught:cursors.resume(reader,selection,token)
            assert caught.value.details=={'reason':'invalid_cursor'}
            # Same persisted signing material, different actual authenticated root.
            page,proof=execute_history(reader,selection)
            assert page['events'] and cursors.issue(reader,proof)!=token
        assert copied._readers_attached==0
    finally:copied.stop()
