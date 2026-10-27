"""Costing method timeline (R104 increment 7, design/advanced-inventory.md §3).

One table, ``costing_methods``: dated switches between ``average`` and ``fifo`` for every stocked item, each
made by a person. No row means average from the beginning, which is every company today, so no existing row
changes and nothing is backfilled. Guards: rows are never deleted, and a row moves ``standing`` ->
``withdrawn`` once and changes nothing else. Neither reads any other row. co0079's receipt-correction dimensions
guard is widened so a customer return's forward true-up may be dated at its landed cost. DDL below is frozen: this migration
never imports current application metadata.
"""
from alembic import op

revision = 'co0100'
down_revision = 'co0099'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE costing_methods (\n\tid VARCHAR(26) NOT NULL, \n\tmethod VARCHAR(8) NOT NULL, \n\teffective_from VARCHAR(10) NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\treason TEXT, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\twithdrawn_at VARCHAR(32), \n\twithdrawn_by VARCHAR(26), \n\twithdrawn_via VARCHAR(16), \n\twithdraw_reason TEXT, \n\twithdraw_audit_event_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_costing_method_method CHECK (method IN ('average', 'fifo')), \n\tCONSTRAINT ck_costing_method_status CHECK (status IN ('standing', 'withdrawn')), \n\tCONSTRAINT ck_costing_method_date CHECK (effective_from LIKE '____-__-__'), \n\tCONSTRAINT ck_costing_method_withdrawn CHECK ((status = 'withdrawn') = (withdrawn_at IS NOT NULL)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(withdraw_audit_event_id) REFERENCES audit_events (id)\n)\n\n",)
INDEXES = ("CREATE UNIQUE INDEX uq_costing_methods_standing_date ON costing_methods (effective_from) WHERE status = 'standing'",)
GUARDS = ("CREATE TRIGGER costing_methods_immutable_delete BEFORE DELETE ON costing_methods BEGIN SELECT RAISE(ABORT, 'immutable costing methods'); END", "CREATE TRIGGER costing_methods_withdraw_only BEFORE UPDATE ON costing_methods WHEN NOT (OLD.status = 'standing' AND NEW.status = 'withdrawn' AND NEW.id IS OLD.id AND NEW.method IS OLD.method AND NEW.effective_from IS OLD.effective_from AND NEW.reason IS OLD.reason AND NEW.created_at IS OLD.created_at AND NEW.created_by IS OLD.created_by AND NEW.created_via IS OLD.created_via AND NEW.audit_event_id IS OLD.audit_event_id) BEGIN SELECT RAISE(ABORT, 'a costing method switch only moves from standing to withdrawn'); END")
# A customer return's forward true-up (a landed cost recognised after it, R104 increment 6) is dated at the landed
# cost, not the return; co0079's dimensions guard required the return's own date for every receipt correction. A
# correction naming its filler (co0098's true-up guard dates it there) may carry the filler's date.
OLD_DIMENSIONS_GUARD = "CREATE TRIGGER receipt_cost_correction_dimensions BEFORE INSERT ON inventory_movements WHEN NEW.kind='recost' AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.kind='receipt') AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.item_id=NEW.item_id AND m.effective_date=NEW.effective_date AND m.asset_account_id=NEW.asset_account_id AND m.offset_account_id=NEW.offset_account_id AND m.currency=NEW.currency AND m.class_id IS NEW.class_id AND (m.returns_movement_id IS NOT NULL OR EXISTS (SELECT 1 FROM item_receipt_lines l WHERE l.movement_id=m.id) OR EXISTS (SELECT 1 FROM assembly_builds b WHERE b.transaction_id=m.transaction_id))) BEGIN SELECT RAISE(ABORT, 'receipt cost correction changed captured dimensions'); END"
DIMENSIONS_GUARD = "CREATE TRIGGER receipt_cost_correction_dimensions BEFORE INSERT ON inventory_movements WHEN NEW.kind='recost' AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.kind='receipt') AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.item_id=NEW.item_id AND (m.effective_date=NEW.effective_date OR NEW.filled_by_movement_id IS NOT NULL) AND m.asset_account_id=NEW.asset_account_id AND m.offset_account_id=NEW.offset_account_id AND m.currency=NEW.currency AND m.class_id IS NEW.class_id AND (m.returns_movement_id IS NOT NULL OR EXISTS (SELECT 1 FROM item_receipt_lines l WHERE l.movement_id=m.id) OR EXISTS (SELECT 1 FROM assembly_builds b WHERE b.transaction_id=m.transaction_id))) BEGIN SELECT RAISE(ABORT, 'receipt cost correction changed captured dimensions'); END"
TABLES = ('costing_methods',)
# What a test rewinding a head file drops: the guards, then the table.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {*TABLES, *(name.casefold() for name in TRIGGERS)}:
        raise RuntimeError('co0100 reserved object exists')
    for statement in DDL + INDEXES + GUARDS:
        db.exec_driver_sql(statement)
    db.exec_driver_sql('DROP TRIGGER receipt_cost_correction_dimensions')
    db.exec_driver_sql(DIMENSIONS_GUARD)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
