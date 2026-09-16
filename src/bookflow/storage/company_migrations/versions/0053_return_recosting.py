"""Let a customer return share in the recosting of the sale it gives back.

A return receipt used to state its cost once and keep it for ever. When a purchase was
entered behind the sale, the sale was recosted and the returns already taken against it were
not, so value the company never paid for was stranded in the inventory asset. The receipt now
names the issue it gives back, and replay hands it a share of what that issue is worth now.

Existing returns are linked here, so a company that already carries the defect is corrected by
the next recalculation of the items it touched rather than being left wrong for ever.
"""
import importlib
from alembic import op

revision = 'co0053'
down_revision = 'co0052'
branch_labels = depends_on = None

TABLE = 'inventory_movements'
TEMP = '_co0053_inventory_movements'
DDL = "\nCREATE TABLE _co0053_inventory_movements (\n\tid VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tposting_batch_id VARCHAR(26) NOT NULL, \n\tposting_line_id VARCHAR(26), \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tsequence BIGINT NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tvalue_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tasset_account_id VARCHAR(26) NOT NULL, \n\toffset_account_id VARCHAR(26) NOT NULL, \n\tclass_id VARCHAR(26), \n\tcorrects_movement_id VARCHAR(26), \n\treverses_movement_id VARCHAR(26), \n\treturns_movement_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_inventory_movement_batch FOREIGN KEY(transaction_id, posting_batch_id) REFERENCES posting_batches (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_posting_line FOREIGN KEY(transaction_id, posting_line_id) REFERENCES posting_lines (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_document_line FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_inventory_movement_corrects FOREIGN KEY(corrects_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_reverses FOREIGN KEY(reverses_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_returns FOREIGN KEY(returns_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT ck_inventory_movement_value_link CHECK ((value_minor_units = 0 AND posting_line_id IS NULL) OR (value_minor_units != 0 AND posting_line_id IS NOT NULL)), \n\tCONSTRAINT uq_inventory_movement_posting_line UNIQUE (posting_line_id), \n\tCONSTRAINT uq_inventory_movement_sequence UNIQUE (sequence), \n\tCONSTRAINT uq_inventory_movement_reversal UNIQUE (reverses_movement_id), \n\tCONSTRAINT ck_inventory_movement_kind CHECK (kind IN ('receipt', 'issue', 'value', 'recost', 'reversal')), \n\tCONSTRAINT ck_inventory_movement_integers CHECK (typeof(quantity_microunits) = 'integer' AND typeof(value_minor_units) = 'integer' AND typeof(sequence) = 'integer' AND sequence > 0), \n\tCONSTRAINT ck_inventory_movement_date CHECK (effective_date LIKE '____-__-__'), \n\tCONSTRAINT ck_inventory_movement_shape CHECK ((kind = 'receipt' AND quantity_microunits > 0 AND value_minor_units >= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'issue' AND quantity_microunits < 0 AND value_minor_units <= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'value' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'recost' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NOT NULL AND reverses_movement_id IS NULL) OR (kind = 'reversal' AND reverses_movement_id IS NOT NULL AND corrects_movement_id IS NULL AND (quantity_microunits != 0 OR value_minor_units != 0))), \n\tCONSTRAINT ck_inventory_movement_returns_kind CHECK (returns_movement_id IS NULL OR kind = 'receipt'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(asset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(offset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)\n\n"
# `0047` asserted that every correction against a receipt is an item-receipt purchase-price
# correction, because at the time that was the only receipt a correction could target. A return
# is a second one, so the guard now admits it -- the dimensions it captured are checked exactly
# as before, and what it may no longer assume is where the receipt came from.
GUARD = "CREATE TRIGGER receipt_cost_correction_dimensions BEFORE INSERT ON inventory_movements WHEN NEW.kind='recost' AND EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.kind='receipt') AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.corrects_movement_id AND m.item_id=NEW.item_id AND m.effective_date=NEW.effective_date AND m.asset_account_id=NEW.asset_account_id AND m.offset_account_id=NEW.offset_account_id AND m.currency=NEW.currency AND m.class_id IS NEW.class_id AND (m.returns_movement_id IS NOT NULL OR EXISTS (SELECT 1 FROM item_receipt_lines l WHERE l.movement_id=m.id))) BEGIN SELECT RAISE(ABORT, 'receipt cost correction changed captured dimensions'); END"
INDEXES = (
    'CREATE INDEX ix_inventory_movements_returns ON inventory_movements (returns_movement_id)',
)
# Every receipt a posted credit memo wrote, paired with the live issue of the invoice line its
# commercial row names. Written as one statement so the link is the database's own join rather
# than a walk this migration invents.
BACKFILL = """
UPDATE inventory_movements AS m SET returns_movement_id = (
    SELECT i.id FROM credit_line_profiles p
    JOIN inventory_movements i
      ON i.transaction_id = p.source_transaction_id
     AND i.document_line_id = p.source_document_line_id
     AND i.kind = 'issue'
     AND NOT EXISTS (SELECT 1 FROM inventory_movements r
                     WHERE r.kind = 'reversal' AND r.reverses_movement_id = i.id)
    WHERE p.transaction_id = m.transaction_id
      AND p.document_line_id = m.document_line_id
      AND p.source_transaction_id IS NOT NULL)
WHERE m.kind = 'receipt' AND m.returns_movement_id IS NULL
  AND EXISTS (SELECT 1 FROM transactions t
              WHERE t.id = m.transaction_id AND t.type = 'credit_memo')
"""


def upgrade():
    c = op.get_bind()
    preserving = importlib.import_module(
        'bookflow.storage.company_migrations.versions.0012_progress_billing')
    q = preserving._quote
    for _, db, _ in c.exec_driver_sql('PRAGMA database_list'):
        names = {r[0].casefold() for r in c.exec_driver_sql(
            'SELECT name FROM ' + q(db) + '.sqlite_schema')}
        if TEMP in names or 'ix_inventory_movements_returns' in names:
            raise RuntimeError('co0053 reserved object exists')
    columns = c.exec_driver_sql('PRAGMA table_xinfo(' + q(TABLE) + ')').all()
    if any(r[1].lower() in ('rowid', 'oid', '_rowid_') for r in columns):
        raise RuntimeError('co0053 unsupported row identity')
    if any(r[1] == 'returns_movement_id' for r in columns):
        raise RuntimeError('co0053 column already present')
    carried = ','.join(['rowid'] + [q(r[1]) for r in columns if r[6] == 0])
    exact = ','.join(['rowid'] + [e for r in columns for e in (
        'typeof(' + q(r[1]) + ')', 'CAST(' + q(r[1]) + ' AS BLOB)')])
    retained = c.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND ("
        "type IN ('trigger','view') OR (type='index' AND tbl_name='inventory_movements')) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind, name, _, _sql in retained:
        if kind in ('trigger', 'view'):
            c.exec_driver_sql('DROP ' + kind.upper() + ' ' + q(name))
    c.exec_driver_sql(DDL)
    c.exec_driver_sql('INSERT INTO ' + q(TEMP) + ' (' + carried + ') SELECT ' + carried
                      + ' FROM ' + q(TABLE))
    # Every carried value survives untouched, compared both ways by storage class and bytes.
    for a, b in ((TABLE, TEMP), (TEMP, TABLE)):
        if c.exec_driver_sql('SELECT ' + exact + ' FROM ' + q(a) + ' EXCEPT SELECT ' + exact
                             + ' FROM ' + q(b)).first():
            raise RuntimeError('co0053 changed raw values')
    c.exec_driver_sql('DROP TABLE ' + q(TABLE))
    c.exec_driver_sql('ALTER TABLE ' + q(TEMP) + ' RENAME TO ' + q(TABLE))
    replaced = importlib.import_module(
        'bookflow.storage.company_migrations.versions.0047_receiving')
    known = next(g for g in replaced.GUARDS
                 if g.startswith('CREATE TRIGGER receipt_cost_correction_dimensions '))
    if not any(name == 'receipt_cost_correction_dimensions' and sql == known
               for _, name, _, sql in retained):
        raise RuntimeError('co0053 unknown receipt correction guard')
    # Linked while the guards are still down: `inventory_movements` is immutable once its own
    # triggers are back, and this is the one moment the rebuild legitimately owns the table.
    c.exec_driver_sql(BACKFILL)
    for kind, name, _, sql in retained:
        c.exec_driver_sql(GUARD if name == 'receipt_cost_correction_dimensions' else sql)
    for sql in INDEXES:
        c.exec_driver_sql(sql)
    if c.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0053 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
