"""Shipment returns (R181 increment 2, design/pick-pack-ship.md §6, §8).

Three new tables -- ``shipment_returns``, ``shipment_return_revisions`` and ``shipment_return_lines`` -- every row
immutable (a void appends a revision). One change to an existing object, moving no stored row:
``inventory_documents.kind`` admits ``shipment_return`` (a return's own entry: Dr Inventory Asset, Cr Shipped Not
Invoiced). SQLite cannot alter a CHECK in place, so the table is rebuilt with the constraint respelled and every value,
row identity, index and trigger carried across verbatim, as co0102 does. DDL below is frozen: this migration never
imports current application metadata.
"""
import importlib

from alembic import op

revision = 'co0103'
down_revision = 'co0102'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE shipment_returns (\n\tid VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\tsales_order_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\treturn_date VARCHAR(10) NOT NULL, \n\treopen BOOLEAN NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_return_date CHECK (return_date LIKE '____-__-__'), \n\tCONSTRAINT uq_shipment_returns_number UNIQUE (number), \n\tCONSTRAINT uq_shipment_returns_transaction UNIQUE (transaction_id), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(sales_order_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_return_revisions (\n\tid VARCHAR(26) NOT NULL, \n\treturn_id VARCHAR(26) NOT NULL, \n\trevision_number INTEGER NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_return_revision_status CHECK (status IN ('returned', 'voided')), \n\tCONSTRAINT ck_shipment_return_revision_number CHECK (typeof(revision_number) = 'integer' AND revision_number > 0), \n\tCONSTRAINT uq_shipment_return_revision_number UNIQUE (return_id, revision_number), \n\tFOREIGN KEY(return_id) REFERENCES shipment_returns (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE shipment_return_lines (\n\tid VARCHAR(26) NOT NULL, \n\treturn_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tshipment_line_identity VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tmovement_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_shipment_return_line_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT uq_shipment_return_line_position UNIQUE (return_id, position), \n\tFOREIGN KEY(return_id) REFERENCES shipment_returns (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(movement_id) REFERENCES inventory_movements (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_shipment_returns_shipment ON shipment_returns (shipment_id)', 'CREATE INDEX ix_shipment_return_lines_identity ON shipment_return_lines (shipment_line_identity)', 'CREATE INDEX ix_shipment_return_lines_movement ON shipment_return_lines (movement_id)')
GUARDS = tuple(
    statement for table in ('shipment_returns', 'shipment_return_revisions', 'shipment_return_lines') for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END"))
REPLACEMENTS = {
    'inventory_documents': (
        ("CONSTRAINT ck_inventory_document_kind CHECK (kind IN ('adjustment', 'recost', 'count', 'landed_cost', "
         "'shipment', 'shipment_cost'))",
         "CONSTRAINT ck_inventory_document_kind CHECK (kind IN ('adjustment', 'recost', 'count', 'landed_cost', "
         "'shipment', 'shipment_cost', 'shipment_return'))"),
    ),
}
CHANGED = tuple(REPLACEMENTS)
TABLES = ('shipment_return_lines', 'shipment_return_revisions', 'shipment_returns')
# What a test rewinding a head file drops: the guards, then the tables (children first).
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0103 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0103 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0103_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0103_' + name for name in CHANGED} | set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0103 reserved object already exists')
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
        temporary = '_co0103_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} '
                                          f'FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0103 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0103 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
