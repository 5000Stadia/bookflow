"""Company selection uses authenticated visibility before any company open."""
import pytest
from bookflow.core import identity_admin_binding as binding
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.core.errors import BookflowError
from bookflow.core.history_commands import resolve_company
from bookflow.hub.identity_admin import TokenBinding
from bookflow.storage.engine import open_database
from tests.test_permission_runtime import path as root_fixture
from tests.permission_admin_support import binding as token_binding, snapshot


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    path = root_fixture.__wrapped__(tmp_path_factory.mktemp('history-commands'))
    with open_database(path, writable=True) as db:
        db.raw.execute('UPDATE companies SET name_key=lower(id)')
        db.raw.execute('UPDATE organizations SET name_key=lower(id)')
    host = Host(path.parent, version=client_version()); host.start()
    try:
        yield host, path
    finally:
        host.stop()


def test_visible_names_and_hidden_suggestions_do_not_open_companies(world):
    host,path = world
    before = host.submit(lambda:snapshot(host._hub.raw))
    with binding.hosted_reader(host,token_binding(path,'R'),request_id='REQUEST') as reader:
        assert resolve_company(reader,'c','option') == 'C'
        assert resolve_company(reader,'o/d','option') == 'D'
        for name in ('E','Z/E','missing'):
            with pytest.raises(BookflowError) as caught:
                resolve_company(reader,name,'option')
            assert caught.value.code == 'E_COMPANY_NOT_FOUND'
            assert not any('E' in x or 'Z' in x for x in caught.value.details['suggestions'])
        assert reader.session.company is None
    with binding.hosted_reader(host,token_binding(path,'H'),request_id='REQUEST') as reader:
        with pytest.raises(BookflowError) as caught:
            resolve_company(reader,'C','option')
        assert caught.value.details['suggestions'] == []
    assert host.submit(lambda:snapshot(host._hub.raw)) == before
    assert host._readers_attached == 0


def test_bound_agent_selects_only_its_current_company_audience(world):
    host,path = world
    credential=TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
    with binding.hosted_reader(host,credential,request_id='REQUEST') as reader:
        assert resolve_company(reader,'O/C','option') == 'C'
        with pytest.raises(BookflowError) as caught:
            resolve_company(reader,'E','option')
        assert caught.value.code == 'E_COMPANY_NOT_FOUND'
        assert reader.session.company is None


def test_prepare_keeps_command_aliases_and_rejects_numeric_bookmarks(world):
    from bookflow.core import registry
    from bookflow.core.context import Context
    from bookflow.core.history_commands import prepare
    host,path = world
    registry.load_all()
    ctx=Context.new('http','History commands').model_copy(update={'request_id':'REQUEST'})
    with binding.hosted_reader(host,token_binding(path,'R'),request_id='REQUEST') as reader:
        cmd=registry.get('audit tail')
        request=prepare(reader,cmd,{'via':'cli','kind':'human','after':'opaque.bookmark'},ctx,'O/C','option')
        assert request.selection.company == 'C'
        assert request.selection.interface == 'cli'
        assert request.selection.actor_kind == 'human'
        assert request.bookmark == 'opaque.bookmark'
        with pytest.raises(BookflowError) as caught:
            prepare(reader,cmd,{'after':3},ctx,'O/C','option')
        assert caught.value.details == {'reason':'invalid_cursor'}
        assert reader.session.company is None


def test_bound_agent_cannot_claim_a_different_principal(world):
    from bookflow.core import registry
    from bookflow.core.context import Context
    from bookflow.core.history_commands import prepare
    registry.load_all()
    host,path=world
    credential=TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
    ctx=Context.new('http','Principal claim').model_copy(update={'request_id':'REQUEST','on_behalf_of':'Q'})
    with binding.hosted_reader(host,credential,request_id='REQUEST') as reader:
        with pytest.raises(BookflowError) as caught:
            prepare(reader,registry.get('hub audit list'),{},ctx,None,'none')
        assert caught.value.code=='E_UNAUTHENTICATED'
        assert reader.session.company is None
