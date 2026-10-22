"""Assembly builds: a finished assembly built from its components (R103).

One new immutable table, ``assembly_builds``, holds every revision of every build: pending (nothing
posted, components reserved), built (posted as a journal entry the inventory ledger owns), voided or
deleted. One guard is widened: a cost correction may name a stock *receipt* only when the receipt is a
return or a received purchase line, and a build's receipt of the assembly is now the third such
receipt -- when a backdated purchase recosts a component a build consumed, the build's receipt of the
assembly is recosted by the same amount, on the same date, so the cost moves on into the assembly.
Nothing existing is rewritten. DDL below is frozen: this migration never imports current application
metadata.
"""
from alembic import op

revision = 'co0079'
down_revision = 'co0073'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE assembly_builds (\n\tid VARCHAR(26) NOT NULL, \n\tbuild_id VARCHAR(26) NOT NULL, \n\trevision_number INTEGER NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tbuild_date VARCHAR(10) NOT NULL, \n\tmemo TEXT, \n\treason TEXT, \n\ttransaction_id VARCHAR(26), \n\tcomponents TEXT NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_assembly_build_revision UNIQUE (build_id, revision_number), \n\tCONSTRAINT ck_assembly_build_status CHECK (status IN ('pending','built','voided','deleted')), \n\tCONSTRAINT ck_assembly_build_quantity CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_assembly_build_revision_number CHECK (revision_number > 0), \n\tCONSTRAINT ck_assembly_build_date CHECK (build_date LIKE '____-__-__'), \n\tCONSTRAINT ck_assembly_build_posted CHECK (status = 'pending' OR status = 'deleted' OR transaction_id IS NOT NULL), \n\tCONSTRAINT ck_assembly_build_pending CHECK (status != 'pending' OR transaction_id IS NULL), \n\tCONSTRAINT ck_assembly_build_components CHECK (json_valid(components) AND json_type(components) = 'array'), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n",)
INDEXES = ('CREATE INDEX ix_assembly_builds_transaction ON assembly_builds (transaction_id)',
           'CREATE INDEX ix_assembly_builds_item ON assembly_builds (item_id, build_date)')
GUARDS = (
    "CREATE TRIGGER assembly_builds_immutable_update BEFORE UPDATE ON assembly_builds BEGIN SELECT RAISE(ABORT, 'immutable assembly build history'); END",
    "CREATE TRIGGER assembly_builds_immutable_delete BEFORE DELETE ON assembly_builds BEGIN SELECT RAISE(ABORT, 'immutable assembly build history'); END",
)
REPLACED = 'receipt_cost_correction_dimensions'
# co0057's text, kept so a test can rewind a file to before this revision.
OLD_GUARD = "CREATE TRIGGER receipt_cost_correction_dimensions BEFORE INSERT ON inventory_movements WHEN NEW.kind='recost' AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.kind='receipt') AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.item_id=NEW.item_id AND m.effective_date=NEW.effective_date AND m.asset_account_id=NEW.asset_account_id AND m.offset_account_id=NEW.offset_account_id AND m.currency=NEW.currency AND m.class_id IS NEW.class_id AND (m.returns_movement_id IS NOT NULL OR EXISTS (SELECT 1 FROM item_receipt_lines l WHERE l.movement_id=m.id))) BEGIN SELECT RAISE(ABORT, 'receipt cost correction changed captured dimensions'); END"
GUARD = "CREATE TRIGGER receipt_cost_correction_dimensions BEFORE INSERT ON inventory_movements WHEN NEW.kind='recost' AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.kind='receipt') AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.item_id=NEW.item_id AND m.effective_date=NEW.effective_date AND m.asset_account_id=NEW.asset_account_id AND m.offset_account_id=NEW.offset_account_id AND m.currency=NEW.currency AND m.class_id IS NEW.class_id AND (m.returns_movement_id IS NOT NULL OR EXISTS (SELECT 1 FROM item_receipt_lines l WHERE l.movement_id=m.id) OR EXISTS (SELECT 1 FROM assembly_builds b WHERE b.transaction_id=m.transaction_id))) BEGIN SELECT RAISE(ABORT, 'receipt cost correction changed captured dimensions'); END"


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {'assembly_builds', 'ix_assembly_builds_transaction', 'ix_assembly_builds_item',
                'assembly_builds_immutable_update', 'assembly_builds_immutable_delete'}:
        raise RuntimeError('co0079 reserved object exists')
    current = db.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?", (REPLACED,)).scalar()
    if current != OLD_GUARD:
        raise RuntimeError('co0079 expects the co0057 receipt cost correction guard')
    for statement in DDL + INDEXES + GUARDS:
        db.exec_driver_sql(statement)
    db.exec_driver_sql('DROP TRIGGER ' + REPLACED)
    db.exec_driver_sql(GUARD)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0079 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
