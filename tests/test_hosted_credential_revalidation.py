"""Credentials remain eligible at execution, after HTTP admission and queueing."""
import concurrent.futures
import json
import threading
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from bookflow.adapters.http import auth
from bookflow.core import registry
from bookflow.core import clock
from bookflow.core.ids import new_id
from bookflow.hub import schema as h
from bookflow.hub.users import common
from tests.test_row3_host import PASSWORD, WB, hosted


def test_queued_revoked_bearer(hosted, monkeypatch):
    observer = hosted.ok("token.issue", {"label": "witness observer"})["secret"]
    host = hosted.handle.host
    before = hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id)
    revoking, queued, release = threading.Event(), threading.Event(), threading.Event()
    cmd = registry.get('token revoke')
    original_plan, original_put = cmd.plan, host._queue.put

    def blocked_revoke(inp, ctx, session):
        plan = original_plan(inp, ctx, session)
        revoking.set()
        assert release.wait(8), 'witness barrier timed out'
        return plan

    def observed_put(job, *args, **kwargs):
        result = original_put(job, *args, **kwargs)
        if revoking.is_set():
            queued.set()
        return result

    monkeypatch.setattr(cmd, 'plan', blocked_revoke)
    monkeypatch.setattr(host._queue, 'put', observed_put)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        revoke = pool.submit(hosted.call, 'token.revoke', {'token': hosted.token})
        try:
            assert revoking.wait(5)
            write = pool.submit(hosted.call, 'company.update', {'fax': 'queued-credential-witness'}, company=hosted.company_id)
            assert queued.wait(5), 'write was not queued behind revocation'
        finally:
            release.set()
        revoke_response, write_response = revoke.result(10), write.result(10)
    after = hosted.ok("company.show", company=hosted.company_id, headers={"Authorization": f"Bearer {observer}"})["info"]
    subsequent = hosted.call('company.list')
    receipt = {'revocation_status': revoke_response.status_code,
               'queued_write_status': write_response.status_code,
               'later_call_status': subsequent.status_code,
               'queued_write_changed_fax': after['fax'] == 'queued-credential-witness'}
    print(json.dumps(receipt))
    assert revoke_response.status_code == 200 and subsequent.status_code == 401
    assert write_response.status_code == 401, 'revoked queued credential executed after revocation committed'
    assert not receipt['queued_write_changed_fax']
    assert hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id,
                     headers={'Authorization': f'Bearer {observer}'}) == before


@contextmanager
def queued_change(host, monkeypatch, change):
    """Commit the fixture authority change ahead of an already admitted call."""
    started, queued, release = threading.Event(), threading.Event(), threading.Event()
    put = host._queue.put

    def observe(job, *args, **kwargs):
        result = put(job, *args, **kwargs)
        if started.is_set():
            queued.set()
        return result

    def writer():
        started.set()
        assert release.wait(8), 'authority barrier timed out'
        db = host._hub
        db.raw.execute('BEGIN IMMEDIATE')
        try:
            change(db)
            db.raw.execute('COMMIT')
        except BaseException:
            db.raw.execute('ROLLBACK')
            raise

    monkeypatch.setattr(host._queue, 'put', observe)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(host.submit, writer)
        try:
            assert started.wait(5)
            yield pool, queued
        finally:
            release.set()
            changing.result(10)


@pytest.mark.parametrize('kind', ['cookie', 'expired bearer', 'changed token kind'])
def test_queued_cookie_revocation_expiry_and_binding_change(hosted, monkeypatch, kind):
    api = TestClient(hosted.handle.app)
    token_id = hosted.token
    headers = hosted.bearer
    if kind == 'cookie':
        assert api.post('/login', json={'username': hosted.login, 'password': PASSWORD}).status_code == 200
        token_id = next(t['token_id'] for t in hosted.ok('token.list')['items'] if t['kind'] == 'session')
        headers = WB
    host = hosted.handle.host
    before = hosted.info()['info']
    before_events = hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id)

    def change(db):
        values = ({'revoked_at': clock.now_iso()} if kind == 'cookie' else
                  {'kind': 'session'} if kind == 'changed token kind' else
                  {'expires_at': '2000-01-01T00:00:00.000Z'})
        db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == token_id).values(**values))

    observer = hosted.ok('token.issue', {'label': 'still authorized'})['secret']
    with queued_change(host, monkeypatch, change) as (pool, queued):
        pending = pool.submit(api.post, f'/companies/{hosted.company_id}/commands/company.update',
                              json={'fax': 'must not commit'}, headers=headers)
        assert queued.wait(5)
    response = pending.result(10)
    assert response.status_code == 401, response.text
    assert set(response.json()['details']) == {'reason'}
    assert hosted.ok('company.show', company=hosted.company_id,
                     headers={'Authorization': f'Bearer {observer}'})['info'] == before
    assert hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id,
                     headers={'Authorization': f'Bearer {observer}'}) == before_events


def provision_agent(hosted):
    """Fixture administration while public Row7 provisioning is still pending."""
    host = hosted.handle.host
    agent = new_id()

    def setup():
        db = host._hub
        human = auth.resolve_token(db, hosted.secret)['user_id']
        org = db.conn.execute(sa.select(h.companies.c.organization_id).where(
            h.companies.c.id == hosted.company_id)).scalar_one()
        db.raw.execute('BEGIN IMMEDIATE')
        try:
            db.conn.execute(h.users.insert().values(id=agent, kind='agent', username='queue-agent',
                display_name='Queue Agent', owner_user_id=human, password_hash=None, hub_admin=True,
                timezone=None, active=True, **common(human, 'system')))
            db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent, epoch=1,
                suspended_at=None, suspension_reason=None))
            db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent, principal_user_id=human,
                assigned_by=human, assigned_at=clock.now_iso(), revoked_at=None))
            db.conn.execute(h.memberships.insert().values(id=new_id(), user_id=agent,
                scope_type='organization', scope_id=org, role='admin', granted_by=human,
                granted_at=clock.now_iso(), revoked_at=None))
            db.raw.execute('COMMIT')
        except BaseException:
            db.raw.execute('ROLLBACK')
            raise
        return human

    human = host.submit(setup)
    issued = hosted.ok('token.issue', {'user': 'queue-agent', 'principal': hosted.login, 'label': 'queued agent'})
    return human, agent, issued


@pytest.mark.parametrize('state', ['epoch', 'suspended', 'unassigned', 'inactive principal'])
def test_agent_authority_invalidated_while_queued(hosted, monkeypatch, state):
    human, agent, issued = provision_agent(hosted)
    host = hosted.handle.host
    before = hosted.info()['info']
    before_events = hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id)

    def change(db):
        if state == 'epoch':
            statement = h.agent_authority.update().where(h.agent_authority.c.agent_user_id == agent).values(epoch=2)
        elif state == 'suspended':
            statement = h.agent_authority.update().where(h.agent_authority.c.agent_user_id == agent).values(
                suspended_at=clock.now_iso(), suspension_reason='test')
        elif state == 'unassigned':
            statement = h.agent_principals.update().where(h.agent_principals.c.agent_user_id == agent).values(revoked_at=clock.now_iso())
        else:
            statement = h.users.update().where(h.users.c.id == human).values(active=False)
        db.conn.execute(statement)

    with queued_change(host, monkeypatch, change) as (pool, queued):
        pending = pool.submit(hosted.call, 'company.update', {'fax': 'invalid agent'}, company=hosted.company_id,
                              headers={'Authorization': f"Bearer {issued['secret']}", 'X-Bookflow-Reason': 'owner directive'})
        assert queued.wait(5)
    response = pending.result(10)
    assert response.status_code == 401, response.text
    assert set(response.json()['details']) == {'reason'}
    assert all(value not in response.text for value in (human, agent, issued['secret'], issued['token_id']))
    if state == 'inactive principal':
        # Restore this fixture actor only after asserting the queued denial.
        def restore():
            db = host._hub
            db.raw.execute('BEGIN IMMEDIATE')
            db.conn.execute(h.users.update().where(h.users.c.id == human).values(active=True))
            db.raw.execute('COMMIT')
        host.submit(restore)
    assert hosted.info()['info'] == before
    assert hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id) == before_events


@pytest.mark.parametrize('dry_run', [False, True])
def test_reader_rejection_before_plan_releases_ownership(hosted, monkeypatch, dry_run):
    host = hosted.handle.host
    started, release = threading.Event(), threading.Event()
    original_reader = host.reader_session
    command = registry.get('company update' if dry_run else 'company show')
    original_plan = command.plan
    planned = []

    def delayed_reader(*args, **kwargs):
        started.set()
        assert release.wait(8)
        return original_reader(*args, **kwargs)

    def observed_plan(*args, **kwargs):
        planned.append(True)
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(host, 'reader_session', delayed_reader)
    monkeypatch.setattr(command, 'plan', observed_plan)
    path = f'/companies/{hosted.company_id}/commands/company.' + ('update?dry_run=true' if dry_run else 'show')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(hosted.api.post, path, json={'fax': 'preview'} if dry_run else {}, headers=hosted.bearer)
        try:
            assert started.wait(5)
            # The write path does not use reader_session; admission uses _reader_hub.
            assert hosted.call('token.revoke', {'token': hosted.token}).status_code == 200
        finally:
            release.set()
        response = pending.result(10)
    assert response.status_code == 401, response.text
    assert not planned
    assert host._readers_attached == 0


def test_queued_advisory_revalidates_before_planning(hosted, monkeypatch):
    host = hosted.handle.host
    cmd = registry.get('presence set')
    planned = []
    original = cmd.plan

    def observed(*args, **kwargs):
        planned.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(cmd, 'plan', observed)

    def revoke(db):
        db.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == hosted.token).values(revoked_at=clock.now_iso()))

    with queued_change(host, monkeypatch, revoke) as (pool, queued):
        pending = pool.submit(hosted.call, 'presence.set',
                              {'record_type': 'company_info', 'record_id': hosted.company_id},
                              company=hosted.company_id)
        assert queued.wait(5)
    assert pending.result(10).status_code == 401
    assert not planned
