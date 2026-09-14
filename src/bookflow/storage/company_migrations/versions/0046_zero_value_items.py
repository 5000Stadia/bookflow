"""Preserve commercial history while admitting zero-value quantities and totals."""
import importlib
from alembic import op
revision = 'co0046'
down_revision = 'co0045'
branch_labels = depends_on = None
CHANGED = ('inventory_movements', 'transaction_revisions', 'purchase_item_lines')
REPLACED = ('inventory_movements_match_posting',)
TRIGGERS = ('document_lines_type_insert',)
NEW_TABLES = ('money_out_revision_profiles',)
REPLACEMENTS = {'transaction_revisions': (('total_minor_units > 0', 'total_minor_units >= 0'),), 'purchase_item_lines': (('amount_minor_units > 0', 'amount_minor_units >= 0'),), 'inventory_movements': (('posting_line_id VARCHAR(26) NOT NULL', 'posting_line_id VARCHAR(26)'), ('quantity_microunits > 0 AND value_minor_units > 0', 'quantity_microunits > 0 AND value_minor_units >= 0'), ('quantity_microunits < 0 AND value_minor_units < 0', 'quantity_microunits < 0 AND value_minor_units <= 0'))}
DDL = ("\nCREATE TABLE money_out_revision_profiles (\n\trevision_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\tfunding_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (revision_id), \n\tFOREIGN KEY(transaction_id) REFERENCES money_out_documents (transaction_id), \n\tFOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tCONSTRAINT ck_money_out_funding_snapshot CHECK (json_valid(funding_snapshot) AND json_type(funding_snapshot) = 'object')\n)\n\n",)
GUARDS = ("CREATE TRIGGER money_out_revision_profiles_no_update BEFORE UPDATE ON money_out_revision_profiles BEGIN SELECT RAISE(ABORT, 'captured funding is immutable'); END", "CREATE TRIGGER money_out_revision_profiles_no_delete BEFORE DELETE ON money_out_revision_profiles BEGIN SELECT RAISE(ABORT, 'captured funding is immutable'); END", "CREATE TRIGGER inventory_movements_match_posting BEFORE INSERT ON inventory_movements WHEN NOT EXISTS (SELECT 1 FROM document_lines d JOIN posting_batches b ON b.transaction_id=d.transaction_id AND b.revision_id=d.revision_id WHERE d.id=NEW.document_line_id AND d.revision_id=NEW.revision_id AND d.transaction_id=NEW.transaction_id AND b.id=NEW.posting_batch_id AND b.effective_date=NEW.effective_date AND d.currency=NEW.currency) OR (NEW.value_minor_units != 0 AND NOT EXISTS (SELECT 1 FROM posting_lines l WHERE l.id=NEW.posting_line_id AND l.transaction_id=NEW.transaction_id AND l.batch_id=NEW.posting_batch_id AND l.account_id=NEW.asset_account_id AND l.debit_minor_units-l.credit_minor_units=NEW.value_minor_units)) BEGIN SELECT RAISE(ABORT, 'inventory movement does not match its owner or value'); END", "CREATE TRIGGER inventory_movements_exact_reversal BEFORE INSERT ON inventory_movements WHEN NEW.kind='reversal' AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.id=NEW.reverses_movement_id AND m.transaction_id=NEW.transaction_id AND m.revision_id=NEW.revision_id AND m.document_line_id=NEW.document_line_id AND m.item_id=NEW.item_id AND m.currency=NEW.currency AND m.effective_date=NEW.effective_date AND m.asset_account_id=NEW.asset_account_id AND m.offset_account_id=NEW.offset_account_id AND m.class_id IS NEW.class_id AND m.quantity_microunits=-NEW.quantity_microunits AND m.value_minor_units=-NEW.value_minor_units) BEGIN SELECT RAISE(ABORT, 'inventory reversal is not exact'); END")

def upgrade():
    c = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    q = preserving._quote
    reserved = {*NEW_TABLES, *('_co0046_' + t for t in CHANGED), 'inventory_movements_exact_reversal', 'money_out_revision_profiles_no_update', 'money_out_revision_profiles_no_delete'}
    for _, db, _ in c.exec_driver_sql('PRAGMA database_list'):
        if any((r[0].casefold() in reserved for r in c.exec_driver_sql('SELECT name FROM ' + q(db) + '.sqlite_schema'))):
            raise RuntimeError('co0046 reserved object exists')
    plans = {}
    for table in CHANGED:
        sql = c.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
        _, parts, suffix = preserving._definitions(sql, table)
        cols = c.exec_driver_sql('PRAGMA table_xinfo(' + q(table) + ')').all()
        if any((r[1].lower() in ('rowid', 'oid', '_rowid_') for r in cols)) or 'WITHOUT' in suffix.upper():
            raise RuntimeError('co0046 unsupported row identity')
        for old, new in REPLACEMENTS[table]:
            found = [i for i, p in enumerate(parts) if old in p]
            if len(found) != 1 or parts[found[0]].count(old) != 1:
                raise RuntimeError('co0046 unknown DDL: ' + table)
            parts[found[0]] = parts[found[0]].replace(old, new, 1)
        if table == 'inventory_movements':
            parts.append('CONSTRAINT ck_inventory_movement_value_link CHECK ((value_minor_units = 0 AND posting_line_id IS NULL) OR (value_minor_units != 0 AND posting_line_id IS NOT NULL))')
        writable = ','.join(['rowid'] + [q(r[1]) for r in cols if r[6] == 0])
        exact = ','.join(['rowid'] + [e for r in cols for e in ('typeof(' + q(r[1]) + ')', 'CAST(' + q(r[1]) + ' AS BLOB)')])
        plans[table] = ('CREATE TABLE ' + q('_co0046_' + table) + ' (' + ','.join(parts) + suffix, writable, exact)
    retained = c.exec_driver_sql("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('trigger','view') OR (type='index' AND tbl_name IN ('inventory_movements','transaction_revisions','purchase_item_lines'))) ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    guards = {name: sql for kind, name, _, sql in retained if kind == 'trigger'}
    old = importlib.import_module('bookflow.storage.company_migrations.versions.0037_inventory')
    if guards.get('inventory_movements_match_posting') != next((s for s in old.GUARDS if s.startswith('CREATE TRIGGER inventory_movements_match_posting '))):
        raise RuntimeError('co0046 unknown movement guard')
    target = "(type = 'journal_entry' AND NEW.kind <> 'journal')"
    if guards.get('document_lines_type_insert', '').count(target) != 1:
        raise RuntimeError('co0046 unknown document guard')
    for kind, name, _, sql in retained:
        if kind in ('trigger', 'view'):
            c.exec_driver_sql('DROP ' + kind.upper() + ' ' + q(name))
    for table, (sql, cols, exact) in plans.items():
        temp = '_co0046_' + table
        c.exec_driver_sql(sql)
        c.exec_driver_sql('INSERT INTO ' + q(temp) + ' (' + cols + ') SELECT ' + cols + ' FROM ' + q(table))
        for a, b in ((table, temp), (temp, table)):
            if c.exec_driver_sql('SELECT ' + exact + ' FROM ' + q(a) + ' EXCEPT SELECT ' + exact + ' FROM ' + q(b)).first():
                raise RuntimeError('co0046 changed raw values')
        c.exec_driver_sql('DROP TABLE ' + q(table))
        c.exec_driver_sql('ALTER TABLE ' + q(temp) + ' RENAME TO ' + q(table))
    for sql in DDL:
        c.exec_driver_sql(sql)
    for kind, name, _, sql in retained:
        if name == 'inventory_movements_match_posting':
            continue
        if name == 'document_lines_type_insert':
            sql = sql.replace(target, "(type = 'journal_entry' AND NEW.kind NOT IN ('journal', 'purchase'))", 1)
        c.exec_driver_sql(sql)
    for sql in GUARDS:
        c.exec_driver_sql(sql)
    if c.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise RuntimeError('co0046 foreign key check failed')

def downgrade():
    raise RuntimeError('Company migrations are forward-only')
