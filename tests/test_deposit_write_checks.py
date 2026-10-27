"""R180 increment 3: the deposit lookup indexes (co0099) and the scoped foreign key check.

co0099 adds three indexes and changes no row. The foreign key check every deposit write makes
before releasing its savepoint now reads only the tables holding a deferred key and the tables
the write wrote, instead of the whole company database: a dangling reference planted in any of
those tables is still refused, exactly as the whole check refused it.
"""
import importlib
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookflow.core.errors import BookflowError
from tests.test_deposit_command import COMPANY, books, receipts_in_undeposited_funds  # noqa: F401 - fixture

MISSING = 'ZZZZZZZZZZZZZZZZZZZZZZZZZZ'


def _company_db(root):
    return next(Path(root).glob('organizations/*/*/company.db'))


def _deposit(books, key='checked'):
    client = books['client']
    receipts_in_undeposited_funds(books)
    page = client.run('deposit sources', dict(date='2026-06-30', limit=200), company=COMPANY)
    sources = [dict(source_type=r['source_type'], source=r['source'], expected_version=r['expected_version'])
               for r in page['items']]
    return client.run('deposit post', dict(operation_key=key, document=dict(mode='inline', deposit_to=books['bank'],
        date='2026-06-04', sources=sources)), company=COMPANY, reason='bank the receipts')['deposit']


def _written_by_a_deposit_post(books, monkeypatch):
    """The tables a real deposit post wrote, as its own check was handed them."""
    from bookflow.company import deposit_drafts
    seen, original = [], deposit_drafts.final_foreign_keys
    def spy(s, written=()):
        seen.append(set(written))
        return original(s, written)
    monkeypatch.setattr(deposit_drafts, 'final_foreign_keys', spy)
    _deposit(books)
    monkeypatch.setattr(deposit_drafts, 'final_foreign_keys', original)
    assert len(seen) == 1
    return seen[0]


def _plant(conn, table):
    """Point one stored row of `table` at a record that does not exist; False if it has none to point."""
    for fk in {row[0]: row for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')}.values():
        _, _, parent, column, *_ = fk
        rowid = conn.execute(f'SELECT rowid FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT 1').fetchone()
        if rowid is None:
            continue
        for name, in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
            conn.execute(f'DROP TRIGGER "{name}"')
        conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?', (MISSING, rowid[0]))
        return True
    return False


def _refused(conn, written):
    from bookflow.company.deposit_drafts import final_foreign_keys
    try:
        final_foreign_keys(SimpleNamespace(company=SimpleNamespace(raw=conn)), written)
    except BookflowError as error:
        assert error.code == 'E_VALIDATION' and error.details == {'reason': 'deposit_draft_foreign_key'}
        return True
    return False


def test_a_dangling_reference_in_every_table_a_deposit_write_wrote_or_could_break_is_refused(books, monkeypatch):
    written = _written_by_a_deposit_post(books, monkeypatch)
    assert {'transactions', 'deposit_memberships', 'deposit_operations', 'deposit_operation_targets',
            'posting_lines', 'audit_entries'} <= written
    conn = sqlite3.connect(_company_db(os.environ['BOOKFLOW_DATA_ROOT']), isolation_level=None)
    try:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('PRAGMA ignore_check_constraints=ON')   # a planted id, not a valid row, is the point
        assert not _refused(conn, written), 'healthy books pass the scoped check'
        deferred = {name for name, sql in conn.execute("SELECT name, sql FROM sqlite_schema WHERE type='table'")
                    if 'DEFERRABLE' in (sql or '').upper()}
        planted = []
        for table in sorted(written | deferred):
            conn.execute('BEGIN')
            try:
                if not _plant(conn, table):
                    continue
                planted.append(table)
                # The whole-database check this replaced refuses it, and so does the scoped one.
                assert conn.execute('PRAGMA main.foreign_key_check').fetchone() is not None
                assert _refused(conn, written), table
                if table in deferred:
                    assert _refused(conn, ()), table   # a deferred key is checked whatever was written
            finally:
                conn.execute('ROLLBACK')
        assert {'transactions', 'deposit_memberships', 'deposit_operations', 'deposit_operation_targets',
                'posting_lines'} <= set(planted)
        assert not _refused(conn, written)
    finally:
        conn.close()


# A deferred key the write itself breaks (a draft consumption naming no operation, a draft with
# no receipt) is refused inside the savepoint and rolled back: tests/test_deposit_draft_financial.py
# `test_consumption_persisted_equivalence_rolls_back_with_caller_state` covers both with this check.


def test_co0099_adds_three_indexes_and_changes_no_row(books, tmp_path):
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateIndex
    from bookflow.company import schema as c
    from tests.test_negative_stock_migration import _at
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0099_deposit_indexes')
    indexes = {i.name: i for t in (c.deposit_memberships, c.deposit_operations) for i in t.indexes}
    assert m.INDEXES == tuple(str(CreateIndex(indexes[name]).compile(dialect=dialect())) for name in m.INDEX_NAMES)
    _deposit(books)
    path = _company_db(os.environ['BOOKFLOW_DATA_ROOT'])
    copy = tmp_path / 'rewound99.db'
    with sqlite3.connect(path) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as conn:
        for index in m.INDEX_NAMES:
            conn.execute(f'DROP INDEX {index}')
        conn.execute("UPDATE alembic_version SET version_num='co0098'")
        conn.commit()
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                            "AND name != 'alembic_version'")]
        before = {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}
        assert conn.execute('SELECT count(*) FROM deposit_memberships').fetchone()[0] > 0
    _at(copy, 'co0099')
    with sqlite3.connect(copy) as conn:
        for name in names:
            assert sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) == before[name], name
        stored = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
        assert set(m.INDEX_NAMES) <= stored
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        # The deposit sources walk now reads by index, not by scanning the tables.
        plan = ' '.join(r[-1] for r in conn.execute(
            'EXPLAIN QUERY PLAN SELECT t.transaction_id FROM deposit_operation_targets t JOIN deposit_operations o '
            'ON o.id = t.operation_id WHERE o.transaction_id IN (?, ?)', ('a', 'b')))
        assert 'ix_deposit_operations_transaction' in plan, plan
