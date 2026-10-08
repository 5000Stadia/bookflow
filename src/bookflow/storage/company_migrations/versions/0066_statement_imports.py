"""Store imported bank statements: their lines per reconciliation draft, and saved CSV mappings.

Three append-only tables (statement_imports, statement_lines, statement_csv_mappings), each with
BEFORE UPDATE/DELETE triggers. Nothing existing changes. DDL below is frozen: this migration never
imports current application metadata.
"""
from alembic import op

revision = 'co0066'
down_revision = 'co0065'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE statement_imports (\n\tid VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tformat TEXT NOT NULL, \n\tfile_sha256 TEXT NOT NULL, \n\tstatement_date TEXT NOT NULL, \n\tending_balance INTEGER, \n\tsuggest_days INTEGER NOT NULL, \n\tline_count INTEGER NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26), \n\tcreated_via TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_statement_import_format CHECK (format IN ('ofx','qfx','csv')), \n\tCONSTRAINT ck_statement_import_sha CHECK (length(file_sha256)=64), \n\tCONSTRAINT ck_statement_import_counts CHECK (typeof(line_count)='integer' AND line_count>=0 AND typeof(suggest_days)='integer' AND suggest_days>=0), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(draft_id) REFERENCES reconciliation_drafts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_statement_imports_draft ON statement_imports (draft_id)', '\nCREATE TABLE statement_lines (\n\timport_id VARCHAR(26) NOT NULL, \n\tordinal INTEGER NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tdraft_id VARCHAR(26) NOT NULL, \n\tline_id TEXT NOT NULL, \n\tfitid TEXT, \n\tdate TEXT NOT NULL, \n\tamount INTEGER NOT NULL, \n\tpayee TEXT NOT NULL, \n\tmemo TEXT NOT NULL, \n\tnumber TEXT NOT NULL, \n\tPRIMARY KEY (import_id, ordinal), \n\tCONSTRAINT uq_statement_line_draft UNIQUE (draft_id, line_id), \n\tFOREIGN KEY(import_id) REFERENCES statement_imports (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(draft_id) REFERENCES reconciliation_drafts (id)\n)\n\n', 'CREATE INDEX ix_statement_lines_account_line ON statement_lines (account_id, line_id)', "\nCREATE TABLE statement_csv_mappings (\n\tid VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tname TEXT NOT NULL, \n\tmapping_snapshot TEXT NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26), \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_statement_mapping_name CHECK (length(trim(name)) BETWEEN 1 AND 80), \n\tCONSTRAINT ck_statement_mapping_json CHECK (json_valid(mapping_snapshot) AND json_type(mapping_snapshot)='object'), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", 'CREATE INDEX ix_statement_csv_mappings_name ON statement_csv_mappings (account_id, name)')
TRIGGERS = ("CREATE TRIGGER statement_imports_no_update BEFORE UPDATE ON statement_imports BEGIN SELECT RAISE(ABORT,'statement_imports is append-only'); END", "CREATE TRIGGER statement_imports_no_delete BEFORE DELETE ON statement_imports BEGIN SELECT RAISE(ABORT,'statement_imports is append-only'); END", "CREATE TRIGGER statement_lines_no_update BEFORE UPDATE ON statement_lines BEGIN SELECT RAISE(ABORT,'statement_lines is append-only'); END", "CREATE TRIGGER statement_lines_no_delete BEFORE DELETE ON statement_lines BEGIN SELECT RAISE(ABORT,'statement_lines is append-only'); END", "CREATE TRIGGER statement_csv_mappings_no_update BEFORE UPDATE ON statement_csv_mappings BEGIN SELECT RAISE(ABORT,'statement_csv_mappings is append-only'); END", "CREATE TRIGGER statement_csv_mappings_no_delete BEFORE DELETE ON statement_csv_mappings BEGIN SELECT RAISE(ABORT,'statement_csv_mappings is append-only'); END")


def upgrade():
    connection = op.get_bind()
    for statement in (*DDL, *TRIGGERS):
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0066 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
