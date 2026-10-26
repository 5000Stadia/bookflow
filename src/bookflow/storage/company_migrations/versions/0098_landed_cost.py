"""Landed cost (R104 increment 6, design/advanced-inventory.md §5).

Two tables -- ``landed_cost_allocations`` and its immutable ``landed_cost_allocation_lines`` -- and two item
columns, ``unit_weight_microunits`` and ``unit_volume_microunits`` (null on every existing item), for the weight
and volume bases. co0093 already admits the movement kind ``landed``, ``landed_on_movement_id`` and the document
kind ``landed_cost``; this migration uses them and redefines none. Guards: allocation lines and a void are
immutable; a reversing line mirrors a live line of its own allocation; a share and a ``landed`` movement land on
a bought receipt of their own item dated on or before them. co0093's true-up guard is widened so a landed cost
may own forward true-ups of the issues, customer returns and found quantities recognised before it.

Every existing item receipt's stored shipping share becomes an allocation line (kind ``receipt_shipping``, basis
quantity, one allocation per receipt with shipping, dated and attributed as the receipt's current revision), so
every landed amount reads from one place. No existing row changes. The clearing account is not made here: it is
made on first use. DDL below is frozen: this migration never imports current application metadata.
"""
import os
import time

from alembic import op

revision = 'co0098'
down_revision = 'co0097'
branch_labels = depends_on = None

COLUMNS = ('ALTER TABLE items ADD COLUMN unit_weight_microunits BIGINT CONSTRAINT ck_items_unit_weight CHECK (unit_weight_microunits IS NULL OR unit_weight_microunits > 0)', 'ALTER TABLE items ADD COLUMN unit_volume_microunits BIGINT CONSTRAINT ck_items_unit_volume CHECK (unit_volume_microunits IS NULL OR unit_volume_microunits > 0)')
DDL = ("\nCREATE TABLE landed_cost_allocations (\n\tid VARCHAR(26) NOT NULL, \n\tnumber VARCHAR(64) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tstatus VARCHAR(8) NOT NULL, \n\tdate VARCHAR(10) NOT NULL, \n\tbasis VARCHAR(8) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\titem_receipt_id VARCHAR(26), \n\tmemo TEXT, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tvoided_at VARCHAR(32), \n\tvoided_by VARCHAR(26), \n\tvoided_via VARCHAR(16), \n\tvoid_reason TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_landed_cost_allocation_kind CHECK (kind IN ('allocation', 'receipt_shipping')), \n\tCONSTRAINT ck_landed_cost_allocation_status CHECK (status IN ('posted', 'voided')), \n\tCONSTRAINT ck_landed_cost_allocation_basis CHECK (basis IN ('quantity', 'value', 'weight', 'volume', 'amounts')), \n\tCONSTRAINT ck_landed_cost_allocation_date CHECK (date LIKE '____-__-__'), \n\tCONSTRAINT ck_landed_cost_allocation_receipt CHECK ((kind = 'receipt_shipping') = (item_receipt_id IS NOT NULL)), \n\tCONSTRAINT ck_landed_cost_allocation_voided CHECK ((status = 'voided') = (voided_at IS NOT NULL)), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(item_receipt_id) REFERENCES item_receipts (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "\nCREATE TABLE landed_cost_allocation_lines (\n\tid VARCHAR(26) NOT NULL, \n\tallocation_id VARCHAR(26) NOT NULL, \n\tposition INTEGER NOT NULL, \n\tcharge_transaction_id VARCHAR(26), \n\tcharge_line_id VARCHAR(26), \n\ttarget_movement_id VARCHAR(26), \n\ttarget_account_id VARCHAR(26), \n\titem_id VARCHAR(26), \n\tbasis VARCHAR(8) NOT NULL, \n\tmeasure BIGINT NOT NULL, \n\tamount_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tlanded_movement_id VARCHAR(26), \n\treverses_line_id VARCHAR(26), \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_landed_cost_line_basis CHECK (basis IN ('quantity', 'value', 'weight', 'volume', 'amounts')), \n\tCONSTRAINT ck_landed_cost_line_integers CHECK (typeof(amount_minor_units) = 'integer' AND typeof(measure) = 'integer' AND measure >= 0), \n\tCONSTRAINT ck_landed_cost_line_target CHECK ((target_movement_id IS NULL) != (target_account_id IS NULL)), \n\tCONSTRAINT ck_landed_cost_line_item CHECK ((target_movement_id IS NULL) = (item_id IS NULL)), \n\tCONSTRAINT ck_landed_cost_line_charge CHECK ((charge_transaction_id IS NULL) = (charge_line_id IS NULL)), \n\tCONSTRAINT uq_landed_cost_line_reversal UNIQUE (reverses_line_id), \n\tFOREIGN KEY(allocation_id) REFERENCES landed_cost_allocations (id), \n\tFOREIGN KEY(charge_transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(target_movement_id) REFERENCES inventory_movements (id), \n\tFOREIGN KEY(target_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(landed_movement_id) REFERENCES inventory_movements (id), \n\tFOREIGN KEY(reverses_line_id) REFERENCES landed_cost_allocation_lines (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n")
INDEXES = ('CREATE INDEX ix_landed_cost_allocations_receipt ON landed_cost_allocations (item_receipt_id)', 'CREATE INDEX ix_landed_cost_allocations_transaction ON landed_cost_allocations (transaction_id)', "CREATE UNIQUE INDEX uq_landed_cost_allocations_number ON landed_cost_allocations (number) WHERE kind = 'allocation'", 'CREATE INDEX ix_landed_cost_lines_allocation ON landed_cost_allocation_lines (allocation_id, position)', 'CREATE INDEX ix_landed_cost_lines_charge ON landed_cost_allocation_lines (charge_transaction_id, charge_line_id)', 'CREATE INDEX ix_landed_cost_lines_target ON landed_cost_allocation_lines (target_movement_id)')
GUARDS = ("CREATE TRIGGER landed_cost_allocation_lines_immutable_update BEFORE UPDATE ON landed_cost_allocation_lines BEGIN SELECT RAISE(ABORT, 'immutable landed cost allocation lines'); END", "CREATE TRIGGER landed_cost_allocation_lines_immutable_delete BEFORE DELETE ON landed_cost_allocation_lines BEGIN SELECT RAISE(ABORT, 'immutable landed cost allocation lines'); END", "CREATE TRIGGER landed_cost_allocations_immutable_delete BEFORE DELETE ON landed_cost_allocations BEGIN SELECT RAISE(ABORT, 'immutable landed cost allocations'); END", "CREATE TRIGGER landed_cost_allocations_void_only BEFORE UPDATE ON landed_cost_allocations WHEN NOT (OLD.status = 'posted' AND NEW.status = 'voided' AND NEW.id IS OLD.id AND NEW.number IS OLD.number AND NEW.kind IS OLD.kind AND NEW.date IS OLD.date AND NEW.basis IS OLD.basis AND NEW.transaction_id IS OLD.transaction_id AND NEW.item_receipt_id IS OLD.item_receipt_id AND NEW.memo IS OLD.memo AND NEW.reason IS OLD.reason AND NEW.created_at IS OLD.created_at AND NEW.created_by IS OLD.created_by AND NEW.created_via IS OLD.created_via AND NEW.audit_event_id IS OLD.audit_event_id) BEGIN SELECT RAISE(ABORT, 'a landed cost allocation only moves from posted to voided'); END", "CREATE TRIGGER landed_cost_allocation_lines_reversal BEFORE INSERT ON landed_cost_allocation_lines WHEN NEW.reverses_line_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM landed_cost_allocation_lines o WHERE o.id = NEW.reverses_line_id AND o.reverses_line_id IS NULL AND o.allocation_id = NEW.allocation_id AND o.amount_minor_units = -NEW.amount_minor_units AND o.target_movement_id IS NEW.target_movement_id AND o.target_account_id IS NEW.target_account_id AND o.charge_line_id IS NEW.charge_line_id) BEGIN SELECT RAISE(ABORT, 'a landed cost reversal line mirrors a live line of its own allocation'); END", "CREATE TRIGGER landed_cost_allocation_lines_target BEFORE INSERT ON landed_cost_allocation_lines WHEN NEW.target_movement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_movements r JOIN landed_cost_allocations a ON a.id = NEW.allocation_id WHERE r.id = NEW.target_movement_id AND r.kind = 'receipt' AND r.returns_movement_id IS NULL AND r.item_id = NEW.item_id AND r.effective_date <= a.date) BEGIN SELECT RAISE(ABORT, 'a landed cost share lands on a bought receipt of its item, dated on or before it'); END", "CREATE TRIGGER inventory_movements_landed_on BEFORE INSERT ON inventory_movements WHEN NEW.kind = 'landed' AND NOT EXISTS (SELECT 1 FROM inventory_movements r WHERE r.id = NEW.landed_on_movement_id AND r.kind = 'receipt' AND r.returns_movement_id IS NULL AND r.item_id = NEW.item_id AND r.effective_date <= NEW.effective_date) BEGIN SELECT RAISE(ABORT, 'a landed cost names a bought receipt of its own item, dated on or before it'); END")
OLD_TRUE_UP_GUARD = "CREATE TRIGGER inventory_movements_true_up_link BEFORE INSERT ON inventory_movements WHEN NEW.filled_by_movement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_movements r JOIN inventory_movements i ON i.id=NEW.corrects_movement_id WHERE r.id=NEW.filled_by_movement_id AND r.kind IN ('receipt','found') AND r.item_id=NEW.item_id AND r.effective_date=NEW.effective_date AND i.kind='issue' AND i.item_id=NEW.item_id AND i.asset_account_id=NEW.asset_account_id AND i.offset_account_id=NEW.offset_account_id AND i.currency=NEW.currency AND i.class_id IS NEW.class_id) BEGIN SELECT RAISE(ABORT, 'inventory true-up must be dated at its receipt or found quantity against an issue of the same item'); END"
TRUE_UP_GUARD = "CREATE TRIGGER inventory_movements_true_up_link BEFORE INSERT ON inventory_movements WHEN NEW.filled_by_movement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_movements r JOIN inventory_movements i ON i.id=NEW.corrects_movement_id WHERE r.id=NEW.filled_by_movement_id AND r.item_id=NEW.item_id AND r.effective_date=NEW.effective_date AND i.item_id=NEW.item_id AND i.asset_account_id=NEW.asset_account_id AND i.offset_account_id=NEW.offset_account_id AND i.currency=NEW.currency AND i.class_id IS NEW.class_id AND ((r.kind IN ('receipt','found') AND i.kind='issue') OR (r.kind='landed' AND (i.kind IN ('issue','found') OR (i.kind='receipt' AND i.returns_movement_id IS NOT NULL))))) BEGIN SELECT RAISE(ABORT, 'inventory true-up must be dated at its receipt, found quantity or landed cost against a movement of the same item whose cost replay decides'); END"
TABLES = ('landed_cost_allocation_lines', 'landed_cost_allocations')
# What a test rewinding a head file to co0095 drops: the guards, then the tables and the two item columns.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)
ITEM_COLUMNS = ('unit_weight_microunits', 'unit_volume_microunits')
# Each standing item receipt line that carries shipping, from its receipt's current revision.
SHIPPING = (
    "SELECT r.id, r.number, r.transaction_id, v.date, v.created_at, v.created_by, v.created_via, v.audit_event_id, "
    "l.movement_id, l.item_id, l.quantity_microunits, l.shipping_minor_units, m.currency "
    "FROM item_receipts r JOIN item_receipt_revisions v ON v.id = r.current_revision_id "
    "JOIN item_receipt_lines l ON l.receipt_id = r.id AND l.financial_revision_id = v.financial_revision_id "
    "JOIN inventory_movements m ON m.id = l.movement_id "
    "WHERE r.status = 'posted' AND l.shipping_minor_units > 0 "
    "AND NOT EXISTS (SELECT 1 FROM inventory_movements x WHERE x.reverses_movement_id = m.id) "
    "ORDER BY r.id, m.sequence")
_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


def _ulid():
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), 'big')
    return ''.join(_ALPHABET[(value >> (5 * (25 - n))) & 31] for n in range(26))


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {*TABLES, *(name.casefold() for name in TRIGGERS)}:
        raise RuntimeError('co0098 reserved object exists')
    present = {row[1] for row in db.exec_driver_sql('PRAGMA table_xinfo(items)')}
    if present & set(ITEM_COLUMNS):
        raise RuntimeError('co0098 column already present on items')
    for statement in COLUMNS + DDL + INDEXES:
        db.exec_driver_sql(statement)
    headers, lines = {}, []
    for row in db.exec_driver_sql(SHIPPING).fetchall():
        (receipt, number, transaction, date, at, by, via, event, movement, item, quantity, shipping, currency) = row
        if receipt not in headers:
            headers[receipt] = (_ulid(), number, 'receipt_shipping', 'posted', date, 'quantity', transaction, receipt,
                                at, by, via, event)
            position = 0
        position += 1
        lines.append((_ulid(), headers[receipt][0], position, movement, item, 'quantity', quantity, shipping, currency,
                      at, by, via, event))
    db.exec_driver_sql(
        'INSERT INTO landed_cost_allocations (id, number, kind, status, date, basis, transaction_id, item_receipt_id, '
        'created_at, created_by, created_via, audit_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        list(headers.values())) if headers else None
    db.exec_driver_sql(
        'INSERT INTO landed_cost_allocation_lines (id, allocation_id, position, target_movement_id, item_id, basis, '
        'measure, amount_minor_units, currency, created_at, created_by, created_via, audit_event_id) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', lines) if lines else None
    for statement in GUARDS:
        db.exec_driver_sql(statement)
    db.exec_driver_sql('DROP TRIGGER inventory_movements_true_up_link')
    db.exec_driver_sql(TRUE_UP_GUARD)
    if db.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0098 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
