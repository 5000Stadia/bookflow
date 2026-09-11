"""Statement charges: a twelfth document type, receivable from the moment it is entered.

No new table. A statement charge is one invoice line with no invoice around it, so it is
stored in the sales tables an invoice already uses -- ``sales_profiles`` for its header and
``sales_line_profiles`` for its single line -- and what has to change is three CHECK
constraints and one trigger, all of which SQLite only widens by rebuilding the table.

``transactions`` gains ``statement_charge`` as a document type. ``sales_profiles`` gains it
as a commercial type carrying no due date: an invoice ages by the due date its terms
compute, and a statement charge has no terms and no invoice, so its own date is the only
date it could age by, and a null due date is the fact that says so rather than a
convention. ``custom_field_scopes`` gains it as a record type, so a charge takes custom
fields like every other document. The document-line kind guard learns that a statement
charge carries the ``sale`` envelope an invoice line carries, which is the pairing that
makes the shared sales writer's output legal here at all.

A statement charge is deliberately **not** added to ``uq_transaction_receivable_number``.
That index exists because an invoice and a credit memo are one number series a customer
reads as one run of numbers; a statement charge takes its own series, the way the anchor
numbers them separately, so sharing the index would refuse a charge numbered 1 for the
sole reason that an invoice 1 exists.

Nothing is backfilled and nothing is settled. A company upgraded here owes exactly what it
owed before: widening a CHECK admits new rows and never rewrites old ones, and no
``applications`` row, trigger or capacity changes, so every settlement that stood before
stands unchanged.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0034'
# co0033 is `build/item-lines` (purchase item lines), in flight on its own branch and not in
# this tree. Whichever of the two lands second points at the other; if item lines has landed
# by the time this is integrated, this becomes 'co0033'.
down_revision = 'co0032'
branch_labels = None
depends_on = None

NEW_TABLES = ()
OBJECTS = ()
DDL = ()
GUARDS = (
    "CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines\nWHEN EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND\n((type = 'journal_entry' AND NEW.kind <> 'journal') OR\n(type IN ('invoice', 'sales_receipt', 'statement_charge') AND NEW.kind <> 'sale') OR\n(type = 'payment' AND NEW.kind <> 'payment') OR\n(type = 'deposit' AND NEW.kind <> 'deposit') OR\n(type IN ('bill', 'vendor_credit') AND NEW.kind <> 'purchase') OR\n(type = 'bill_payment' AND NEW.kind <> 'bill_payment') OR\n(type = 'credit_memo' AND NEW.kind <> 'credit') OR\n(type = 'sales_tax_payment' AND NEW.kind <> 'sales_tax_payment') OR\n(type = 'customer_refund' AND NEW.kind <> 'refund')))\nBEGIN SELECT RAISE(ABORT, 'document line kind does not match transaction type'); END",
)

CHANGED = ('transactions', 'sales_profiles', 'custom_field_scopes')
# The one trigger this revision deliberately rewrites, verified against the exact text the
# revision that last froze it wrote. Anything else stored on the rebuilt tables is a local
# guard whose meaning this migration cannot know, and it stops rather than guessing.
REPLACED = ('document_lines_type_insert',)
# The exact text this migration expects, and what it becomes.
REPLACEMENTS = {
    'transactions': ((
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund', 'vendor_credit'))",
        "CONSTRAINT ck_transaction_type CHECK (type IN ('journal_entry', 'invoice', 'sales_receipt', 'payment', 'deposit', 'bill', 'bill_payment', 'credit_memo', 'sales_tax_payment', 'customer_refund', 'vendor_credit', 'statement_charge'))"),),
    'sales_profiles': ((
        "CONSTRAINT ck_sales_profile_type_due CHECK ((type = 'invoice' AND due_date IS NOT NULL) OR (type = 'sales_receipt' AND due_date IS NULL))",
        "CONSTRAINT ck_sales_profile_type_due CHECK ((type = 'invoice' AND due_date IS NOT NULL) OR (type IN ('sales_receipt', 'statement_charge') AND due_date IS NULL))"),),
    'custom_field_scopes': ((
        "CONSTRAINT ck_custom_field_scopes_record_type CHECK (record_type IN ('customer','vendor','employee','other_name','item','journal_entry','invoice','sales_receipt','credit_memo','payment','deposit','bill','bill_payment','check','credit_card_charge','transfer','inventory_adjustment','vendor_credit','proposal','work_order','estimate','sales_order','purchase_order','item_receipt','statement'))",
        "CONSTRAINT ck_custom_field_scopes_record_type CHECK (record_type IN ('customer','vendor','employee','other_name','item','journal_entry','invoice','sales_receipt','credit_memo','payment','deposit','bill','bill_payment','check','credit_card_charge','transfer','inventory_adjustment','vendor_credit','proposal','work_order','estimate','sales_order','purchase_order','item_receipt','statement','statement_charge'))"),),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0034 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0034 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0034_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def _expected_guards():
    """The exact text of each replaced trigger, taken from the revision that last froze it.

    Every other trigger is dropped and restored verbatim by the rebuild, so only the ones this
    revision rewrites have to be recognised -- and those have to match to the byte, or what is
    stored is a local guard whose meaning this migration cannot know.
    """
    known = {}
    for name in ('0032_vendor_credits',):
        module = importlib.import_module('bookflow.storage.company_migrations.versions.' + name)
        for statement in module.GUARDS:
            known[statement.split()[2]] = statement
    return {name: known[name] for name in REPLACED if name in known}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0034_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0034 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    expected = _expected_guards()
    if set(REPLACED) - set(expected) or any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0034 unknown document type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in expected:
            continue
        if re.search(r'(?i)\b(?:NEW|OLD)\s*\.\s*["`\[]?(?:type|record_type)\b', sql) and re.search(
                r'(?i)\bON\s+["`\[]?(?:transactions|document_lines|sales_profiles|custom_field_scopes)\b', sql):
            raise RuntimeError('co0034 unknown competing document type guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0034_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0034 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for statement in DDL:
        connection.exec_driver_sql(statement)
    for _, name, statement in retained:
        if name not in REPLACED:
            connection.exec_driver_sql(statement)
    for statement in GUARDS:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0034 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
