"""R74: a permission observation is reused across transactions only behind a fresh token read.

The oracle throughout: in every new transaction the reused observation equals a fresh
complete read, and the decision a person sees follows the change on the very next
transaction -- through real commands, a second connection and a second process.
"""
import importlib
import sqlite3
from pathlib import Path

import pytest

import bookflow
from bookflow.core.config import Config, os_login
from bookflow.hub import permission_catalog as c, permission_runtime as runtime, permission_snapshot as snap
from bookflow.hub.agent_authority import AdministrationError
from bookflow.storage.engine import open_database
from tests.conftest import Cli, make_legacy

READ = c.Requirement('ledger.read', 'member')
POST = c.Requirement('ledger.post', 'standard')
MIGRATION = importlib.import_module('bookflow.storage.hub_migrations.versions.0014_authority_generation')


@pytest.fixture
def loads(monkeypatch):
    """Count complete root loads; the reuse key does not depend on this private loader."""
    runtime.forget_reused_observations()
    count = [0]
    original = snap._load_root

    def counted(*args, **kwargs):
        count[0] += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(snap, '_load_root', counted)
    yield count
    runtime.forget_reused_observations()


@pytest.fixture
def world(root):
    client = bookflow.connect(data_root=str(root))
    company = client.company.list()['items'][0]['company_id']
    clerk = client.run('user add', {'username': 'clerk', 'password': 'clerk-password-1', 'company': company,
                                    'role': 'standard'})['user_id']
    installer = Config.load(root / 'config.toml').user_table(os_login())['user_id']
    return dict(root=root, client=client, company=company, clerk=clerk, installer=installer)


def admitted(root, actor, company, requirement=READ, principal=None):
    """One new read transaction on a new connection: the next thing any request would do."""
    with open_database(Path(root) / 'hub.db', False) as tx:
        reused = runtime._operation_observation(tx)
        assert reused == runtime.observe_current(tx), 'a reused observation differs from a fresh read'
        try:
            runtime.require_company(tx, actor=actor, principal=principal, company=company, requirement=requirement)
            return True
        except AdministrationError:
            return False


def stamp(root):
    with open_database(Path(root) / 'hub.db', False) as tx:
        return runtime.authority_stamp(tx)


def test_fresh_install_carries_every_trigger_the_runtime_relies_on(root):
    with open_database(root / 'hub.db', False) as tx:
        assert runtime._triggers_intact(tx)
        assert runtime.authority_stamp(tx) is not None
    assert MIGRATION.TABLES == runtime.AUTHORITY_TABLES
    assert {f'authority_generation_{t}_{e.lower()}': (t, MIGRATION.trigger_sql(t, e))
            for t in MIGRATION.TABLES for e in MIGRATION.EVENTS} == runtime.AUTHORITY_TRIGGERS


def test_unchanged_authority_is_reused_across_transactions(world, loads):
    root, company = world['root'], world['company']
    assert admitted(root, world['clerk'], company)
    first = loads[0]
    for _ in range(5):
        assert admitted(root, world['clerk'], company)
        assert admitted(root, world['installer'], company, POST)
    # Each probe also takes one deliberately fresh read for the oracle; the reused path took none.
    assert loads[0] - first == 10


@pytest.mark.parametrize('table', runtime.AUTHORITY_TABLES)
def test_every_write_to_every_input_table_redraws_the_token_in_that_transaction(world, table):
    root = world['root']
    db = sqlite3.connect(root / 'hub.db', isolation_level=None)
    try:
        columns = [row[1] for row in db.execute(f'PRAGMA table_info({table})')]
        key = columns[0]
        db.execute('BEGIN IMMEDIATE')
        seen = [db.execute('SELECT token,generation FROM authority_generation').fetchone()]
        row = db.execute(f'SELECT * FROM {table} ORDER BY rowid LIMIT 1').fetchone()
        assert row is not None, table
        db.execute(f'UPDATE {table} SET {key}={key} WHERE rowid=(SELECT min(rowid) FROM {table})')
        seen.append(db.execute('SELECT token,generation FROM authority_generation').fetchone())
        db.execute(f'DELETE FROM {table} WHERE rowid=(SELECT min(rowid) FROM {table})')
        seen.append(db.execute('SELECT token,generation FROM authority_generation').fetchone())
        db.execute(f'INSERT INTO {table} VALUES ({",".join("?" * len(row))})', row)
        seen.append(db.execute('SELECT token,generation FROM authority_generation').fetchone())
        assert len({token for token, _ in seen}) == 4 and [g for _, g in seen] == list(range(seen[0][1], seen[0][1] + 4))
        db.execute('ROLLBACK')
        assert db.execute('SELECT token,generation FROM authority_generation').fetchone() == seen[0]
    finally:
        db.close()


def test_membership_role_and_capability_changes_reach_the_next_transaction(world, loads):
    root, run, company, clerk = world['root'], world['client'].run, world['company'], world['clerk']
    assert admitted(root, clerk, company) and admitted(root, clerk, company, POST)
    run('membership grant', {'user': clerk, 'company': company, 'role': 'readonly'})
    assert admitted(root, clerk, company) and not admitted(root, clerk, company, POST)  # role change
    version = run('membership list', {'user': clerk})['items'][0]['version']
    run('membership grant', {'user': clerk, 'company': company, 'role': 'standard', 'expected_version': version,
                             'grants': [], 'denies': ['ledger.post']})
    assert admitted(root, clerk, company) and not admitted(root, clerk, company, POST)  # capability deny
    version = run('membership list', {'user': clerk})['items'][0]['version']
    run('membership grant', {'user': clerk, 'company': company, 'role': 'standard', 'expected_version': version,
                             'grants': [], 'denies': []})
    assert admitted(root, clerk, company, POST)  # deny lifted
    run('membership revoke', {'user': clerk, 'company': company})
    assert not admitted(root, clerk, company)  # revocation takes effect at once
    run('membership grant', {'user': clerk, 'company': company, 'role': 'standard'})
    assert admitted(root, clerk, company, POST)


def test_agent_assignment_suspension_and_authorization_reach_the_next_transaction(world, loads):
    root, run, company = world['root'], world['client'].run, world['company']
    p, q = (run('user add', {'username': name, 'password': name + '-password-1', 'company': company,
                             'role': 'owner'})['user_id'] for name in ('principal-p', 'principal-q'))
    agent = run('agent create', {'username': 'probe-agent', 'owner': 'principal-p'}, reason='probe')['agent']['agent_id']
    run('agent assign', {'agent': agent, 'principal': p, 'confirm_permitted_use': True}, reason='probe')
    run('membership grant', {'user': agent, 'company': company, 'role': 'standard'})
    assert not admitted(root, agent, company, POST, principal=p)  # assigned, not yet authorized
    authorized = run('agent authorize', {'agent': agent, 'confirm_permitted_use': True}, reason='probe')
    assert not authorized['agent']['authority']['suspended']
    assert admitted(root, agent, company, POST, principal=p)
    assert not admitted(root, agent, company, POST, principal=q)  # not assigned to Q
    run('agent assign', {'agent': agent, 'principal': q, 'confirm_permitted_use': True}, reason='probe')
    assert admitted(root, agent, company, POST, principal=q)
    unassigned = run('agent unassign', {'agent': agent, 'principal': q}, reason='probe')
    assert unassigned['agent']['authority']['suspended']  # binding loss suspends the whole agent
    assert not admitted(root, agent, company, POST, principal=p)
    run('agent authorize', {'agent': agent, 'confirm_permitted_use': True, 'acknowledge_fresh_context': True},
        reason='probe')
    assert admitted(root, agent, company, POST, principal=p)
    run('membership revoke', {'user': agent, 'company': company})  # own authority loss suspends
    assert not admitted(root, agent, company, POST, principal=p)


def test_user_deactivation_written_by_any_path_reaches_the_next_transaction(world, loads):
    root, company, clerk = world['root'], world['company'], world['clerk']
    assert admitted(root, clerk, company)
    with sqlite3.connect(root / 'hub.db') as db:
        db.execute('UPDATE users SET active=0 WHERE id=?', (clerk,))
    assert not admitted(root, clerk, company)
    with sqlite3.connect(root / 'hub.db') as db:
        db.execute('UPDATE users SET active=1 WHERE id=?', (clerk,))
    assert admitted(root, clerk, company)


@pytest.mark.legacy_permissions
def test_catalog_activation_reaches_the_next_transaction(root, loads):
    client = bookflow.connect(data_root=str(root))
    company = client.company.list()['items'][0]['company_id']
    installer = Config.load(root / 'config.toml').user_table(os_login())['user_id']
    assert admitted(root, installer, company)
    before = stamp(root)
    with open_database(root / 'hub.db', False) as tx:
        legacy = runtime._operation_observation(tx)
    state = client.permission.show()
    client.permission.activate(expected_generation=state['generation'], expected_catalog_sha256=state['catalog_sha256'])
    assert stamp(root)[0] != before[0]
    with open_database(root / 'hub.db', False) as tx:
        activated = runtime._operation_observation(tx)
        assert activated is not legacy and activated == runtime.observe_current(tx)
    assert admitted(root, installer, company)


def test_a_change_committed_by_another_process_reaches_the_next_transaction(world, loads):
    root, company, clerk = world['root'], world['company'], world['clerk']
    assert admitted(root, clerk, company)
    Cli(root).run('membership', 'revoke', 'clerk', '--company', company, '--json')
    assert not admitted(root, clerk, company)


def test_a_change_during_a_transaction_behaves_as_a_fresh_read_does(world, loads):
    root, company, clerk = world['root'], world['company'], world['clerk']
    assert admitted(root, clerk, company)
    with open_database(root / 'hub.db', False) as tx:
        runtime.require_company(tx, actor=clerk, principal=None, company=company, requirement=READ)
        with sqlite3.connect(root / 'hub.db') as other:  # a second connection commits a revocation
            other.execute("UPDATE memberships SET revoked_at='2026-09-28T00:00:00.000Z' WHERE user_id=?", (clerk,))
        # Inside the open snapshot both paths still see the facts it began with, exactly as before R74.
        assert runtime._operation_observation(tx) == runtime.observe_current(tx)
        runtime.require_company(tx, actor=clerk, principal=None, company=company, requirement=READ)
    assert not admitted(root, clerk, company)


def test_own_write_and_savepoint_rollback_in_one_transaction(world, loads):
    root, company, clerk = world['root'], world['company'], world['clerk']
    assert admitted(root, clerk, company)
    with open_database(root / 'hub.db', True) as tx:
        tx.raw.execute('BEGIN IMMEDIATE')
        tx.raw.execute('SAVEPOINT probe')
        tx.raw.execute("UPDATE memberships SET revoked_at='2026-09-28T00:00:00.000Z' WHERE user_id=?", (clerk,))
        assert runtime._operation_observation(tx) == runtime.observe_current(tx)
        with pytest.raises(AdministrationError):
            runtime.require_company(tx, actor=clerk, principal=None, company=company, requirement=READ)
        tx.raw.execute('ROLLBACK TO probe')
        assert runtime._operation_observation(tx) == runtime.observe_current(tx)
        runtime.require_company(tx, actor=clerk, principal=None, company=company, requirement=READ)
        tx.raw.execute('ROLLBACK')
    assert admitted(root, clerk, company)


def test_a_dropped_trigger_turns_reuse_off_rather_than_serving_stale_facts(world, loads):
    root, company, clerk = world['root'], world['company'], world['clerk']
    assert admitted(root, clerk, company)
    with sqlite3.connect(root / 'hub.db') as db:
        db.execute('DROP TRIGGER authority_generation_memberships_update')
    for _ in range(2):
        assert admitted(root, clerk, company)
    first = loads[0]
    assert admitted(root, clerk, company)
    assert loads[0] - first == 2  # the probe's fresh read and a fresh reuse-path read: nothing was stored
    with sqlite3.connect(root / 'hub.db') as db:  # a write the token no longer sees
        db.execute("UPDATE memberships SET revoked_at='2026-09-28T00:00:00.000Z' WHERE user_id=?", (clerk,))
    assert not admitted(root, clerk, company)


def test_a_hub_without_the_generation_observes_fresh_every_time(world, loads):
    root, company, clerk = world['root'], world['company'], world['clerk']
    with sqlite3.connect(root / 'hub.db') as db:
        for name in runtime.AUTHORITY_TRIGGERS:
            db.execute('DROP TRIGGER ' + name)
        db.execute('DROP TABLE authority_generation')
    assert stamp(root) is None
    with open_database(root / 'hub.db', False) as tx:
        before = loads[0]
        runtime._stamped_observation(tx)
        runtime._stamped_observation(tx)
        assert loads[0] - before == 2
