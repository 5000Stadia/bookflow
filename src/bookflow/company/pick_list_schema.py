"""Pick lists (co0104, R181 increment 3; design/pick-pack-ship.md §4, §8).

A **pick list** tells a picker which units of which open orders to take from which bins at one site, in pick order;
the picker confirms what was picked with **entries**, appended as count entries are, and `shipment post --pick-list`
ships what was picked. A pick posts no money and, by default, moves nothing: the units stay placed in their bins
until the shipment takes them. A site that names a **staging bin** (``inventory_sites.staging_bin_id``) has each
entry move the picked units from their bin to staging (placements of source ``pick``, netting to zero), and the
shipment takes them from staging.

- ``pick_lists``: the immutable header -- number, site, date, picker, memo.
- ``pick_list_revisions``: append-only; revision 1 is ``open``, then ``done`` (closed, or every order shipped from it)
  or ``cancelled``. The newest is current, its ``revision_number`` the list's version.
- ``pick_list_lines``: immutable. One order line's units to take from one bin (and suggested lot), in pick order; a
  ``short`` line is the part nothing at the site holds.
- ``pick_entries``: immutable, append-only. What a picker confirmed for one line: ``add`` (a scan adds 1) or ``set``,
  with the bin, lot or serial number in hand and the code scanned. With a staging bin, the entry's own placements
  (``source_line_id`` = the entry) move the picked units and net to zero, checked when the entry row is written.
- ``pick_list_shipments``: immutable. A shipment posted from the list for one of its orders.

Moving staged units back when a list is cancelled writes placements under the cancelling revision's id, netting to
zero, checked when that revision row is written.
"""
import sqlalchemy as sa

STATUSES = ('open', 'done', 'cancelled')
MODES = ('add', 'set')
SEQUENCE = 'pick_list'
FEATURE = 'pick_and_pack'
SETTING = 'pick_and_pack_enabled'


def define_tables(metadata, C, T):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    def provenance(what):
        return (C('created_at', sa.String(32), f'UTC time this {what} was recorded.', nullable=False),
                C('created_by', sa.String(26), f'Company principal that recorded this {what}.', nullable=False),
                C('created_via', sa.String(16), f'Interface that recorded this {what}.', nullable=False),
                ident('audit_event_id', f'Audit event that committed this {what}.', 'audit_events.id'))

    lists = T('pick_lists',
        ident('id', 'Stable ULID of this pick list.', primary_key=True),
        C('number', sa.String(64), 'Pick list number, from the pick_list sequence (P-1, P-2, ...).', nullable=False),
        ident('site_id', 'Inventory site the units are picked at.', 'inventory_sites.id'),
        C('pick_date', sa.String(10), 'Date the list is for, YYYY-MM-DD.', nullable=False),
        C('picker', sa.String(200), 'Who picks it: a user or free text; null when nobody was named.', nullable=True),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        *provenance('pick list'),
        sa.CheckConstraint("pick_date LIKE '____-__-__'", name='ck_pick_list_date'),
        sa.UniqueConstraint('number', name='uq_pick_lists_number'),
        description='Pick lists: which units of which open orders to take from which bins at one site. Immutable; '
                    'its state is its newest revision.')

    revisions = T('pick_list_revisions',
        ident('id', 'Stable ULID of this revision.', primary_key=True),
        ident('pick_list_id', 'Pick list this revision restates.', 'pick_lists.id'),
        C('revision_number', sa.Integer, 'One-based revision number; the newest is current and is the version.',
          nullable=False),
        C('status', sa.String(10), 'open, done or cancelled.', nullable=False),
        C('reason', sa.Text, 'Why this revision was written, as the caller gave it.', nullable=True),
        *provenance('pick list revision'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_pick_list_revision_status'),
        sa.CheckConstraint("typeof(revision_number) = 'integer' AND revision_number > 0",
                           name='ck_pick_list_revision_number'),
        sa.UniqueConstraint('pick_list_id', 'revision_number', name='uq_pick_list_revision_number'),
        description='Append-only pick list revisions: open, then done or cancelled; the newest is current.')

    lines = T('pick_list_lines',
        ident('id', 'Stable ULID of this line.', primary_key=True),
        ident('pick_list_id', 'Pick list this line belongs to.', 'pick_lists.id'),
        C('position', sa.Integer, 'One-based position in pick order.', nullable=False),
        ident('sales_order_id', 'Sales order the units are for.', 'work_documents.id'),
        C('order_line_id', sa.String(26), 'The order line\'s line_id, as `sales-order show` lists it.', nullable=False),
        ident('root_document_id', 'Billing root document of the order line (the order, or the estimate it came from).'),
        ident('root_line_id', 'Billing root line identity of the order line.'),
        ident('item_id', 'Item to pick.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Quantity to pick in the item\'s base unit, microunits.',
          nullable=False),
        ident('bin_id', 'Bin to take them from; null for no bin.', 'inventory_bins.id', nullable=True),
        ident('lot_id', 'Lot suggested (earliest-expiring unexpired first); null when none.', 'lots.id',
              nullable=True),
        C('short', sa.Boolean, 'True for the part of the order line nothing at the site holds.', nullable=False),
        *provenance('pick list line'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0",
                           name='ck_pick_list_line_quantity'),
        sa.CheckConstraint('position > 0', name='ck_pick_list_line_position'),
        sa.UniqueConstraint('pick_list_id', 'position', name='uq_pick_list_line_position'),
        sa.Index('ix_pick_list_lines_root', 'root_document_id', 'root_line_id'),
        description='Immutable lines of a pick list: one order line\'s units from one bin, in pick order.')

    entries = T('pick_entries',
        ident('id', 'Stable ULID of this entry.', primary_key=True),
        ident('pick_list_id', 'Pick list the entry confirms.', 'pick_lists.id'),
        ident('line_id', 'Pick list line the units were picked for.', 'pick_list_lines.id'),
        C('ordinal', sa.Integer, 'One-based order of entries on the list.', nullable=False),
        C('mode', sa.String(4), 'add (this many more; a scan adds 1) or set (the line holds this many).',
          nullable=False),
        C('quantity_microunits', sa.BigInteger, 'Quantity entered in the item\'s base unit, microunits.',
          nullable=False),
        ident('bin_id', 'Bin the units were taken from; null for no bin.', 'inventory_bins.id', nullable=True),
        ident('lot_id', 'Lot picked; null when unassigned.', 'lots.id', nullable=True),
        ident('serial_number_id', 'Serial number of the unit picked; null when none.', 'serial_numbers.id',
              nullable=True),
        C('scanned_code', sa.String(200), 'Code scanned, when the entry came from a scan; else null.', nullable=True),
        ident('staging_bin_id', 'Staging bin the entry moved its units to or from; null: it moved nothing.',
              'inventory_bins.id', nullable=True),
        C('entry_date', sa.String(10), 'Date the units were picked, YYYY-MM-DD: the staging placements\' date.',
          nullable=False),
        *provenance('pick entry'),
        sa.CheckConstraint(f'mode IN ({quoted(MODES)})', name='ck_pick_entry_mode'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits >= 0",
                           name='ck_pick_entry_quantity'),
        sa.CheckConstraint("entry_date LIKE '____-__-__'", name='ck_pick_entry_date'),
        sa.CheckConstraint('lot_id IS NULL OR serial_number_id IS NULL', name='ck_pick_entry_number'),
        sa.UniqueConstraint('pick_list_id', 'ordinal', name='uq_pick_entry_ordinal'),
        sa.Index('ix_pick_entries_line', 'line_id', 'ordinal'),
        description='Append-only picks confirmed on a pick list: add or set, with the bin, lot or serial number in '
                    'hand.')

    shipped = T('pick_list_shipments',
        ident('id', 'Stable ULID of this link.', primary_key=True),
        ident('pick_list_id', 'Pick list the shipment was posted from.', 'pick_lists.id'),
        ident('shipment_id', 'Shipment posted from it.', 'shipments.id'),
        ident('sales_order_id', 'Order the shipment shipped.', 'work_documents.id'),
        *provenance('pick list shipment'),
        sa.UniqueConstraint('pick_list_id', 'shipment_id', name='uq_pick_list_shipment'),
        sa.Index('ix_pick_list_shipments_shipment', 'shipment_id'),
        description='Immutable: a shipment posted from a pick list for one of its orders. Live while the shipment '
                    'is not voided.')
    return {'pick_lists': lists, 'pick_list_revisions': revisions, 'pick_list_lines': lines,
            'pick_entries': entries, 'pick_list_shipments': shipped}


def company_info_column(C):
    return C(SETTING, sa.Boolean, 'Pick and pack: pick lists (and, later, packing) as optional layers on shipping; '
             'off by default.', nullable=False, server_default='0')


def staging_column(C):
    return C('staging_bin_id', sa.String(26), 'Bin picked units move to until they ship; null: picks move nothing.',
             sa.ForeignKey('inventory_bins.id'), nullable=True)


TABLES = ('pick_lists', 'pick_list_revisions', 'pick_list_lines', 'pick_entries', 'pick_list_shipments')
# Every row is immutable; revisions and entries append. The two balance guards read only the placements written
# under the new row's own id (no standing-row count).
GUARDS = tuple(
    statement for table in TABLES for statement in (
        f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END",
        f"CREATE TRIGGER {table}_immutable_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'immutable {table.replace('_', ' ')}'); END")) + (
    # A pick's placements are written first and net to zero per item, lot and serial number.
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
