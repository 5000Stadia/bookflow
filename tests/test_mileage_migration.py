"""co0083 adds the mileage tables and admits kind `mileage` into the customer-work family.

The rebuild itself is co0077's, proven there on a populated database (and here through the head of the
chain by tests/test_sales_order_migration.py, whose populated co0072 file now passes through co0083 too).
What this file pins is co0083's own contract: its frozen text is what the metadata declares, a fresh
database reaches the head twice without change, and the guards admit a mileage document in its two states
and its two billing relations, and nothing else.
"""
import importlib
import sqlite3

import pytest

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_estimate_void_migration import _chain

M = importlib.import_module('bookflow.storage.company_migrations.versions.0083_mileage')


def _constraint(table_, name):
    return str(next(x for x in table_.constraints if getattr(x, 'name', None) == name).sqltext)


def test_the_frozen_replacements_are_what_the_metadata_declares():
    assert M.revision == 'co0083' and M.revision in _chain() and HEADS['company'] == 'co0083'
    assert M.CHANGED == ('work_documents', 'work_billing_conversions')
    assert "'sales_order','mileage')" in _constraint(c.work_documents, 'ck_work_kind')
    assert "(kind = 'mileage' AND status IN ('recorded','voided'))" in _constraint(c.work_documents, 'ck_work_status')
    assert "'mileage_invoice','mileage_sales_receipt')" in _constraint(c.work_billing_conversions, 'ck_work_billing_relation')
    for name, (before, after) in M.TRIGGERS.items():
        assert before != after and name in before and 'mileage' in after and 'mileage' not in before
    for table in ('vehicles', 'mileage_rates', 'mileage_trips', 'mileage_reimbursements'):
        assert table in c.metadata.tables


def test_a_fresh_database_reaches_the_head_and_admits_a_mileage_document_and_nothing_else(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before, 'reaching the head twice must change nothing'
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')

        def document(identity, kind, status):
            raw.execute("INSERT INTO work_documents (id, version, created_at, created_by, created_via, updated_at,"
                        " updated_by, updated_via, kind, number, current_revision_id, status, active, estimate_group_id)"
                        " VALUES (?, 1, 'now', 'U', 'cli', 'now', 'U', 'cli', ?, ?, 'R'||?, ?, 1, NULL)",
                        (identity, kind, 'N-' + identity, identity, status))

        for status in ('recorded', 'voided'):
            document('M' + status, 'mileage', status)
        for identity, kind, status in (('M1', 'mileage', 'open'), ('M2', 'mileage', 'draft'), ('X1', 'trip', 'recorded')):
            with pytest.raises(sqlite3.IntegrityError):
                document(identity, kind, status)
        sql = dict(raw.execute("SELECT name, sql FROM sqlite_schema WHERE name IN ('work_revision_kind',"
                               " 'work_billing_conversion_birth', 'work_billing_conversions')").fetchall())
        assert sql['work_revision_kind'] == M.TRIGGERS['work_revision_kind'][1]
        assert sql['work_billing_conversion_birth'] == M.TRIGGERS['work_billing_conversion_birth'][1]
        assert "'mileage_sales_receipt'" in sql['work_billing_conversions']
