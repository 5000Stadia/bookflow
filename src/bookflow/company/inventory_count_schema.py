"""Physical counts and cycle counts (co0093, R104 increment 2; design/advanced-inventory.md §6).

A count is a **recorded input**. What is kept is what the counters found -- each line's counted
quantity, fixed once the count is recorded -- and the difference from what the books held is
*derived* at the count's position in its day, the way an issue's cost is derived. So paperwork
entered after the count but dated before it lands on the right side of it, and the shelf stays as
counted.

- ``inventory_counts``: the header. Its status moves open -> recorded -> voided, or open ->
  cancelled; ``version`` guards each change.
- ``inventory_count_lines``: one per item and place on the sheet (or found while counting). Its
  ``counted_microunits`` is null until the count is recorded and then fixed: a trigger refuses any
  other change.
- ``inventory_count_entries``: append-only. Two people, or a person and an agent, can count one
  count at once; each entry is its own row, and a line's counted quantity is its entries read in
  order (``set`` replaces, ``add`` adds).
- ``inventory_count_postings``: which journal entries a count's differences were posted in. A count
  has one standing entry at most: deriving it again corrects that entry by a new revision, and a
  count left with no difference voids it; a later difference then posts a new entry.

Bins, lots and serial numbers are columns from the start so later increments count them without a
rebuild; until then every line is the unassigned bucket at the count's site.
"""
import sqlalchemy as sa

COUNT_STATUSES = ('open', 'recorded', 'cancelled', 'voided')
LINE_ORIGINS = ('sheet', 'found')
ENTRY_MODES = ('set', 'add')
POSTING_PURPOSES = ('record', 'rederive')


def define_tables(metadata, C, T, common):
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

    counts = T('inventory_counts',
        *common(),
        C('number', sa.String(64), 'Count number, unique among counts.', nullable=False),
        C('status', sa.String(16), 'open, recorded, cancelled (nothing recorded) or voided (recorded, then retired).', nullable=False),
        ident('site_id', 'Site counted.', 'inventory_sites.id'),
        C('count_date', sa.String(10), 'Date of the count, YYYY-MM-DD; its differences are derived and posted at this date.', nullable=False),
        C('blind', sa.Boolean, 'Whether the sheet hides expected quantities and differences from the counters.', nullable=False),
        C('selection', sa.Text, 'JSON object: what the sheet was made from (every stocked item, named items, a category, bins, or a cycle).', nullable=False),
        ident('adjustment_account_id', 'Account carrying the other side of the posted differences; null until recorded.',
              'accounts.id', nullable=True),
        ident('class_id', 'Class captured on the posted differences; null when unclassified.', 'classes.id', nullable=True),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        C('reason', sa.Text, 'Why the count was created, as the caller gave it.', nullable=True),
        C('recorded_at', sa.String(32), 'UTC time the count was recorded; null until then.', nullable=True),
        C('recorded_by', sa.String(26), 'Company principal that recorded the count; null until then.', nullable=True),
        C('recorded_via', sa.String(16), 'Interface the count was recorded through; null until then.', nullable=True),
        C('record_sequence', sa.BigInteger, "Stock sequence number taken when the count was recorded; orders counts on one date.", nullable=True),
        C('end_reason', sa.Text, 'Why the count was cancelled or voided; null otherwise.', nullable=True),
        sa.UniqueConstraint('number', name='uq_inventory_count_number'),
        sa.CheckConstraint(f'status IN ({quoted(COUNT_STATUSES)})', name='ck_inventory_count_status'),
        sa.CheckConstraint("count_date LIKE '____-__-__'", name='ck_inventory_count_date'),
        sa.CheckConstraint('blind IN (0,1)', name='ck_inventory_count_blind'),
        sa.CheckConstraint("(status IN ('recorded', 'voided')) = (recorded_at IS NOT NULL AND adjustment_account_id IS NOT NULL "
                           "AND record_sequence IS NOT NULL)", name='ck_inventory_count_recorded'),
        sa.Index('ix_inventory_counts_site', 'site_id', 'count_date'),
        description='Physical counts: what was counted, where and when; differences are derived, never stored.')

    lines = T('inventory_count_lines',
        ident('id', 'Stable ULID of this count line.', primary_key=True),
        ident('count_id', 'Count this line belongs to.', 'inventory_counts.id'),
        C('position', sa.Integer, 'One-based position on the sheet.', nullable=False),
        ident('item_id', 'Item counted.', 'items.id'),
        ident('bin_id', 'Bin counted; null for no bin.', 'inventory_bins.id', nullable=True),
        ident('lot_id', 'Lot counted; null when unassigned.', 'lots.id', nullable=True),
        ident('serial_number_id', 'Serial number counted; null when unassigned.', 'serial_numbers.id', nullable=True),
        C('place_key', sa.String(80), 'Bin, lot and serial number ids joined by "|" (empty for none); one line per item and place.', nullable=False),
        C('origin', sa.String(8), 'sheet (the books held it, or it was named) or found (entered for a place not on the sheet).', nullable=False),
        C('counted_microunits', sa.BigInteger, 'Counted quantity in base micro-units, fixed when the count is recorded; null before, and on a line nobody counted.', nullable=True),
        *provenance('count line'),
        sa.UniqueConstraint('count_id', 'item_id', 'place_key', name='uq_inventory_count_line_place'),
        sa.UniqueConstraint('count_id', 'position', name='uq_inventory_count_line_position'),
        sa.CheckConstraint(f'origin IN ({quoted(LINE_ORIGINS)})', name='ck_inventory_count_line_origin'),
        sa.CheckConstraint('position > 0', name='ck_inventory_count_line_position'),
        sa.CheckConstraint("counted_microunits IS NULL OR (typeof(counted_microunits) = 'integer' AND counted_microunits >= 0)",
                           name='ck_inventory_count_line_counted'),
        sa.CheckConstraint('lot_id IS NULL OR serial_number_id IS NULL', name='ck_inventory_count_line_number'),
        sa.Index('ix_inventory_count_lines_item', 'item_id', 'count_id'),
        description='Lines of a physical count: one per item and place; the counted quantity is fixed when recorded.')

    entries = T('inventory_count_entries',
        ident('id', 'Stable ULID of this entry.', primary_key=True),
        ident('count_id', 'Count this entry was made on.', 'inventory_counts.id'),
        ident('line_id', 'Count line this entry counts.', 'inventory_count_lines.id'),
        C('ordinal', sa.Integer, 'Order of this entry within its count, from 1.', nullable=False),
        C('mode', sa.String(8), 'set (the quantity at the place is this) or add (this many more).', nullable=False),
        C('quantity_microunits', sa.BigInteger, 'Quantity entered, base micro-units.', nullable=False),
        C('scanned_code', sa.String(128), 'Barcode the entry was scanned from; null when typed.', nullable=True),
        *provenance('entry'),
        sa.UniqueConstraint('count_id', 'ordinal', name='uq_inventory_count_entry_ordinal'),
        sa.CheckConstraint(f'mode IN ({quoted(ENTRY_MODES)})', name='ck_inventory_count_entry_mode'),
        sa.CheckConstraint("typeof(quantity_microunits) = 'integer' AND quantity_microunits >= 0",
                           name='ck_inventory_count_entry_quantity'),
        sa.Index('ix_inventory_count_entries_line', 'line_id', 'ordinal'),
        description='Append-only entries of a physical count; each counter writes their own.')

    postings = T('inventory_count_postings',
        ident('transaction_id', 'Journal entry carrying this posting of the count.', 'transactions.id', primary_key=True),
        ident('count_id', 'Count whose differences the journal entry posts.', 'inventory_counts.id'),
        C('purpose', sa.String(16), 'record (the count recorded) or rederive (posted again after a re-derivation had voided the earlier entry).', nullable=False),
        *provenance('count posting'),
        sa.CheckConstraint(f'purpose IN ({quoted(POSTING_PURPOSES)})', name='ck_inventory_count_posting_purpose'),
        sa.Index('ix_inventory_count_postings_count', 'count_id'),
        description="Journal entries that posted a physical count's derived differences.")
    return {'inventory_counts': counts, 'inventory_count_lines': lines, 'inventory_count_entries': entries,
            'inventory_count_postings': postings}


GUARDS = (
    "CREATE TRIGGER inventory_count_lines_fixed BEFORE UPDATE ON inventory_count_lines "
    "WHEN OLD.counted_microunits IS NOT NULL OR NEW.id IS NOT OLD.id OR NEW.count_id IS NOT OLD.count_id "
    "OR NEW.position IS NOT OLD.position OR NEW.item_id IS NOT OLD.item_id OR NEW.bin_id IS NOT OLD.bin_id "
    "OR NEW.lot_id IS NOT OLD.lot_id OR NEW.serial_number_id IS NOT OLD.serial_number_id "
    "OR NEW.place_key IS NOT OLD.place_key OR NEW.origin IS NOT OLD.origin "
    "OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by IS NOT OLD.created_by "
    "OR NEW.created_via IS NOT OLD.created_via OR NEW.audit_event_id IS NOT OLD.audit_event_id "
    "BEGIN SELECT RAISE(ABORT, 'a count line is fixed once recorded'); END",
    "CREATE TRIGGER inventory_count_lines_no_delete BEFORE DELETE ON inventory_count_lines "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory count lines'); END",
    "CREATE TRIGGER inventory_count_entries_immutable_update BEFORE UPDATE ON inventory_count_entries "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory count entries'); END",
    "CREATE TRIGGER inventory_count_entries_immutable_delete BEFORE DELETE ON inventory_count_entries "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory count entries'); END",
    "CREATE TRIGGER inventory_count_postings_immutable_update BEFORE UPDATE ON inventory_count_postings "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory count postings'); END",
    "CREATE TRIGGER inventory_count_postings_immutable_delete BEFORE DELETE ON inventory_count_postings "
    "BEGIN SELECT RAISE(ABORT, 'immutable inventory count postings'); END",
    "CREATE TRIGGER inventory_counts_no_delete BEFORE DELETE ON inventory_counts "
    "BEGIN SELECT RAISE(ABORT, 'inventory counts are cancelled or voided, never deleted'); END",
    # An entry lands only on an open count, on one of its own lines.
    "CREATE TRIGGER inventory_count_entries_open BEFORE INSERT ON inventory_count_entries "
    "WHEN NOT EXISTS (SELECT 1 FROM inventory_counts k JOIN inventory_count_lines l ON l.count_id = k.id "
    "WHERE k.id = NEW.count_id AND l.id = NEW.line_id AND k.status = 'open') "
    "BEGIN SELECT RAISE(ABORT, 'entries land only on an open count, on its own lines'); END",
)
