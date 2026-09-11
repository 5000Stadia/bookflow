"""Billing groups and the durable record of one batch of invoices.

Additive only. Four new tables and the two immutability triggers that keep a recorded batch
run from ever being rewritten. No existing table is rebuilt and no CHECK is widened, because a
batch writes ordinary invoices: every row it creates is a row ``invoice post`` already creates,
so ``transactions``, ``document_lines`` and every guard over them are untouched.

Nothing is backfilled. A company upgraded here has no billing groups and no batch history,
which is exactly what it had before.

DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0036'
down_revision = 'co0032'
branch_labels = None
depends_on = None

NEW_TABLES = ('billing_groups', 'billing_group_members', 'invoice_batches', 'invoice_batch_results')
OBJECTS = ('billing_group_members', 'billing_groups', 'invoice_batch_results', 'invoice_batch_results_immutable_delete', 'invoice_batch_results_immutable_update', 'invoice_batches', 'invoice_batches_immutable_delete', 'invoice_batches_immutable_update', 'ix_invoice_batch_results_customer', 'ix_invoice_batches_group')
DDL = (
    'CREATE TABLE billing_groups (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_billing_group_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_billing_group_name_nonblank CHECK (length(trim(name)) > 0)\n)',
    "CREATE TABLE billing_group_members (\n\tid VARCHAR(26) NOT NULL, \n\tgroup_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_billing_group_member_customer UNIQUE (group_id, customer_id), \n\tCONSTRAINT uq_billing_group_member_position UNIQUE (group_id, position), \n\tCONSTRAINT ck_billing_group_member_position_positive CHECK (typeof(position) = 'integer' AND position > 0), \n\tFOREIGN KEY(group_id) REFERENCES billing_groups (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)",
    "CREATE TABLE invoice_batches (\n\tid VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tdate VARCHAR(10) NOT NULL, \n\tbilling_group_id VARCHAR(26), \n\tbilling_group_name VARCHAR(200), \n\tretry_of_batch_id VARCHAR(26), \n\trequested_count INTEGER NOT NULL, \n\tcreated_count INTEGER NOT NULL, \n\tfailed_count INTEGER NOT NULL, \n\tcreated_total_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\trequest_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_invoice_batch_requested_positive CHECK (typeof(requested_count) = 'integer' AND requested_count > 0), \n\tCONSTRAINT ck_invoice_batch_created_nonnegative CHECK (typeof(created_count) = 'integer' AND created_count >= 0), \n\tCONSTRAINT ck_invoice_batch_failed_nonnegative CHECK (typeof(failed_count) = 'integer' AND failed_count >= 0), \n\tCONSTRAINT ck_invoice_batch_counts_agree CHECK (created_count + failed_count = requested_count), \n\tCONSTRAINT ck_invoice_batch_total_nonnegative CHECK (typeof(created_total_minor_units) = 'integer' AND created_total_minor_units >= 0), \n\tCONSTRAINT ck_invoice_batch_request_object CHECK (json_valid(request_snapshot) AND json_type(request_snapshot) = 'object'), \n\tCONSTRAINT ck_invoice_batch_group_pair CHECK ((billing_group_id IS NULL) = (billing_group_name IS NULL)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(retry_of_batch_id) REFERENCES invoice_batches (id)\n)",
    "CREATE TABLE invoice_batch_results (\n\tid VARCHAR(26) NOT NULL, \n\tbatch_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\tcustomer_label VARCHAR(1004) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\ttransaction_id VARCHAR(26), \n\tnumber VARCHAR(64), \n\ttotal_minor_units BIGINT, \n\tcurrency VARCHAR(3), \n\terror_code VARCHAR(64), \n\terror_message TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_invoice_batch_result_position UNIQUE (batch_id, position), \n\tCONSTRAINT uq_invoice_batch_result_customer UNIQUE (batch_id, customer_id), \n\tCONSTRAINT ck_invoice_batch_result_status CHECK (status IN ('created', 'failed')), \n\tCONSTRAINT ck_invoice_batch_result_position_positive CHECK (typeof(position) = 'integer' AND position > 0), \n\tCONSTRAINT ck_invoice_batch_result_created_facts CHECK ((status = 'created') = (transaction_id IS NOT NULL AND number IS NOT NULL AND total_minor_units IS NOT NULL AND currency IS NOT NULL)), \n\tCONSTRAINT ck_invoice_batch_result_failed_facts CHECK ((status = 'failed') = (error_code IS NOT NULL)), \n\tCONSTRAINT ck_invoice_batch_result_total_positive CHECK (total_minor_units IS NULL OR (typeof(total_minor_units) = 'integer' AND total_minor_units > 0)), \n\tFOREIGN KEY(batch_id) REFERENCES invoice_batches (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id)\n)",
    'CREATE INDEX ix_invoice_batch_results_customer ON invoice_batch_results (customer_id, id)',
    'CREATE INDEX ix_invoice_batches_group ON invoice_batches (billing_group_id, id)',
)
GUARDS = (
    "CREATE TRIGGER invoice_batches_immutable_update BEFORE UPDATE ON invoice_batches BEGIN SELECT RAISE(ABORT, 'immutable invoice batch history'); END",
    "CREATE TRIGGER invoice_batches_immutable_delete BEFORE DELETE ON invoice_batches BEGIN SELECT RAISE(ABORT, 'immutable invoice batch history'); END",
    "CREATE TRIGGER invoice_batch_results_immutable_update BEFORE UPDATE ON invoice_batch_results BEGIN SELECT RAISE(ABORT, 'immutable invoice batch history'); END",
    "CREATE TRIGGER invoice_batch_results_immutable_delete BEFORE DELETE ON invoice_batch_results BEGIN SELECT RAISE(ABORT, 'immutable invoice batch history'); END",
)


def upgrade():
    connection = op.get_bind()
    reserved = {name.casefold() for name in OBJECTS}
    # Preflight the whole local namespace before any mutation: an unrelated local table,
    # index, trigger or view that happens to share one of these names is somebody's own
    # object and this migration stops rather than dropping or shadowing it.
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in reserved:
                raise RuntimeError('co0036 batch invoicing storage name collision')
    for statement in (*DDL, *GUARDS):
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0036 batch invoicing storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0036 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
