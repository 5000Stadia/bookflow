"""Admit a private deposit delete receipt, and add immutable deposit cancellation receipts.

Two things happen here and they are one change. `deposit delete` is a real verb of the
deposit aggregate, so the permanent operation ledger has to be able to record it: the one
frozen CHECK body naming the deposit commands is widened, by the same preserving rebuild
co0023 used to admit `deposit coordinate`, with every stored raw value compared before and
after. Then the retained-deletion table itself is created.

The runner owns backup, transaction, foreign-key settings and head publication; this
revision never imports metadata.
"""
import importlib
import re
from alembic import op

revision = "co0054"
down_revision = "co0053"
branch_labels = depends_on = None

CHANGES = {
    'deposit_operations': (
        "CONSTRAINT ck_deposit_operation_command CHECK (command IN ('deposit post','deposit update','deposit void','deposit coordinate'))",
        "CONSTRAINT ck_deposit_operation_command CHECK (command IN ('deposit post','deposit update','deposit void','deposit coordinate','deposit delete'))"),
}
# The name the migration-derived checks read. CHANGES drives this migration's own
# rebuild; CHANGED is the vocabulary tests/test_bill_payment_migration._rebuilt_since
# collects, and a rebuild it cannot see reads to every later migration's preservation
# test as a table that simply vanished.
CHANGED = tuple(CHANGES)

DDL = ("\nCREATE TABLE deposit_deletions (\n\ttransaction_id VARCHAR(26) NOT NULL, \n\tfamily TEXT NOT NULL, \n\trevision_id VARCHAR(26) NOT NULL, \n\tfrom_status TEXT NOT NULL, \n\tfrom_version INTEGER NOT NULL, \n\tresult_version INTEGER NOT NULL, \n\tcancellation_batch_id VARCHAR(26), \n\toperation_id VARCHAR(26), \n\toperation_key TEXT NOT NULL, \n\trequest_hash TEXT NOT NULL, \n\trequest_snapshot TEXT NOT NULL, \n\tresult_snapshot TEXT NOT NULL, \n\tcreated_at TEXT NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tprincipal_id VARCHAR(26), \n\tcreated_via TEXT NOT NULL, \n\treason TEXT NOT NULL, \n\taudit_event_id VARCHAR(26) NOT NULL, \n\tPRIMARY KEY (transaction_id), \n\tFOREIGN KEY(transaction_id) REFERENCES transactions (id), \n\tFOREIGN KEY(transaction_id, revision_id) REFERENCES transaction_revisions (transaction_id, id), \n\tFOREIGN KEY(cancellation_batch_id) REFERENCES posting_batches (id), \n\tFOREIGN KEY(operation_id) REFERENCES deposit_operations (id), \n\tCONSTRAINT uq_deposit_delete_operation UNIQUE (created_by, operation_key), \n\tCONSTRAINT ck_deposit_delete_family CHECK (family IN ('deposit')), \n\tCONSTRAINT ck_deposit_delete_status CHECK (from_status IN ('posted','voided')), \n\tCONSTRAINT ck_deposit_delete_version CHECK (typeof(from_version)='integer' AND from_version>0 AND typeof(result_version)='integer' AND result_version=from_version+1), \n\tCONSTRAINT ck_deposit_delete_reason CHECK (length(trim(reason)) BETWEEN 1 AND 140), \n\tCONSTRAINT ck_deposit_delete_request_snapshot CHECK (json_valid(request_snapshot) AND json_type(request_snapshot)='object'), \n\tCONSTRAINT ck_deposit_delete_result_snapshot CHECK (json_valid(result_snapshot) AND json_type(result_snapshot)='object'), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id)\n)\n\n", "CREATE TRIGGER deposit_deletions_owner BEFORE INSERT ON deposit_deletions WHEN NOT EXISTS (SELECT 1 FROM transactions t WHERE t.id=NEW.transaction_id AND t.type=NEW.family AND t.status='voided' AND t.current_revision_id=NEW.revision_id AND t.version=NEW.result_version AND t.void_posting_batch_id IS NEW.cancellation_batch_id) OR (NEW.cancellation_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.cancellation_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind='reversal')) OR EXISTS (SELECT 1 FROM deposit_current_memberships m WHERE m.transaction_id=NEW.transaction_id) OR EXISTS (SELECT 1 FROM reconciliation_keys k JOIN reconciliation_current_members c ON c.key_id=k.id WHERE k.transaction_id=NEW.transaction_id) BEGIN SELECT RAISE(ABORT,'deposit deletion owner mismatch'); END", "CREATE TRIGGER deposit_deletions_no_update BEFORE UPDATE ON deposit_deletions BEGIN SELECT RAISE(ABORT,'deposit deletion is immutable'); END", "CREATE TRIGGER deposit_deletions_no_delete BEFORE DELETE ON deposit_deletions BEGIN SELECT RAISE(ABORT,'deposit deletion is immutable'); END", "CREATE TRIGGER deposit_deletions_transaction_fence BEFORE UPDATE ON transactions WHEN EXISTS (SELECT 1 FROM deposit_deletions d WHERE d.transaction_id=OLD.id) BEGIN SELECT RAISE(ABORT,'deleted deposit is immutable'); END")

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
        raise RuntimeError('co0054 unsupported row identity or table suffix')
    old, new = CHANGES[table]
    found = [i for i, part in enumerate(parts) if part.strip() == old]
    if len(found) != 1:
        raise RuntimeError('co0054 unknown operation constraint')
    parts[found[0]] = parts[found[0]].replace(old, new, 1)
    temporary = '_co0054_' + table
    create = 'CREATE TABLE main.' + _quote(temporary) + ' (' + ','.join(parts) + suffix
    writable = ','.join(['rowid'] + [_quote(row[1]) for row in columns if row[6] == 0])
    # CAST text to BLOB retains embedded NUL; typeof separates affinity values.
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({_quote(row[1])})', _quote(row[1]), f'CAST({_quote(row[1])} AS BLOB)')])
    return create, writable, selected


def _widen():
    connection = op.get_bind()
    if connection.exec_driver_sql('PRAGMA foreign_keys').scalar() != 0:
        raise RuntimeError('co0054 requires the preserving migration runner')
    if connection.exec_driver_sql('PRAGMA main.foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0054 invalid initial foreign keys')
    reserved = {'_co0054_' + table for table in CHANGES}
    names = set(CHANGES) | reserved
    for _, schema, _ in connection.exec_driver_sql('PRAGMA database_list').all():
        objects = connection.exec_driver_sql(f'SELECT type,name,tbl_name,sql FROM {_quote(schema)}.sqlite_schema').all()
        for kind, name, owner, sql in objects:
            if name.lower() in reserved:
                raise RuntimeError('co0054 reserved object collision')
            if schema != 'main' and (name.lower() in names or owner.lower() in names or _mentions(sql, names)):
                raise RuntimeError('co0054 external operation dependency')
    # TEMP is absent from database_list until instantiated on some SQLite builds.
    temp = connection.exec_driver_sql('SELECT name,tbl_name,sql FROM temp.sqlite_schema').all()
    if any(name.lower() in names or owner.lower() in names or _mentions(sql, names) for name, owner, sql in temp):
        raise RuntimeError('co0054 temporary operation dependency')
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
            raise RuntimeError('co0054 unknown operation history guard')
    # Drop views/triggers before replacement, preserving their exact SQL. This
    # prevents custom triggers firing on the copy and avoids dangling views.
    for kind in ('trigger', 'view'):
        for object_kind, name, _, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{_quote(name)}')
    for table, (create, writable, selected) in plans.items():
        temporary = '_co0054_' + table
        connection.exec_driver_sql(create)
        connection.exec_driver_sql(f'INSERT INTO main.{_quote(temporary)} ({writable}) SELECT {writable} FROM main.{_quote(table)}')
        for left, right in ((table, temporary), (temporary, table)):
            if connection.exec_driver_sql(f'SELECT {selected} FROM main.{_quote(left)} EXCEPT SELECT {selected} FROM main.{_quote(right)}').fetchone() is not None:
                raise RuntimeError('co0054 rebuilt raw values differ')
        connection.exec_driver_sql(f'DROP TABLE main.{_quote(table)}')
        connection.exec_driver_sql(f'ALTER TABLE main.{_quote(temporary)} RENAME TO {_quote(table)}')
    for _, _, _, statement in retained:
        connection.exec_driver_sql(statement)
    schema_after = {(kind,name):(owner,sql) for kind,name,owner,sql in
        connection.exec_driver_sql("SELECT type,name,tbl_name,sql FROM main.sqlite_schema WHERE sql IS NOT NULL").all()}
    for kind,name,owner,sql in schema_before:
        if not (kind=='table' and name in CHANGES) and schema_after.get((kind,name)) != (owner,sql):
            raise RuntimeError('co0054 changed external schema reference')
    for name,expected in external_fks.items():
        if connection.exec_driver_sql(f'PRAGMA main.foreign_key_list({_quote(name)})').all()!=expected:
            raise RuntimeError('co0054 changed external foreign key reference')
    if connection.exec_driver_sql('PRAGMA main.foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0054 final foreign key check failed')



def upgrade():
    _widen()
    connection = op.get_bind()
    for statement in DDL:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise RuntimeError("co0054 foreign key check failed")


def downgrade():
    raise RuntimeError("Company migrations are forward-only")
