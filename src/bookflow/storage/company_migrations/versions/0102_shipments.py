"""Shipments and the books (R181 increment 1, design/pick-pack-ship.md §8).

Five new tables -- ``shipments``, ``shipment_revisions``, ``shipment_lines``, ``shipment_billings`` and
``shipment_recognitions`` -- every row immutable (revisions, billings and recognitions append). Two changes to
existing objects, neither moving a stored row:

- ``inventory_documents.kind`` admits ``shipment`` (a shipment's own entry) and ``shipment_cost`` (the cost a
  billed shipment moves from Shipped Not Invoiced to cost of goods sold). SQLite cannot alter a CHECK in place, so
  the table is rebuilt with the constraint respelled and every value, row identity, index and trigger carried
  across verbatim, as co0096 does.
- ``company_info.invoice_when_shipping`` (``ask`` default, ``always``, ``never``).

The Shipped Not Invoiced account is made on the first shipment, so no account is added here. DDL below is frozen:
this migration never imports current application metadata.
"""
import importlib

import sqlalchemy as sa
from alembic import op

revision = 'co0102'
down_revision = 'co0100'
branch_labels = depends_on = None

DDL = ('\nCREATE TABLE shipments (\n\tid VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tsales_order_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_shipments_number UNIQUE (number), \n\tCONSTRAINT uq_shipments_transaction UNIQUE (transaction_id), \n\tFOREIGN KEY(sales_order_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n', "\nCREATE TABLE shipment_revisions (\n\tid VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\trevision_number INTEGER NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\tship_date VARCHAR(10) NOT NULL, \n\tcount_position VARCHAR(8) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tship_method_id VARCHAR(26), \n\tcarrier TEXT, \n\ttracking_numbers TEXT NOT NULL, \n\tship_to TEXT, \n\tmemo TEXT, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_revision_status CHECK (status IN ('draft', 'shipped', 'cancelled', 'voided')), \n\tCONSTRAINT ck_shipment_revision_position CHECK (count_position IN ('before', 'after')), \n\tCONSTRAINT ck_shipment_revision_date CHECK (ship_date LIKE '____-__-__'), \n\tCONSTRAINT ck_shipment_revision_number CHECK (typeof(revision_number) = 'integer' AND revision_number > 0), \n\tCONSTRAINT ck_shipment_revision_tracking CHECK (json_valid(tracking_numbers) AND json_type(tracking_numbers) = 'array'), \n\tCONSTRAINT uq_shipment_revision_number UNIQUE (shipment_id, revision_number), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(ship_method_id) REFERENCES ship_methods (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_lines (\n\tid VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\tline_identity VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\troot_document_id VARCHAR(26) NOT NULL, \n\troot_line_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tbin_id VARCHAR(26), \n\tdescription TEXT, \n\tasset_account_id VARCHAR(26) NOT NULL, \n\tcogs_account_id VARCHAR(26) NOT NULL, \n\tclass_id VARCHAR(26), \n\tmovement_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_line_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT uq_shipment_line_position UNIQUE (revision_id, position), \n\tFOREIGN KEY(revision_id) REFERENCES shipment_revisions (id), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(asset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(cogs_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id), \n\tFOREIGN KEY(movement_id) REFERENCES inventory_movements (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_billings (\n\tid VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\tshipment_line_identity VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_billing_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT uq_shipment_billing UNIQUE (transaction_id, line_id, shipment_line_identity), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(line_id) REFERENCES document_line_identities (id), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_recognitions (\n\tid VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\tshipment_line_identity VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_recognition_amount CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units != 0), \n\tCONSTRAINT ck_shipment_recognition_date CHECK (effective_date LIKE '____-__-__'), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_shipments_customer ON shipments (customer_id)', 'CREATE INDEX ix_shipments_order ON shipments (sales_order_id)', 'CREATE INDEX ix_shipment_lines_identity ON shipment_lines (line_identity)', 'CREATE INDEX ix_shipment_lines_movement ON shipment_lines (movement_id)', 'CREATE INDEX ix_shipment_lines_root ON shipment_lines (root_document_id, root_line_id)', 'CREATE INDEX ix_shipment_billings_line ON shipment_billings (shipment_line_identity)', 'CREATE INDEX ix_shipment_billings_shipment ON shipment_billings (shipment_id)', 'CREATE INDEX ix_shipment_recognitions_line ON shipment_recognitions (shipment_line_identity, effective_date)')
GUARDS = tuple(
    statement for table in ('shipments', 'shipment_revisions', 'shipment_lines', 'shipment_billings',
                            'shipment_recognitions') for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END"))
REPLACEMENTS = {
    'inventory_documents': (
        ("CONSTRAINT ck_inventory_document_kind CHECK (kind IN ('adjustment', 'recost', 'count', 'landed_cost'))",
         "CONSTRAINT ck_inventory_document_kind CHECK (kind IN ('adjustment', 'recost', 'count', 'landed_cost', "
         "'shipment', 'shipment_cost'))"),
    ),
}
CHANGED = tuple(REPLACEMENTS)
TABLES = ('shipment_recognitions', 'shipment_billings', 'shipment_lines', 'shipment_revisions', 'shipments')
COMPANY_COLUMNS = ('invoice_when_shipping',)
# What a test rewinding a head file drops: the guards, then the tables (children first), then the column.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0102 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0102 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0102_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0102_' + name for name in CHANGED} | set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0102 reserved object already exists')
    columns = {row[1].lower() for row in connection.exec_driver_sql('PRAGMA table_xinfo(company_info)')}
    if columns & set(COMPANY_COLUMNS):
        raise RuntimeError('co0102 company column already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join("'%s'" % name for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN (" + changed + "))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0102_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} '
                                          f'FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0102 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    op.add_column('company_info', sa.Column(
        'invoice_when_shipping', sa.String(8),
        sa.CheckConstraint("invoice_when_shipping IN ('ask', 'always', 'never')",
                           name='ck_company_info_invoice_when_shipping'),
        nullable=False, server_default='ask'))
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0102 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
