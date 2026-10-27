"""Landed cost (co0098, R104 increment 6; design/advanced-inventory.md §5).

A freight bill, a duty charge or a broker's fee is entered as an ordinary expense line on the
**Landed Cost Clearing** account (system role ``landed_cost_clearing``); a vendor credit's line on it is a
freight credit, a negative charge. ``landed-cost post`` then spreads charges over the receipts that brought
the stock in, and the allocation is recorded here:

- ``landed_cost_allocations``: the header. ``kind`` is ``allocation`` (a ``landed-cost post``, posted as its
  own journal entry, ``transaction_id``) or ``receipt_shipping`` (an item receipt's own shipping, spread when
  the receipt was entered and posted inside the receipt's entry, never through the clearing account). Its
  status moves ``posted`` -> ``voided`` once, and nothing else about it ever changes.
- ``landed_cost_allocation_lines``: immutable. One per charge line and target: the receipt movement (or, for
  a charge that is not capitalised, the expense or cost-of-goods account) it lands on, the basis and measure
  it was shared by, its signed amount and the ``landed`` movement that carries it into stock. A void writes
  a reversing line for each (``reverses_line_id``); a line no reversal names is *live*. A charge line's
  unallocated amount is its amount less its live allocation lines.

Every amount is integer minor units and every measure an integer (base microunits, minor units, or
microunits times microunits for weight and volume), so the shares are exact.
"""
import sqlalchemy as sa

KINDS = ('allocation', 'receipt_shipping')
STATUSES = ('posted', 'voided')
BASES = ('quantity', 'value', 'weight', 'volume', 'amounts')
CLEARING_ROLE = 'landed_cost_clearing'


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

    allocations = T('landed_cost_allocations',
        ident('id', 'Stable ULID of this allocation.', primary_key=True),
        C('number', sa.String(64), 'Allocation number (LC-n), or the item receipt number for its own shipping.', nullable=False),
        C('kind', sa.String(16), 'allocation (landed-cost post) or receipt_shipping (an item receipt\'s own shipping).', nullable=False),
        C('status', sa.String(8), 'posted, or voided once its lines are reversed.', nullable=False),
        C('date', sa.String(10), 'Date the allocation is recognised, YYYY-MM-DD.', nullable=False),
        C('basis', sa.String(8), 'quantity, value, weight, volume or amounts.', nullable=False),
        ident('transaction_id', 'Journal entry the allocation posted as, or the item receipt\'s own entry.', 'transactions.id'),
        ident('item_receipt_id', 'Item receipt whose own shipping this spreads; null for landed-cost post.',
              'item_receipts.id', nullable=True),
        C('memo', sa.Text, 'Memo as entered; null when blank.', nullable=True),
        C('reason', sa.Text, 'Why the allocation was made, as the caller gave it.', nullable=True),
        *provenance('allocation'),
        C('voided_at', sa.String(32), 'UTC time the allocation was voided; null while posted.', nullable=True),
        C('voided_by', sa.String(26), 'Company principal that voided the allocation; null while posted.', nullable=True),
        C('voided_via', sa.String(16), 'Interface the allocation was voided through; null while posted.', nullable=True),
        C('void_reason', sa.Text, 'Why the allocation was voided; null while posted.', nullable=True),
        sa.CheckConstraint(f'kind IN ({quoted(KINDS)})', name='ck_landed_cost_allocation_kind'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_landed_cost_allocation_status'),
        sa.CheckConstraint(f'basis IN ({quoted(BASES)})', name='ck_landed_cost_allocation_basis'),
        sa.CheckConstraint("date LIKE '____-__-__'", name='ck_landed_cost_allocation_date'),
        sa.CheckConstraint("(kind = 'receipt_shipping') = (item_receipt_id IS NOT NULL)",
                           name='ck_landed_cost_allocation_receipt'),
        sa.CheckConstraint("(status = 'voided') = (voided_at IS NOT NULL)", name='ck_landed_cost_allocation_voided'),
        sa.Index('uq_landed_cost_allocations_number', 'number', unique=True, sqlite_where=sa.text("kind = 'allocation'")),
        sa.Index('ix_landed_cost_allocations_transaction', 'transaction_id'),
        sa.Index('ix_landed_cost_allocations_receipt', 'item_receipt_id'),
        description='Landed cost allocations: a landed-cost post, or an item receipt\'s own shipping. Status moves '
                    'posted -> voided once; nothing else changes.')

    lines = T('landed_cost_allocation_lines',
        ident('id', 'Stable ULID of this immutable allocation line.', primary_key=True),
        ident('allocation_id', 'Allocation this line belongs to.', 'landed_cost_allocations.id'),
        C('position', sa.Integer, 'One-based position within the allocation; a reversal repeats its original\'s.', nullable=False),
        ident('charge_transaction_id', 'Document holding the charge line; null for a receipt\'s own shipping.',
              'transactions.id', nullable=True),
        ident('charge_line_id', 'The charge: the id of the document whose Landed Cost Clearing lines it is (they are one charge '
                                'whatever an edit does to its lines); null for a receipt\'s own shipping.', nullable=True),
        ident('target_movement_id', 'Receipt movement the share lands on; null when allocated to an account.',
              'inventory_movements.id', nullable=True),
        ident('target_account_id', 'Expense or cost-of-goods account a share not capitalised goes to; null for a receipt.',
              'accounts.id', nullable=True),
        ident('item_id', 'Item of the target receipt; null when allocated to an account.', 'items.id', nullable=True),
        C('basis', sa.String(8), 'quantity, value, weight, volume or amounts.', nullable=False),
        C('measure', sa.BigInteger, 'What the target weighed in the basis: base microunits, minor units, microunits times '
                                     'microunits per unit, or the stated amount.', nullable=False),
        C('amount_minor_units', sa.BigInteger, 'Signed share of the charge, in minor units; negative for a freight credit.',
          nullable=False),
        C('currency', sa.String(3), 'Home currency of the amount.', nullable=False),
        ident('landed_movement_id', 'The landed movement that carries this share into stock; null for a receipt\'s own '
                                    'shipping (inside the receipt) and for an account.', 'inventory_movements.id',
              nullable=True),
        ident('reverses_line_id', 'Allocation line this line reverses on a void; null otherwise.',
              'landed_cost_allocation_lines.id', nullable=True),
        *provenance('allocation line'),
        sa.CheckConstraint(f'basis IN ({quoted(BASES)})', name='ck_landed_cost_line_basis'),
        sa.CheckConstraint("typeof(amount_minor_units) = 'integer' AND typeof(measure) = 'integer' AND measure >= 0",
                           name='ck_landed_cost_line_integers'),
        sa.CheckConstraint('(target_movement_id IS NULL) != (target_account_id IS NULL)', name='ck_landed_cost_line_target'),
        sa.CheckConstraint('(target_movement_id IS NULL) = (item_id IS NULL)', name='ck_landed_cost_line_item'),
        sa.CheckConstraint('(charge_transaction_id IS NULL) = (charge_line_id IS NULL)', name='ck_landed_cost_line_charge'),
        sa.UniqueConstraint('reverses_line_id', name='uq_landed_cost_line_reversal'),
        sa.Index('ix_landed_cost_lines_allocation', 'allocation_id', 'position'),
        sa.Index('ix_landed_cost_lines_charge', 'charge_transaction_id', 'charge_line_id'),
        sa.Index('ix_landed_cost_lines_target', 'target_movement_id'),
        description='Immutable shares of landed-cost charges by target receipt; a void writes reversing lines.')
    carries = T('landed_cost_carries',
        ident('id', 'Stable ULID of this immutable carry.', primary_key=True),
        ident('from_movement_id', 'Receipt movement a document change retired with its stock unchanged.',
              'inventory_movements.id'),
        ident('to_movement_id', 'The receipt movement that restated it and carries its landed cost on.',
              'inventory_movements.id'),
        *provenance('carry'),
        sa.UniqueConstraint('from_movement_id', name='uq_landed_cost_carry_from'),
        description='A receipt a landed cost lands on, restated unchanged by an edit of its document (a memo, a '
                    'reference): its landed cost carries on to the restatement.')
    return {'landed_cost_allocations': allocations, 'landed_cost_allocation_lines': lines,
            'landed_cost_carries': carries}


def weight_columns(C):
    """``items.unit_weight_microunits`` and ``unit_volume_microunits``: per base unit, for the weight and volume bases."""
    return (C('unit_weight_microunits', sa.BigInteger, 'Weight of one base unit in micro-units of the company\'s weight '
                                                        'unit, for landed cost by weight; null when not set.',
              sa.CheckConstraint('unit_weight_microunits IS NULL OR unit_weight_microunits > 0',
                                 name='ck_items_unit_weight'), nullable=True),
            C('unit_volume_microunits', sa.BigInteger, 'Volume of one base unit in micro-units of the company\'s volume '
                                                        'unit, for landed cost by volume; null when not set.',
              sa.CheckConstraint('unit_volume_microunits IS NULL OR unit_volume_microunits > 0',
                                 name='ck_items_unit_volume'), nullable=True))


# The guards, in the database so they hold when the application is bypassed. Each reads one row or standing
# rows only, never a running total in replay order.
GUARDS = (
    "CREATE TRIGGER landed_cost_allocation_lines_immutable_update BEFORE UPDATE ON landed_cost_allocation_lines "
    "BEGIN SELECT RAISE(ABORT, 'immutable landed cost allocation lines'); END",
    "CREATE TRIGGER landed_cost_allocation_lines_immutable_delete BEFORE DELETE ON landed_cost_allocation_lines "
    "BEGIN SELECT RAISE(ABORT, 'immutable landed cost allocation lines'); END",
    "CREATE TRIGGER landed_cost_allocations_immutable_delete BEFORE DELETE ON landed_cost_allocations "
    "BEGIN SELECT RAISE(ABORT, 'immutable landed cost allocations'); END",
    # Only posted -> voided, once, and nothing else.
    "CREATE TRIGGER landed_cost_allocations_void_only BEFORE UPDATE ON landed_cost_allocations WHEN NOT "
    "(OLD.status = 'posted' AND NEW.status = 'voided' AND NEW.id IS OLD.id AND NEW.number IS OLD.number "
    "AND NEW.kind IS OLD.kind AND NEW.date IS OLD.date AND NEW.basis IS OLD.basis AND NEW.transaction_id IS "
    "OLD.transaction_id AND NEW.item_receipt_id IS OLD.item_receipt_id AND NEW.memo IS OLD.memo AND NEW.reason IS "
    "OLD.reason AND NEW.created_at IS OLD.created_at AND NEW.created_by IS OLD.created_by AND NEW.created_via IS "
    "OLD.created_via AND NEW.audit_event_id IS OLD.audit_event_id) "
    "BEGIN SELECT RAISE(ABORT, 'a landed cost allocation only moves from posted to voided'); END",
    # A reversal mirrors its original exactly and belongs to the same allocation.
    "CREATE TRIGGER landed_cost_allocation_lines_reversal BEFORE INSERT ON landed_cost_allocation_lines "
    "WHEN NEW.reverses_line_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM landed_cost_allocation_lines o "
    "WHERE o.id = NEW.reverses_line_id AND o.reverses_line_id IS NULL AND o.allocation_id = NEW.allocation_id "
    "AND o.amount_minor_units = -NEW.amount_minor_units AND o.target_movement_id IS NEW.target_movement_id "
    "AND o.target_account_id IS NEW.target_account_id AND o.charge_line_id IS NEW.charge_line_id) "
    "BEGIN SELECT RAISE(ABORT, 'a landed cost reversal line mirrors a live line of its own allocation'); END",
    # A share lands on a bought receipt of its own item (never a customer return), dated on or before it.
    "CREATE TRIGGER landed_cost_allocation_lines_target BEFORE INSERT ON landed_cost_allocation_lines "
    "WHEN NEW.target_movement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_movements r "
    "JOIN landed_cost_allocations a ON a.id = NEW.allocation_id WHERE r.id = NEW.target_movement_id "
    "AND r.kind = 'receipt' AND r.returns_movement_id IS NULL AND r.item_id = NEW.item_id AND r.effective_date <= a.date) "
    "BEGIN SELECT RAISE(ABORT, 'a landed cost share lands on a bought receipt of its item, dated on or before it'); END",
    "CREATE TRIGGER landed_cost_carries_immutable_update BEFORE UPDATE ON landed_cost_carries "
    "BEGIN SELECT RAISE(ABORT, 'immutable landed cost carries'); END",
    "CREATE TRIGGER landed_cost_carries_immutable_delete BEFORE DELETE ON landed_cost_carries "
    "BEGIN SELECT RAISE(ABORT, 'immutable landed cost carries'); END",
    # A carry joins two bought receipts of one item on one date, the first retired, the second standing.
    "CREATE TRIGGER landed_cost_carries_same BEFORE INSERT ON landed_cost_carries WHEN NOT EXISTS (SELECT 1 FROM "
    "inventory_movements o JOIN inventory_movements n ON n.id = NEW.to_movement_id WHERE o.id = NEW.from_movement_id "
    "AND o.kind = 'receipt' AND n.kind = 'receipt' AND o.item_id = n.item_id AND o.effective_date = n.effective_date "
    "AND o.quantity_microunits = n.quantity_microunits AND o.value_minor_units = n.value_minor_units "
    "AND EXISTS (SELECT 1 FROM inventory_movements x WHERE x.reverses_movement_id = o.id)) "
    "BEGIN SELECT RAISE(ABORT, 'a landed cost carries only from a retired receipt to its unchanged restatement'); END",
    # A landed movement names a bought receipt of its own item, dated on or before it.
    "CREATE TRIGGER inventory_movements_landed_on BEFORE INSERT ON inventory_movements WHEN NEW.kind = 'landed' "
    "AND NOT EXISTS (SELECT 1 FROM inventory_movements r WHERE r.id = NEW.landed_on_movement_id "
    "AND r.kind = 'receipt' AND r.returns_movement_id IS NULL AND r.item_id = NEW.item_id "
    "AND r.effective_date <= NEW.effective_date) "
    "BEGIN SELECT RAISE(ABORT, 'a landed cost names a bought receipt of its own item, dated on or before it'); END",
)

# co0093's true-up guard, widened: a landed cost owes the issues, customer returns and found quantities
# recognised before it a forward true-up dated at it (same item, same captured accounts and class).
TRUE_UP_GUARD = (
    "CREATE TRIGGER inventory_movements_true_up_link BEFORE INSERT ON inventory_movements WHEN "
    "NEW.filled_by_movement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_movements r JOIN "
    "inventory_movements i ON i.id=NEW.corrects_movement_id WHERE r.id=NEW.filled_by_movement_id AND "
    "r.item_id=NEW.item_id AND r.effective_date=NEW.effective_date AND i.item_id=NEW.item_id AND "
    "i.asset_account_id=NEW.asset_account_id AND i.offset_account_id=NEW.offset_account_id AND "
    "i.currency=NEW.currency AND i.class_id IS NEW.class_id AND ((r.kind IN ('receipt','found') AND "
    "i.kind='issue') OR (r.kind='landed' AND (i.kind IN ('issue','found') OR (i.kind='receipt' AND "
    "i.returns_movement_id IS NOT NULL))))) BEGIN SELECT RAISE(ABORT, 'inventory true-up must be dated at its "
    "receipt, found quantity or landed cost against a movement of the same item whose cost replay decides'); END")
