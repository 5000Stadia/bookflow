"""The two registered deposit reads, actually callable, on every real surface.

Registration was row A; this module is the evidence that a user gets a document.
Native, CLI, the local command socket and HTTP all run the same reader-bound
owner, and none of them reaches the plan-time refusal.
"""
import json
import os

import pytest

import bookflow
from bookflow.company import deposit_public_reads as reads
from bookflow.company import deposit_read_models as m
from bookflow.core import registry
from bookflow.core.deposit_request import COMMANDS as DEPOSIT_COMMANDS
from bookflow.core.errors import BookflowError
from tests import deposit_public_support as support
from tests.conftest import Cli
from tests.deposit_public_support import denying

VOLATILE = ('current_observed_at',)


@pytest.fixture(scope='module')
def world(public_deposit_world):
    yield public_deposit_world
    support.stop(public_deposit_world)


def stable(document):
    return {key: value for key, value in document.items() if key not in VOLATILE}


def native(world, command, raw):
    """The offline dispatch path: no host, so the reader takes the root lock."""
    return bookflow.connect(data_root=str(world['root'])).run(command, raw, company=world['cid'])


def cli(world, *args):
    return Cli(world['root']).json(*args, '--company', world['cid'])


# --------------------------------------------------------------- four surfaces


def test_every_surface_returns_the_same_public_document(world):
    support.stop(world)
    deposit = world['deposit']
    offline_show = native(world, 'deposit show', {'deposit': deposit})
    offline_items = native(world, 'deposit items', {'deposit': deposit, 'kind': 'additional'})
    cli_show = cli(world, 'deposit', 'show', deposit)
    cli_items = cli(world, 'deposit', 'items', deposit, '--kind', 'additional')
    assert offline_show['deposit_id'] == deposit and offline_show['totals']['bank_total']['minor_units'] == 6300
    assert offline_items['total_count'] == 2 and len(offline_items['items']) == 2
    assert registry.REGISTRY['deposit show'].output_model.model_validate_json(json.dumps(offline_show))
    assert registry.REGISTRY['deposit items'].output_model.model_validate_json(json.dumps(offline_items))
    support.start(world, serving=True)
    call = support.api(world)
    http_show = call('deposit.show', {'deposit': deposit}, company=world['cid'])
    http_items = call('deposit.items', {'deposit': deposit, 'kind': 'additional'}, company=world['cid'])
    # A CLI subprocess has its own pid, so this one really travels the local
    # command socket into the running host rather than taking the lock.
    socket_show = cli(world, 'deposit', 'show', deposit)
    socket_items = cli(world, 'deposit', 'items', deposit, '--kind', 'additional')
    for name, group in (('show', (offline_show, cli_show, http_show, socket_show)),
                        ('items', (offline_items, cli_items, http_items, socket_items))):
        first = stable(group[0])
        for other in group[1:]:
            assert stable(other) == first, name
        for document in group:
            assert set(document) - set(first) == set(VOLATILE) or set(document) == set(first) | set(VOLATILE)


def test_the_plan_time_refusal_is_unreachable_from_every_registered_route(world, monkeypatch):
    """Enforcement, not declaration: the guard exists and nothing can reach it.

    The registered planner still refuses, exactly as the projected history
    family's does. What makes the commands callable is that dispatch and the
    hosted adapter select the reader-bound owner before either generic path.
    """
    seen = []
    from bookflow.core import dispatch

    def watched(cmd, *args, **kwargs):
        seen.append(cmd.name)
        return original(cmd, *args, **kwargs)
    original = dispatch.execute
    monkeypatch.setattr(dispatch, 'execute', watched)
    monkeypatch.setattr('bookflow.adapters.http.execution.execute', watched)
    for name in DEPOSIT_COMMANDS:
        with pytest.raises(BookflowError) as caught:
            registry.REGISTRY[name].plan(None, None, None)
        assert caught.value.code == 'E_INTERNAL'
    support.stop(world)
    native(world, 'deposit show', {'deposit': world['deposit']})
    # A control on both routes, so an inert watcher cannot pass this test.
    native(world, 'company show', {})
    support.start(world, serving=True)
    call = support.api(world)
    call('deposit.show', {'deposit': world['deposit']}, company=world['cid'])
    call('company.show', {}, company=world['cid'])
    assert not (set(seen) & DEPOSIT_COMMANDS), seen
    assert seen.count('company show') == 2, seen


def test_the_offline_branch_is_selected_before_any_second_root_lock(world, monkeypatch):
    from bookflow.core import deposit_offline
    calls = []
    original = deposit_offline.run_command

    def watched(*args, **kwargs):
        calls.append(args[1].name)
        return original(*args, **kwargs)
    monkeypatch.setattr(deposit_offline, 'run_command', watched)
    support.stop(world)
    native(world, 'deposit items', {'deposit': world['deposit'], 'kind': 'sources'})
    assert calls == ['deposit items']


# ------------------------------------------------------------------- bindings


def test_hosted_execution_passes_the_original_credential_not_a_token_binding(world, monkeypatch):
    """A TokenBinding is a private administration value the owners refuse."""
    from bookflow.adapters.http.app import Credential
    from bookflow.core import publication_deposit
    from bookflow.core.publication import OSBinding
    from bookflow.hub.identity_admin import TokenBinding
    support.stop(world)
    support.start(world, serving=True)
    seen = []
    original = publication_deposit.execute_detail

    def watched(reader, request, binding, **kwargs):
        seen.append(binding)
        assert type(binding) is not TokenBinding
        # The reader itself binds through the private TokenBinding, and the
        # bridge refuses to hand that out as an execution binding.
        with pytest.raises(Exception):
            reader.execution_binding()
        return original(reader, request, binding, **kwargs)
    monkeypatch.setattr(publication_deposit, 'execute_detail', watched)
    monkeypatch.setattr('bookflow.adapters.http.execution.publication_deposit', publication_deposit,
                        raising=False)
    support.api(world)('deposit.show', {'deposit': world['deposit']}, company=world['cid'])
    assert len(seen) == 1 and type(seen[0]) is Credential
    assert seen[0].token_id and seen[0].on_behalf_of is None


def test_the_offline_binding_comes_from_the_reader_bridge(world, monkeypatch):
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core import publication_deposit
    from bookflow.core.publication import OSBinding
    support.stop(world)
    seen = []
    original = publication_deposit.execute_detail

    def watched(reader, request, binding, **kwargs):
        seen.append((binding, reader.execution_binding()))
        return original(reader, request, binding, **kwargs)
    monkeypatch.setattr(publication_deposit, 'execute_detail', watched)
    monkeypatch.setattr('bookflow.core.deposit_offline.publication_deposit', publication_deposit)
    native(world, 'deposit show', {'deposit': world['deposit']})
    assert len(seen) == 1
    binding, bridged = seen[0]
    assert type(binding) is OSBinding and binding is bridged
    assert binding.token_id is None and binding.root == world['root']


def test_the_audience_refuses_a_binding_that_is_not_this_readers(world):
    from bookflow.company import deposit_public_authority as pa
    from bookflow.core import identity_admin_binding as ib
    from bookflow.core.config import os_login
    from bookflow.core.context import Context
    from bookflow.core.publication import OSBinding
    from bookflow.hub.identity_admin import TokenBinding
    support.stop(world)
    support.start(world, serving=True)
    ctx = Context.new('python', 'Binding agreement witness')
    admitted = OSBinding.capture(world['host'], os_login())
    token = TokenBinding(world['issued']['secret'], world['issued']['token_id'], admitted.user_id,
                         'bearer', None, world['root'] / 'hub.db', ctx.request_id)
    with ib.hosted_reader(world['host'], admitted, request_id=ctx.request_id) as reader:
        with pytest.raises(BookflowError) as caught:
            pa.audience(reader, token)
        assert caught.value.code == 'E_UNAUTHENTICATED'
        # The identity carries everything a synthesized binding would use, and
        # a value built from it is still not this reader's own producer.
        identity = reader.authenticate()
        forged = OSBinding(world['root'], identity.actor, identity.os_login, identity.actor_kind,
                           identity.hub_admin, 'someone-else', None)
        with pytest.raises(BookflowError):
            pa.audience(reader, forged)


# --------------------------------------------------------- denial and cleanup


@pytest.mark.parametrize('command,raw', [
    ('deposit show', {}),
    ('deposit items', {'kind': 'sources'}),
    ('deposit items', {'kind': 'cash_allocations'}),
])
def test_a_denied_connected_capability_is_refused_on_every_surface(world, command, raw):
    support.stop(world)
    support.start(world, serving=True)
    with denying(world, ('customer-work',)):
        call = support.api(world)
        body = dict(raw, deposit=world['work'])
        hosted = call(command.replace(' ', '.'), body, company=world['cid'], expect='error')
        assert hosted['code'] == 'E_RECORD_NOT_FOUND' and not hosted['details']
        # The same reader still reads a deposit with no work-linked dependency.
        assert call(command.replace(' ', '.'), dict(raw, deposit=world['deposit']),
                    company=world['cid'])['deposit_id'] == world['deposit']
        arguments = ['deposit', command.split(' ')[1], world['work']]
        if 'kind' in raw:
            arguments += ['--kind', raw['kind']]
        document, code = Cli(world['root']).error(*arguments, '--company', world['cid'])
        assert document['code'] == 'E_RECORD_NOT_FOUND' and not document['details']
    support.stop(world)
    with denying_offline(world):
        with pytest.raises(BookflowError) as caught:
            native(world, command, dict(raw, deposit=world['work']))
        assert caught.value.code == 'E_RECORD_NOT_FOUND' and not caught.value.details


import contextlib


@contextlib.contextmanager
def denying_offline(world):
    """Apply the deny with the host up, then hand the lock back to the reader."""
    support.start(world, serving=True)
    support.set_denies(world, ('customer-work',))
    support.stop(world)
    try:
        yield
    finally:
        support.start(world, serving=True)
        support.set_denies(world, ())
        support.stop(world)


def test_readers_are_released_after_success_and_after_refusal(world):
    support.stop(world)
    support.start(world, serving=True)
    call = support.api(world)
    assert world['host']._readers_attached == 0
    call('deposit.show', {'deposit': world['deposit']}, company=world['cid'])
    assert world['host']._readers_attached == 0
    absent = call('deposit.show', {'deposit': support.ABSENT}, company=world['cid'], expect='error')
    assert absent['code'] == 'E_RECORD_NOT_FOUND' and not absent['details']
    assert world['host']._readers_attached == 0
    stale = call('deposit.items', {'deposit': world['deposit'], 'kind': 'sources',
                                   'page': {'cursor': 'not-a-cursor'}},
                 company=world['cid'], expect='error')
    assert stale['code'] == 'E_VALIDATION' and stale['details'] == {'field': 'cursor'}
    assert world['host']._readers_attached == 0
    # And the host still stops cleanly, which a leaked reader would prevent.
    support.stop(world)


# ------------------------------------------------------------- request shaping


def test_context_company_and_dry_run_rules_match_the_registered_contract(world):
    support.stop(world)
    support.start(world, serving=True)
    call = support.api(world)
    rejected = call('deposit.show', {'deposit': world['deposit'], 'reason': 'in the body'},
                    company=world['cid'], expect='error')
    assert rejected['code'] == 'E_CONTEXT_IN_INPUT' and rejected['details']['fields'] == ['reason']
    unknown = call('deposit.show', {'deposit': world['deposit'], 'unexpected': 1},
                   company=world['cid'], expect='error')
    assert unknown['code'] == 'E_VALIDATION'
    support.stop(world)
    with pytest.raises(BookflowError) as caught:
        bookflow.connect(data_root=str(world['root'])).run('deposit show', {'deposit': world['deposit']})
    assert caught.value.code == 'E_COMPANY_NOT_FOUND' and caught.value.details == {'source': 'none'}
    with pytest.raises(BookflowError) as caught:
        bookflow.connect(data_root=str(world['root'])).run(
            'deposit show', {'deposit': world['deposit']}, company='No Such Company')
    assert caught.value.code == 'E_COMPANY_NOT_FOUND'
    document, code = Cli(world['root']).error('deposit', 'show', world['deposit'],
                                              '--company', world['cid'], '--dry-run')
    assert document['code'] == 'E_USAGE'


# ------------------------------------------------------- actor and principal


def bound_pair(world, tag, *, deny_principal=False):
    """A real agent bound to a real human principal, with a real bearer credential.

    The permission owner treats an edit to an agent's own membership as a loss of
    its authority and suspends it, so this witness denies the human principal:
    that is the case where the actor still admits the capability and only the
    intersection can refuse.
    """
    from bookflow.core import clock
    from bookflow.hub import schema as h
    from tests.conftest import make_actor
    from tests.test_row7_credentials import writer
    support.stop(world)
    company = world['cid']
    principal = make_actor(world['root'], 'deposit-principal-' + tag, company_role=(company, 'owner'))
    agent = make_actor(world['root'], 'deposit-agent-' + tag, kind='agent',
                       owner_user_id=principal, company_role=(company, 'owner'))
    now = clock.now_iso()
    with writer(world['root']) as db:
        if deny_principal:
            # Denied from the start. Editing either subject's membership after a
            # pair is bound invalidates that pair's authority by design, so the
            # capability question can only be asked of a pair bound this way.
            assert db.conn.execute(h.memberships.update().where(
                h.memberships.c.user_id == principal,
                h.memberships.c.scope_type == 'company',
                h.memberships.c.scope_id == company).values(
                denies='["customer-work"]')).rowcount == 1
        db.conn.execute(h.agent_authority.insert().values(
            agent_user_id=agent, epoch=1, suspended_at=None, suspension_reason=None,
            version=1, updated_at=now, updated_by=principal, updated_via='cli',
            authorized_at=now, authorized_by=principal, permitted_use_at=now,
            fresh_context_ack_at=now, fresh_context_required=0))
        db.conn.execute(h.agent_principals.insert().values(
            agent_user_id=agent, principal_user_id=principal, assigned_by=principal,
            assigned_at=now, revoked_at=None))
    secret = world['client'].token.issue(user=agent, principal=principal,
                                         label='Bound deposit reader ' + tag)['secret']
    support.start(world, serving=True)
    from fastapi.testclient import TestClient
    client = TestClient(world['handle'].app)

    def call(deposit):
        return client.post(f"/companies/{world['cid']}/commands/deposit.show",
                           json={'deposit': deposit},
                           headers={'Authorization': 'Bearer ' + secret}).json()
    return dict(agent=agent, principal=principal, call=call)


def denies_of(world, user_id):
    import sqlite3
    connection = sqlite3.connect(world['root'] / 'hub.db')
    try:
        return connection.execute(
            "SELECT denies FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
            (user_id, world['cid'])).fetchone()[0]
    finally:
        connection.close()


def test_the_intersection_of_actor_and_principal_governs_the_read(world):
    """The bound human's deny refuses the read even though the actor admits it."""
    allowed = bound_pair(world, 'open')
    assert allowed['call'](world['work'])['deposit_id'] == world['work']
    assert allowed['call'](world['deposit'])['deposit_id'] == world['deposit']
    denied = bound_pair(world, 'bound', deny_principal=True)
    # The actor's own membership still admits the connected capability.
    assert denies_of(world, denied['agent']) in (None, '[]')
    assert 'customer-work' in denies_of(world, denied['principal'])
    refused = denied['call'](world['work'])
    assert refused['code'] == 'E_RECORD_NOT_FOUND' and not refused['details']
    # A deposit with no work-linked dependency stays readable for the same pair.
    assert denied['call'](world['deposit'])['deposit_id'] == world['deposit']
    support.stop(world)
