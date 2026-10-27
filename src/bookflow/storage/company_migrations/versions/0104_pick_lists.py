"""Pick lists (R181 increment 3, design/pick-pack-ship.md §4, §8).

Five new tables -- ``pick_lists``, ``pick_list_revisions``, ``pick_list_lines``, ``pick_entries`` and
``pick_list_shipments`` -- every row immutable (revisions and entries append), with two guards that a pick's staging
placements net to zero. Three changes to existing objects, moving no stored row: ``inventory_placements.source``
admits ``pick`` (SQLite cannot alter a CHECK in place, so the table is rebuilt with the constraint respelled and every
value, row identity, index and trigger carried across verbatim, as co0103 does); ``inventory_sites.staging_bin_id``
(null: picks move nothing); ``company_info.pick_and_pack_enabled`` (off). DDL below is frozen: this migration never
imports current application metadata.
"""
import importlib

import sqlalchemy as sa
from alembic import op

revision = 'co0104'
down_revision = 'co0103'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE pick_lists (\n\tid VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tpick_date VARCHAR(10) NOT NULL, \n\tpicker VARCHAR(200), \n\tmemo TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pick_list_date CHECK (pick_date LIKE '____-__-__'), \n\tCONSTRAINT uq_pick_lists_number UNIQUE (number), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE pick_list_revisions (\n\tid VARCHAR(26) NOT NULL, \n\tpick_list_id VARCHAR(26) NOT NULL, \n\trevision_number INTEGER NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pick_list_revision_status CHECK (status IN ('open', 'done', 'cancelled')), \n\tCONSTRAINT ck_pick_list_revision_number CHECK (typeof(revision_number) = 'integer' AND revision_number > 0), \n\tCONSTRAINT uq_pick_list_revision_number UNIQUE (pick_list_id, revision_number), \n\tFOREIGN KEY(pick_list_id) REFERENCES pick_lists (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE pick_list_lines (\n\tid VARCHAR(26) NOT NULL, \n\tpick_list_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tsales_order_id VARCHAR(26) NOT NULL, \n\torder_line_id VARCHAR(26) NOT NULL, \n\troot_document_id VARCHAR(26) NOT NULL, \n\troot_line_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tbin_id VARCHAR(26), \n\tlot_id VARCHAR(26), \n\tshort BOOLEAN NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pick_list_line_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_pick_list_line_position CHECK (position > 0), \n\tCONSTRAINT uq_pick_list_line_position UNIQUE (pick_list_id, position), \n\tFOREIGN KEY(pick_list_id) REFERENCES pick_lists (id), \n\tFOREIGN KEY(sales_order_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(lot_id) REFERENCES lots (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE pick_entries (\n\tid VARCHAR(26) NOT NULL, \n\tpick_list_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\tordinal INTEGER NOT NULL, \n\tmode VARCHAR(4) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tbin_id VARCHAR(26), \n\tlot_id VARCHAR(26), \n\tserial_number_id VARCHAR(26), \n\tscanned_code VARCHAR(200), \n\tstaging_bin_id VARCHAR(26), \n\tentry_date VARCHAR(10) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_pick_entry_mode CHECK (mode IN ('add', 'set')), \n\tCONSTRAINT ck_pick_entry_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits >= 0), \n\tCONSTRAINT ck_pick_entry_date CHECK (entry_date LIKE '____-__-__'), \n\tCONSTRAINT ck_pick_entry_number CHECK (lot_id IS NULL OR serial_number_id IS NULL), \n\tCONSTRAINT uq_pick_entry_ordinal UNIQUE (pick_list_id, ordinal), \n\tFOREIGN KEY(pick_list_id) REFERENCES pick_lists (id), \n\tFOREIGN KEY(line_id) REFERENCES pick_list_lines (id), \n\tFOREIGN KEY(bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(lot_id) REFERENCES lots (id), \n\tFOREIGN KEY(serial_number_id) REFERENCES serial_numbers (id), \n\tFOREIGN KEY(staging_bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", '\nCREATE TABLE pick_list_shipments (\n\tid VARCHAR(26) NOT NULL, \n\tpick_list_id VARCHAR(26) NOT NULL, \n\tshipment_id VARCHAR(26) NOT NULL, \n\tsales_order_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_pick_list_shipment UNIQUE (pick_list_id, shipment_id), \n\tFOREIGN KEY(pick_list_id) REFERENCES pick_lists (id), \n\tFOREIGN KEY(shipment_id) REFERENCES shipments (id), \n\tFOREIGN KEY(sales_order_id) REFERENCES work_documents (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n')
INDEXES = ('CREATE INDEX ix_pick_list_lines_root ON pick_list_lines (root_document_id, root_line_id)', 'CREATE INDEX ix_pick_entries_line ON pick_entries (line_id, ordinal)', 'CREATE INDEX ix_pick_list_shipments_shipment ON pick_list_shipments (shipment_id)')
GUARDS = tuple(
    statement for table in ('pick_lists', 'pick_list_revisions', 'pick_list_lines', 'pick_entries',
                            'pick_list_shipments') for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END")) + (
    "CREATE TRIGGER pick_entries_balanced BEFORE INSERT ON pick_entries "
    "WHEN EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'pick' AND p.source_line_id = NEW.id "
    "GROUP BY p.item_id, p.lot_id, p.serial_number_id HAVING sum(p.quantity_microunits) != 0) "
    "BEGIN SELECT RAISE(ABORT, 'pick placements do not net to zero'); END",
    "CREATE TRIGGER pick_list_revisions_balanced BEFORE INSERT ON pick_list_revisions "
    "WHEN EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'pick' AND p.source_line_id = NEW.id "
    "GROUP BY p.item_id, p.lot_id, p.serial_number_id HAVING sum(p.quantity_microunits) != 0) "
    "BEGIN SELECT RAISE(ABORT, 'pick placements do not net to zero'); END",
    "CREATE TRIGGER inventory_placements_open_pick BEFORE INSERT ON inventory_placements "
    "WHEN NEW.source = 'pick' AND (EXISTS (SELECT 1 FROM pick_entries e WHERE e.id = NEW.source_line_id) "
    "OR EXISTS (SELECT 1 FROM pick_list_revisions r WHERE r.id = NEW.source_line_id)) "
    "BEGIN SELECT RAISE(ABORT, 'placement added to a pick already written'); END",
)
REPLACEMENTS = {
    'inventory_placements': (
        ("CONSTRAINT ck_inventory_placement_source CHECK (source IN ('movement', 'transfer', 'assignment', 'count'))",
         "CONSTRAINT ck_inventory_placement_source CHECK (source IN ('movement', 'transfer', 'assignment', 'count', "
         "'pick'))"),
    ),
}
CHANGED = tuple(REPLACEMENTS)
TABLES = ('pick_list_shipments', 'pick_entries', 'pick_list_lines', 'pick_list_revisions', 'pick_lists')
COMPANY_COLUMNS = ('pick_and_pack_enabled',)
SITE_COLUMNS = ('staging_bin_id',)
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
        raise RuntimeError('co0104 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0104 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0104_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0104_' + name for name in CHANGED} | set(TABLES) | set(TRIGGERS)
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0104 reserved object already exists')
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
        temporary = '_co0104_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} '
                                          f'FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0104 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    op.add_column('company_info', sa.Column('pick_and_pack_enabled', sa.Boolean, nullable=False, server_default='0'))
    connection.exec_driver_sql('ALTER TABLE inventory_sites ADD COLUMN staging_bin_id VARCHAR(26) '
                               'REFERENCES inventory_bins (id)')
    for statement in DDL + INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0104 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
