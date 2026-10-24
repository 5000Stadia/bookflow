"""External-accountant designations mirrored in the hub (R121).

Two new tables: ``company_accountants`` (each company's active designations) and
``company_accountant_sync`` (whether those rows are known to match the company's books). Each company's
designations are read here from its own books, read-only; a company whose books cannot be read is marked
unverified, and membership changes there are refused until a write to it compares them again. Books from
before co0088 hold no designation. The DDL is literal: this revision is frozen.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from alembic import op

revision = 'hub0016'
down_revision = 'hub0015'
branch_labels = None
depends_on = None
DDL = ('\nCREATE TABLE company_accountants (\n\tcompany_id VARCHAR(26) NOT NULL, \n\tuser_id VARCHAR(26) NOT NULL, \n\tthrough_date VARCHAR(10) NOT NULL, \n\tdesignated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (company_id, user_id)\n)\n\n', "\nCREATE TABLE company_accountant_sync (\n\tcompany_id VARCHAR(26) NOT NULL, \n\tstate VARCHAR(12) NOT NULL, \n\tchecked_at VARCHAR(32) NOT NULL, \n\tproblem VARCHAR(200), \n\tfingerprint VARCHAR(64), \n\tPRIMARY KEY (company_id), \n\tCONSTRAINT ck_company_accountant_sync_state CHECK (state IN ('verified','unverified'))\n)\n\n")
INDEXES = ('CREATE INDEX ix_company_accountants_user ON company_accountants (user_id, company_id)',)
NEW_OBJECTS = ('company_accountants', 'company_accountant_sync', 'ix_company_accountants_user')


def read_designations(path):
    """[(user_id, through_date, updated_at)] of a company's active designations; raises when it cannot be read."""
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=0.2)) as db:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='accountant_designations'").fetchone():
            if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='company_info'").fetchone():
                raise sqlite3.DatabaseError('not a company database')
            return []
        return [tuple(r) for r in db.execute(
            'SELECT user_id, through_date, updated_at FROM accountant_designations WHERE active=1')]


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_OBJECTS):
        raise RuntimeError('hub0016 reserved object exists')
    for statement in DDL + INDEXES:
        db.exec_driver_sql(statement)
    hub_file = next((row[2] for row in db.exec_driver_sql('PRAGMA database_list') if row[1] == 'main'), '')
    root = Path(hub_file).parent if hub_file else None
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for company_id, rel in db.exec_driver_sql('SELECT id, path FROM companies').fetchall():
        try:
            if root is None:
                raise sqlite3.DatabaseError('the hub has no file')
            rows = read_designations(root / rel / 'company.db')
        except (sqlite3.Error, OSError) as e:
            db.exec_driver_sql("INSERT INTO company_accountant_sync VALUES (?, 'unverified', ?, ?, NULL)",
                               (company_id, now, f'{type(e).__name__}: {e}'[:200]))
            continue
        for user_id, through, at in rows:
            db.exec_driver_sql('INSERT INTO company_accountants VALUES (?, ?, ?, ?)', (company_id, user_id, through, at))
        fingerprint = hashlib.sha256('\n'.join(sorted(f'{u}|{d}' for u, d, _ in rows)).encode()).hexdigest()
        db.exec_driver_sql("INSERT INTO company_accountant_sync VALUES (?, 'verified', ?, NULL, ?)", (company_id, now, fingerprint))


def downgrade():
    raise RuntimeError('Hub migrations are forward-only')
