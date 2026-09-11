"""The Items tab: a second line family on the bill's own purchase envelope.

``purchase_item_lines`` is a sibling of ``purchase_expense_lines`` -- one row per envelope in
``document_lines``, the same ``purchase`` kind, the same revision -- carrying what makes a line
an item rather than a typed account: the item, the quantity, the unit cost and the account
captured from the item's own purchase profile. Nothing about the envelope, the numbering, the
payable or the posting had to learn the difference, so no other table is touched by it.

``purchase_profiles`` is rebuilt for two reasons, both of which live in a CHECK, and SQLite
only changes a CHECK by rebuilding the table. It gains ``item_total_minor_units`` beside
``expense_total_minor_units``, and each of the two is widened from positive to nonnegative: a
bill entered wholly on one tab owes a real zero for the other, and the figure that must stay
positive is the revision's own total, which is their sum and is constrained where it lives.

Nothing is backfilled and nothing is restated. Every existing bill keeps its expense total
exactly as it was written and takes an item total of zero, which is what it has: the new table
is empty, and widening a CHECK admits new rows without rewriting old ones.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
from alembic import op

revision = 'co0033'
down_revision = 'co0032'
branch_labels = None
depends_on = None

NEW_TABLES = ('purchase_item_lines',)
OBJECTS = ('ix_purchase_item_lines_item', 'purchase_item_lines',
           'purchase_item_lines_immutable_delete', 'purchase_item_lines_immutable_update')
DDL = (
    "CREATE TABLE purchase_item_lines (\n\tdocument_line_id VARCHAR(26) NOT NULL, \n\ttransaction_id VARCHAR(26) NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\titem_id VARCHAR(26) NOT NULL, \n\taccount_id VARCHAR(26) NOT NULL, \n\tquantity_microunits BIGINT NOT NULL, \n\tunit_cost_minor_units BIGINT, \n\tamount_minor_units BIGINT NOT NULL, \n\tcustomer_id VARCHAR(26), \n\tbillable BOOLEAN NOT NULL, \n\tline_snapshot TEXT NOT NULL, \n\tPRIMARY KEY (document_line_id), \n\tCONSTRAINT uq_purchase_item_line_owner UNIQUE (transaction_id, revision_id, document_line_id), \n\tCONSTRAINT fk_purchase_item_line_revision FOREIGN KEY(transaction_id, revision_id) REFERENCES purchase_profiles (transaction_id, revision_id), \n\tCONSTRAINT fk_purchase_item_line_envelope FOREIGN KEY(transaction_id, revision_id, document_line_id) REFERENCES document_lines (transaction_id, revision_id, id), \n\tCONSTRAINT ck_purchase_quantity_microunits_positive CHECK (typeof(quantity_microunits) = 'integer' AND quantity_microunits > 0), \n\tCONSTRAINT ck_purchase_amount_minor_units_positive CHECK (typeof(amount_minor_units) = 'integer' AND amount_minor_units > 0), \n\tCONSTRAINT ck_purchase_line_snapshot_object CHECK (json_valid(line_snapshot) AND json_type(line_snapshot) = 'object'), \n\tCONSTRAINT ck_purchase_item_unit_cost CHECK (unit_cost_minor_units IS NULL OR (typeof(unit_cost_minor_units) = 'integer' AND unit_cost_minor_units >= 0)), \n\tCONSTRAINT ck_purchase_item_billable_job CHECK (billable IN (0, 1) AND (billable = 0 OR customer_id IS NOT NULL)), \n\tFOREIGN KEY(item_id) REFERENCES items (id), \n\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n\tFOREIGN KEY(customer_id) REFERENCES customers (id)\n)",
    'CREATE INDEX ix_purchase_item_lines_item ON purchase_item_lines (item_id, revision_id)',
)
GUARDS = (
    "CREATE TRIGGER purchase_item_lines_immutable_update BEFORE UPDATE ON purchase_item_lines BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END",
    "CREATE TRIGGER purchase_item_lines_immutable_delete BEFORE DELETE ON purchase_item_lines BEGIN SELECT RAISE(ABORT, 'immutable purchase history'); END",
)

CHANGED = ('purchase_profiles',)
# No stored trigger is rewritten: every guard on the rebuilt table is dropped with it and put
# back verbatim, including any a local installation added.
REPLACED = ()
# The exact co0025 text this migration expects, and what it becomes. The column is added where
# the header carries its other total, not appended, so the stored shape reads the way the
# table was written rather than the way it was patched.
REPLACEMENTS = {
    'purchase_profiles': (
        ('expense_total_minor_units BIGINT NOT NULL',
         'expense_total_minor_units BIGINT NOT NULL, \n\titem_total_minor_units BIGINT NOT NULL'),
        ("CONSTRAINT ck_purchase_expense_total_minor_units_positive CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units > 0)",
         "CONSTRAINT ck_purchase_expense_total_minor_units_nonnegative CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units >= 0), \n\tCONSTRAINT ck_purchase_item_total_minor_units_nonnegative CHECK (typeof(item_total_minor_units) = 'integer' AND item_total_minor_units >= 0)"),
    ),
}
# The column each rebuilt table gains, and the value every stored row takes for it. A bill
# written before the Items tab existed bought no items, so its item total is zero as a fact
# rather than as a placeholder.
ADDED = {'purchase_profiles': (('item_total_minor_units', '0'),)}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0033 cannot preserve custom row identity')
    added = ADDED[table]
    if any(row[1] in {name for name, _ in added} for row in columns):
        raise RuntimeError('co0033 total column already exists: ' + table)
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0033 unknown column or constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0033_' + table) + ' (' + ','.join(parts) + suffix
    kept = ['rowid'] + [quote(row[1]) for row in columns if row[6] == 0]
    writable = ','.join(kept + [quote(name) for name, _ in added])
    reading = ','.join(kept + [value for _, value in added])
    # Compared through typeof/quote/CAST so a text 3 and an integer 3, and a blob with an
    # embedded NUL, are all told apart rather than silently equal.
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, reading, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0033_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0033 reserved object already exists')
    for _, name, _ in connection.exec_driver_sql('PRAGMA database_list'):
        attached = '"' + name.replace('"', '""') + '"'
        for row in connection.exec_driver_sql('SELECT name FROM ' + attached + '.sqlite_schema'):
            if row[0].casefold() in {value.casefold() for value in OBJECTS}:
                raise RuntimeError('co0033 item line storage name collision')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, reading, selected) in plans.items():
        temporary = '_co0033_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {reading} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0033 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for statement in DDL:
        connection.exec_driver_sql(statement)
    for _, name, statement in retained:
        if name not in REPLACED:
            connection.exec_driver_sql(statement)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    for name in NEW_TABLES:
        if connection.exec_driver_sql('SELECT 1 FROM main."' + name + '" LIMIT 1').fetchone():
            raise RuntimeError('co0033 item line storage must be empty')
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0033 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
