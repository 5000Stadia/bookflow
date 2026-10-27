"""Bins and cycle counts (R104 increment 5, design/advanced-inventory.md §2 and §6).

Bins and the bin column of every placement exist since co0089; this migration adds what using them needs
and what cycle counts keep: the ``cycle_classes`` list and ``items.cycle_class_id`` (null on every existing
item, so nothing is due), and the guards that keep bins exact in the database -- a placement names only a bin
of its own site, a bin sits in a bin of its own site one level up and never changes site, parent or level, a
bin and a cycle class are never deleted, and an item's default bin at a site is a bin of that site. Each guard
reads only its own row and the bin it names, never anything in replay order. No existing row is changed.
DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0097'
down_revision = 'co0095'
branch_labels = depends_on = None

DDL = "\nCREATE TABLE cycle_classes (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tinterval_days INTEGER NOT NULL, \n\tdescription VARCHAR(1000), \n\tactive BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_cycle_class_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_cycle_class_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_cycle_class_interval CHECK (typeof(interval_days) = 'integer' AND interval_days BETWEEN 1 AND 3660), \n\tCONSTRAINT ck_cycle_class_active CHECK (active IN (0,1))\n)\n\n"
COLUMN = 'ALTER TABLE items ADD COLUMN cycle_class_id VARCHAR(26) REFERENCES cycle_classes (id) ON DELETE RESTRICT'
# Every list reference on items is indexed, so a class's items are found without a scan.
INDEX = 'CREATE INDEX ix_items_cycle_class_id ON items (cycle_class_id)'
INDEX_NAME = 'ix_items_cycle_class_id'
GUARDS = (
    "CREATE TRIGGER inventory_placements_bin_site BEFORE INSERT ON inventory_placements WHEN NEW.bin_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins b WHERE b.id = NEW.bin_id AND b.site_id = NEW.site_id) BEGIN SELECT RAISE(ABORT, 'a placement names a bin of another site'); END",
    "CREATE TRIGGER inventory_bins_parent BEFORE INSERT ON inventory_bins WHEN (NEW.parent_id IS NULL AND NEW.depth != 1) OR (NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins p WHERE p.id = NEW.parent_id AND p.site_id = NEW.site_id AND p.depth = NEW.depth - 1)) BEGIN SELECT RAISE(ABORT, 'a bin sits in a bin of its own site, one level up'); END",
    "CREATE TRIGGER inventory_bins_fixed_place BEFORE UPDATE OF site_id, parent_id, depth ON inventory_bins WHEN NEW.site_id IS NOT OLD.site_id OR NEW.parent_id IS NOT OLD.parent_id OR NEW.depth IS NOT OLD.depth BEGIN SELECT RAISE(ABORT, 'a bin keeps its site, parent and level'); END",
    "CREATE TRIGGER inventory_bins_no_delete BEFORE DELETE ON inventory_bins BEGIN SELECT RAISE(ABORT, 'inventory bins are deactivated, never deleted'); END",
    "CREATE TRIGGER item_site_settings_bin_insert BEFORE INSERT ON item_site_settings WHEN NEW.default_bin_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins b WHERE b.id = NEW.default_bin_id AND b.site_id = NEW.site_id) BEGIN SELECT RAISE(ABORT, 'a default bin is a bin of its own site'); END",
    "CREATE TRIGGER item_site_settings_bin_update BEFORE UPDATE ON item_site_settings WHEN NEW.default_bin_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins b WHERE b.id = NEW.default_bin_id AND b.site_id = NEW.site_id) BEGIN SELECT RAISE(ABORT, 'a default bin is a bin of its own site'); END",
    "CREATE TRIGGER cycle_classes_no_delete BEFORE DELETE ON cycle_classes BEGIN SELECT RAISE(ABORT, 'cycle classes are deactivated, never deleted'); END",
)
# What a test rewinding a head file to co0095 drops: the triggers, the column and the table.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    connection = op.get_bind()
    names = {row[0].casefold() for row in connection.exec_driver_sql('SELECT name FROM sqlite_schema')}
    reserved = {'cycle_classes', INDEX_NAME, *(name.casefold() for name in TRIGGERS)}
    if names & reserved:
        raise RuntimeError('co0097 reserved object exists')
    if 'cycle_class_id' in {row[1] for row in connection.exec_driver_sql('PRAGMA table_xinfo(items)')}:
        raise RuntimeError('co0097 column already present: items.cycle_class_id')
    connection.exec_driver_sql(DDL)
    connection.exec_driver_sql(COLUMN)
    connection.exec_driver_sql(INDEX)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0097 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
