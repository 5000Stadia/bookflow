"""co0121: reconciliation statement copies retain their inbox line (R108 increment 6).

Frozen DDL. Rebuild only the import header to widen its format check; preserve all rows,
indexes and triggers. The migration runner disables foreign keys during schema migration.
"""
from alembic import op

revision = 'co0121'
down_revision = 'co0120'
branch_labels = depends_on = None

DDL = "\nCREATE TABLE statement_imports_inbox (\n\tid VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tformat TEXT NOT NULL, \n\tfile_sha256 TEXT NOT NULL, \n\tstatement_date TEXT NOT NULL, \n\tending_balance INTEGER, \n\tsuggest_days INTEGER NOT NULL, \n\tline_count INTEGER NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26), \n\tcreated_via TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_statement_import_format CHECK (format IN ('ofx','qfx','csv','inbox')), \n\tCONSTRAINT ck_statement_import_sha CHECK (length(file_sha256)=64), \n\tCONSTRAINT ck_statement_import_counts CHECK (typeof(line_count)='integer' AND line_count>=0 AND typeof(suggest_days)='integer' AND suggest_days>=0), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(draft_id) REFERENCES reconciliation_drafts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n"


def upgrade():
    conn = op.get_bind()
    columns = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info(statement_lines)')}
    if 'bank_line_id' in columns:
        raise RuntimeError('co0121 reserved column already exists')
    # Keep local indexes and triggers as well as the shipped append-only guards.
    objects = list(conn.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE tbl_name='statement_imports' "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY type, name").scalars())
    conn.exec_driver_sql(DDL)
    conn.exec_driver_sql('INSERT INTO statement_imports_inbox SELECT * FROM statement_imports')
    conn.exec_driver_sql('DROP TABLE statement_imports')
    conn.exec_driver_sql('ALTER TABLE statement_imports_inbox RENAME TO statement_imports')
    for sql in objects:
        conn.exec_driver_sql(sql)
    conn.exec_driver_sql('ALTER TABLE statement_lines ADD COLUMN bank_line_id VARCHAR(26) REFERENCES bank_lines (id)')
    conn.exec_driver_sql('CREATE INDEX ix_statement_lines_bank_line ON statement_lines (bank_line_id)')
    if conn.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0121 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
