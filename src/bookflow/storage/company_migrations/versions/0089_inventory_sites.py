"""Advanced inventory, increment 1 (R104, design/advanced-inventory.md §2): sites, placements, transfers.

Nine new tables: inventory_sites (with the default site "Main"), inventory_bins, lots and
serial_numbers (empty until later increments, created now so placements reference them from the
start), item_site_settings, the three transfer tables and inventory_placements, the
quantity-only ledger of where stock is. One nullable site_id column each on work_lines,
purchase_order_lines and assembly_builds.

Every existing movement that moves quantity gets one unassigned placement at Main, carrying the
movement's own id, date, sequence and provenance (a reversal's placement retires its original's), so
an upgraded company reads exactly as before. The guards are created after the backfill: from then on
a movement is refused unless its placements, written first under a deferred foreign key, sum to its
quantity. Nothing existing is rewritten. DDL below is frozen: this migration never imports current
application metadata.
"""
import os
import time

from alembic import op

revision = 'co0089'
down_revision = 'co0084'
branch_labels = depends_on = None

DDL = ('\nCREATE TABLE inventory_sites (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tdescription VARCHAR(1000), \n\taddress TEXT, \n\tis_default BOOLEAN NOT NULL, \n\ttracks_bins BOOLEAN NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_inventory_site_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_inventory_site_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_inventory_site_flags CHECK (is_default IN (0,1) AND tracks_bins IN (0,1) AND active IN (0,1)), \n\tCONSTRAINT ck_inventory_site_default_active CHECK (is_default = 0 OR active = 1)\n)\n\n', '\nCREATE TABLE inventory_bins (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tparent_id VARCHAR(26), \n\tname VARCHAR(200) NOT NULL, \n\tfull_name VARCHAR(700) NOT NULL, \n\tfull_name_key VARCHAR(1400) NOT NULL, \n\tdepth INTEGER NOT NULL, \n\tbarcode VARCHAR(128), \n\tdescription VARCHAR(1000), \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_inventory_bin_full_name UNIQUE (site_id, full_name_key), \n\tCONSTRAINT ck_inventory_bin_depth CHECK (depth BETWEEN 1 AND 3), \n\tCONSTRAINT ck_inventory_bin_active CHECK (active IN (0,1)), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(parent_id) REFERENCES inventory_bins (id)\n)\n\n', '\nCREATE TABLE lots (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(128) NOT NULL, \n\tnumber_key VARCHAR(256) NOT NULL, \n\texpiry_date VARCHAR(10), \n\tmemo TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_lot_number UNIQUE (item_id, number_key), \n\tFOREIGN KEY(item_id) REFERENCES items (id)\n)\n\n', '\nCREATE TABLE serial_numbers (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(128) NOT NULL, \n\tnumber_key VARCHAR(256) NOT NULL, \n\tmemo TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_serial_number UNIQUE (item_id, number_key), \n\tFOREIGN KEY(item_id) REFERENCES items (id)\n)\n\n', '\nCREATE TABLE item_site_settings (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\treorder_point_min_microunits BIGINT, \n\treorder_point_max_microunits BIGINT, \n\tdefault_bin_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_item_site_setting UNIQUE (item_id, site_id), \n\tCONSTRAINT ck_item_site_reorder_min CHECK (reorder_point_min_microunits IS NULL OR reorder_point_min_microunits >= 0), \n\tCONSTRAINT ck_item_site_reorder_max CHECK (reorder_point_max_microunits IS NULL OR reorder_point_min_microunits IS NULL OR reorder_point_max_microunits >= reorder_point_min_microunits), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(default_bin_id) REFERENCES inventory_bins (id)\n)\n\n', "\nCREATE TABLE inventory_transfers (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tcurrent_revision_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_inventory_transfer_number UNIQUE (number), \n\tCONSTRAINT ck_inventory_transfer_status CHECK (status IN ('posted', 'voided')), \n\tFOREIGN KEY(current_revision_id) REFERENCES inventory_transfer_revisions (id) DEFERRABLE INITIALLY DEFERRED\n)\n\n", "\nCREATE TABLE inventory_transfer_revisions (\n\tid VARCHAR(26) NOT NULL, \n\ttransfer_id VARCHAR(26) NOT NULL, \n\trevision_number INTEGER NOT NULL, \n\toperation VARCHAR(16) NOT NULL, \n\tdate VARCHAR(10) NOT NULL, \n\tcount_position VARCHAR(8) NOT NULL, \n\tfrom_site_id VARCHAR(26) NOT NULL, \n\tto_site_id VARCHAR(26) NOT NULL, \n\tmemo TEXT, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_inventory_transfer_revision UNIQUE (transfer_id, revision_number), \n\tCONSTRAINT ck_inventory_transfer_operation CHECK (operation IN ('post', 'update', 'void')), \n\tCONSTRAINT ck_inventory_transfer_position CHECK (count_position IN ('before', 'after')), \n\tCONSTRAINT ck_inventory_transfer_date CHECK (date LIKE '____-__-__'), \n\tFOREIGN KEY(transfer_id) REFERENCES inventory_transfers (id), \n\tFOREIGN KEY(from_site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(to_site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE inventory_transfer_lines (\n\tid VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tline_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tunit_id VARCHAR(26), \n\tunit_factor_nanounits BIGINT NOT NULL, \n\tfrom_bin_id VARCHAR(26), \n\tto_bin_id VARCHAR(26), \n\tlot_id VARCHAR(26), \n\tdescription TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_inventory_transfer_line_position UNIQUE (revision_id, position), \n\tCONSTRAINT uq_inventory_transfer_line_identity UNIQUE (revision_id, line_id), \n\tCONSTRAINT ck_inventory_transfer_line_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_inventory_transfer_line_position CHECK (position > 0), \n\tFOREIGN KEY(revision_id) REFERENCES inventory_transfer_revisions (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(unit_id) REFERENCES unit_conversions (id), \n\tFOREIGN KEY(from_bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(to_bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(lot_id) REFERENCES lots (id)\n)\n\n", "\nCREATE TABLE inventory_placements (\n\tid VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tsite_id VARCHAR(26) NOT NULL, \n\tbin_id VARCHAR(26), \n\tlot_id VARCHAR(26), \n\tserial_number_id VARCHAR(26), \n\tquantity_microunits BIGINT NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tcount_position VARCHAR(8) NOT NULL, \n\tsequence BIGINT NOT NULL, \n\tsource VARCHAR(16) NOT NULL, \n\tmovement_id VARCHAR(26), \n\ttransfer_revision_id VARCHAR(26), \n\tsource_line_id VARCHAR(26), \n\treverses_placement_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_inventory_placement_reversal UNIQUE (reverses_placement_id), \n\tCONSTRAINT ck_inventory_placement_integers CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits != 0 AND typeof(sequence) = 'integer' AND sequence > 0), \n\tCONSTRAINT ck_inventory_placement_date CHECK (effective_date LIKE '____-__-__'), \n\tCONSTRAINT ck_inventory_placement_position CHECK (count_position IN ('before', 'after')), \n\tCONSTRAINT ck_inventory_placement_source CHECK (source IN ('movement', 'transfer', 'assignment', 'count')), \n\tCONSTRAINT ck_inventory_placement_owner CHECK ((source = 'movement') = (movement_id IS NOT NULL) AND (source = 'transfer') = (transfer_revision_id IS NOT NULL) AND (source = 'movement' OR source_line_id IS NOT NULL)), \n\tCONSTRAINT ck_inventory_placement_number CHECK (lot_id IS NULL OR serial_number_id IS NULL), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(site_id) REFERENCES inventory_sites (id), \n\tFOREIGN KEY(bin_id) REFERENCES inventory_bins (id), \n\tFOREIGN KEY(lot_id) REFERENCES lots (id), \n\tFOREIGN KEY(serial_number_id) REFERENCES serial_numbers (id), \n\tFOREIGN KEY(movement_id) REFERENCES inventory_movements (id) DEFERRABLE INITIALLY DEFERRED, \n\tFOREIGN KEY(transfer_revision_id) REFERENCES inventory_transfer_revisions (id) DEFERRABLE INITIALLY DEFERRED, \n\tFOREIGN KEY(reverses_placement_id) REFERENCES inventory_placements (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n")
INDEXES = ('CREATE UNIQUE INDEX uq_inventory_site_default ON inventory_sites (is_default) WHERE is_default = 1', 'CREATE INDEX ix_inventory_placements_item ON inventory_placements (item_id, effective_date, sequence)', 'CREATE INDEX ix_inventory_placements_movement ON inventory_placements (movement_id)', 'CREATE INDEX ix_inventory_placements_site ON inventory_placements (site_id, item_id, effective_date)', 'CREATE INDEX ix_inventory_placements_transfer ON inventory_placements (transfer_revision_id)')
GUARDS = (
    "CREATE TRIGGER inventory_placements_immutable_update BEFORE UPDATE ON inventory_placements BEGIN SELECT RAISE(ABORT, 'immutable inventory placements'); END",
    "CREATE TRIGGER inventory_placements_immutable_delete BEFORE DELETE ON inventory_placements BEGIN SELECT RAISE(ABORT, 'immutable inventory placements'); END",
    "CREATE TRIGGER inventory_transfer_revisions_immutable_update BEFORE UPDATE ON inventory_transfer_revisions BEGIN SELECT RAISE(ABORT, 'immutable inventory transfer revisions'); END",
    "CREATE TRIGGER inventory_transfer_revisions_immutable_delete BEFORE DELETE ON inventory_transfer_revisions BEGIN SELECT RAISE(ABORT, 'immutable inventory transfer revisions'); END",
    "CREATE TRIGGER inventory_transfer_lines_immutable_update BEFORE UPDATE ON inventory_transfer_lines BEGIN SELECT RAISE(ABORT, 'immutable inventory transfer lines'); END",
    "CREATE TRIGGER inventory_transfer_lines_immutable_delete BEFORE DELETE ON inventory_transfer_lines BEGIN SELECT RAISE(ABORT, 'immutable inventory transfer lines'); END",
    "CREATE TRIGGER inventory_movements_placed BEFORE INSERT ON inventory_movements WHEN NEW.quantity_microunits != coalesce((SELECT sum(p.quantity_microunits) FROM inventory_placements p WHERE p.movement_id = NEW.id), 0) OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.movement_id = NEW.id AND (p.item_id != NEW.item_id OR p.effective_date != NEW.effective_date OR p.sequence != NEW.sequence)) BEGIN SELECT RAISE(ABORT, 'inventory movement placements do not sum to its quantity'); END",
    "CREATE TRIGGER inventory_placements_open_movement BEFORE INSERT ON inventory_placements WHEN NEW.movement_id IS NOT NULL AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id = NEW.movement_id) BEGIN SELECT RAISE(ABORT, 'placement added to a movement already written'); END",
    "CREATE TRIGGER inventory_transfer_revisions_balanced BEFORE INSERT ON inventory_transfer_revisions WHEN EXISTS (SELECT 1 FROM inventory_placements p WHERE p.transfer_revision_id = NEW.id GROUP BY p.item_id, p.lot_id, p.serial_number_id HAVING sum(p.quantity_microunits) != 0) OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.transfer_revision_id = NEW.id AND p.reverses_placement_id IS NULL AND p.effective_date != NEW.date) BEGIN SELECT RAISE(ABORT, 'inventory transfer placements do not net to zero'); END",
    "CREATE TRIGGER inventory_placements_open_transfer BEFORE INSERT ON inventory_placements WHEN NEW.transfer_revision_id IS NOT NULL AND EXISTS (SELECT 1 FROM inventory_transfer_revisions r WHERE r.id = NEW.transfer_revision_id) BEGIN SELECT RAISE(ABORT, 'placement added to a transfer revision already written'); END",
    "CREATE TRIGGER inventory_placements_exact_reversal BEFORE INSERT ON inventory_placements WHEN NEW.reverses_placement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_placements o WHERE o.id = NEW.reverses_placement_id AND o.item_id = NEW.item_id AND o.site_id = NEW.site_id AND o.bin_id IS NEW.bin_id AND o.lot_id IS NEW.lot_id AND o.serial_number_id IS NEW.serial_number_id AND o.quantity_microunits = -NEW.quantity_microunits AND o.effective_date = NEW.effective_date) BEGIN SELECT RAISE(ABORT, 'inventory placement reversal is not exact'); END",
    "CREATE TRIGGER inventory_sites_no_delete BEFORE DELETE ON inventory_sites BEGIN SELECT RAISE(ABORT, 'inventory sites are deactivated, never deleted'); END",
)
COLUMNS = (
    'ALTER TABLE work_lines ADD COLUMN site_id VARCHAR(26) REFERENCES inventory_sites (id)',
    'ALTER TABLE purchase_order_lines ADD COLUMN site_id VARCHAR(26) REFERENCES inventory_sites (id)',
    'ALTER TABLE assembly_builds ADD COLUMN site_id VARCHAR(26) REFERENCES inventory_sites (id)',
)
TABLES = ('inventory_sites', 'inventory_bins', 'lots', 'serial_numbers', 'item_site_settings', 'inventory_transfers',
          'inventory_transfer_revisions', 'inventory_transfer_lines', 'inventory_placements')
BACKFILL = (
    "INSERT INTO inventory_placements (id, item_id, site_id, bin_id, lot_id, serial_number_id, quantity_microunits, "
    "effective_date, count_position, sequence, source, movement_id, transfer_revision_id, source_line_id, "
    "reverses_placement_id, created_at, created_by, created_via, audit_event_id) "
    "SELECT m.id, m.item_id, ?, NULL, NULL, NULL, m.quantity_microunits, m.effective_date, 'before', m.sequence, "
    "'movement', m.id, NULL, NULL, CASE WHEN m.kind = 'reversal' THEN m.reverses_movement_id END, "
    "m.created_at, m.created_by, m.created_via, m.audit_event_id "
    "FROM inventory_movements m WHERE m.quantity_microunits != 0 ORDER BY m.sequence")
_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


def _ulid():
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), 'big')
    return ''.join(_ALPHABET[(value >> (5 * (25 - n))) & 31] for n in range(26))


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(TABLES):
        raise RuntimeError('co0089 reserved object exists')
    for statement in DDL + INDEXES + COLUMNS:
        db.exec_driver_sql(statement)
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    site = _ulid()
    db.exec_driver_sql(
        "INSERT INTO inventory_sites (id, version, created_at, created_by, created_via, updated_at, updated_by, "
        "updated_via, name, name_key, description, address, is_default, tracks_bins, active) "
        "VALUES (?, 1, ?, 'system', 'migration', ?, 'system', 'migration', 'Main', 'main', NULL, NULL, 1, 0, 1)",
        (site, now, now))
    db.exec_driver_sql(BACKFILL, (site,))
    for statement in GUARDS:
        db.exec_driver_sql(statement)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0089 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
