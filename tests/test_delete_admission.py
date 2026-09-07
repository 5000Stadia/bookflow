"""G0 admission only: no financial Delete implementation or activation provider."""
import json
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from bookflow import BookflowError
from bookflow.core import registry, dispatch
from bookflow.core.context import Context, Interface
from bookflow.core.models import StrictModel
from bookflow.hub import access

CAPABILITIES = tuple('transaction.' + family + '.delete' for family in
                     ('journal_entry', 'invoice', 'sales_receipt', 'payment'))
ROLES = ('readonly', 'standard', 'admin', 'owner', 'hub_admin')


class Input(StrictModel):
    pass


class Output(StrictModel):
    value: str


@pytest.fixture
def commands(monkeypatch):
    registry.load_all()
    monkeypatch.setattr(registry, 'REGISTRY', dict(registry.REGISTRY))
    nouns = registry.all_nouns()
    monkeypatch.setattr(registry, 'all_nouns', lambda: nouns + ['admission-fixture'])
    called = []
    result = {}
    for verb, capability, marker, write in (
            ('delete', CAPABILITIES[1], False, True),
            ('inspect', 'fixture.inspection', True, False),
            ('recover', CAPABILITIES[0], False, True),
            ('resource', 'ledger.read', False, False),
            ('ordinary', 'ledger.read', False, False)):
        def plan(inp, ctx, s):
            called.append('plan')
            return registry.Plan(Output(value='sensitive fixture result'))
        cmd = registry.command('admission-fixture ' + verb, scope='company',
            description='Test-owned admission command.', input_model=Input,
            output_model=Output, capability=capability, explicit_grant_only=marker,
            required_role='standard' if write else 'member', writes={'company'} if write else set())(plan)
        if write:
            def apply(plan, ctx, s):
                called.append('apply')
                return registry.Applied(plan.preview, [], 'Fixture only')
            cmd.applier(apply)
        result[verb] = cmd
    def recover(inp, ctx, s):
        called.append('saved facts')
        return registry.MatchedRecovery(Output(value='sensitive saved operation'))
    result['recover'].permanent_recovery = recover
    result['resource'].resource_requirements = ((CAPABILITIES[2], 'standard'),)
    return result, called


def denied(fn):
    with pytest.raises(BookflowError) as caught:
        fn()
    assert caught.value.code == 'E_PERMISSION'
    assert caught.value.details == {'reason': 'capability_not_activated'}
    return caught.value.to_dict()


@pytest.mark.parametrize('role', ROLES)
def test_default_resource_denies_all_delete_families_before_role_bypass(role):
    # No database/company lookup is possible here: activation must precede it.
    s = SimpleNamespace(is_hub_admin=role == 'hub_admin', memberships=[], role=role)
    for capability in CAPABILITIES:
        denied(lambda: access.require_resource(s, capability, 'standard'))


def test_execute_denies_before_pending_maintenance(commands, monkeypatch):
    cmds, called = commands
    maintenance = []
    def flush(hub):
        maintenance.append('config')
    def moves(*args):
        maintenance.append('moves')
    monkeypatch.setattr(dispatch, '_complete_pending_organizations', moves)
    # A normal command must reach the same maintenance path; stop before its
    # ordinary authorization so this witness requires no database fixture.
    monkeypatch.setattr(dispatch, 'run_in_session', lambda *a, **kw: {'ordinary': True})
    session = SimpleNamespace(is_hub_admin=True, hub=SimpleNamespace(writable=True),
                              config=SimpleNamespace(flush_pending=flush))
    context = Context.new(Interface.python, 'g0-ordering')
    denied(lambda: dispatch.execute(cmds['delete'], {}, context, session))
    assert maintenance == [] and called == []
    assert dispatch.execute(cmds['ordinary'], {}, context, session) == {'ordinary': True}
    assert maintenance == ['config', 'moves'] and called == []


@pytest.mark.parametrize('dry_run', [False, True])
def test_early_recovery_precedes_saved_facts_and_company_open(commands, monkeypatch, dry_run):
    cmds, called = commands
    def unexpected(*args, **kwargs):
        pytest.fail('Company authorization/opening preceded explicit activation denial')
    monkeypatch.setattr(dispatch, 'authorize', unexpected)
    s = SimpleNamespace(is_hub_admin=True)
    denied(lambda: dispatch._permanent_recovery(cmds['recover'], Input(),
        Context.new(Interface.python, 'g0'), s, 'private selector', 'option', dry_run))
    assert called == []


@pytest.mark.parametrize('verb', ['delete', 'inspect', 'resource'])
def test_ordinary_authorization_denies_before_company_resolution(commands, monkeypatch, verb):
    cmds, called = commands
    monkeypatch.setattr(dispatch, 'resolve_company', lambda *a: pytest.fail('Resolved hidden company'))
    denied(lambda: dispatch.authorize(cmds[verb], Context.new(Interface.python, 'g0'),
                                     SimpleNamespace(is_hub_admin=True), company_selector='hidden'))
    assert called == []


def test_fixture_policy_is_local_and_does_not_skip_resource_roles(commands, monkeypatch):
    cmds, _ = commands
    s = SimpleNamespace(company_row={'id': 'C', 'organization_id': 'O'}, is_hub_admin=False,
        memberships=[{'scope_type': 'company', 'scope_id': 'C', 'role': 'readonly'}])
    calls = []
    # Test-local override, never a runtime/config/Context provider API.
    with monkeypatch.context() as patch:
        patch.setattr(access, 'require_explicit_grant', lambda session, capability: calls.append(capability))
        with pytest.raises(BookflowError) as caught:
            access.require_resource(s, CAPABILITIES[0], 'standard')
        assert caught.value.code == 'E_PERMISSION' and 'reason' not in caught.value.details
        s.memberships[0]['role'] = 'standard'
        access.require_resource(s, CAPABILITIES[0], 'standard')
        access.require_command_activation(s, cmds['inspect'])
        assert calls == [CAPABILITIES[0], CAPABILITIES[0], 'fixture.inspection']
    denied(lambda: access.require_resource(s, CAPABILITIES[0], 'standard'))


def test_marker_metadata_is_strict_and_current_live_catalog_is_unchanged():
    from bookflow.hub.permission_catalog import DELETE_NAMES
    registry.load_all()
    assert registry.EXPLICIT_GRANT_ONLY_CAPABILITIES == set(CAPABILITIES) == set(DELETE_NAMES)
    assert not any(c.requires_explicit_grant for c in registry.all_commands(include_standalone=True))
    for extra in ({'explicit_grant_only': False}, {'policy_provider': 'allow'}):
        with pytest.raises(ValidationError):
            Input.model_validate(extra)
    for options in ({'explicit_grant_only': 'false'}, {'explicit_grant_only': True, 'bootstrap': True},
                    {'capability': CAPABILITIES[0], 'bootstrap': True}):
        with pytest.raises(ValueError):
            registry.command('fixture invalid', scope='company', description='Invalid.',
                             input_model=Input, output_model=Output, **options)


@pytest.mark.parametrize('verb', ['inspect', 'resource'])
def test_read_inspection_marker_is_honest_in_help(commands, verb):
    from bookflow.adapters.mcp.catalog import command_help
    cmd = commands[0][verb]
    doc = command_help(cmd.name, 'input_schema')
    assert 'explicit grant required (not activated)' in doc['authorization']
    assert 'explicit_grant_only' not in doc['input_schema']['properties']


def test_real_roles_core_cli_http_and_mcp_admission(root, commands, monkeypatch, tmp_path, capsys):
    """Real fixture identities/default resolver; actual adapters, no network listener."""
    import bookflow
    from fastapi.testclient import TestClient
    from bookflow.adapters.cli.app import main
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from tests.conftest import make_actor, as_user
    from tests.test_payment_publication_freshness import raw_snapshot

    cmds, called = commands
    owner = bookflow.connect(data_root=str(root))
    cid = owner.company.list()['items'][0]['company_id']
    identities = []
    for role in ROLES:
        login = 'g0-' + role
        make_actor(root, login, hub_admin=role == 'hub_admin',
                   company_role=None if role == 'hub_admin' else (cid, role))
        client = as_user(root, login)
        assert client.run('account list', {}, company=cid)['items']
        token = client.token.issue(label='G0 fixture')['secret']
        identities.append((role, login, client, token))
    before = raw_snapshot(root, cid)
    expected = None
    results = []
    for role, login, client, token in identities:
        for verb in ('delete', 'inspect', 'recover', 'resource'):
            for preview in ([False, True] if cmds[verb].is_write else [False]):
                error = denied(lambda: client.run(cmds[verb].name, {}, company=cid, dry_run=preview))
                expected = expected or error
                assert error == expected
                with monkeypatch.context() as patch:
                    patch.setattr(dispatch, 'os_login', lambda: login)
                    args = ['--json', '--data-root', str(root), '--company', cid]
                    if preview:
                        args.append('--dry-run')
                    patch.setattr(sys, 'argv', ['bookflow', *args, 'admission-fixture', verb])
                    with pytest.raises(SystemExit) as exited:
                        main()
                output = capsys.readouterr()
                assert exited.value.code != 0
                assert json.loads(output.err) == expected, output
                assert not output.out
                results.append([role, verb, preview, 'core/cli'])
    assert called == [] and raw_snapshot(root, cid) == before

    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    try:
        with TestClient(handle.app) as http:
            for role, login, client, token in identities:
                headers = {'Authorization': 'Bearer ' + token,
                           'X-Bookflow-Session-Id': '01ARZ3NDEKTSV4RRFFQ69G5FAV'}
                for verb in ('delete', 'inspect', 'recover', 'resource'):
                    for preview in ([False, True] if cmds[verb].is_write else [False]):
                        response = http.post(f'/companies/{cid}/commands/admission-fixture.{verb}', json={},
                            headers=headers, params={'dry_run': 'true'} if preview else {})
                        assert response.status_code == 403 and response.json() == expected, response.text
                        response = http.post('/adapters/mcp/intents/new', headers=headers, json={
                            'arguments': {'command': cmds[verb].name, 'company': cid, 'dry_run': preview},
                            'company_selection': {'value': cid, 'source': 'option'}})
                        assert response.status_code == 403 and response.json() == expected, response.text
                        assert 'sensitive' not in response.text
                        results.append([role, verb, preview, 'http/mcp'])
    finally:
        handle.stop()
    assert called == [] and raw_snapshot(root, cid) == before
    (tmp_path/'denials.json').write_text(json.dumps({'error': expected, 'cases': results,
        'planner_apply_saved_fact_calls': called, 'company_before_after': before}, indent=2))


@pytest.mark.parametrize('started', [False, True])
def test_retained_success_cannot_publish_after_fixture_policy_is_removed(commands, monkeypatch, started):
    import asyncio
    from contextlib import contextmanager
    from bookflow.core import publication
    from bookflow.adapters.http.publication import PublicationMiddleware, protect
    cmds, called = commands
    session = SimpleNamespace(actor=SimpleNamespace(id='U', kind='human'), is_hub_admin=True,
                              memberships=[], hub=None)
    credential = SimpleNamespace(token_id='T', revalidate=lambda db: None)
    @contextmanager
    def reader(host, cred):
        yield session
    monkeypatch.setattr(publication, 'publication_reader', reader)
    monkeypatch.setattr(publication, 'resolve_company', lambda *a: {'id': 'C', 'organization_id': 'O'})
    permit = publication.PublicationPermit(cmds['inspect'], Input(), Context.new(Interface.http, 'g0'),
        ('U', 'human', True), frozenset(), ('C', 'O'), {}, execution_succeeded=True)
    with monkeypatch.context() as patch:
        patch.setattr(access, 'require_explicit_grant', lambda *a: None)
        permit.check(None, credential)
    sent = []
    class Document:
        def check(self, **kw):
            permit.check(None, credential)
    document = Document()
    document.credential = credential
    async def app(scope, receive, send):
        if started:
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        protect(document)
        if not started:
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'sensitive saved operation'})
    async def send(message):
        sent.append(message)
    async def receive():
        return {'type': 'http.request'}
    async def run():
        await PublicationMiddleware(app)({'type': 'http', 'path': '/fixture'}, receive, send)
    if started:
        with pytest.raises(ConnectionAbortedError):
            asyncio.run(run())
        assert len(sent) == 1 and sent[0]['type'] == 'http.response.start'
    else:
        asyncio.run(run())
        assert sent[0]['status'] == 403
        assert json.loads(sent[1]['body'])['code'] == 'E_PERMISSION'
    assert all(b'sensitive' not in message.get('body', b'') for message in sent)
    assert called == []
