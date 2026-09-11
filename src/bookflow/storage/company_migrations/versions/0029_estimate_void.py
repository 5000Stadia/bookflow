"""A quote document can be voided: one widened CHECK, and nothing else changed.

An estimate is non-posting, so voiding one moves no money -- but it has to be a state the
document can actually hold, and `ck_work_status` enumerates the states. SQLite cannot alter a
CHECK in place, so `work_documents` is rebuilt with the one constraint respelled and every
value, row identity, index and trigger carried across verbatim.

DDL below is frozen: this migration never imports current application metadata.
"""
import importlib
from alembic import op

revision = 'co0029'
down_revision = 'co0028'
branch_labels = None
depends_on = None

# The proposal/estimate clause of ck_work_status. The work-order clause enumerates different
# states, so this fragment appears exactly once in the stored definition.
REPLACEMENTS = {
    'work_documents': (
        "status IN ('draft','open','accepted','declined','superseded','cancelled')",
        "status IN ('draft','open','accepted','declined','superseded','cancelled','voided')"),
}
CHANGED = tuple(REPLACEMENTS)

# The same enumeration guards the revision rows, one table away, and has to move with it.
# Matched against the exact statement co0010 shipped: an unrecognized spelling is somebody
# else's local object and is carried across untouched rather than guessed at.
TRIGGERS = {
    'work_revision_kind': (
        "CREATE TRIGGER work_revision_kind BEFORE INSERT ON work_revisions WHEN NOT EXISTS "
        "(SELECT 1 FROM work_documents d WHERE d.id = NEW.document_id AND ((d.kind IN "
        "('proposal','estimate') AND NEW.status IN ('draft','open','accepted','declined',"
        "'superseded','cancelled')) OR (d.kind = 'work_order' AND NEW.status IN ('draft',"
        "'scheduled','in_progress','on_hold','complete','cancelled')))) BEGIN SELECT "
        "RAISE(ABORT, 'invalid work revision state'); END",
        "CREATE TRIGGER work_revision_kind BEFORE INSERT ON work_revisions WHEN NOT EXISTS "
        "(SELECT 1 FROM work_documents d WHERE d.id = NEW.document_id AND ((d.kind IN "
        "('proposal','estimate') AND NEW.status IN ('draft','open','accepted','declined',"
        "'superseded','cancelled','voided')) OR (d.kind = 'work_order' AND NEW.status IN ('draft',"
        "'scheduled','in_progress','on_hold','complete','cancelled')))) BEGIN SELECT "
        "RAISE(ABORT, 'invalid work revision state'); END"),
}


def _rebuild(connection, table):
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(table)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0027 cannot preserve custom row identity')
    old, new = REPLACEMENTS[table]
    found = [i for i, part in enumerate(parts) if old in part]
    if len(found) != 1 or parts[found[0]].count(old) != 1:
        raise RuntimeError('co0027 unknown constraint: ' + table)
    parts[found[0]] = parts[found[0]].replace(old, new, 1)
    create = 'CREATE TABLE ' + quote('_co0027_' + table) + ' (' + ','.join(parts) + suffix
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
    reserved = {'_co0027_' + name for name in CHANGED}
    existing = connection.exec_driver_sql('SELECT name FROM sqlite_schema').scalars().all()
    if reserved.intersection(existing):
        raise RuntimeError('co0027 reserved object already exists')
    plans = {table: _rebuild(connection, table) for table in CHANGED}
    # Every local view and trigger, plus the indexes the rebuilt table owns: a trigger may name
    # the table from anywhere, and an index on it disappears with it. All are recreated verbatim.
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name IN ('work_documents'))) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name").all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0027_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({writable}) SELECT {writable} FROM {quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
                raise RuntimeError('co0027 rebuilt values differ: ' + table)
        connection.exec_driver_sql(f'DROP TABLE {quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO {quote(table)}')
    stored = {name: sql for kind, name, sql in retained if kind == 'trigger'}
    for name, (before, _) in TRIGGERS.items():
        if stored.get(name) != before:
            raise RuntimeError('co0027 unknown work revision state guard: ' + name)
    for _, name, statement in retained:
        connection.exec_driver_sql(TRIGGERS[name][1] if name in TRIGGERS else statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0027 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
