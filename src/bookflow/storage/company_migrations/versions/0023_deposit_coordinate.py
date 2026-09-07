"""Preserve operation history while admitting private coordinate v2 receipts.

Only the two frozen CHECK bodies change. The runner owns backup, transaction,
foreign-key settings and head publication; this revision never imports metadata.
"""
import importlib
import re
from alembic import op

revision = 'co0023'
down_revision = 'co0022'
branch_labels = None
depends_on = None

CHANGES = {
    'deposit_operations': (
        "CONSTRAINT ck_deposit_operation_command CHECK (command IN ('deposit post','deposit update','deposit void'))",
        "CONSTRAINT ck_deposit_operation_command CHECK (command IN ('deposit post','deposit update','deposit void','deposit coordinate'))"),
    'deposit_operation_items': (
        "CONSTRAINT ck_deposit_operation_item_kind CHECK (kind IN ('request_sources','request_additional','memberships','document_changes','cash_allocations','bank_changes'))",
        "CONSTRAINT ck_deposit_operation_item_kind CHECK (kind IN ('request_sources','request_additional','memberships','document_changes','cash_allocations','bank_changes','source_components','source_applications','source_allocations','source_document_changes'))"),
}


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _mentions(sql, names):
    # Conservative dependency recognition: a literal mention also rejects an
    # external trigger rather than risking writes into an attached database.
    return any(re.search(r'(?i)(?<![\w])' + re.escape(name) + r'(?![\w])', sql or '') for name in names)


def _plan(connection, table):
    parser = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    sql = connection.exec_driver_sql("SELECT sql FROM main.sqlite_schema WHERE type='table' AND name=?", (table,)).scalar_one()
    _, parts, suffix = parser._definitions(sql, table)
    columns = connection.exec_driver_sql(f'PRAGMA main.table_xinfo({_quote(table)})').all()
    if (not columns or any(row[1].lower() in ('rowid','_rowid_','oid') for row in columns)
            or suffix.strip().upper() not in (')', ') STRICT')):
        raise RuntimeError('co0023 unsupported row identity or table suffix')
    old, new = CHANGES[table]
    found = [i for i, part in enumerate(parts) if part.strip() == old]
    if len(found) != 1:
        raise RuntimeError('co0023 unknown operation constraint')
    parts[found[0]] = parts[found[0]].replace(old, new, 1)
    temporary = '_co0023_' + table
    create = 'CREATE TABLE main.' + _quote(temporary) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [_quote(row[1]) for row in columns if row[6] == 0])
    # CAST text to BLOB retains embedded NUL; typeof separates affinity values.
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({_quote(row[1])})', _quote(row[1]), f'CAST({_quote(row[1])} AS BLOB)')])
    return create, writable, selected


def upgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql('PRAGMA foreign_keys').scalar() != 0:
        raise RuntimeError('co0023 requires the preserving migration runner')
    if connection.exec_driver_sql('PRAGMA main.foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0023 invalid initial foreign keys')
    reserved = {'_co0023_' + table for table in CHANGES}
    names = set(CHANGES) | reserved
    for _, schema, _ in connection.exec_driver_sql('PRAGMA database_list').all():
        objects = connection.exec_driver_sql(f'SELECT type,name,tbl_name,sql FROM {_quote(schema)}.sqlite_schema').all()
        for kind, name, owner, sql in objects:
            if name.lower() in reserved:
                raise RuntimeError('co0023 reserved object collision')
            if schema != 'main' and (name.lower() in names or owner.lower() in names or _mentions(sql, names)):
                raise RuntimeError('co0023 external operation dependency')
    # TEMP is absent from database_list until instantiated on some SQLite builds.
    temp = connection.exec_driver_sql('SELECT name,tbl_name,sql FROM temp.sqlite_schema').all()
    if any(name.lower() in names or owner.lower() in names or _mentions(sql, names) for name, owner, sql in temp):
        raise RuntimeError('co0023 temporary operation dependency')
    plans = {table: _plan(connection, table) for table in CHANGES}
    schema_before = connection.exec_driver_sql("SELECT type,name,tbl_name,sql FROM main.sqlite_schema WHERE sql IS NOT NULL").all()
    # SQLite validates view/trigger references during ALTER TABLE. Include the
    # transitive view/trigger closure, not unrelated guards. Lexical recognition
    # is conservative: a literal/comment mention is retained too, since rejecting
    # an extension SQL dialect is safer than silently missing a real dependency.
    dependent = set(CHANGES)
    while True:
        added = {name.lower() for kind,name,owner,sql in schema_before
                 if kind in ('view','trigger') and
                 (owner.lower() in dependent or _mentions(sql,dependent))}
        if added <= dependent:break
        dependent.update(added)
    retained = sorted((row for row in schema_before
        if (row[0] in ('view','trigger') and row[1].lower() in dependent) or
           (row[0]=='index' and row[2] in CHANGES)),
        key=lambda row:({'view':0,'index':1,'trigger':2}[row[0]],row[1]))
    external_fks = {name: connection.exec_driver_sql(f'PRAGMA main.foreign_key_list({_quote(name)})').all()
                    for kind,name,_,_ in schema_before if kind=='table'}
    previous = importlib.import_module('bookflow.storage.company_migrations.versions.0021_deposit_operations')
    guards = {name: sql for kind, name, owner, sql in schema_before if kind == 'trigger'}
    for statement in previous.GUARDS:
        if guards.get(statement.split()[2]) != statement:
            raise RuntimeError('co0023 unknown operation history guard')
    # Drop views/triggers before replacement, preserving their exact SQL. This
    # prevents custom triggers firing on the copy and avoids dangling views.
    for kind in ('trigger', 'view'):
        for object_kind, name, _, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{_quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0023_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO main.{_quote(temporary)} ({writable}) SELECT {writable} FROM main.{_quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM main.{_quote(left)} EXCEPT SELECT {selected} FROM main.{_quote(right)}').fetchone() is not None:
                raise RuntimeError('co0023 rebuilt raw values differ')
        connection.exec_driver_sql(f'DROP TABLE main.{_quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE main.{_quote(temporary)} RENAME TO {_quote(table)}')
    for _, _, _, statement in retained:
        connection.exec_driver_sql(statement)
    schema_after = {(kind,name):(owner,sql) for kind,name,owner,sql in
        connection.exec_driver_sql("SELECT type,name,tbl_name,sql FROM main.sqlite_schema WHERE sql IS NOT NULL").all()}
    for kind,name,owner,sql in schema_before:
        if not (kind=='table' and name in CHANGES) and schema_after.get((kind,name)) != (owner,sql):
            raise RuntimeError('co0023 changed external schema reference')
    for name,expected in external_fks.items():
        if connection.exec_driver_sql(f'PRAGMA main.foreign_key_list({_quote(name)})').all()!=expected:
            raise RuntimeError('co0023 changed external foreign key reference')
    if connection.exec_driver_sql('PRAGMA main.foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0023 final foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
