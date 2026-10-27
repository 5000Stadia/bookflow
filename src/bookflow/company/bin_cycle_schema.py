"""Bins and cycle counts (co0097, R104 increment 5; design/advanced-inventory.md §2 and §6).

Bins (``inventory_bins``) and the bin on every placement already exist (co0089); this increment uses them.
What it adds:

- ``cycle_classes``: a list of count intervals (name, interval in days), seeded on first use as A 30, B 90
  and C 365; ``items.cycle_class_id`` names an item's class (null: never due).
- Guards that keep bins exact in the database, each checked on its own row, so none reads anything in replay
  order: a placement names only a bin of its own site; a bin's parent is a bin of the same site one level up;
  a bin never changes site, parent or depth and is never deleted; an item's default bin at a site is a bin of
  that site. Bins sum to their site by construction (a placement carries both), so nothing has to add them up.
"""
import sqlalchemy as sa


def define_tables(metadata, C, T, common):
    classes = T('cycle_classes',
        *common(),
        C('name', sa.String(200), 'Class name as entered, such as A.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('interval_days', sa.Integer, 'Days between counts of an item in this class at a site.', nullable=False),
        C('description', sa.String(1000), 'Description as entered; null when blank.', nullable=True),
        C('active', sa.Boolean, 'Whether items may be given this class.', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_cycle_class_name_key'),
        sa.CheckConstraint('length(trim(name)) > 0', name='ck_cycle_class_name_nonblank'),
        sa.CheckConstraint("typeof(interval_days) = 'integer' AND interval_days BETWEEN 1 AND 3660", name='ck_cycle_class_interval'),
        sa.CheckConstraint('active IN (0,1)', name='ck_cycle_class_active'),
        description='Cycle-count classes: how often an item in the class is due to be counted at each site.')
    return {'cycle_classes': classes}


def cycle_class_column(C):
    return C('cycle_class_id', sa.String(26), 'Cycle-count class of the item; null when it is never due for a cycle count.',
             sa.ForeignKey('cycle_classes.id', ondelete='RESTRICT'), nullable=True)  # as every list reference on items


GUARDS = (
    "CREATE TRIGGER inventory_placements_bin_site BEFORE INSERT ON inventory_placements "
    "WHEN NEW.bin_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins b WHERE b.id = NEW.bin_id "
    "AND b.site_id = NEW.site_id) "
    "BEGIN SELECT RAISE(ABORT, 'a placement names a bin of another site'); END",
    "CREATE TRIGGER inventory_bins_parent BEFORE INSERT ON inventory_bins "
    "WHEN (NEW.parent_id IS NULL AND NEW.depth != 1) OR (NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM "
    "inventory_bins p WHERE p.id = NEW.parent_id AND p.site_id = NEW.site_id AND p.depth = NEW.depth - 1)) "
    "BEGIN SELECT RAISE(ABORT, 'a bin sits in a bin of its own site, one level up'); END",
    "CREATE TRIGGER inventory_bins_fixed_place BEFORE UPDATE OF site_id, parent_id, depth ON inventory_bins "
    "WHEN NEW.site_id IS NOT OLD.site_id OR NEW.parent_id IS NOT OLD.parent_id OR NEW.depth IS NOT OLD.depth "
    "BEGIN SELECT RAISE(ABORT, 'a bin keeps its site, parent and level'); END",
    "CREATE TRIGGER inventory_bins_no_delete BEFORE DELETE ON inventory_bins "
    "BEGIN SELECT RAISE(ABORT, 'inventory bins are deactivated, never deleted'); END",
    "CREATE TRIGGER item_site_settings_bin_insert BEFORE INSERT ON item_site_settings "
    "WHEN NEW.default_bin_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins b WHERE b.id = NEW.default_bin_id "
    "AND b.site_id = NEW.site_id) "
    "BEGIN SELECT RAISE(ABORT, 'a default bin is a bin of its own site'); END",
    "CREATE TRIGGER item_site_settings_bin_update BEFORE UPDATE ON item_site_settings "
    "WHEN NEW.default_bin_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_bins b WHERE b.id = NEW.default_bin_id "
    "AND b.site_id = NEW.site_id) "
    "BEGIN SELECT RAISE(ABORT, 'a default bin is a bin of its own site'); END",
    "CREATE TRIGGER cycle_classes_no_delete BEFORE DELETE ON cycle_classes "
    "BEGIN SELECT RAISE(ABORT, 'cycle classes are deactivated, never deleted'); END",
)
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)
