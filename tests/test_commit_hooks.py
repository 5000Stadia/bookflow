"""Actual commit owners; observers delegate to production admission closure."""
import ast
from pathlib import Path
import sqlite3
import threading

import pytest

from bookflow import BookflowError
from bookflow.core.commit_hooks import CommitHooks, Impact, OWNERS, Outcome
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, Interface, client_version
from bookflow.core.host import Host
from bookflow.core.publication_admission import AdmissionCancelled


# Actual function path, owner, literal commit count at the accepted foundation.
# Autocommit refresh and visibility-only paths are asserted separately below.
EXPECTED = {
    ('core/dispatch.py', '_apply'): ('dispatch.apply', 8),
    ('core/dispatch.py', 'open_company'): ('dispatch.open_company', 1),
    ('core/dispatch.py', '_record_migration'): ('dispatch.record_migration', 1),
    ('core/dispatch.py', '_complete_trash'): ('dispatch.complete_trash', 1),
    ('adapters/http/app.py', '_issue_session.job'): ('http.issue_session', 1),
    ('adapters/http/app.py', '_revoke'): ('http.revoke', 1),
    ('adapters/http/auth.py', 'refresh_token'): ('http.refresh_token', 0),
    ('commands/company_cmds.py', 'apply_company_rename'): ('company.rename', 2),
    ('commands/hub_cmds.py', 'run_init'): ('hub.init', 2),
    ('commands/hub_cmds.py', 'apply_upgrade'): ('hub.upgrade', 2),
    ('commands/hub_cmds.py', 'apply_org_rename'): ('hub.org_rename', 1),
    ('commands/hub_cmds.py', 'apply_company_attach'): ('hub.attach_projection', 0),
    ('commands/hub_cmds.py', 'apply_company_new'): ('hub.company_new', 1),
    ('commands/hub_cmds.py', 'apply_demo_reset'): ('hub.demo_reset', 1),
    ('commands/host_cmds.py', 'migrate_everything.job'): ('host.migrate_everything', 1),
    ('core/host.py', 'Host._sweep_on_writer'): ('host.sweep', 1),
    ('hub/moves.py', 'complete_company_move'): ('moves.company', 1),
    ('hub/moves.py', 'complete_org_move'): ('moves.org', 1),
    ('storage/migrate.py', 'migrate_to_head'): ('migration.head', 1),
    ('storage/migrate.py', 'migrate_company'): ('migration.company', 1),
    ('company/rollout.py', 'create_company_folder'): ('rollout.company', 1),
    ('company/profiles.py', 'apply_standard_profile'): ('profiles.standard', 1),
    ('company/attachment_gc.py', '_finish'): ('attachments.finish', 1),
    ('company/attachment_gc.py', 'collect'): ('attachments.collect', 1),
    ('core/config.py', 'Config.flush_pending'): ('config.flush', 1),
}


def test_manual_actual_owner_inventory():
    import bookflow
    root = Path(bookflow.__file__).parent
    assert set(OWNERS) == {owner for owner, count in EXPECTED.values()}
    assert OWNERS['http.refresh_token'] is Impact.LIVENESS
    assert OWNERS['host.sweep'] is Impact.EXPIRED
    assert OWNERS['config.flush'] is Impact.PROJECTION
    for (file, qual), (owner, count) in EXPECTED.items():
        node = ast.parse((root / file).read_text())
        for part in qual.split('.'):
            node = next(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == part)
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        assert sum(n.func.attr == 'commit' for n in calls) == count, (file, qual)
        assert any(n.func.attr == 'operation' and ast.literal_eval(n.args[0]) == owner for n in calls), (file, qual)
        for n in calls:
            if n.func.attr == 'commit':
                assert ast.literal_eval(n.args[1]) == owner
        if owner in {'http.refresh_token', 'hub.attach_projection'}:
            assert any(n.func.attr == 'autocommit' and ast.literal_eval(n.args[1]) == owner for n in calls)
    # Adding a textual commit outside the single hook executor must fail this
    # inventory, even if it is not listed in EXPECTED.
    actual = []
    for file in root.rglob('*.py'):
        for n in ast.walk(ast.parse(file.read_text())):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            if n.func.attr == 'execute' and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'COMMIT':
                actual.append(str(file.relative_to(root)))
            if n.func.attr == 'commit' and not n.args:
                assert str(file.relative_to(root)) == 'storage/traced_sqlite.py'
    assert actual == ['core/commit_hooks.py']
    for file in root.rglob('*.py'):
        if file.name == 'commit_hooks.py':
            continue
        for node in ast.walk(ast.parse(file.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
            if name in {'migrate_to_head', 'flush_pending', 'refresh_token', 'apply_standard_profile'}:
                assert any(k.arg == 'commits' for k in node.keywords), (file, name, node.lineno)

    # A1's real queue forwards the owned hook, not merely an inventory label.
    host = (root / 'core/host.py').read_text()
    assert 'auth.refresh_token(self._ensure_hub_on_writer(), token_id, kind, commits=self._commit_hooks)' in host


@pytest.fixture
def owner_host(root, client):
    cid = client.company.list()['items'][0]['company_id']
    login = os_login()
    uid = Config.load(root / 'config.toml').user_table(login)['user_id']
    host = Host(root, version=client_version())
    host.start()
    try:
        yield host, uid, login, cid
    finally:
        host.stop()


def command(owner_host, name, data, *, company=True, key=None):
    from bookflow.core import registry
    from bookflow.core.dispatch import execute
    host, uid, login, cid = owner_host
    ctx = Context.new(Interface.python, 'commit-owner-witness')
    if key:
        ctx = ctx.model_copy(update={'idempotency_key': key})
    return host.run_write(uid, login, lambda s: execute(
        registry.get(name), data, ctx, s, company_selector=cid if company else None))


def observe(monkeypatch, host, *, fail_owner=None):
    events, barriers = [], {}
    real_before, real_after = CommitHooks._before, CommitHooks._after
    def before(self, operation, owner, impact):
        real_before(self, operation, owner, impact)
        if self is not host._commit_hooks:
            return
        if operation.barrier is not None:
            barriers[id(operation)] = operation.barrier
        events.append(('before', operation, owner, operation.committed))
        if owner == fail_owner:
            raise sqlite3.OperationalError('injected before actual commit')
    def after(self, operation, outcome):
        real_after(self, operation, outcome)
        if self is not host._commit_hooks:
            return
        events.append(('after', operation, outcome, operation.committed))
        barriers.pop(id(operation), None)
    monkeypatch.setattr(CommitHooks, '_before', before)
    monkeypatch.setattr(CommitHooks, '_after', after)
    return events, barriers


def test_actual_company_partial_write_keeps_durable_effect(owner_host, monkeypatch):
    from bookflow.core import dispatch
    host, uid, login, cid = owner_host
    events, barriers = observe(monkeypatch, host)
    generation = host.publication_admission.begin_validation()
    def fail(s, ctx):
        raise sqlite3.OperationalError('injected registry repair failure')
    monkeypatch.setattr(dispatch, '_repair_projection', fail)
    with pytest.raises(BookflowError) as exc:
        command(owner_host, 'company update', {'legal_name': 'Durable company truth'})
    assert exc.value.code == 'E_PARTIAL_WRITE'
    assert exc.value.details['durable'] == ['company_info']
    assert exc.value.details['cause'] == 'E_IO'
    assert [(e[2], e[3]) for e in events if e[0] == 'after' and e[1].owner == 'dispatch.apply'] == [(Outcome.PARTIAL, 1)]
    assert host.submit(lambda: host._companies[cid].raw.execute('SELECT legal_name FROM company_info').fetchone()[0]) == 'Durable company truth'
    assert host.submit(lambda: host._hub.raw.execute('SELECT legal_name FROM companies WHERE id=?', (cid,)).fetchone()[0]) != 'Durable company truth'
    with pytest.raises(AdmissionCancelled):
        host.publication_admission.admit(generation, 'old')
    assert not barriers and host._commit_hooks._operation is None


def test_actual_rollback_after_close_does_not_resurrect(owner_host, monkeypatch):
    host = owner_host[0]
    before = host.submit(lambda: host._hub.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0])
    events, barriers = observe(monkeypatch, host, fail_owner='dispatch.apply')
    generation = host.publication_admission.begin_validation()
    with pytest.raises((BookflowError, sqlite3.OperationalError)):
        command(owner_host, 'token issue', {'label': 'must roll back'}, company=False)
    assert host.submit(lambda: host._hub.raw.execute("SELECT count(*) FROM api_tokens WHERE label='must roll back'").fetchone()[0]) == 0
    assert host.submit(lambda: host._hub.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]) == before
    assert any(e[0] == 'after' and e[2] is Outcome.ROLLED_BACK for e in events)
    with pytest.raises(AdmissionCancelled):
        host.publication_admission.admit(generation, 'old')
    assert not barriers


def test_actual_nested_rename_one_operation_and_no_loop_ack(owner_host, monkeypatch):
    import asyncio
    host, uid, login, cid = owner_host
    # This loop never runs. Actual writer COMMIT/finish must not require it.
    loop = asyncio.new_event_loop()
    events, barriers = observe(monkeypatch, host)
    wake = threading.Event()
    subscription, _ = host.subscribe('hub', loop, wake)
    try:
        out = command(owner_host, 'company rename', {'name': 'Owned renamed company', 'move': True})
        assert out['display_name'] == 'Owned renamed company'
        assert not wake.is_set() and loop._ready  # notification parked, writer completed
    finally:
        host.unsubscribe(subscription)
        loop.close()
    before = [e for e in events if e[0] == 'before']
    assert {'company.rename', 'moves.company'} <= {e[2] for e in before}
    relevant = [e for e in before if e[2] in {'company.rename', 'moves.company'}]
    assert len({id(e[1]) for e in relevant}) == 1
    assert sum(e[0] == 'after' and e[1] is relevant[0][1] for e in events) == 1
    assert relevant[0][3] == 0
    assert not barriers and host._commit_hooks._operation is None


def test_config_pending_is_effective_before_file_projection(owner_host, monkeypatch):
    host, uid, login, cid = owner_host
    events, barriers = observe(monkeypatch, host)
    def fail_save(self):
        # The intent is already committed and visible through normal Config.load.
        assert Config.load(self.path).user_table(login)['default_company'] == cid
        assert host._hub.raw.execute('SELECT count(*) FROM pending_config').fetchone()[0] == 1
        raise OSError('injected file projection failure')
    monkeypatch.setattr(Config, 'save', fail_save)
    with pytest.raises(BookflowError) as exc:
        command(owner_host, 'company use', {'company': cid}, company=False)
    assert exc.value.code == 'E_PARTIAL_WRITE'
    assert Config.load(host.data_root / 'config.toml').user_table(login)['default_company'] == cid
    assert any(e[0] == 'before' and e[2] == 'dispatch.apply' for e in events)
    assert any(e[0] == 'before' and e[2] == 'config.flush' for e in events)
    assert not barriers


def test_refresh_autocommit_extends_liveness_without_invalidation(owner_host, monkeypatch):
    from bookflow.adapters.http import auth
    host, uid, login, cid = owner_host
    # Use the actual session-issuance owner, followed by its actual refresh queue.
    from bookflow.adapters.http.app import _issue_session
    command(owner_host, 'user set-password', {'username': login, 'password': 'correct-horse-battery'}, company=False)
    password = host.submit(lambda: host._hub.raw.execute('SELECT password_hash FROM users WHERE id=?', (uid,)).fetchone()[0])
    secret = _issue_session(host, uid, username=login, expected_password_hash=password)
    from bookflow.hub.credentials import token_hash
    token = host.submit(lambda: host._hub.raw.execute('SELECT id FROM api_tokens WHERE token_hash=?', (token_hash(secret),)).fetchone()[0])
    old = host.submit(lambda: host._hub.raw.execute('SELECT expires_at FROM api_tokens WHERE id=?', (token,)).fetchone()[0])
    events, barriers = observe(monkeypatch, host)
    generation = host.publication_admission.begin_validation()
    from bookflow.core import clock
    from datetime import timedelta
    later = clock.now() + timedelta(seconds=45)
    monkeypatch.setattr(clock, 'now', lambda: later)
    assert host.enqueue_token_refresh(token, 'session')
    host.submit(lambda: None)  # FIFO completion, no polling or sleep.
    row = host.submit(lambda: host._hub.raw.execute('SELECT last_used_at,expires_at FROM api_tokens WHERE id=?', (token,)).fetchone())
    assert row[0] == clock.now_iso() and row[1] > old
    assert [(e[2], e[3]) for e in events if e[0] == 'after'] == [(Outcome.COMMITTED, 1)]
    assert events[0][2] == 'http.refresh_token'
    host.publication_admission.finish(host.publication_admission.admit(generation, 'still-current'))
    assert not barriers


def test_default_host_hooks_close_gate_at_actual_commit(owner_host):
    host = owner_host[0]
    generation = host.publication_admission.begin_validation()
    command(owner_host, 'company update', {'fax': 'nonactivating increment'})
    with pytest.raises(AdmissionCancelled):
        host.publication_admission.admit(generation, 'old-generation')
    current=host.publication_admission.begin_validation()
    host.publication_admission.finish(host.publication_admission.admit(current, 'reopened'))
    assert host._commit_hooks._operation is None


def test_actual_login_logout_and_missing_password_roll_back(owner_host, monkeypatch):
    from bookflow.adapters.http.app import _issue_session, _revoke
    from bookflow.hub.credentials import token_hash
    host, uid, login, cid = owner_host
    command(owner_host, 'user set-password', {'username': login, 'password': 'correct-horse-battery'}, company=False)
    password = host.submit(lambda: host._hub.raw.execute('SELECT password_hash FROM users WHERE id=?', (uid,)).fetchone()[0])
    events, barriers = observe(monkeypatch, host)
    secret = _issue_session(host, uid, username=login, expected_password_hash=password)
    token = host.submit(lambda: host._hub.raw.execute('SELECT id FROM api_tokens WHERE token_hash=?', (token_hash(secret),)).fetchone()[0])
    result = host.run_write(uid, login, lambda s: _revoke(s, token, 'logout'))
    assert result == {'ok': True}
    assert host.submit(lambda: host._hub.raw.execute('SELECT revoked_at FROM api_tokens WHERE id=?', (token,)).fetchone()[0])
    assert {'http.issue_session', 'http.revoke'} <= {e[2] for e in events if e[0] == 'before'}
    count = host.submit(lambda: host._hub.raw.execute('SELECT count(*) FROM api_tokens').fetchone()[0])
    with pytest.raises(BookflowError) as exc:
        _issue_session(host, uid, username=login, expected_password_hash='stale password')
    assert exc.value.code == 'E_LOGIN_FAILED'
    assert host.submit(lambda: host._hub.raw.execute('SELECT count(*) FROM api_tokens').fetchone()[0]) == count
    assert host.submit(lambda: host._hub.write_transaction) is False
    assert not barriers


def test_actual_noop_repair_and_advisory_branches(owner_host, monkeypatch):
    host = owner_host[0]
    events, barriers = observe(monkeypatch, host)
    command(owner_host, 'company update', {'fax': 'same'})
    events.clear()
    out = command(owner_host, 'company update', {'fax': 'same'})
    assert out['changed_fields'] == []
    assert any(e[0] == 'before' and e[2] == 'dispatch.apply' for e in events)
    events.clear()
    out = command(owner_host, 'presence set', {'record_type': 'company_info', 'record_id': owner_host[3]})
    assert out['editing_by']
    assert any(e[0] == 'after' and e[1].owner == 'dispatch.apply' and e[3] == 2 for e in events)
    events.clear()
    out = command(owner_host, 'organization new', {'name': 'Idempotent owner'}, company=False, key='stable-create')
    assert not out['idempotent_replay']
    assert any(e[0] == 'before' and e[2] == 'dispatch.apply' for e in events)
    events.clear()
    replay = command(owner_host, 'organization new', {'name': 'Idempotent owner'}, company=False, key='stable-create')
    assert replay['idempotent_replay']
    assert not [e for e in events if e[0] == 'before' and e[2] == 'dispatch.apply']
    assert not barriers


def test_existing_host_cleanup_resolves_failed_commit(owner_host, monkeypatch):
    """Direct logout has no local rollback handler; the real writer cleans it."""
    from bookflow.adapters.http.app import _revoke
    host, uid, login, cid = owner_host
    issued = command(owner_host, 'token issue', {'label': 'cleanup witness'}, company=False)
    events, barriers = observe(monkeypatch, host, fail_owner='http.revoke')
    with pytest.raises(sqlite3.OperationalError):
        host.run_write(uid, login, lambda s: _revoke(s, issued['token_id'], 'logout'))
    assert host.submit(lambda: host._hub.raw.execute('SELECT revoked_at FROM api_tokens WHERE id=?', (issued['token_id'],)).fetchone()[0]) is None
    assert [(e[2], e[3]) for e in events if e[0] == 'after'] == [(Outcome.ROLLED_BACK, 0)]
    assert not barriers and host._commit_hooks._operation is None


def test_hosted_owner_refuses_wrong_or_missing_writer_before_operation(owner_host):
    host=owner_host[0]
    for hooks in (host._commit_hooks,CommitHooks(host.publication_admission)):
        with pytest.raises(RuntimeError,match='owning writer thread'):
            with hooks.operation('dispatch.apply'):
                pytest.fail('wrong writer entered')
        assert hooks._operation is None and hooks._depth==0
    current=host.publication_admission.begin_validation()
    host.publication_admission.finish(host.publication_admission.admit(current,'still-open'))
