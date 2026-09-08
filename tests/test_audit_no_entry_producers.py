"""Stored producer evidence only; no historical visibility policy is asserted."""
from pathlib import Path
import sqlite3

import bookflow

from bookflow.core.audit import decode_snapshot
from bookflow.core.config import Config, os_login
from tests.test_payment_receipts import method

COMPANY = 'Demo Plumbing Co'


def _rows(path):
    """Exact retained rows, including audit blobs; never open a default data root."""
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return {name: db.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid').fetchall()
                for (name,) in tables}


def _audit(path):
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return ({row['id']: dict(row) for row in db.execute('SELECT * FROM audit_events')},
                {row['id']: dict(row) for row in db.execute('SELECT * FROM audit_entries')})


def _new_event(path, before, command):
    events, entries = _audit(path)
    old_events, old_entries = before
    assert {key: events[key] for key in old_events} == old_events
    assert {key: entries[key] for key in old_entries} == old_entries
    added = set(events) - set(old_events)
    assert len(added) == 1
    event = events[added.pop()]
    assert event['command'] == command
    new_entries = [entries[key] for key in set(entries) - set(old_entries)]
    assert all(row['event_id'] == event['id'] for row in new_entries)
    return event, new_entries


def test_real_init_creation_restoration_and_mapped_repeats(tmp_path, monkeypatch):
    root = tmp_path / 'bootstrap'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    created = client.init()
    assert created['created'] is True
    hub = root / 'hub.db'
    event, entries = _new_event(hub, ({}, {}), 'init')
    assert event['actor_id'] == created['user_id']
    assert {(row['record_type'], row['record_id'], row['action']) for row in entries} == {
        ('user', created['user_id'], 'create'), ('user', created['system_user_id'], 'create')}
    assert len(entries) == 2
    before = _rows(hub)
    assert client.init()['created'] is False
    assert _rows(hub) == before

    # Authentic documented recovery trigger on this disposable root only.
    (root / 'config.toml').unlink()
    baseline = _audit(hub)
    restored = client.init()
    assert restored['created'] is False and restored['user_id'] == created['user_id']
    event, entries = _new_event(hub, baseline, 'init')
    assert entries == []
    assert event['actor_id'] == created['user_id'] and event['actor_kind'] == 'human'
    assert event['summary'] == 'restored the local login mapping'
    assert Config.load(root / 'config.toml').user_table(os_login())['user_id'] == created['user_id']
    after = _rows(hub)
    assert {k: v for k, v in after.items() if k != 'audit_events'} == {
        k: v for k, v in before.items() if k != 'audit_events'}
    assert client.init()['created'] is False
    assert _rows(hub) == after


def test_real_company_use_emits_empty_entry_event_even_for_same_default(client, root):
    # Warm the copied hub through its ordinary owner before taking the baseline.
    company = next(row for row in client.company.list()['items'] if row['display_name'] == COMPANY)
    hub = root / 'hub.db'
    for _ in range(2):
        before = _rows(hub)
        audit_before = _audit(hub)
        result = client.run('company use', {'company': company['company_id']})
        assert result['company_id'] == company['company_id']
        event, entries = _new_event(hub, audit_before, 'company use')
        assert entries == []
        assert event['actor_id'] == Config.load(root / 'config.toml').user_table(os_login())['user_id']
        assert Config.load(root / 'config.toml').user_table(os_login())['default_company'] == company['company_id']
        after = _rows(hub)
        assert {k: v for k, v in after.items() if k != 'audit_events'} == {
            k: v for k, v in before.items() if k != 'audit_events'}
    # Deliberately no assertion about the currently unsupported projection.


def test_real_no_effect_payment_operation_has_entry_but_permanent_retry_has_no_event(client, root):
    customer = client.customer.create(name='No-entry receipt owner', company=COMPANY)['id']
    received = client.run('payment receive', dict(customer=customer, date='2026-06-02', amount='1.50',
        payment_method=method(client), operation_key='no-entry-receive'), company=COMPANY)
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    before = _rows(path)
    audit_before = _audit(path)
    args = dict(payment=received['id'], expected_version=received['version'], operation_key='no-entry-no-effect')
    result = client.run('payment update', args, reason='Confirm receipt', company=COMPANY)
    assert result['changed'] is False and result['version'] == received['version']
    event, entries = _new_event(path, audit_before, 'payment update')
    assert sorted((row['record_type'], row['action']) for row in entries) == [
        ('payment_operation', 'create'), ('payment_operation_item', 'create')]
    captured = decode_snapshot(next(row['after'] for row in entries if row['record_type'] == 'payment_operation'))
    item = decode_snapshot(next(row['after'] for row in entries if row['record_type'] == 'payment_operation_item'))
    assert item['operation_id'] == captured['id'] and item['audit_event_id'] == event['id']
    assert item['kind'] == 'source_components' and item['ordinal'] == 1
    assert item['item_snapshot'] == received['current']['components'][0]
    assert item['item_snapshot']['received_minor_units'] == 150
    assert item['item_snapshot']['available_minor_units'] == 150
    assert item['item_snapshot']['applied_minor_units'] == 0
    assert captured['operation_key'] == args['operation_key']
    assert captured['audit_event_id'] == event['id']
    assert captured['effect_snapshot']['effect'] == result['effect']
    after = _rows(path)
    assert len(after['payment_operations']) == len(before['payment_operations']) + 1
    assert len(after['payment_operation_items']) == len(before['payment_operation_items']) + 1
    assert {k: v for k, v in after.items() if k not in ('audit_events', 'audit_entries', 'payment_operations', 'payment_operation_items', 'principals')} == {
        k: v for k, v in before.items() if k not in ('audit_events', 'audit_entries', 'payment_operations', 'payment_operation_items', 'principals')}
    # Ordinary dispatch refreshes only the current principal's last_seen_at,
    # even for a newly recorded financial no-effect operation (info.upsert_principal).
    for old, new in zip(before['principals'], after['principals'], strict=True):
        if old[0] == event['actor_id']:
            assert new[:-1] == old[:-1] and new[-1] >= old[-1]
        else:
            assert new == old
    audit_after = _audit(path)
    hub_before = _rows(root / 'hub.db')
    replay = client.run('payment update', args, reason='Confirm receipt', company=COMPANY)
    assert replay['idempotent_replay'] is True and replay['changed'] is False
    assert replay['effect'] == result['effect']
    assert _audit(path) == audit_after
    assert _rows(path) == after
    assert _rows(root / 'hub.db') == hub_before

