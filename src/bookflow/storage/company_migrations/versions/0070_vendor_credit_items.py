"""Vendor credits with item lines: returning stock to the supplier is one document (R178).

A vendor credit gains the Items tab a bill already has. ``vendor_credit_item_lines`` is a
sibling of ``vendor_credit_expense_lines`` -- one row per envelope in ``document_lines``, the
same ``purchase`` kind -- carrying the item, the quantity, the unit cost and the account
captured from the item. A stocked item credits its Inventory Asset account at the credited
amount and takes the quantity off the shelf.

Two tables are rebuilt, because each change lives in a CHECK and SQLite only changes a CHECK by
rebuilding the table:

* ``vendor_credit_profiles`` gains ``item_total_minor_units`` beside ``expense_total_minor_units``
  and each is widened from positive to nonnegative, exactly as ``purchase_profiles`` was when the
  bill gained its Items tab (co0033). The figure that stays positive is the revision's own
  total, which is their sum. Every existing credit takes an item total of zero, which is what it
  has.
* ``inventory_movements`` admits a sixth movement kind, ``vendor_return``: quantity out at a value
  the vendor stated by crediting it, not at the weighted average. Nothing is backfilled; no
  such movement exists yet.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
from alembic import op

revision = 'co0070'
down_revision = 'co0068'
branch_labels = depends_on = None

NEW_TABLES = ('vendor_credit_item_lines',)
CHANGED = ('inventory_movements', 'vendor_credit_profiles')
DDL = {
    'inventory_movements': "CREATE TABLE _co0070_inventory_movements (\n\tid VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tposting_batch_id VARCHAR(26) NOT NULL, \n\tposting_line_id VARCHAR(26), \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tsequence BIGINT NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tvalue_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tasset_account_id VARCHAR(26) NOT NULL, \n\toffset_account_id VARCHAR(26) NOT NULL, \n\tclass_id VARCHAR(26), \n\tcorrects_movement_id VARCHAR(26), \n\treverses_movement_id VARCHAR(26), \n\treturns_movement_id VARCHAR(26), \n\tfilled_by_movement_id VARCHAR(26), \n\tfallback_unit_cost_minor_units BIGINT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_inventory_movement_batch FOREIGN KEY(transaction_id, posting_batch_id) REFERENCES posting_batches (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_posting_line FOREIGN KEY(transaction_id, posting_line_id) REFERENCES posting_lines (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_document_line FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_inventory_movement_corrects FOREIGN KEY(corrects_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_reverses FOREIGN KEY(reverses_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_returns FOREIGN KEY(returns_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_filled_by FOREIGN KEY(filled_by_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT ck_inventory_movement_value_link CHECK ((value_minor_units = 0 AND posting_line_id IS NULL) OR (value_minor_units != 0 AND posting_line_id IS NOT NULL)), \n\tCONSTRAINT uq_inventory_movement_posting_line UNIQUE (posting_line_id), \n\tCONSTRAINT uq_inventory_movement_sequence UNIQUE (sequence), \n\tCONSTRAINT uq_inventory_movement_reversal UNIQUE (reverses_movement_id), \n\tCONSTRAINT ck_inventory_movement_kind CHECK (kind IN ('receipt', 'issue', 'value', 'vendor_return', 'recost', 'reversal')), \n\tCONSTRAINT ck_inventory_movement_integers CHECK (typeof(quantity_microunits) = 'integer' AND typeof(value_minor_units) = 'integer' AND typeof(sequence) = 'integer' AND sequence > 0), \n\tCONSTRAINT ck_inventory_movement_date CHECK (effective_date LIKE '____-__-__'), \n\tCONSTRAINT ck_inventory_movement_shape CHECK ((kind = 'receipt' AND quantity_microunits > 0 AND value_minor_units >= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'issue' AND quantity_microunits < 0 AND value_minor_units <= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'value' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'vendor_return' AND quantity_microunits < 0 AND value_minor_units <= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'recost' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NOT NULL AND reverses_movement_id IS NULL) OR (kind = 'reversal' AND reverses_movement_id IS NOT NULL AND corrects_movement_id IS NULL AND (quantity_microunits != 0 OR value_minor_units != 0))), \n\tCONSTRAINT ck_inventory_movement_returns_kind CHECK (returns_movement_id IS NULL OR kind = 'receipt'), \n\tCONSTRAINT ck_inventory_movement_filled_by_kind CHECK (filled_by_movement_id IS NULL OR kind = 'recost'), \n\tCONSTRAINT ck_inventory_movement_fallback_cost CHECK (fallback_unit_cost_minor_units IS NULL OR (kind = 'issue' AND typeof(fallback_unit_cost_minor_units) = 'integer' AND fallback_unit_cost_minor_units >= 0)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(asset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(offset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)",
    'vendor_credit_profiles': "CREATE TABLE _co0070_vendor_credit_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\ttype VARCHAR(32) NOT NULL, \n\tvendor_id VARCHAR(26) NOT NULL, \n\tap_account_id VARCHAR(26) NOT NULL, \n\tsupplier_reference VARCHAR(128), \n\tsupplier_reference_key VARCHAR(256), \n\texpense_total_minor_units BIGINT NOT NULL, \n\titem_total_minor_units BIGINT NOT NULL, \n\tprofile_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tCONSTRAINT uq_vendor_credit_profile_owner UNIQUE (transaction_id, revision_id), \n\tCONSTRAINT fk_vendor_credit_profile_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT fk_vendor_credit_profile_type FOREIGN KEY(transaction_id, type) REFERENCES transactions (id, type), \n\tCONSTRAINT ck_vendor_credit_profile_type CHECK (type = 'vendor_credit'), \n\tCONSTRAINT ck_vendor_credit_profile_reference_pair CHECK ((supplier_reference IS NULL) = (supplier_reference_key IS NULL)), \n\tCONSTRAINT ck_vendor_credit_expense_total_minor_units_nonnegative CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units >= 0), \n\tCONSTRAINT ck_vendor_credit_item_total_minor_units_nonnegative CHECK (typeof(item_total_minor_units) = 'integer' AND item_total_minor_units >= 0), \n\tCONSTRAINT ck_vendor_credit_profile_snapshot_object CHECK (json_valid(profile_snapshot) AND json_type(profile_snapshot) = 'object'), \n\tFOREIGN KEY(vendor_id) REFERENCES vendors (id), \n\tFOREIGN KEY(ap_account_id) REFERENCES accounts (id)\n)"
}
# The column each rebuilt table gains, and the value every stored row takes for it.
ADDED = {'vendor_credit_profiles': (('item_total_minor_units', '0'),)}
NEW_DDL = (
    "CREATE TABLE vendor_credit_item_lines (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tunit_cost_minor_units BIGINT, \n\tamount_minor_units BIGINT NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tbillable BOOLEAN NOT NULL, \n\tline_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (document_line_id), \n\tCONSTRAINT uq_vendor_credit_item_line_owner UNIQUE (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_vendor_credit_item_line_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES vendor_credit_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_vendor_credit_item_line_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT ck_vendor_credit_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_vendor_credit_quantity_microunits_positive CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_vendor_credit_line_snapshot_object CHECK (json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'), \n\tCONSTRAINT ck_vendor_credit_item_unit_cost CHECK (unit_cost_minor_units IS NULL OR (typeof(unit_cost_minor_units) = 'integer' AND unit_cost_minor_units >= 0)), \n\tCONSTRAINT ck_vendor_credit_item_billable_job CHECK (billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)",
    'CREATE INDEX ix_vendor_credit_item_lines_item ON vendor_credit_item_lines (item_id, revision_id)'
)
GUARDS = (
    "CREATE TRIGGER vendor_credit_item_lines_immutable_update BEFORE UPDATE ON vendor_credit_item_lines BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END",
    "CREATE TRIGGER vendor_credit_item_lines_immutable_delete BEFORE DELETE ON vendor_credit_item_lines BEGIN SELECT RAISE(ABORT, 'immutable vendor credit history'); END",
)
INDEXES = ()

# What each rebuilt table's stored text gains, as (before, after) pairs. Not used by upgrade();
# a test that needs the co0068 file back from a head file with these tables empty runs them
# backwards instead of keeping a second copy of the old text.
REWIND = {
    'vendor_credit_profiles': (
        ('expense_total_minor_units BIGINT NOT NULL, \n\tprofile_snapshot',
         'expense_total_minor_units BIGINT NOT NULL, \n\titem_total_minor_units BIGINT NOT NULL, \n\tprofile_snapshot'),
        ("CONSTRAINT ck_vendor_credit_expense_total_minor_units_positive CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units > 0), \n\tCONSTRAINT ck_vendor_credit_profile_snapshot_object",
         "CONSTRAINT ck_vendor_credit_expense_total_minor_units_nonnegative CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units >= 0), \n\tCONSTRAINT ck_vendor_credit_item_total_minor_units_nonnegative CHECK (typeof(item_total_minor_units) = 'integer' AND item_total_minor_units >= 0), \n\tCONSTRAINT ck_vendor_credit_profile_snapshot_object"),
    ),
    'inventory_movements': (
        ("kind IN ('receipt', 'issue', 'value', 'recost', 'reversal')",
         "kind IN ('receipt', 'issue', 'value', 'vendor_return', 'recost', 'reversal')"),
        ("OR (kind = 'recost' AND quantity_microunits = 0",
         "OR (kind = 'vendor_return' AND quantity_microunits < 0 AND value_minor_units <= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'recost' AND quantity_microunits = 0"),
    ),
}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0070_' + name for name in CHANGED} | set(NEW_TABLES) | {
        'ix_vendor_credit_item_lines_item', 'vendor_credit_item_lines_immutable_update',
        'vendor_credit_item_lines_immutable_delete'}
    for _, db, _ in connection.exec_driver_sql('PRAGMA database_list'):
        names = {row[0].casefold() for row in connection.exec_driver_sql(
            'SELECT name FROM ' + quote(db) + '.sqlite_schema')}
        if names & {name.casefold() for name in reserved}:
            raise RuntimeError('co0070 reserved object exists')
    plans = {}
    for table in CHANGED:
        columns = connection.exec_driver_sql('PRAGMA table_xinfo(' + quote(table) + ')').all()
        if any(row[1].lower() in ('rowid', 'oid', '_rowid_') for row in columns):
            raise RuntimeError('co0070 unsupported row identity')
        added = ADDED.get(table, ())
        if any(row[1] in {name for name, _ in added} for row in columns):
            raise RuntimeError('co0070 column already present: ' + table)
        kept = ['rowid'] + [quote(row[1]) for row in columns if row[6] == 0]
        plans[table] = (
            ','.join(kept + [quote(name) for name, _ in added]),
            ','.join(kept + [value for _, value in added]),
            ','.join(['rowid'] + [expression for row in columns for expression in
                                  ('typeof(' + quote(row[1]) + ')', 'quote(' + quote(row[1]) + ')',
                                   'CAST(' + quote(row[1]) + ' AS BLOB)')]))
    changed = ','.join("'%s'" % name for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type='index' AND tbl_name IN (" + changed + "))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql('DROP ' + kind.upper() + ' main.' + quote(name))
    for table in CHANGED:
        writable, reading, selected = plans[table]
        temporary = '_co0070_' + table
        connection.exec_driver_sql(DDL[table])
        connection.exec_driver_sql('INSERT INTO ' + quote(temporary) + ' (' + writable + ') SELECT '
                                   + reading + ' FROM ' + quote(table))
        # Every carried value survives untouched, compared both ways by storage class and bytes.
        carried = ','.join(['rowid'] + [expression for row in connection.exec_driver_sql(
            'PRAGMA table_xinfo(' + quote(table) + ')').all() for expression in
            ('typeof(' + quote(row[1]) + ')', 'quote(' + quote(row[1]) + ')',
             'CAST(' + quote(row[1]) + ' AS BLOB)')])
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql('SELECT ' + carried + ' FROM ' + quote(left)
                                          + ' EXCEPT SELECT ' + carried + ' FROM ' + quote(right)).fetchone():
                raise RuntimeError('co0070 rebuilt values differ: ' + table)
        connection.exec_driver_sql('DROP TABLE ' + quote(table))
        connection.exec_driver_sql('ALTER TABLE ' + quote(temporary) + ' RENAME TO ' + quote(table))
    for statement in NEW_DDL:
        connection.exec_driver_sql(statement)
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    for statement in INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0070 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
