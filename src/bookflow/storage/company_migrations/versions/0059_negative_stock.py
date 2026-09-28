"""Let a sale take stock below zero at a provisional cost, and a later receipt true it up.

``inventory_movements`` gains two nullable columns. ``fallback_unit_cost_minor_units`` is the
item's purchase cost captured on an issue when it is written, so the provisional cost of a sale
from an item that never held stock is a fact of the ledger rather than a read of a mutable item
record. ``filled_by_movement_id`` names, on a ``recost``, the receipt whose arrival trued up a
provisional cost; the new guard holds that true-up to the receipt's own date and item and to the
dimensions the issue it corrects captured.

Nothing is backfilled: no issue written before this revision was ever below zero, and NULL is
the true value of both columns for every row that exists. SQLite cannot add a foreign key or a
check to a table in place, so the table is rebuilt. DDL below is frozen: this migration never
imports current application metadata.
"""
import importlib
from alembic import op

revision = 'co0059'
down_revision = 'co0058'
branch_labels = depends_on = None

TABLE = 'inventory_movements'
TEMP = '_co0059_inventory_movements'
CHANGED = (TABLE,)
DDL = "\nCREATE TABLE _co0059_inventory_movements (\n\tid VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tposting_batch_id VARCHAR(26) NOT NULL, \n\tposting_line_id VARCHAR(26), \n\tdocument_line_id VARCHAR(26) NOT NULL, \n\teffective_date VARCHAR(10) NOT NULL, \n\tsequence BIGINT NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tvalue_minor_units BIGINT NOT NULL, \n\tcurrency VARCHAR(3) NOT NULL, \n\tasset_account_id VARCHAR(26) NOT NULL, \n\toffset_account_id VARCHAR(26) NOT NULL, \n\tclass_id VARCHAR(26), \n\tcorrects_movement_id VARCHAR(26), \n\treverses_movement_id VARCHAR(26), \n\treturns_movement_id VARCHAR(26), \n\tfilled_by_movement_id VARCHAR(26), \n\tfallback_unit_cost_minor_units BIGINT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_inventory_movement_batch FOREIGN KEY(transaction_id, posting_batch_id) REFERENCES posting_batches (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_posting_line FOREIGN KEY(transaction_id, posting_line_id) REFERENCES posting_lines (transaction_id, id), \n\tCONSTRAINT fk_inventory_movement_document_line FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT fk_inventory_movement_corrects FOREIGN KEY(corrects_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_reverses FOREIGN KEY(reverses_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_returns FOREIGN KEY(returns_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT fk_inventory_movement_filled_by FOREIGN KEY(filled_by_movement_id) REFERENCES inventory_movements (id), \n\tCONSTRAINT ck_inventory_movement_value_link CHECK ((value_minor_units = 0 AND posting_line_id IS NULL) OR (value_minor_units != 0 AND posting_line_id IS NOT NULL)), \n\tCONSTRAINT uq_inventory_movement_posting_line UNIQUE (posting_line_id), \n\tCONSTRAINT uq_inventory_movement_sequence UNIQUE (sequence), \n\tCONSTRAINT uq_inventory_movement_reversal UNIQUE (reverses_movement_id), \n\tCONSTRAINT ck_inventory_movement_kind CHECK (kind IN ('receipt', 'issue', 'value', 'recost', 'reversal')), \n\tCONSTRAINT ck_inventory_movement_integers CHECK (typeof(quantity_microunits) = 'integer' AND typeof(value_minor_units) = 'integer' AND typeof(sequence) = 'integer' AND sequence > 0), \n\tCONSTRAINT ck_inventory_movement_date CHECK (effective_date LIKE '____-__-__'), \n\tCONSTRAINT ck_inventory_movement_shape CHECK ((kind = 'receipt' AND quantity_microunits > 0 AND value_minor_units >= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'issue' AND quantity_microunits < 0 AND value_minor_units <= 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'value' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NULL AND reverses_movement_id IS NULL) OR (kind = 'recost' AND quantity_microunits = 0 AND value_minor_units != 0 AND corrects_movement_id IS NOT NULL AND reverses_movement_id IS NULL) OR (kind = 'reversal' AND reverses_movement_id IS NOT NULL AND corrects_movement_id IS NULL AND (quantity_microunits != 0 OR value_minor_units != 0))), \n\tCONSTRAINT ck_inventory_movement_returns_kind CHECK (returns_movement_id IS NULL OR kind = 'receipt'), \n\tCONSTRAINT ck_inventory_movement_filled_by_kind CHECK (filled_by_movement_id IS NULL OR kind = 'recost'), \n\tCONSTRAINT ck_inventory_movement_fallback_cost CHECK (fallback_unit_cost_minor_units IS NULL OR (kind = 'issue' AND typeof(fallback_unit_cost_minor_units) = 'integer' AND fallback_unit_cost_minor_units >= 0)), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(asset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(offset_account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(class_id) REFERENCES classes (id)\n)\n\n"
INDEXES = (
    'CREATE INDEX ix_inventory_movements_filled_by ON inventory_movements (filled_by_movement_id)',
)
GUARDS = (
    "CREATE TRIGGER inventory_movements_true_up_link BEFORE INSERT ON inventory_movements WHEN NEW.filled_by_movement_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM inventory_movements r JOIN inventory_movements i ON i.id=NEW.corrects_movement_id WHERE r.id=NEW.filled_by_movement_id AND r.kind='receipt' AND r.item_id=NEW.item_id AND r.effective_date=NEW.effective_date AND i.kind='issue' AND i.item_id=NEW.item_id AND i.asset_account_id=NEW.asset_account_id AND i.offset_account_id=NEW.offset_account_id AND i.currency=NEW.currency AND i.class_id IS NEW.class_id) BEGIN SELECT RAISE(ABORT, 'inventory true-up must be dated at its receipt against an issue of the same item'); END",
)
# Names this revision brings into existence; a database that already has one is not ours.
RESERVED = (TEMP, 'ix_inventory_movements_filled_by', 'inventory_movements_true_up_link')


def upgrade():
    c = op.get_bind()
    preserving = importlib.import_module(
        'bookflow.storage.company_migrations.versions.0012_progress_billing')
    q = preserving._quote
    for _, db, _ in c.exec_driver_sql('PRAGMA database_list'):
        names = {r[0].casefold() for r in c.exec_driver_sql(
            'SELECT name FROM ' + q(db) + '.sqlite_schema')}
        if names & {name.casefold() for name in RESERVED}:
            raise RuntimeError('co0059 reserved object exists')
    columns = c.exec_driver_sql('PRAGMA table_xinfo(' + q(TABLE) + ')').all()
    if any(r[1].lower() in ('rowid', 'oid', '_rowid_') for r in columns):
        raise RuntimeError('co0059 unsupported row identity')
    if any(r[1] in ('filled_by_movement_id', 'fallback_unit_cost_minor_units') for r in columns):
        raise RuntimeError('co0059 column already present')
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
            raise RuntimeError('co0059 changed raw values')
    c.exec_driver_sql('DROP TABLE ' + q(TABLE))
    c.exec_driver_sql('ALTER TABLE ' + q(TEMP) + ' RENAME TO ' + q(TABLE))
    for kind, name, _, sql in retained:
        c.exec_driver_sql(sql)
    for sql in INDEXES + GUARDS:
        c.exec_driver_sql(sql)
    if c.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0059 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
