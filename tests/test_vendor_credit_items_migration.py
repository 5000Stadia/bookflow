"""co0070 gives a vendor credit its Items tab and the stock ledger a sixth movement kind.

One new table, and two rebuilt because each change lives in a CHECK: ``vendor_credit_profiles``
gains ``item_total_minor_units`` and widens its expense total from positive to nonnegative;
``inventory_movements`` admits ``vendor_return``. The questions are the ones every rebuild has to
answer: does a populated database come out with every row and every unrelated object exactly as
it went in, is the frozen text the one the shipped metadata declares, does a second run change
nothing, and do the new immutability fences hold.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateTable

from bookflow.company import schema
from bookflow.storage.migrate import HEADS, known_revisions
from tests.test_bill_payment_migration import _rebuilt_since
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0070_vendor_credit_items')
PREVIOUS = 'co0069'


def _objects(conn):
    return {name: sql for name, sql in conn.execute(
        'SELECT name, sql FROM sqlite_schema WHERE sql IS NOT NULL')}


def test_the_migration_follows_the_party_merge_revision():
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions('company')
    if HEADS['company'] != M.revision:
        assert M.revision < HEADS['company']


def test_the_frozen_text_is_what_the_shipped_metadata_declares():
    rebuilt = _rebuilt_since(M.revision)
    for table in M.CHANGED:
        if table in rebuilt:
            continue    # a later revision owns today's text for this one
        expected = str(CreateTable(schema.metadata.tables[table]).compile(dialect=dialect())).strip()
        assert M.DDL[table].strip() == expected.replace(
            'CREATE TABLE ' + table + ' (', 'CREATE TABLE _co0070_' + table + ' (', 1)
    for table in M.NEW_TABLES:
        if table not in rebuilt:
            assert M.NEW_DDL[0] == str(CreateTable(schema.metadata.tables[table]).compile(
                dialect=dialect())).strip()


def test_a_populated_previous_revision_upgrades_with_its_rows_and_neighbours_untouched(tmp_path):
    path = tmp_path / 'populated.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('CREATE TABLE local_note (id TEXT PRIMARY KEY, body TEXT)')
        conn.execute("CREATE TRIGGER local_note_guard BEFORE DELETE ON local_note "
                     "BEGIN SELECT RAISE(ABORT, 'local'); END")
        conn.execute("INSERT INTO local_note VALUES ('1', 'kept verbatim')")
        # A credit written before the Items tab existed, with the parents its foreign keys name.
        conn.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                     " on_behalf_of, interface, client_name, client_version, client_host,"
                     " session_id, request_id, idempotency_key, reason, directive_id,"
                     " directive_code, source_ref, undo_of_event_id, summary)"
                     " VALUES ('E1', 1, 'now', 'vendor-credit post', 'U', 'user', NULL, 'cli', 'x', '1',"
                     " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post vendor credit')")
        conn.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                     " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                     " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                     " VALUES ('T1', 1, 'now', 'U', 'cli', 'now', 'U', 'cli', 'vendor_credit',"
                     " 'VC-1', 'R1', 'posted', NULL, NULL, NULL, NULL)")
        conn.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                     " transaction_id, revision_number, supersedes_revision_id, date, number,"
                     " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                     " custom_fields_snapshot, audit_event_id)"
                     " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2026-07-28', 'VC-1',"
                     " 'vendor', 'V1', NULL, 16840, 'USD', '{}', '{}', 'E1')")
        _insert(conn, 'vendors', id='V1', name='Alto', name_key='alto', active=1)
        _insert(conn, 'accounts', id='A1', name='AP', name_key='ap', full_name='AP',
                full_name_key='ap', depth=1, path='AP', type='accounts_payable',
                currency='USD', active=1)
        _insert(conn, 'vendor_credit_profiles', revision_id='R1', transaction_id='T1',
                type='vendor_credit', vendor_id='V1', ap_account_id='A1', expense_total_minor_units=16840,
                profile_snapshot='{}', created_at='2026-07-28T00:00:00Z', created_by='U', created_via='cli')
        conn.commit()
        before = _objects(conn)
        assert 'item_total_minor_units' not in before['vendor_credit_profiles']

    _at(path, M.revision)

    with sqlite3.connect(path) as conn:
        after = _objects(conn)
        assert conn.execute('SELECT body FROM local_note').fetchone() == ('kept verbatim',)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        # The credit kept every value and took an item total of zero, which is what it has.
        assert conn.execute('SELECT expense_total_minor_units, item_total_minor_units, profile_snapshot '
                            'FROM vendor_credit_profiles').fetchall() == [(16840, 0, '{}')]
        owned = {*M.CHANGED, *M.NEW_TABLES, 'ix_vendor_credit_item_lines_item',
                 'vendor_credit_item_lines_immutable_update', 'vendor_credit_item_lines_immutable_delete'}
        assert {k: v for k, v in after.items() if k in before and k not in owned} \
            == {k: v for k, v in before.items() if k not in owned}
        assert set(after) - set(before) == owned - set(M.CHANGED)
        assert set(before) - set(after) == set()
        assert "'vendor_return'" in after['inventory_movements']
        assert 'item_total_minor_units' in after['vendor_credit_profiles']
        # Every trigger and index the rebuilt tables carried came back verbatim.
        for name in ('inventory_movements_match_posting', 'inventory_movements_immutable_update',
                     'inventory_movements_true_up_link', 'vendor_credit_profiles_immutable_update',
                     'vendor_credit_profiles_transaction_id_type', 'ix_inventory_movements_item',
                     'ix_vendor_credit_profiles_reference'):
            assert after[name] == before[name]
        assert not any(name.startswith('_co0070_') for name in after)


def test_reapplying_the_head_changes_nothing(tmp_path):
    path = tmp_path / 'fresh.db'
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        before = _objects(conn)
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        assert _objects(conn) == before


def test_a_reserved_name_already_in_use_stops_the_migration(tmp_path):
    path = tmp_path / 'reserved.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE ix_vendor_credit_item_lines_item (id INTEGER)')
        conn.commit()
    with pytest.raises(Exception) as caught:
        _at(path, M.revision)
    assert 'co0070' in str(caught.value) or 'co0070' in str(getattr(caught.value, '__cause__', ''))


def test_the_credited_item_rows_are_immutable_and_hold_their_shape(tmp_path):
    path = tmp_path / 'fences.db'
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        row = dict(document_line_id='L1', transaction_id='T1', revision_id='R1',
                   created_at='2026-07-28T00:00:00Z', created_by='U1', created_via='cli',
                   item_id='I1', account_id='A1', quantity_microunits=4_000_000,
                   unit_cost_minor_units=4210, amount_minor_units=16840, billable=0,
                   line_snapshot='{}')

        def insert(**changes):
            values = {**row, **changes}
            conn.execute('INSERT INTO vendor_credit_item_lines ({}) VALUES ({})'.format(
                ','.join(values), ','.join('?' * len(values))), tuple(values.values()))

        insert()
        # Nothing about a stored credit is rewritten or removed.
        with pytest.raises(sqlite3.DatabaseError, match='immutable vendor credit history'):
            conn.execute("UPDATE vendor_credit_item_lines SET amount_minor_units = 1")
        with pytest.raises(sqlite3.DatabaseError, match='immutable vendor credit history'):
            conn.execute('DELETE FROM vendor_credit_item_lines')
        # A credited row is worth something and moves some quantity; billable names a job.
        for changes in (dict(document_line_id='L2', amount_minor_units=0),
                        dict(document_line_id='L3', quantity_microunits=0),
                        dict(document_line_id='L4', billable=1)):
            with pytest.raises(sqlite3.IntegrityError):
                insert(**changes)
