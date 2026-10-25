"""Check printing (R152): the print queue, print runs, spoiled numbers and alignment.

Six new tables (see ``company/check_print_schema.py``): ``check_print_marks`` (a cheque saved to be
printed), ``check_print_runs`` and ``check_print_run_items`` (each press of Print and the number each
cheque took in it), ``check_print_outcomes`` (the answer to "Did checks print OK?"),
``check_spoiled_numbers`` (paper thrown away) and ``check_print_settings`` (per-account style and
fine alignment). The first five are append-only, held by guards. Nothing existing changes: a
cheque already written keeps its number and is not in the queue. DDL below is frozen.
"""
from alembic import op

revision = 'co0092'
down_revision = 'co0088'
branch_labels = depends_on = None
DDL = ("\nCREATE TABLE check_print_marks (\n\ttransaction_id VARCHAR(26) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (transaction_id), \n\tCONSTRAINT ck_check_print_mark_type CHECK (type IN ('journal_entry', 'bill_payment')), \n\tCONSTRAINT fk_check_print_mark_document FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE check_print_runs (\n\tid VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tstyle VARCHAR(16) NOT NULL, \n\tfirst_number VARCHAR(64) NOT NULL, \n\tchecks_on_first_page INTEGER NOT NULL, \n\toffset_x_hundredths INTEGER NOT NULL, \n\toffset_y_hundredths INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_check_print_run_kind CHECK (kind IN ('batch', 'reprint')), \n\tCONSTRAINT ck_check_print_run_style CHECK (style IN ('voucher', 'standard', 'wallet')), \n\tCONSTRAINT ck_check_print_run_first_page CHECK (checks_on_first_page BETWEEN 1 AND 3), \n\tCONSTRAINT ck_check_print_run_offsets CHECK (offset_x_hundredths BETWEEN -200 AND 200 AND offset_y_hundredths BETWEEN -200 AND 200), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE check_print_run_items (\n\tid VARCHAR(26) NOT NULL, \n\trun_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tcheck_number VARCHAR(64) NOT NULL, \n\tcheck_number_key VARCHAR(64) NOT NULL, \n\tcheck_sequence BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_check_print_item_position UNIQUE (run_id, position), \n\tCONSTRAINT ck_check_print_item_position CHECK (position >= 1), \n\tCONSTRAINT ck_check_print_item_sequence CHECK (typeof(check_sequence) = 'integer' AND check_sequence > 0), \n\tFOREIGN KEY(run_id) REFERENCES check_print_runs (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE check_print_outcomes (\n\titem_id VARCHAR(26) NOT NULL, \n\toutcome VARCHAR(16) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (item_id), \n\tCONSTRAINT ck_check_print_outcome CHECK (outcome IN ('printed', 'failed', 'cancelled')), \n\tFOREIGN KEY(item_id) REFERENCES check_print_run_items (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE check_spoiled_numbers (\n\tid VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tcheck_number VARCHAR(64) NOT NULL, \n\tcheck_number_key VARCHAR(64) NOT NULL, \n\tcheck_sequence BIGINT NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trun_id VARCHAR(26) NOT NULL, \n\treason VARCHAR(16) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_check_spoiled_reason CHECK (reason IN ('misprinted', 'reprinted')), \n\tCONSTRAINT ck_check_spoiled_sequence CHECK (typeof(check_sequence) = 'integer' AND check_sequence > 0), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(run_id) REFERENCES check_print_runs (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE check_print_settings (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tstyle VARCHAR(16) NOT NULL, \n\toffset_x_hundredths INTEGER NOT NULL, \n\toffset_y_hundredths INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_check_print_settings_account UNIQUE (account_id), \n\tCONSTRAINT ck_check_print_settings_style CHECK (style IN ('voucher', 'standard', 'wallet')), \n\tCONSTRAINT ck_check_print_settings_offsets CHECK (offset_x_hundredths BETWEEN -200 AND 200 AND offset_y_hundredths BETWEEN -200 AND 200), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_check_print_runs_account ON check_print_runs (account_id, created_at)', 'CREATE INDEX ix_check_print_items_number ON check_print_run_items (check_number_key)', 'CREATE INDEX ix_check_print_items_transaction ON check_print_run_items (transaction_id)', 'CREATE INDEX ix_check_spoiled_numbers_account ON check_spoiled_numbers (account_id, check_number_key)')
APPEND_ONLY = ('check_print_marks', 'check_print_runs', 'check_print_run_items', 'check_print_outcomes',
               'check_spoiled_numbers')
GUARDS = tuple(
    f"CREATE TRIGGER {table}_immutable_{verb} BEFORE {verb.upper()} ON {table} "
    f"BEGIN SELECT RAISE(ABORT, 'immutable check printing record'); END"
    for table in APPEND_ONLY for verb in ('update', 'delete'))
RESERVED = ('check_print_marks', 'check_print_runs', 'check_print_run_items', 'check_print_outcomes',
            'check_spoiled_numbers', 'check_print_settings', 'ix_check_print_runs_account',
            'ix_check_print_items_number', 'ix_check_print_items_transaction', 'ix_check_spoiled_numbers_account',
            *(f'{table}_immutable_{verb}' for table in APPEND_ONLY for verb in ('update', 'delete')))


def upgrade():
    connection = op.get_bind()
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in RESERVED} & existing:
        raise RuntimeError('co0092 reserved object already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0092 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
