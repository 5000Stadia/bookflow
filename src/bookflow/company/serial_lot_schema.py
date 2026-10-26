"""Serial and lot numbers (co0095, R104 increment 4; design/advanced-inventory.md §4).

Inventory parts and assemblies gain ``items.tracking``: ``none`` (the default), ``serial`` or ``lot``.
The numbers themselves live in ``lots`` and ``serial_numbers`` (co0089); where a number is comes only
from placements, so a number's placements, plus the unassigned bucket, always sum to what a place
holds -- the quantity tie, one level down.

``inventory_assignments`` numbers units later and posts nothing: it moves units at one place between
the unassigned bucket and lots or serial numbers, either units on hand on a date or the units one
document line moved (``movement_id``). Its placements are written first and the row is refused unless
they net to zero per item and place, as a transfer revision's are.

The guards, all in the database so they hold when the application is bypassed:

- a serial number places one whole base unit either way, and a lot or serial number belongs to the
  placement's own item, which tracks that kind of number;
- a serial number's running quantity, in replay order (date, count position, sequence), stays within
  -1 to 1: -1 is a unit sold before its receipt is recorded, and 2 is a second unit of one number in
  stock, which the database refuses (as it refuses -2). Only standing placements count: a placement a
  reversal retires and the reversal itself are left out together. A movement and a transfer revision are checked when their row is
  written (their placements are all in place by then); an assignment's and a count's placements, one
  per number, as each is written;
- an assignment's placements net to zero per item and place, and nothing is added to one once written.
"""
import sqlalchemy as sa

TRACKING = ('none', 'serial', 'lot')
ASSIGNMENT_DIRECTIONS = ('assign', 'unassign')
UNIT = 1_000_000


def define_tables(metadata, C, T):
    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    assignments = T('inventory_assignments',
        ident('id', 'Stable ULID of this immutable assignment.', primary_key=True),
        ident('item_id', 'Tracked item whose units are numbered.', 'items.id'),
        ident('site_id', 'Site the units are at.', 'inventory_sites.id'),
        ident('bin_id', 'Bin the units are in; null for no bin.', 'inventory_bins.id', nullable=True),
        C('date', sa.String(10), 'Date the numbering applies from, YYYY-MM-DD (a document line\'s own date).', nullable=False),
        C('count_position', sa.String(8), 'before or after the counts of its site on its date.', nullable=False),
        C('direction', sa.String(8), 'assign (unassigned units take numbers) or unassign (numbered units become unassigned).', nullable=False),
        ident('movement_id', 'Movement whose units this numbers (which unit a sale took); null for units on hand.',
              'inventory_movements.id', nullable=True),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        C('reason', sa.Text, 'Why the units were numbered, as the caller gave it.', nullable=True),
        C('created_at', sa.String(32), 'UTC time this assignment was recorded.', nullable=False),
        C('created_by', sa.String(26), 'Company principal that recorded this assignment.', nullable=False),
        C('created_via', sa.String(16), 'Interface that recorded this assignment.', nullable=False),
        ident('audit_event_id', 'Audit event that committed this assignment.', 'audit_events.id'),
        sa.CheckConstraint("date LIKE '____-__-__'", name='ck_inventory_assignment_date'),
        sa.CheckConstraint("count_position IN ('before', 'after')", name='ck_inventory_assignment_position'),
        sa.CheckConstraint("direction IN ('assign', 'unassign')", name='ck_inventory_assignment_direction'),
        sa.Index('ix_inventory_assignments_item', 'item_id', 'date'),
        sa.Index('ix_inventory_assignments_movement', 'movement_id'),
        description='Numbering of tracked units after the fact: units at one place moved between unassigned and '
                    'lots or serial numbers. Posts nothing.')
    return {'inventory_assignments': assignments}


def tracking_column(C):
    return C('tracking', sa.String(8), 'Number tracking of an inventory part or assembly: none, serial or lot.',
             sa.CheckConstraint("tracking IN ('none', 'serial', 'lot')", name='ck_items_tracking'),
             nullable=False, default='none', server_default=sa.text("'none'"))


# Replay order of a placement: date, then its count position's rank, then sequence.
_RANK = "(CASE {p}.count_position WHEN 'before' THEN 0 WHEN 'count' THEN 1 ELSE 2 END)"


def _standing(p):
    """SQL true when placement alias ``p`` stands: it is no reversal, and no reversal retires it. A reversal keeps
    its original's date and position under a later sequence, so a retired pair is left out as a whole, never
    read as two steps (as ``serial_lots.check`` reads it)."""
    return (f"{p}.reverses_placement_id IS NULL AND NOT EXISTS (SELECT 1 FROM inventory_placements x "
            f"WHERE x.reverses_placement_id = {p}.id)")


def _out_of_range(serial, groups):
    """SQL true when the standing placements of ``serial`` run outside -1 to 1 unit at the step of ``groups``
    (an alias over that serial's standing placements)."""
    return (f"(SELECT sum(r.quantity_microunits) FROM inventory_placements r WHERE r.serial_number_id = {serial} "
            f"AND {_standing('r')} AND (r.effective_date, {_RANK.format(p='r')}, r.sequence) <= "
            f"({groups}.effective_date, {_RANK.format(p=groups)}, {groups}.sequence)) NOT BETWEEN -{UNIT} AND {UNIT}")


def _owner_range(owner_column):
    return (f"EXISTS (SELECT 1 FROM inventory_placements n JOIN inventory_placements g "
            f"ON g.serial_number_id = n.serial_number_id WHERE n.{owner_column} = NEW.id "
            f"AND n.serial_number_id IS NOT NULL AND {_standing('g')} AND {_out_of_range('n.serial_number_id', 'g')})")


_MESSAGE = "'a serial number would be in stock twice, or owed twice (below -1)'"


GUARDS = (
    "CREATE TRIGGER inventory_assignments_immutable_update BEFORE UPDATE ON inventory_assignments "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory assignments'); END",
    "CREATE TRIGGER inventory_assignments_immutable_delete BEFORE DELETE ON inventory_assignments "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory assignments'); END",
    # A serial number is one whole unit; a number is the placement's own item's, of the kind it tracks.
    "CREATE TRIGGER inventory_placements_serial_unit BEFORE INSERT ON inventory_placements "
    f"WHEN NEW.serial_number_id IS NOT NULL AND abs(NEW.quantity_microunits) != {UNIT} "
    "BEGIN SELECT RAISE(ABORT, 'a serial number places one whole unit'); END",
    "CREATE TRIGGER inventory_placements_number_item BEFORE INSERT ON inventory_placements "
    "WHEN (NEW.lot_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM lots l WHERE l.id = NEW.lot_id AND l.item_id = NEW.item_id)) "
    "OR (NEW.serial_number_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM serial_numbers n "
    "WHERE n.id = NEW.serial_number_id AND n.item_id = NEW.item_id)) "
    "OR (NEW.reverses_placement_id IS NULL AND ((NEW.lot_id IS NOT NULL AND (SELECT tracking FROM items "
    "WHERE id = NEW.item_id) != 'lot') OR (NEW.serial_number_id IS NOT NULL AND (SELECT tracking FROM items "
    "WHERE id = NEW.item_id) != 'serial'))) "
    "BEGIN SELECT RAISE(ABORT, 'a placement names a number of another item or of a kind its item does not track'); END",
    # A serial number's running quantity stays within -1 to 1: never two of one number in stock.
    "CREATE TRIGGER inventory_movements_serial_range BEFORE INSERT ON inventory_movements "
    f"WHEN {_owner_range('movement_id')} "
    f"BEGIN SELECT RAISE(ABORT, {_MESSAGE}); END",
    "CREATE TRIGGER inventory_transfer_revisions_serial_range BEFORE INSERT ON inventory_transfer_revisions "
    f"WHEN {_owner_range('transfer_revision_id')} "
    f"BEGIN SELECT RAISE(ABORT, {_MESSAGE}); END",
    # A standing assignment or count placement is checked as it is written (one per number each); a reversal
    # retires a placement exactly and is checked with the change it belongs to.
    "CREATE TRIGGER inventory_placements_serial_range BEFORE INSERT ON inventory_placements "
    "WHEN NEW.serial_number_id IS NOT NULL AND NEW.source IN ('assignment', 'count') "
    "AND NEW.reverses_placement_id IS NULL AND EXISTS ("
    f"SELECT 1 FROM (SELECT NEW.effective_date AS d, {_RANK.format(p='NEW')} AS k, NEW.sequence AS q "
    f"UNION SELECT effective_date, {_RANK.format(p='inventory_placements')}, sequence FROM inventory_placements "
    "WHERE serial_number_id = NEW.serial_number_id) g WHERE "
    "coalesce((SELECT sum(r.quantity_microunits) FROM inventory_placements r "
    f"WHERE r.serial_number_id = NEW.serial_number_id AND {_standing('r')} "
    f"AND (r.effective_date, {_RANK.format(p='r')}, r.sequence) <= (g.d, g.k, g.q)), 0) "
    f"+ (CASE WHEN (NEW.effective_date, {_RANK.format(p='NEW')}, NEW.sequence) <= (g.d, g.k, g.q) "
    f"THEN NEW.quantity_microunits ELSE 0 END) NOT BETWEEN -{UNIT} AND {UNIT}) "
    f"BEGIN SELECT RAISE(ABORT, {_MESSAGE}); END",
    # An assignment's placements net to zero per item and place, on its own date; nothing joins it later.
    "CREATE TRIGGER inventory_assignments_balanced BEFORE INSERT ON inventory_assignments "
    "WHEN NOT EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'assignment' AND p.source_line_id = NEW.id) "
    "OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'assignment' AND p.source_line_id = NEW.id "
    "GROUP BY p.item_id, p.site_id, p.bin_id HAVING sum(p.quantity_microunits) != 0) "
    "OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.source = 'assignment' AND p.source_line_id = NEW.id "
    "AND (p.item_id != NEW.item_id OR p.site_id != NEW.site_id OR p.bin_id IS NOT NEW.bin_id "
    "OR p.effective_date != NEW.date OR p.reverses_placement_id IS NOT NULL)) "
    "BEGIN SELECT RAISE(ABORT, 'inventory assignment placements do not net to zero at their place'); END",
    "CREATE TRIGGER inventory_placements_open_assignment BEFORE INSERT ON inventory_placements "
    "WHEN NEW.source = 'assignment' AND EXISTS (SELECT 1 FROM inventory_assignments a WHERE a.id = NEW.source_line_id) "
    "BEGIN SELECT RAISE(ABORT, 'placement added to an inventory assignment already written'); END",
)

INDEXES = (
    'CREATE INDEX ix_inventory_placements_serial ON inventory_placements (serial_number_id)',
    'CREATE INDEX ix_inventory_placements_lot ON inventory_placements (lot_id)',
)
