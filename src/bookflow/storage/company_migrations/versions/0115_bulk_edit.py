"""co0115. Grid edits (R107 increment 6, design/bulk-import.md §7, §8a).

Two new tables, moving no stored row: ``bulk_edit_runs`` (one grid edit: the selection and what was asked, previewed
then applied as one batch) and ``bulk_edit_rows`` (its diff, one row per record changed or refused, with the apply
outcome written once). Guards: nothing here is ever deleted; a run changes only its state, version and batch (once
set); a row takes its outcome once and nothing else. The writes themselves are the batch ledger's (co0108). DDL below
is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0115'
down_revision = 'co0114'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE bulk_edit_runs (\n\tid VARCHAR(26) NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tstate VARCHAR(16) NOT NULL, \n\tatomic BOOLEAN NOT NULL, \n\tselection TEXT NOT NULL, \n\toperations TEXT NOT NULL, \n\tlabel VARCHAR(200) NOT NULL, \n\trecord_count INTEGER NOT NULL, \n\tunchanged_count INTEGER NOT NULL, \n\tbatch_id VARCHAR(26), \n\treason TEXT, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_bulk_edit_run_state CHECK (state IN ('previewed', 'applying', 'applied', 'applied_partly', 'rolled_back', 'cancelled', 'failed')), \n\tCONSTRAINT ck_bulk_edit_run_kind CHECK (kind IN ('customer', 'vendor', 'item', 'account')), \n\tFOREIGN KEY(batch_id) REFERENCES batches (id)\n)\n\n", "\nCREATE TABLE bulk_edit_rows (\n\trun_id VARCHAR(26) NOT NULL, \n\tseq INTEGER NOT NULL, \n\trecord_type VARCHAR(64) NOT NULL, \n\trecord_id VARCHAR(26) NOT NULL, \n\tlabel VARCHAR(1004) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tchanges TEXT NOT NULL, \n\tcode VARCHAR(64), \n\tmessage TEXT, \n\toutcome VARCHAR(16), \n\toutcome_code VARCHAR(64), \n\toutcome_message TEXT, \n\tversion_after INTEGER, \n\tmerged_over TEXT, \n\tevent_id VARCHAR(26), \n\tapplied_at VARCHAR(32), \n\tPRIMARY KEY (run_id, seq), \n\tCONSTRAINT ck_bulk_edit_row_status CHECK (status IN ('change', 'refused')), \n\tCONSTRAINT ck_bulk_edit_row_outcome CHECK (outcome IS NULL OR outcome IN ('updated', 'merged', 'conflict', 'refused', 'not_run', 'rolled_back')), \n\tFOREIGN KEY(run_id) REFERENCES bulk_edit_runs (id), \n\tFOREIGN KEY(event_id) REFERENCES audit_events (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_bulk_edit_runs_created ON bulk_edit_runs (created_at)', 'CREATE INDEX ix_bulk_edit_rows_record ON bulk_edit_rows (record_type, record_id)')
GUARDS = ("CREATE TRIGGER bulk_edit_runs_no_delete BEFORE DELETE ON bulk_edit_runs BEGIN SELECT RAISE(ABORT, 'immutable grid edits'); END", "CREATE TRIGGER bulk_edit_rows_no_delete BEFORE DELETE ON bulk_edit_rows BEGIN SELECT RAISE(ABORT, 'immutable grid edit rows'); END", "CREATE TRIGGER bulk_edit_runs_frozen BEFORE UPDATE ON bulk_edit_runs WHEN NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind OR NEW.atomic IS NOT OLD.atomic OR NEW.selection IS NOT OLD.selection OR NEW.operations IS NOT OLD.operations OR NEW.label IS NOT OLD.label OR NEW.record_count IS NOT OLD.record_count OR NEW.unchanged_count IS NOT OLD.unchanged_count OR NEW.reason IS NOT OLD.reason OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by OR NEW.created_via IS NOT OLD.created_via OR (OLD.batch_id IS NOT NULL AND NEW.batch_id IS NOT OLD.batch_id) BEGIN SELECT RAISE(ABORT, 'a grid edit changes only its state and batch'); END", "CREATE TRIGGER bulk_edit_rows_outcome_once BEFORE UPDATE ON bulk_edit_rows WHEN OLD.outcome IS NOT NULL OR NEW.outcome IS NULL OR NEW.run_id IS NOT OLD.run_id OR NEW.seq IS NOT OLD.seq OR NEW.record_type IS NOT OLD.record_type OR NEW.record_id IS NOT OLD.record_id OR NEW.label IS NOT OLD.label OR NEW.version IS NOT OLD.version OR NEW.status IS NOT OLD.status OR NEW.changes IS NOT OLD.changes OR NEW.code IS NOT OLD.code OR NEW.message IS NOT OLD.message BEGIN SELECT RAISE(ABORT, 'a grid edit row takes its outcome once'); END")
TABLES = ('bulk_edit_rows', 'bulk_edit_runs')
# What a test rewinding this head drops: the guards, then the tables (children first).
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    reserved = set(TABLES) | set(TRIGGERS) | {statement.split()[2] for statement in INDEXES}
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0115 reserved object already exists')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
