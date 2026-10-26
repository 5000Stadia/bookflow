"""More cheques to print (R152 follow-up): sales tax payments and customer refunds by check.

A remittance to a tax agency and a refund to a customer, paid from a bank account by check, are
the same paper out of the same chequebook as a check from Write Checks or a payment from Pay
Bills. Saved "to be printed", they join the print queue and take their number from the print run,
so the chequebook's one-number-one-cheque rule has to cover them. Two CHECKs widen:

- ``check_instruments.type`` admits ``sales_tax_payment`` and ``customer_refund``, so a printed
  one carries its number in the one place every cheque number lives;
- ``check_print_marks.type`` admits the same two, so either can be saved to be printed.

Nothing stored changes: every existing row is a check or a bill payment. SQLite cannot alter a
CHECK in place, so both tables are rebuilt with the constraint respelled and every value, row
identity, index and trigger carried across verbatim, as co0083 does. A stored definition this
migration does not recognize is somebody's local edit, and the migration stops rather than guess.
DDL below is frozen: this migration never imports current application metadata.
"""
import importlib

from alembic import op

revision = 'co0096'
down_revision = 'co0093'
branch_labels = None
depends_on = None

REPLACEMENTS = {
    'check_instruments': (
        ("CONSTRAINT ck_check_instrument_type CHECK (type IN ('journal_entry', 'bill_payment'))",
         "CONSTRAINT ck_check_instrument_type CHECK (type IN ('journal_entry', 'bill_payment', "
         "'sales_tax_payment', 'customer_refund'))"),
    ),
    'check_print_marks': (
        ("CONSTRAINT ck_check_print_mark_type CHECK (type IN ('journal_entry', 'bill_payment'))",
         "CONSTRAINT ck_check_print_mark_type CHECK (type IN ('journal_entry', 'bill_payment', "
         "'sales_tax_payment', 'customer_refund'))"),
    ),
}
CHANGED = tuple(REPLACEMENTS)


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0096 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0096 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0096_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0096_' + name for name in CHANGED}
    existing = {name.casefold() for name in connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()}
    if {name.casefold() for name in reserved} & existing:
        raise RuntimeError('co0096 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    changed = ','.join("'%s'" % name for name in CHANGED)
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN (" + changed + "))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0096_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0096 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0096 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
