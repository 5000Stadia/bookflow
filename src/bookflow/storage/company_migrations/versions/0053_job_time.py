"""Customer work gains a fourth kind: time recorded against a job.

Recorded time is stored as a one-line customer-work document, so it needs no table of its
own -- what it needs is for `work_documents` to admit the kind and the one state that kind
holds, and for `custom_field_scopes` to admit it as a record type, the way it admits the
three work kinds standing beside it. SQLite cannot alter a CHECK in place, so both tables are
rebuilt with their constraints respelled and every value, row identity, index and trigger
carried across verbatim, exactly as co0029 rebuilt `work_documents` to admit a voided estimate.

`ck_work_group` already reads correctly for the new kind: it requires a group on an estimate
and forbids one on everything else, and recorded time has none.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
from alembic import op

revision = 'co0053'
down_revision = 'co0052'
branch_labels = None
depends_on = None

# Matched against the exact text standing after co0029. The kind clause appears once in
# `ck_work_kind`; the work-order clause of `ck_work_status` appears once and is the one the
# time clause is appended after, because the proposal/estimate clause enumerates other states.
REPLACEMENTS = {
    'work_documents': (
        ("kind IN ('proposal','estimate','work_order')",
         "kind IN ('proposal','estimate','work_order','time_activity')"),
        ("(kind = 'work_order' AND status IN ('draft','scheduled','in_progress','on_hold','complete','cancelled'))",
         "(kind = 'work_order' AND status IN ('draft','scheduled','in_progress','on_hold','complete','cancelled'))"
         " OR (kind = 'time_activity' AND status IN ('recorded','voided'))"),
    ),
    'custom_field_scopes': (
        ("'proposal','work_order','estimate','sales_order'",
         "'proposal','work_order','estimate','time_activity','sales_order'"),
    ),
    'work_billing_conversions': (
        ("relation IN ('estimate_invoice','estimate_sales_receipt','work_order_invoice','work_order_sales_receipt')",
         "relation IN ('estimate_invoice','estimate_sales_receipt','work_order_invoice','work_order_sales_receipt',"
         "'time_activity_invoice','time_activity_sales_receipt')"),
    ),
}
CHANGED = tuple(REPLACEMENTS)

# The same enumeration guards the revision rows, one table away, and has to move with it.
# Matched against the exact statement co0029 left behind: an unrecognized spelling is somebody
# else's local object and is carried across untouched rather than guessed at.
_BIRTH_BEFORE = (
    "CREATE TRIGGER work_billing_conversion_birth BEFORE INSERT ON work_billing_conversions\n"
    "WHEN NOT EXISTS (SELECT 1 FROM work_documents s JOIN transactions d ON d.id = NEW.destination_transaction_id\n"
    "JOIN transaction_revisions r ON r.transaction_id = d.id AND r.id = NEW.destination_revision_id\n"
    "WHERE s.id = NEW.source_document_id AND s.kind IN ('estimate','work_order')\n"
    "AND d.type = NEW.destination_type AND NEW.relation = s.kind || '_' || d.type AND r.revision_number = 1)\n"
    "BEGIN SELECT RAISE(ABORT, 'invalid work billing conversion lineage'); END")
_GUARD_BEFORE = (
    "CREATE TRIGGER work_revision_kind BEFORE INSERT ON work_revisions WHEN NOT EXISTS "
    "(SELECT 1 FROM work_documents d WHERE d.id = NEW.document_id AND ((d.kind IN "
    "('proposal','estimate') AND NEW.status IN ('draft','open','accepted','declined',"
    "'superseded','cancelled','voided')) OR (d.kind = 'work_order' AND NEW.status IN ('draft',"
    "'scheduled','in_progress','on_hold','complete','cancelled')))) BEGIN SELECT "
    "RAISE(ABORT, 'invalid work revision state'); END")
TRIGGERS = {
    'work_revision_kind': (
        _GUARD_BEFORE,
        _GUARD_BEFORE.replace(
            "'scheduled','in_progress','on_hold','complete','cancelled')))) BEGIN",
            "'scheduled','in_progress','on_hold','complete','cancelled')) OR (d.kind = "
            "'time_activity' AND NEW.status IN ('recorded','voided')))) BEGIN")),
    'work_billing_conversion_birth': (
        _BIRTH_BEFORE,
        _BIRTH_BEFORE.replace("s.kind IN ('estimate','work_order')",
                              "s.kind IN ('estimate','work_order','time_activity')")),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0053 cannot preserve custom row identity')
    for old, new in REPLACEMENTS[table]:
        found = [i for i, part in enumerate(parts) if old in part]
        if len(found) != 1 or parts[found[0]].count(old) != 1:
            raise RuntimeError('co0053 unknown constraint: ' + table)
        parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0053_' + table) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    # Compared as (type, quoted literal, blob) so a rebuilt row that differs in storage class
    # or in a value SQLite would compare equal across affinities still fails the check.
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    reserved = {'_co0053_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0053 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    # Every local view and trigger, plus the indexes the rebuilt table owns: a trigger may name
    # the table from anywhere, and an index on it disappears with it. All are recreated verbatim.
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('work_documents','custom_field_scopes','work_billing_conversions'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0053_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0053 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    for name, (before, _) in TRIGGERS.items():
        if stored.get(name) != before:
            raise RuntimeError('co0053 unknown work guard: ' + name)
    for _, name, statement in retained:
        connection.exec_driver_sql(TRIGGERS[name][1] if name in TRIGGERS else statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0053 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
