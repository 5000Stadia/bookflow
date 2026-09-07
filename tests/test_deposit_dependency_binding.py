"""Actual offline authenticated dispatch and unchanged hosted OS capture."""
import copy
import pytest
from bookflow.core import registry
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from bookflow.company import deposit_dependency_history as history
from bookflow.company.deposit_dependency_models import InspectionRoot
from tests.test_deposit_lifecycle import driver, additional_document
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_commit_hooks import owner_host


def observe(client, monkeypatch, callback, company=COMPANY):
    command = registry.get('company show'); original = command.plan; found = []
    def plan(inp, ctx, s):
        found.append(callback(s))
        return original(inp, ctx, s)
    with monkeypatch.context() as patch:
        patch.setattr(command, 'plan', plan)
        client.run('company show', {}, company=company)
    assert len(found) == 1
    return found[0]


def test_offline_actual_binding_guard_roundtrip_and_tamper(client, sale, driver, monkeypatch):
    document = additional_document(client, sale)
    posted = driver.run('post', {'operation_key': 'binding-history', 'document': document})
    root = InspectionRoot(kind='deposit', id=posted.current.id)
    before = driver.dump()
    def captured(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, root, binding)
        assert not facts.unknown, facts.unknown
        token = history.issue(s, recipe, facts, binding)
        comparison = history.compare(s, token, root, binding)
        assert comparison.matches and not comparison.unknown_history and comparison.changes == ()
        assert comparison.baseline == comparison.current
        with pytest.raises(BookflowError) as error:
            history.compare(s, token[:-2] + ('AA' if token[-2:] != 'AA' else 'BB'), root, binding)
        assert error.value.code == 'E_VALIDATION'
        with pytest.raises(BookflowError) as error:
            history.capture(s, root, None)
        assert error.value.code == 'E_UNAUTHENTICATED'
        return token
    assert observe(client, monkeypatch, captured)
    assert driver.dump() == before


def test_hosted_capture_equals_authenticated_session_constructor(owner_host):
    host, uid, login, _ = owner_host
    binding = OSBinding.capture(host, login)
    actual = host.run_write(uid, login, lambda s: OSBinding.from_session(s))
    assert binding == actual
    assert (binding.user_id, binding.actor_kind, binding.on_behalf_of, binding.authority_epoch, binding.token_id) == (uid, 'human', None, None, None)


def _storage(root, company_path):
    import sqlite3
    result = []
    for path in (root/'hub.db', company_path):
        with sqlite3.connect(path) as db:
            result.append(tuple(db.iterdump()))
    return tuple(result)


@pytest.fixture
def bound_people(root, client):
    from tests.conftest import make_actor, as_user
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    from bookflow.core import clock
    company = client.company.list()['items'][0]['company_id']
    first = make_actor(root, 'deposit-principal-one', company_role=(company, 'standard'))
    second = make_actor(root, 'deposit-principal-two', company_role=(company, 'standard'))
    agent = make_actor(root, 'deposit-history-agent', kind='agent', owner_user_id=first, company_role=(company, 'standard'))
    with writer(root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent, epoch=3, suspended_at=None, suspension_reason=None))
        for principal in (first, second):
            db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent, principal_user_id=principal,
                assigned_by=first, assigned_at=clock.now_iso(), revoked_at=None))
    return dict(company=company, first=first, second=second, agent=agent,
        one=as_user(root, 'deposit-principal-one'), two=as_user(root, 'deposit-principal-two'),
        bot=as_user(root, 'deposit-history-agent'))


def _credential(client, actor, principal):
    from bookflow.adapters.http.app import Credential
    issued = client.token.issue(user=actor, principal=principal, label='owned deposit binding witness')
    return Credential(actor, issued['token_id'], 'bearer', issued['label'], on_behalf_of=principal,
                      actor_kind='agent', secret=issued['secret'])


@pytest.mark.parametrize('case', ['principal_switch', 'renewal', 'revoked', 'expired', 'epoch', 'principal_loss', 'missing_principal'])
def test_actual_agent_credential_and_principal_matrix(root, client, sale, driver, monkeypatch, bound_people, case):
    from tests.test_row7_credentials import writer
    from tests.test_row8_journal import database_path
    from bookflow.hub import schema as h
    from bookflow.core import clock
    people = bound_people
    document = additional_document(client, sale)
    posted = driver.run('post', dict(operation_key='agent-matrix', document=document))
    target = InspectionRoot(kind='deposit', id=posted.current.id)
    credential = _credential(client, people['agent'], people['first'])
    def issue(s):
        recipe, facts = history.capture(s, target, credential)
        return history.issue(s, recipe, facts, credential)
    guard = observe(people['bot'], monkeypatch, issue, people['company'])
    expected = None
    if case in ('principal_switch', 'renewal'):
        credential = _credential(client, people['agent'], people['second'] if case == 'principal_switch' else people['first'])
        expected = 'E_VALIDATION' if case == 'principal_switch' else None
    elif case in ('revoked', 'expired', 'epoch', 'principal_loss'):
        with writer(root) as db:
            if case == 'revoked':
                db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == credential.token_id).values(revoked_at=clock.now_iso()))
            elif case == 'expired':
                db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == credential.token_id).values(expires_at='2000-01-01T00:00:00.000Z'))
            elif case == 'epoch':
                db.conn.execute(h.agent_authority.update().where(h.agent_authority.c.agent_user_id == people['agent']).values(epoch=4))
            else:
                db.conn.execute(h.memberships.update().where(h.memberships.c.user_id == people['first']).values(revoked_at=clock.now_iso()))
        expected = 'E_PERMISSION' if case == 'principal_loss' else 'E_UNAUTHENTICATED'
        if case == 'epoch':
            credential = _credential(client, people['agent'], people['first'])
            expected = 'E_VALIDATION'  # fresh admission cannot revive the old epoch's guard
    else:
        expected = 'E_UNAUTHENTICATED'
    before = _storage(root, database_path(client))
    def check(s):
        actual = OSBinding.from_session(s) if case == 'missing_principal' else credential
        if expected is None:
            assert history.compare(s, guard, target, actual).matches
        else:
            with pytest.raises(BookflowError) as caught:
                history.compare(s, guard, target, actual)
            assert caught.value.code == expected
            rendered = str(caught.value.details)
            assert all(identifier not in rendered for identifier in (people['agent'], people['first'], people['second']))
            assert 'unknown_history' not in rendered
    observe(people['bot'], monkeypatch, check, people['company'])
    assert _storage(root, database_path(client)) == before


def test_equal_membership_human_guard_is_not_transferable(root, client, sale, driver, monkeypatch, bound_people):
    from tests.test_row8_journal import database_path
    people = bound_people
    posted = driver.run('post', dict(operation_key='human-bind', document=additional_document(client, sale)))
    target = InspectionRoot(kind='deposit', id=posted.current.id)
    def issue(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, target, binding)
        return history.issue(s, recipe, facts, binding)
    guard = observe(people['one'], monkeypatch, issue, people['company'])
    before = _storage(root, database_path(client))
    def denied(s):
        with pytest.raises(BookflowError) as caught:
            history.compare(s, guard, target, OSBinding.from_session(s))
        assert caught.value.code == 'E_VALIDATION'
        assert caught.value.details == {'field': 'dependency_guard', 'reason': 'invalid_guard'}
    observe(people['two'], monkeypatch, denied, people['company'])
    assert _storage(root, database_path(client)) == before


def test_authorized_other_actor_exact_recovery_does_not_revalidate_old_guard(root, client, sale, driver, monkeypatch, bound_people):
    from tests.test_row8_journal import database_path
    from bookflow.company import deposit_lifecycle as lifecycle
    from bookflow.core.context import Context, Interface
    document = additional_document(client, sale)
    body = dict(operation_key='cross-actor-receipt', document=document)
    posted = driver.run('post', body)
    before = _storage(root, database_path(client))
    def recover(s):
        result = lifecycle.prepare(s, Context.new(Interface.python, 'authorized recovery'),
            lifecycle.INPUTS['post'].model_validate(body), 'post')
        assert result.idempotent_replay and result.effect == posted.effect and result.current == posted.current
        assert result.dependency_guard == posted.dependency_guard
    observe(bound_people['two'], monkeypatch, recover, bound_people['company'])
    assert _storage(root, database_path(client)) == before


def test_os_remapping_invalidates_the_existing_execution_producer(root,client,sale,driver,monkeypatch,bound_people):
    from bookflow.core.config import Config
    from tests.test_row8_journal import database_path
    people=bound_people
    posted=driver.run('post',dict(operation_key='os-remap-deposit',document=additional_document(client,sale)))
    target=InspectionRoot(kind='deposit',id=posted.current.id)
    def issue(s):
        binding=OSBinding.from_session(s)
        recipe,facts=history.capture(s,target,binding)
        return history.issue(s,recipe,facts,binding),binding
    guard,binding=observe(people['one'],monkeypatch,issue,people['company'])
    config=Config.load(root/'config.toml');config.set_user(binding.login,people['second']);config.save()
    before=_storage(root,database_path(client))
    def check(s):
        with pytest.raises(BookflowError) as caught:history.compare(s,guard,target,binding)
        assert caught.value.code=='E_UNAUTHENTICATED' and caught.value.details=={'reason':'OS binding changed'}
    observe(people['one'],monkeypatch,check,people['company'])
    assert _storage(root,database_path(client))==before


def test_private_writer_retains_the_actual_bearer_and_revalidates_before_dml(root,client,sale,driver,monkeypatch,bound_people):
    from dataclasses import replace
    from bookflow.company import deposit_lifecycle as lifecycle, deposit_persistence as persistence
    from bookflow.core.context import Context,Interface
    from tests.test_row7_credentials import writer
    from tests.test_row8_journal import database_path
    from bookflow.hub import schema as h
    from bookflow.core import clock
    people=bound_people
    credential=_credential(client,people['agent'],people['first'])
    document=additional_document(client,sale)
    ctx=Context.new(Interface.http,'actual bearer private writer',on_behalf_of=people['first'])
    def prepare(s,key):
        body=lifecycle.INPUTS['post'].model_validate(dict(operation_key=key,document=document))
        return lifecycle.prepare(s,ctx,body,'post',binding=credential),s
    plan,admitted=observe(people['bot'],monkeypatch,lambda s:prepare(s,'bound-live'),people['company'])
    before=_storage(root,database_path(client))
    wrong_context=Context.new(Interface.http,'wrong fixed principal',on_behalf_of=people['second'])
    with pytest.raises(BookflowError) as mismatch:
        with driver.session() as writer_session:
            s=replace(writer_session,actor=admitted.actor,os_login=admitted.os_login,memberships=admitted.memberships)
            persistence.execute(s,wrong_context,plan)
    assert mismatch.value.code=='E_UNAUTHENTICATED'
    assert _storage(root,database_path(client))==before
    with driver.session() as writer_session:
        s=replace(writer_session,actor=admitted.actor,os_login=admitted.os_login,memberships=admitted.memberships)
        output=persistence.execute(s,ctx,plan)
        assert output.changed and output.effect.financial.bank_total==1000
        event=s.company.raw.execute('SELECT actor_id,actor_kind,on_behalf_of,interface FROM audit_events WHERE id=?',(output.effect.audit_event_id,)).fetchone()
        assert event==(people['agent'],'agent',people['first'],'http')
    live_plan=plan
    plan,admitted=observe(people['bot'],monkeypatch,lambda s:prepare(s,'bound-revoked'),people['company'])
    with writer(root) as db:
        db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id==credential.token_id).values(revoked_at=clock.now_iso()))
    before=_storage(root,database_path(client))
    with pytest.raises(BookflowError) as error:
        with driver.session() as writer_session:
            s=replace(writer_session,actor=admitted.actor,os_login=admitted.os_login,memberships=admitted.memberships)
            persistence.execute(s,ctx,plan)
    assert error.value.code=='E_UNAUTHENTICATED'
    assert _storage(root,database_path(client))==before
    # Exact recovery skips the old business guard, never current credentials.
    for path in ('prepare','execute'):
        with pytest.raises(BookflowError) as denied:
            with driver.session() as writer_session:
                s=replace(writer_session,actor=admitted.actor,os_login=admitted.os_login,memberships=admitted.memberships)
                if path=='execute':persistence.execute(s,ctx,live_plan)
                else:lifecycle.prepare(s,ctx,lifecycle.INPUTS['post'].model_validate_json(live_plan.input_json),'post',binding=credential)
        assert denied.value.code=='E_UNAUTHENTICATED'
        assert _storage(root,database_path(client))==before
    credential=_credential(client,people['agent'],people['first'])
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==people['first']).values(role='readonly'))
    before=_storage(root,database_path(client))
    def read_but_not_write(s):
        target=InspectionRoot(kind='deposit',id=output.current.id)
        recipe,facts=history.capture(s,target,credential)
        assert history.issue(s,recipe,facts,credential)
        for key in ('bound-live','never-posted-readonly'):
            body=lifecycle.INPUTS['post'].model_validate(dict(operation_key=key,document=document))
            with pytest.raises(BookflowError) as denied:lifecycle.prepare(s,ctx,body,'post',binding=credential)
            assert denied.value.code=='E_PERMISSION' and not denied.value.details
    observe(people['bot'],monkeypatch,read_but_not_write,people['company'])
    assert _storage(root,database_path(client))==before


def test_explicit_empty_guard_is_invalid_not_an_absent_guard(client,sale,driver,monkeypatch):
    from bookflow.company import deposit_lifecycle as lifecycle
    from bookflow.core.context import Context,Interface
    posted=driver.run('post',dict(operation_key='empty-guard-post',document=additional_document(client,sale)))
    before=driver.dump()
    def check(s):
        inp=lifecycle.INPUTS['void'].model_validate(dict(operation_key='empty-guard-void',deposit=posted.current.id,expected_version=1,dependency_guard=''))
        with pytest.raises(BookflowError) as error:
            lifecycle.prepare(s,Context.new(Interface.python,'empty guard',reason='Explicitly reject malformed guard'),inp,'void')
        assert error.value.code=='E_VALIDATION' and error.value.details=={'field':'dependency_guard','reason':'invalid_guard'}
    observe(client,monkeypatch,check)
    assert driver.dump()==before
