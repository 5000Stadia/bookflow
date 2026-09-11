"""co0039 adds one nullable column to `accounts` and changes nothing else.

The column is where an account declares which section of the statement of cash flows it
belongs to. Null means "take the section this account's type gives", which is what every
company had before the column existed, so the questions this file asks are: does the shipped
column match what the metadata declares, does the constraint admit exactly the three sections
and nothing else, does a populated older database come out the other side with every value and
every local object intact, and does it come out declaring nothing -- because a backfill here
would move an existing company's statement without anyone asking for it.
"""
import importlib
import sqlite3
from pathlib import Path
from typing import get_args

import pytest

from bookflow.company import accounts as chart
from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_bill_payment_migration import _rebuilt_since

M = importlib.import_module(
    'bookflow.storage.company_migrations.versions.0039_account_cash_flow_section')

PRIOR = 'co0033'


def _chain():
    """Every company revision the shipped migrations declare, read from the files."""
    versions = Path(__file__).resolve().parents[1] / 'src/bookflow/storage/company_migrations/versions'
    return {importlib.import_module('bookflow.storage.company_migrations.versions.' + path.stem).revision
            for path in sorted(versions.glob('[0-9]*.py'))}


def _at(path, revision):
    """A company database stopped part-way along the chain, the way the runner builds one."""
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        db.raw.execute('PRAGMA foreign_keys=OFF')
        try:
            command.upgrade(_config('company', db.conn), revision)
            db.raw.commit()
        finally:
            db.raw.execute('PRAGMA foreign_keys=ON')


def _account(raw, identity, kind, section=None):
    raw.execute("INSERT INTO accounts (id, version, created_at, created_by, created_via,"
                " updated_at, updated_by, updated_via, active, name, name_key, full_name,"
                " full_name_key, depth, path, type, currency, track_reimbursable_expenses"
                + (', cash_flow_section' if section is not None else '') +
                ") VALUES (?, 1, 'now', 'U', 'cli', 'now', 'U', 'cli', 1, ?, ?, ?, ?, 1, ?, ?,"
                " 'USD', 0" + (', ?' if section is not None else '') + ')',
                (identity, identity, identity.lower(), identity, identity.lower(),
                 '/' + identity + '/', kind) + ((section,) if section is not None else ()))


def test_the_frozen_column_is_the_one_the_metadata_now_declares():
    """The migration's frozen text and the live metadata have to say the same thing."""
    column = c.accounts.c[M.COLUMN]
    assert str(column.type) == 'VARCHAR(16)' and column.nullable
    assert M.SECTIONS == get_args(chart.CashFlowSection)
    declared = next(constraint for constraint in c.accounts.constraints
                    if getattr(constraint, 'name', None) == M.CONSTRAINT)
    for section in M.SECTIONS:
        assert f"'{section}'" in str(declared.sqltext) and f"'{section}'" in M.DDL[0]
    assert len(M.DDL) == 1 and M.DDL[0].startswith(f'ALTER TABLE {M.TABLE} ADD COLUMN {M.COLUMN} ')
    # No rebuild: nothing on `accounts` is dropped, so no temporary table exists to name.
    assert '_co0039_' not in M.DDL[0] and not hasattr(M, 'CHANGED')


def test_a_fresh_database_reaches_the_head_carrying_the_column(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        # This migration is in the chain, not necessarily its end - a later revision may
        # follow it. Claiming to be the head is a pin that every new migration falsifies.
        assert M.revision in _chain() and M.down_revision in _chain()
        column = next(row for row in db.raw.execute(f'PRAGMA table_info({M.TABLE})')
                      if row[1] == M.COLUMN)
        assert column[2] == 'VARCHAR(16)' and column[3] == 0 and column[4] is None
        assert M.CONSTRAINT in db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='accounts'").fetchone()[0]
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_constraint_admits_every_section_and_nothing_else(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        for index, section in enumerate(M.SECTIONS):
            _account(raw, f'Declared {index}', 'fixed_asset', section)
        _account(raw, 'Silent', 'fixed_asset')
        assert raw.execute('SELECT cash_flow_section FROM accounts WHERE id=?',
                           ('Silent',)).fetchone() == (None,)
        for refused in ('Operating', 'operations', '', 'none'):
            with pytest.raises(sqlite3.IntegrityError):
                _account(raw, 'Refused ' + refused, 'fixed_asset', refused)
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("UPDATE accounts SET cash_flow_section='elsewhere' WHERE id='Silent'")
        raw.commit()


def test_a_populated_co0033_database_keeps_every_value_and_declares_nothing(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PRIOR)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PRIOR,)
        assert M.COLUMN not in {row[1] for row in raw.execute(f'PRAGMA table_info({M.TABLE})')}
        raw.execute('PRAGMA foreign_keys=OFF')
        for identity, kind in (('Equipment', 'fixed_asset'), ('Accumulated', 'fixed_asset'),
                               ('Checking', 'bank'), ('Wear', 'expense'), ('Loan', 'long_term_liability')):
            _account(raw, identity, kind)
        # One row carrying an embedded NUL, which a quote()-only copy would truncate,
        # plus local objects of every kind on the table this revision touches.
        raw.execute('UPDATE accounts SET note=? WHERE id=?', ('A\x00B', 'Equipment'))
        raw.execute('CREATE TABLE local_account_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_account_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_account_expression ON local_account_bytes (length(t))')
        raw.execute('CREATE INDEX local_account_on_accounts ON accounts (type, full_name)')
        raw.execute('CREATE VIEW local_account_view AS SELECT id, type FROM accounts')
        raw.execute("CREATE TRIGGER local_account_trigger AFTER INSERT ON accounts"
                    " BEGIN SELECT 1; END")
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        # The new column is the one difference this revision is allowed to make, so the
        # value witness omits it and the assertions below cover it directly.
        before = {name: table(raw, name, omit_columns=(M.COLUMN,) if name == M.TABLE else ())
                  for name in names}
        # Whatever the migrations after this one rebuild - derived, because a hand-listed
        # exclusion goes stale the moment another revision lands.
        rebuilt = _rebuilt_since(M.revision)
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() if row[1] not in rebuilt}

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PRIOR, HEADS['company'])
        assert {name: table(db.raw, name, omit_columns=(M.COLUMN,) if name == M.TABLE else ())
                for name in names} == before
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert {row for row in objects if row[1] != M.TABLE} <= after, \
            'a local table, index, view or trigger was lost'
        # Nothing is backfilled, so every upgraded account still takes the section its
        # type gives and no company's statement of cash flows moves under it.
        rows = db.raw.execute(f'SELECT type, {M.COLUMN} FROM accounts').fetchall()
        assert rows and {section for _kind, section in rows} == {None}
        assert {kind: chart.cash_flow_section(kind, section) for kind, section in rows} == {
            kind: chart.CASH_FLOW_SECTION_BY_TYPE.get(kind) for kind, _section in rows}
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]

    saved = list((tmp_path / 'backups').glob(f'*-from-{PRIOR}.db'))
    assert len(saved) == 1


def test_a_database_that_already_carries_the_column_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PRIOR)
    with sqlite3.connect(path) as raw:
        raw.execute(f'ALTER TABLE {M.TABLE} ADD COLUMN {M.COLUMN} VARCHAR(16)')
        raw.commit()
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as caught:  # the runner wraps whatever the migration raised
            migrate_to_head(db, 'company', tmp_path / 'backups')
    assert 'co0039' in str(caught.value) or 'co0039' in str(getattr(caught.value, '__cause__', ''))
