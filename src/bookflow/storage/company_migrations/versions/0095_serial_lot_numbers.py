"""Serial and lot numbers (R104 increment 4, design/advanced-inventory.md §4).

``items.tracking`` (none, serial or lot; every existing item is ``none``, so nothing it holds changes),
the ``inventory_assignments`` table that numbers units after the fact, two indexes on placements (by
serial number and by lot), and the guards that keep numbers exact in the database: a serial number
places one whole unit and is never in stock twice (its running quantity, in replay order, stays within
-1 to 1), a number belongs to its placement's own item, and an assignment's placements net to zero at
their place. ``lots`` and ``serial_numbers`` already exist (co0089). No existing row is changed: the
column is added with its default, and every stored placement is unassigned. DDL below is frozen: this
migration never imports current application metadata.
"""
from alembic import op

revision = 'co0095'
down_revision = 'co0085'
branch_labels = depends_on = None

COLUMN = "ALTER TABLE items ADD COLUMN tracking VARCHAR(8) DEFAULT 'none' NOT NULL CONSTRAINT ck_items_tracking CHECK (tracking IN ('none', 'serial', 'lot'))"
DDL = "\nCREATE TABLE inventory_assignments (\n\tid VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tbin_id VARCHAR(26), \n\tdate VARCHAR(10) NOT NULL, \n\tcount_position VARCHAR(8) NOT NULL, \n\tdirection VARCHAR(8) NOT NULL, \n\tmovement_id VARCHAR(26), \n\tmemo TEXT, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_inventory_assignment_date CHECK (date LIKE '____-__-__'), \n\tCONSTRAINT ck_inventory_assignment_position CHECK (count_position IN ('before', 'after')), \n\tCONSTRAINT ck_inventory_assignment_direction CHECK (direction IN ('assign', 'unassign')), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(movement_id) REFERENCES inventory_movements (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n"
INDEXES = ('CREATE INDEX ix_inventory_assignments_item ON inventory_assignments (item_id, date)',
           'CREATE INDEX ix_inventory_assignments_movement ON inventory_assignments (movement_id)',
           'CREATE INDEX ix_inventory_placements_serial ON inventory_placements (serial_number_id)',
           'CREATE INDEX ix_inventory_placements_lot ON inventory_placements (lot_id)')
GUARDS = (
    "CREATE TRIGGER inventory_assignments_immutable_update BEFORE UPDATE ON inventory_assignments BEGIN SELECT RAISE(ABORT, 'immutable inventory assignments'); END",
    "CREATE TRIGGER inventory_assignments_immutable_delete BEFORE DELETE ON inventory_assignments BEGIN SELECT RAISE(ABORT, 'immutable inventory assignments'); END",
    "CREATE TRIGGER inventory_placements_serial_unit BEFORE INSERT ON inventory_placements WHEN NEW.serial_number_id IS NOT NULL AND abs(NEW.quantity_microunits) != 1000000 BEGIN SELECT RAISE(ABORT, 'a serial number places one whole unit'); END",
    "CREATE TRIGGER inventory_placements_number_item BEFORE INSERT ON inventory_placements WHEN (NEW.lot_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM lots l WHERE l.id = NEW.lot_id AND l.item_id = NEW.item_id)) OR (NEW.serial_number_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM serial_numbers n WHERE n.id = NEW.serial_number_id AND n.item_id = NEW.item_id)) OR (NEW.reverses_placement_id IS NULL AND ((NEW.lot_id IS NOT NULL AND (SELECT tracking FROM items WHERE id = NEW.item_id) != 'lot') OR (NEW.serial_number_id IS NOT NULL AND (SELECT tracking FROM items WHERE id = NEW.item_id) != 'serial'))) BEGIN SELECT RAISE(ABORT, 'a placement names a number of another item or of a kind its item does not track'); END",
    "CREATE TRIGGER inventory_movements_serial_range BEFORE INSERT ON inventory_movements WHEN EXISTS (SELECT 1 FROM inventory_placements n JOIN inventory_placements g ON g.serial_number_id = n.serial_number_id WHERE n.movement_id = NEW.id AND n.serial_number_id IS NOT NULL AND (SELECT sum(r.quantity_microunits) FROM inventory_placements r WHERE r.serial_number_id = n.serial_number_id AND (r.effective_date, (CASE r.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), r.sequence) <= (g.effective_date, (CASE g.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), g.sequence)) NOT BETWEEN -1000000 AND 1000000) BEGIN SELECT RAISE(ABORT, 'a serial number would be in stock twice, or taken twice'); END",
    "CREATE TRIGGER inventory_transfer_revisions_serial_range BEFORE INSERT ON inventory_transfer_revisions WHEN EXISTS (SELECT 1 FROM inventory_placements n JOIN inventory_placements g ON g.serial_number_id = n.serial_number_id WHERE n.transfer_revision_id = NEW.id AND n.serial_number_id IS NOT NULL AND (SELECT sum(r.quantity_microunits) FROM inventory_placements r WHERE r.serial_number_id = n.serial_number_id AND (r.effective_date, (CASE r.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), r.sequence) <= (g.effective_date, (CASE g.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), g.sequence)) NOT BETWEEN -1000000 AND 1000000) BEGIN SELECT RAISE(ABORT, 'a serial number would be in stock twice, or taken twice'); END",
    "CREATE TRIGGER inventory_placements_serial_range BEFORE INSERT ON inventory_placements WHEN NEW.serial_number_id IS NOT NULL AND NEW.source IN ('assignment', 'count') AND EXISTS (SELECT 1 FROM (SELECT NEW.effective_date AS d, (CASE NEW.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END) AS k, NEW.sequence AS q UNION SELECT effective_date, (CASE inventory_placements.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), sequence FROM inventory_placements WHERE serial_number_id = NEW.serial_number_id) g WHERE coalesce((SELECT sum(r.quantity_microunits) FROM inventory_placements r WHERE r.serial_number_id = NEW.serial_number_id AND (r.effective_date, (CASE r.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), r.sequence) <= (g.d, g.k, g.q)), 0) + (CASE WHEN (NEW.effective_date, (CASE NEW.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END), NEW.sequence) <= (g.d, g.k, g.q) THEN NEW.quantity_microunits ELSE 0 END) NOT BETWEEN -1000000 AND 1000000) BEGIN SELECT RAISE(ABORT, 'a serial number would be in stock twice, or taken twice'); END",
    "CREATE TRIGGER inventory_assignments_balanced BEFORE INSERT ON inventory_assignments WHEN NOT EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'assignment' AND p.source_line_id = NEW.id) OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'assignment' AND p.source_line_id = NEW.id GROUP BY p.item_id, p.site_id, p.bin_id HAVING sum(p.quantity_microunits) != 0) OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'assignment' AND p.source_line_id = NEW.id AND (p.item_id != NEW.item_id OR p.site_id != NEW.site_id OR p.bin_id IS NOT NEW.bin_id OR p.effective_date != NEW.date OR p.reverses_placement_id IS NOT NULL)) BEGIN SELECT RAISE(ABORT, 'inventory assignment placements do not net to zero at their place'); END",
    "CREATE TRIGGER inventory_placements_open_assignment BEFORE INSERT ON inventory_placements WHEN NEW.source = 'assignment' AND EXISTS (SELECT 1 FROM inventory_assignments a WHERE a.id = NEW.source_line_id) BEGIN SELECT RAISE(ABORT, 'placement added to an inventory assignment already written'); END",
)
# What a test rewinding a head file to co0085 drops: the triggers first, then the indexes and the table.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)
INDEX_NAMES = ('ix_inventory_placements_serial', 'ix_inventory_placements_lot')


def upgrade():
    connection = op.get_bind()
    names = {row[0].casefold() for row in connection.exec_driver_sql('SELECT name FROM sqlite_schema')}
    reserved = {'inventory_assignments', *INDEX_NAMES, *(name.casefold() for name in TRIGGERS)}
    if names & reserved:
        raise RuntimeError('co0095 reserved object exists')
    if 'tracking' in {row[1] for row in connection.exec_driver_sql('PRAGMA table_xinfo(items)')}:
        raise RuntimeError('co0095 column already present: items.tracking')
    connection.exec_driver_sql(COLUMN)
    connection.exec_driver_sql(DDL)
    for statement in INDEXES + GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0095 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
