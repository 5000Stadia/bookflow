"""Where stock is: sites, bins, lot and serial numbers, placements and transfers (co0089, R104).

Design: ``design/advanced-inventory.md`` §2. ``inventory_movements`` stays the ledger of quantity and
value per item. Where the units are -- at which site, and later at which bin, lot or serial number --
is a second fact with another shape, so it has its own append-only, quantity-only ledger,
``inventory_placements``:

- every movement that moves quantity is *placed*: its placements are written before it, under a
  deferred foreign key, and a trigger refuses the movement unless they sum to its quantity, and
  refuses a placement added to a movement already written;
- a transfer revision's placements net to zero per item, lot and serial number, checked the same
  way when the revision row is written;
- so on every date an item's placements sum to the quantity the replay holds (the *quantity tie*,
  asserted by every reading by site beside the asset tie).

A placement with no lot and no serial number on a tracked item is the **unassigned** bucket; lots,
serial numbers and bins are created empty now (they are used from later increments) so placements
reference them from the start. Placements are never updated or deleted.

A transfer moves quantity, never value: it writes placements only, no journal entry and no movement,
so cost (one average per item, company-wide) and the inventory asset are untouched.
"""
import sqlalchemy as sa

# ``pick`` moves picked units to a site's staging bin (R181 increment 3, co0104).
PLACEMENT_SOURCES = ('movement', 'transfer', 'assignment', 'count', 'pick')
COUNT_POSITIONS = ('before', 'after')
# A placement may also be a count's own difference, between that date's before and after (increment 2).
PLACEMENT_POSITIONS = ('before', 'count', 'after')
TRANSFER_STATUSES = ('posted', 'voided')
TRANSFER_OPERATIONS = ('post', 'update', 'void')


def define_tables(metadata, C, T, common):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False, deferred=False):
        constraints = []
        if target:
            constraints.append(sa.ForeignKey(target, deferrable=True, initially='DEFERRED') if deferred
                               else sa.ForeignKey(target))
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    def provenance(what):
        return (C('created_at', sa.String(32), f'UTC time this {what} was recorded.', nullable=False),
                C('created_by', sa.String(26), f'Company principal that recorded this {what}.', nullable=False),
                C('created_via', sa.String(16), f'Interface that recorded this {what}.', nullable=False),
                ident('audit_event_id', f'Audit event that committed this {what}.', 'audit_events.id'))

    sites = T('inventory_sites',
        *common(),
        C('name', sa.String(200), 'Site name as entered: a warehouse, a van, a consignment location.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('description', sa.String(1000), 'What or where the site is, as entered; null when blank.', nullable=True),
        C('address', sa.Text, 'Postal address of the site as free text; null when blank.', nullable=True),
        C('is_default', sa.Boolean, 'Whether stock lines naming no site are placed here; exactly one active site is.', nullable=False),
        C('tracks_bins', sa.Boolean, 'Whether stock at this site is located to bins (bins arrive in a later increment).', nullable=False),
        C('active', sa.Boolean, 'Whether new stock lines and transfers may name this site.', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_inventory_site_name_key'),
        sa.CheckConstraint('length(trim(name)) > 0', name='ck_inventory_site_name_nonblank'),
        sa.CheckConstraint('is_default IN (0,1) AND tracks_bins IN (0,1) AND active IN (0,1)', name='ck_inventory_site_flags'),
        sa.CheckConstraint('is_default = 0 OR active = 1', name='ck_inventory_site_default_active'),
        description='Inventory sites: the places the company keeps its own stock (warehouses, vans, consignment).')
    sa.Index('uq_inventory_site_default', sites.c.is_default, unique=True, sqlite_where=sa.text('is_default = 1'))

    bins = T('inventory_bins',
        *common(),
        ident('site_id', 'Site this bin is in.', 'inventory_sites.id'),
        ident('parent_id', 'Enclosing bin at the same site; null at the top level.', 'inventory_bins.id', nullable=True),
        C('name', sa.String(200), 'This level of the bin name, as entered.', nullable=False),
        C('full_name', sa.String(700), 'Colon-joined path such as A:3:Top.', nullable=False),
        C('full_name_key', sa.String(1400), 'Case-folded full name; unique per site.', nullable=False),
        C('depth', sa.Integer, 'Level of the bin, 1 to 3 (pallet, row, section).', nullable=False),
        C('barcode', sa.String(128), 'Scannable label code; null when none.', nullable=True),
        C('description', sa.String(1000), 'Description as entered; null when blank.', nullable=True),
        C('active', sa.Boolean, 'Whether new stock lines may name this bin.', nullable=False),
        sa.UniqueConstraint('site_id', 'full_name_key', name='uq_inventory_bin_full_name'),
        sa.CheckConstraint('depth BETWEEN 1 AND 3', name='ck_inventory_bin_depth'),
        sa.CheckConstraint('active IN (0,1)', name='ck_inventory_bin_active'),
        description='Bins inside a site, up to three levels deep; used from a later increment.')

    lots = T('lots',
        *common(),
        ident('item_id', 'Lot-tracked item this lot belongs to.', 'items.id'),
        C('number', sa.String(128), 'Lot number as entered.', nullable=False),
        C('number_key', sa.String(256), 'Case-folded lot number; unique per item.', nullable=False),
        C('expiry_date', sa.String(10), 'Expiry date of the lot, YYYY-MM-DD; null when none.', nullable=True),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        sa.UniqueConstraint('item_id', 'number_key', name='uq_lot_number'),
        description='Lot numbers of lot-tracked items; where a lot is comes only from placements. Used from a later increment.')

    serials = T('serial_numbers',
        *common(),
        ident('item_id', 'Serial-tracked item this unit belongs to.', 'items.id'),
        C('number', sa.String(128), 'Serial number as entered.', nullable=False),
        C('number_key', sa.String(256), 'Case-folded serial number; unique per item.', nullable=False),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        sa.UniqueConstraint('item_id', 'number_key', name='uq_serial_number'),
        description='Serial numbers of serial-tracked items; where a unit is comes only from placements. Used from a later increment.')

    settings = T('item_site_settings',
        *common(),
        ident('item_id', 'Inventory item these settings are for.', 'items.id'),
        ident('site_id', 'Site these settings are for.', 'inventory_sites.id'),
        C('reorder_point_min_microunits', sa.BigInteger, 'Reorder minimum at this site in base micro-units; null when none.', nullable=True),
        C('reorder_point_max_microunits', sa.BigInteger, 'Reorder maximum at this site in base micro-units; null when none.', nullable=True),
        ident('default_bin_id', 'Bin stock of this item is placed in at this site by default; null when none.', 'inventory_bins.id', nullable=True),
        sa.UniqueConstraint('item_id', 'site_id', name='uq_item_site_setting'),
        sa.CheckConstraint('reorder_point_min_microunits IS NULL OR reorder_point_min_microunits >= 0', name='ck_item_site_reorder_min'),
        sa.CheckConstraint('reorder_point_max_microunits IS NULL OR reorder_point_min_microunits IS NULL '
                           'OR reorder_point_max_microunits >= reorder_point_min_microunits', name='ck_item_site_reorder_max'),
        description='Per-site reorder points and default bin of an item.')

    transfers = T('inventory_transfers',
        *common(),
        C('number', sa.String(64), 'Transfer number, unique among transfers.', nullable=False),
        C('status', sa.String(16), 'posted or voided (final).', nullable=False),
        ident('current_revision_id', 'Revision that states the transfer as it stands.',
              'inventory_transfer_revisions.id', deferred=True),
        sa.UniqueConstraint('number', name='uq_inventory_transfer_number'),
        sa.CheckConstraint(f'status IN ({quoted(TRANSFER_STATUSES)})', name='ck_inventory_transfer_status'),
        description='Inventory transfers between sites: quantity moved, no value and no journal entry.')

    revisions = T('inventory_transfer_revisions',
        ident('id', 'Stable ULID of this immutable revision.', primary_key=True),
        ident('transfer_id', 'Transfer this revision belongs to.', 'inventory_transfers.id'),
        C('revision_number', sa.Integer, 'Revision of the transfer, from 1; the highest is current.', nullable=False),
        C('operation', sa.String(16), 'post, update or void.', nullable=False),
        C('date', sa.String(10), 'Date the stock left and arrived, YYYY-MM-DD.', nullable=False),
        C('count_position', sa.String(8), 'before or after the counts of its sites on its date.', nullable=False),
        ident('from_site_id', 'Site the stock left.', 'inventory_sites.id'),
        ident('to_site_id', 'Site the stock arrived at; the same site when moving between its bins.', 'inventory_sites.id'),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        C('reason', sa.Text, 'Why this revision was written, as the caller gave it.', nullable=True),
        *provenance('revision'),
        sa.UniqueConstraint('transfer_id', 'revision_number', name='uq_inventory_transfer_revision'),
        sa.CheckConstraint(f'operation IN ({quoted(TRANSFER_OPERATIONS)})', name='ck_inventory_transfer_operation'),
        sa.CheckConstraint(f'count_position IN ({quoted(COUNT_POSITIONS)})', name='ck_inventory_transfer_position'),
        sa.CheckConstraint("date LIKE '____-__-__'", name='ck_inventory_transfer_date'),
        description='Immutable revisions of inventory transfers.')

    lines = T('inventory_transfer_lines',
        ident('id', 'Revision-local line ULID.', primary_key=True),
        ident('revision_id', 'Revision this line belongs to.', 'inventory_transfer_revisions.id'),
        ident('line_id', 'Stable line identity carried across revisions.'),
        C('position', sa.Integer, 'One-based position within the revision.', nullable=False),
        ident('item_id', 'Inventory item moved.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Quantity moved in the item base unit, micro-units.', nullable=False),
        ident('unit_id', 'Unit the quantity was entered in; null for the base unit.', 'unit_conversions.id', nullable=True),
        C('unit_factor_nanounits', sa.BigInteger, 'Base units per entered unit, in nano-units.', nullable=False),
        ident('from_bin_id', 'Bin the stock left; null for no bin.', 'inventory_bins.id', nullable=True),
        ident('to_bin_id', 'Bin the stock arrived in; null for no bin.', 'inventory_bins.id', nullable=True),
        ident('lot_id', 'Lot moved; null when unassigned.', 'lots.id', nullable=True),
        C('description', sa.Text, 'Line description as entered; null when blank.', nullable=True),
        sa.UniqueConstraint('revision_id', 'position', name='uq_inventory_transfer_line_position'),
        sa.UniqueConstraint('revision_id', 'line_id', name='uq_inventory_transfer_line_identity'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0", name='ck_inventory_transfer_line_quantity'),
        sa.CheckConstraint('position > 0', name='ck_inventory_transfer_line_position'),
        description='Immutable lines of an inventory transfer revision.')

    placements = T('inventory_placements',
        ident('id', 'Stable ULID of this immutable placement.', primary_key=True),
        ident('item_id', 'Item whose units this places.', 'items.id'),
        ident('site_id', 'Site the units are at.', 'inventory_sites.id'),
        ident('bin_id', 'Bin the units are in; null for no bin.', 'inventory_bins.id', nullable=True),
        ident('lot_id', 'Lot the units carry; null when unassigned.', 'lots.id', nullable=True),
        ident('serial_number_id', 'Serial number of the unit; null when unassigned.', 'serial_numbers.id', nullable=True),
        C('quantity_microunits', sa.BigInteger, 'Signed quantity placed, micro-units; never zero.', nullable=False),
        C('effective_date', sa.String(10), 'Date the units are there from, YYYY-MM-DD.', nullable=False),
        C('count_position', sa.String(8), "before or after the counts of its site on its date, or count for a count's own difference.", nullable=False),
        C('sequence', sa.BigInteger, 'Company-wide recorded order, shared with movements.', nullable=False),
        C('source', sa.String(16), 'What wrote it: movement, transfer, assignment or count.', nullable=False),
        ident('movement_id', 'Movement whose quantity this places; null unless the source is a movement.',
              'inventory_movements.id', nullable=True, deferred=True),
        ident('transfer_revision_id', 'Transfer revision that moved these units; null unless the source is a transfer.',
              'inventory_transfer_revisions.id', nullable=True, deferred=True),
        ident('source_line_id', 'Line of the source document this placement belongs to; null on a movement.', nullable=True),
        ident('reverses_placement_id', 'Placement this one exactly retires; null unless a reversal.',
              'inventory_placements.id', nullable=True),
        *provenance('placement'),
        sa.UniqueConstraint('reverses_placement_id', name='uq_inventory_placement_reversal'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits != 0 "
                           "AND typeof(sequence) = 'integer' AND sequence > 0", name='ck_inventory_placement_integers'),
        sa.CheckConstraint("effective_date LIKE '____-__-__'", name='ck_inventory_placement_date'),
        sa.CheckConstraint(f'count_position IN ({quoted(PLACEMENT_POSITIONS)})', name='ck_inventory_placement_position'),
        sa.CheckConstraint(f'source IN ({quoted(PLACEMENT_SOURCES)})', name='ck_inventory_placement_source'),
        sa.CheckConstraint("(source = 'movement') = (movement_id IS NOT NULL) AND "
                           "(source = 'transfer') = (transfer_revision_id IS NOT NULL) AND "
                           "(source = 'movement' OR source_line_id IS NOT NULL)", name='ck_inventory_placement_owner'),
        sa.CheckConstraint('lot_id IS NULL OR serial_number_id IS NULL', name='ck_inventory_placement_number'),
        sa.Index('ix_inventory_placements_item', 'item_id', 'effective_date', 'sequence'),
        sa.Index('ix_inventory_placements_site', 'site_id', 'item_id', 'effective_date'),
        sa.Index('ix_inventory_placements_movement', 'movement_id'),
        sa.Index('ix_inventory_placements_transfer', 'transfer_revision_id'),
        # What one command wrote is read back by sequence, after every company write (increment 2).
        sa.Index('ix_inventory_placements_sequence', 'sequence'),
        description='Immutable quantity-only ledger of where stock is: every movement and transfer placed at a site.')
    return {'inventory_sites': sites, 'inventory_bins': bins, 'lots': lots, 'serial_numbers': serials,
            'item_site_settings': settings, 'inventory_transfers': transfers,
            'inventory_transfer_revisions': revisions, 'inventory_transfer_lines': lines,
            'inventory_placements': placements}


def guard_statements():
    """The triggers that make placements a ledger the rows cannot violate."""
    immutable = ('inventory_placements', 'inventory_transfer_revisions', 'inventory_transfer_lines')
    out = []
    for table in immutable:
        out.append(f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
                   f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END")
        out.append(f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
                   f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END")
    out += [
        # A movement's placements are written first; the movement is refused unless they sum to its
        # quantity on its own item and date.
        "CREATE TRIGGER inventory_movements_placed BEFORE INSERT ON inventory_movements "
        "WHEN NEW.quantity_microunits != coalesce((SELECT sum(p.quantity_microunits) FROM inventory_placements p "
        "WHERE p.movement_id = NEW.id), 0) OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.movement_id = NEW.id "
        "AND (p.item_id != NEW.item_id OR p.effective_date != NEW.effective_date OR p.sequence != NEW.sequence)) "
        "BEGIN SELECT RAISE(ABORT, 'inventory movement placements do not sum to its quantity'); END",
        "CREATE TRIGGER inventory_placements_open_movement BEFORE INSERT ON inventory_placements "
        "WHEN NEW.movement_id IS NOT NULL AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id = NEW.movement_id) "
        "BEGIN SELECT RAISE(ABORT, 'placement added to a movement already written'); END",
        # A transfer revision's placements net to zero per item, lot and serial number.
        "CREATE TRIGGER inventory_transfer_revisions_balanced BEFORE INSERT ON inventory_transfer_revisions "
        "WHEN EXISTS (SELECT 1 FROM inventory_placements p WHERE p.transfer_revision_id = NEW.id "
        "GROUP BY p.item_id, p.lot_id, p.serial_number_id HAVING sum(p.quantity_microunits) != 0) "
        "OR EXISTS (SELECT 1 FROM inventory_placements p WHERE p.transfer_revision_id = NEW.id "
        "AND p.reverses_placement_id IS NULL AND p.effective_date != NEW.date) "
        "BEGIN SELECT RAISE(ABORT, 'inventory transfer placements do not net to zero'); END",
        "CREATE TRIGGER inventory_placements_open_transfer BEFORE INSERT ON inventory_placements "
        "WHEN NEW.transfer_revision_id IS NOT NULL AND EXISTS (SELECT 1 FROM inventory_transfer_revisions r "
        "WHERE r.id = NEW.transfer_revision_id) "
        "BEGIN SELECT RAISE(ABORT, 'placement added to a transfer revision already written'); END",
        # A reversal retires exactly the placement it names.
        "CREATE TRIGGER inventory_placements_exact_reversal BEFORE INSERT ON inventory_placements "
        "WHEN NEW.reverses_placement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_placements o "
        "WHERE o.id = NEW.reverses_placement_id AND o.item_id = NEW.item_id AND o.site_id = NEW.site_id "
        "AND o.bin_id IS NEW.bin_id AND o.lot_id IS NEW.lot_id AND o.serial_number_id IS NEW.serial_number_id "
        "AND o.quantity_microunits = -NEW.quantity_microunits AND o.effective_date = NEW.effective_date) "
        "BEGIN SELECT RAISE(ABORT, 'inventory placement reversal is not exact'); END",
        "CREATE TRIGGER inventory_sites_no_delete BEFORE DELETE ON inventory_sites "
        "BEGIN SELECT RAISE(ABORT, 'inventory sites are deactivated, never deleted'); END",
    ]
    return tuple(out)
