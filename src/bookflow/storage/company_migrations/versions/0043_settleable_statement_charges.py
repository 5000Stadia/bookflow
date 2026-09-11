"""A statement charge becomes something a customer's money can settle.

``co0034`` shipped the charge itself: entered straight onto a customer, stored in the sales
tables, ageing on its own date and printed on the statement. What it could not do was be paid.
Three insertion fences and one CHECK constraint each admitted the invoice and nothing else, so
every settlement edge stopped at the charge's door -- and the refusal was at the database, which
is the worst place for it, because half a widening is how a settled charge disappears from a
report while the aging it belongs to still balances.

No table gains a column and no row is rewritten. ``applications`` and ``payment_selection_items``
already reference ``transactions.id`` with no type column of their own, and a statement charge
already writes the ``sales_profiles`` row that ``applications_exact_party`` joins to prove the
payer and the receivable account match. What changes is only which document types those fences
admit:

* ``applications_paid_transaction_id_type`` -- the settlement edge itself;
* ``payment_selection_items_invoice_id_type`` -- a shared draft's selected rows;
* ``settlement_line_keys_transaction_id_type`` -- the durable line ordinals a settlement
  allocates against;
* ``ck_recovery_invoice_type`` on ``payment_selection_recovery_items`` -- the attempted-edit
  evidence a large selection uploads, whose composite foreign key is ``(id, type)`` and would
  otherwise refuse the charge it is recording.

Only the CHECK needs a table rebuild; the triggers are dropped and recreated, which the rebuild
already does for every stored trigger. ``payment_selection_recovery_items`` holds attempted
edits and no money, so widening its CHECK cannot change an account.

Nothing is backfilled. Widening a fence admits new rows and never invents one, so a company
upgraded here owes exactly what it owed before, and every settlement that stood still stands.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0043'
# The chain is not monotonic -- co0034 -> co0037 -> co0035 -> co0036 -> co0038 -> co0039 ->
# co0040 is what the shipped `down_revision` pairs actually say, because branches landed out
# of number order. co0040 is the head this was written against; co0041 and co0042 are numbers
# claimed on branches that are not in this tree, so pointing at either would break the chain.
down_revision = 'co0040'
branch_labels = None
depends_on = None

NEW_TABLES = ()
OBJECTS = ()
DDL = ()

# The three fences, rewritten to admit the statement charge beside the invoice. Every other
# word is the text co0014 froze, so the diff a reader has to check is one `IN` clause each.
GUARDS = (
    "CREATE TRIGGER settlement_line_keys_transaction_id_type BEFORE INSERT ON settlement_line_keys\nWHEN NEW.transaction_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.transaction_id AND type IN ('invoice', 'statement_charge'))\nBEGIN SELECT RAISE(ABORT, 'payment reference has wrong document type'); END",
    "CREATE TRIGGER applications_paid_transaction_id_type BEFORE INSERT ON applications\nWHEN NEW.paid_transaction_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.paid_transaction_id AND type IN ('invoice', 'statement_charge'))\nBEGIN SELECT RAISE(ABORT, 'payment reference has wrong document type'); END",
    "CREATE TRIGGER payment_selection_items_invoice_id_type BEFORE INSERT ON payment_selection_items\nWHEN NEW.invoice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM transactions WHERE id = NEW.invoice_id AND type IN ('invoice', 'statement_charge'))\nBEGIN SELECT RAISE(ABORT, 'payment reference has wrong document type'); END",
)

CHANGED = ('payment_selection_recovery_items',)
# The three triggers this revision deliberately rewrites, verified against the exact text of the
# revision that last froze them. Anything else stored on the rebuilt table is a local guard whose
# meaning this migration cannot know, and it stops rather than guessing.
REPLACED = ('applications_paid_transaction_id_type', 'payment_selection_items_invoice_id_type',
            'settlement_line_keys_transaction_id_type')
# The exact text this migration expects, and what it becomes.
REPLACEMENTS = {
    'payment_selection_recovery_items': ((
        "CONSTRAINT ck_recovery_invoice_type CHECK (COALESCE((invoice_type = 'invoice'), 0))",
        "CONSTRAINT ck_recovery_invoice_type CHECK (COALESCE((invoice_type IN ('invoice', 'statement_charge')), 0))"),),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0043 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0043 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0043_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def _expected_guards():
    """The exact text of each replaced trigger, taken from the revision that last froze it.

    Every other trigger is dropped and restored verbatim by the rebuild, so only the ones this
    revision rewrites have to be recognised -- and those have to match to the byte, or what is
    stored is a local guard whose meaning this migration cannot know. All three were last frozen
    by co0014: co0028 rewrote `applications_exact_party` and the *paying* side's type fence, and
    left these untouched.
    """
    known = {}
    for name in ('0014_customer_payments',):
        module = importlib.import_module('bookflow.storage.company_migrations.versions.' + name)
        for statement in module.GUARDS:
            known[statement.split()[2]] = statement
    return {name: known[name] for name in REPLACED if name in known}


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = set(OBJECTS) | {'_co0043_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0043 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join(f"'{name}'" for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        f'OR (type = \'index\' AND tbl_name IN ({changed}))) '
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    expected = _expected_guards()
    if set(REPLACED) - set(expected) or any(stored.get(name) != sql for name, sql in expected.items()):
        raise RuntimeError('co0043 unknown settlement type guard')
    # Keep unknown local objects verbatim; do not guess through competing guards.
    for name, sql in stored.items():
        if name in expected:
            continue
        if re.search(r"(?i)\btype\s*(?:=|IN)\s*\(?\s*'invoice'", sql) and re.search(
                r'(?i)\bON\s+["`\[]?(?:applications|payment_selection_items|settlement_line_keys|payment_selection_recovery_items)\b', sql):
            raise RuntimeError('co0043 unknown competing settlement guard')
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0043_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0043 rebuilt values differ: ' + table)
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
        raise RuntimeError('co0043 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
