"""co0013 appends policy without altering existing rows, DDL or local objects."""
import sqlite3
import pytest
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_progress_billing_schema import co11_template, prior


def test_populated_local_company_preservation(prior, monkeypatch):
    with open_database(prior, writable=True) as db:
        with monkeypatch.context() as historical:
            historical.setitem(HEADS, 'company', 'co0012')
            migrate_to_head(db, 'company', None)
        raw = db.raw
        raw.execute("ALTER TABLE company_info ADD COLUMN local_note TEXT NOT NULL DEFAULT 'local' CHECK(length(local_note)>0)")
        raw.execute('ALTER TABLE company_info ADD COLUMN local_size INTEGER GENERATED ALWAYS AS (length(local_note)) VIRTUAL')
        raw.execute('CREATE INDEX pref_local_index ON company_info(local_note)')
        raw.execute('CREATE VIEW pref_local_view AS SELECT local_note,local_size FROM company_info')
        raw.execute("CREATE TRIGGER pref_local_guard BEFORE UPDATE OF local_note ON company_info BEGIN SELECT RAISE(ABORT,'local guard'); END")
        columns = [r[1] for r in raw.execute('PRAGMA table_xinfo(company_info)')]
        tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before = {name: raw.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in tables}
        objects = raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT IN ('company_info','alembic_version') ORDER BY type,name").fetchall()
        assert migrate_to_head(db, 'company', None) == ('co0012', 'co0013')
        assert [r[1] for r in raw.execute('PRAGMA table_xinfo(company_info)')][:len(columns)] == columns
        assert raw.execute('SELECT ' + ','.join(columns) + ' FROM company_info ORDER BY rowid').fetchall() == before['company_info']
        for name in tables:
            if name not in ('company_info', 'alembic_version'):
                assert raw.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() == before[name]
        assert raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT IN ('company_info','alembic_version') ORDER BY type,name").fetchall() == objects
        assert raw.execute('SELECT estimates_enabled,progress_billing_enabled,close_estimates_after_billing FROM company_info').fetchall() == [(1,1,0)]
        with pytest.raises(sqlite3.IntegrityError, match='local guard'):
            raw.execute("UPDATE company_info SET local_note='changed'")
        for field in ('estimates_enabled', 'progress_billing_enabled', 'close_estimates_after_billing'):
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(f'UPDATE company_info SET {field}=NULL')
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_fresh_policy_columns(tmp_path):
    with open_database(tmp_path/'company.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, 'co0013')
        fields = {row[1]: row for row in db.raw.execute('PRAGMA table_info(company_info)')}
        for name, default in [('estimates_enabled','1'), ('progress_billing_enabled','1'), ('close_estimates_after_billing','0')]:
            assert fields[name][2:5] == ('BOOLEAN', 1, "'" + default + "'")
