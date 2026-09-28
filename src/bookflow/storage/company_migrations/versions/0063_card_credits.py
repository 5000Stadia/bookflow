"""A credit card credit is a money-out document of its own.

A refund onto a company card -- a returned part, a vendor's credit to the card -- posts through
the register exactly as a card charge does, in the other direction. ``money_out_documents``
records which document a person entered, and its CHECK names the kinds it allows, so this
revision widens that CHECK with ``card_credit``. SQLite only widens a CHECK by rebuilding the
table: every row is copied and compared byte for byte, and the table's index, and every view and
trigger in the file, are put back exactly as they were. No other table, column or row changes.
DDL below is frozen: this migration never imports current application metadata.
"""
import importlib

from alembic import op

revision = 'co0063'
down_revision = 'co0062'
branch_labels = depends_on = None

TABLE = 'money_out_documents'
TEMPORARY = '_co0063_money_out_documents'
OLD = "CONSTRAINT ck_money_out_kind CHECK (kind IN ('check', 'card_charge', 'transfer'))"
NEW = "CONSTRAINT ck_money_out_kind CHECK (kind IN ('check', 'card_charge', 'card_credit', 'transfer'))"


def upgrade():
    connection = op.get_bind()
    preserving = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
    quote = preserving._quote
    if connection.exec_driver_sql('SELECT 1 FROM sqlite_schema WHERE name=?', (TEMPORARY,)).fetchone():
        raise RuntimeError('co0063 reserved object already exists')
    sql = connection.exec_driver_sql("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (TABLE,)).scalar_one()
    _, parts, suffix = preserving._definitions(sql, TABLE)
    found = [index for index, part in enumerate(parts) if OLD in part]
    if len(found) != 1 or parts[found[0]].count(OLD) != 1:
        raise RuntimeError('co0063 unknown money-out kind constraint')
    parts[found[0]] = parts[found[0]].replace(OLD, NEW, 1)
    columns = connection.exec_driver_sql(f'PRAGMA table_xinfo({quote(TABLE)})').all()
    if any(row[1].lower() in ('rowid', '_rowid_', 'oid') for row in columns) or 'WITHOUT' in suffix.upper():
        raise RuntimeError('co0063 cannot preserve custom row identity')
    writable = ','.join(['rowid'] + [quote(row[1]) for row in columns if row[6] == 0])
    selected = ','.join(['rowid'] + [expr for row in columns for expr in
        (f'typeof({quote(row[1])})', f'quote({quote(row[1])})', f'CAST({quote(row[1])} AS BLOB)')])
    # Views and triggers name this table; dropping them first keeps the rename from checking
    # them against a table that is momentarily absent. They come back verbatim below.
    retained = connection.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND (type IN ('view','trigger') "
        "OR (type = 'index' AND tbl_name = ?)) "
        "ORDER BY CASE type WHEN 'view' THEN 0 WHEN 'index' THEN 1 ELSE 2 END,name", (TABLE,)).all()
    for kind in ('trigger', 'view'):
        for object_kind, name, _ in retained:
            if object_kind == kind:
                connection.exec_driver_sql(f'DROP {kind.upper()} main.{quote(name)}')
    connection.exec_driver_sql('CREATE TABLE ' + quote(TEMPORARY) + ' (' + ','.join(parts) + suffix)
    connection.exec_driver_sql(f'INSERT INTO {quote(TEMPORARY)} ({writable}) SELECT {writable} FROM {quote(TABLE)}')
    for left, right in ((TABLE, TEMPORARY), (TEMPORARY, TABLE)):
        if connection.exec_driver_sql(f'SELECT {selected} FROM {quote(left)} EXCEPT SELECT {selected} FROM {quote(right)}').fetchone() is not None:
            raise RuntimeError('co0063 rebuilt values differ')
    connection.exec_driver_sql(f'DROP TABLE {quote(TABLE)}')
    connection.exec_driver_sql(f'ALTER TABLE {quote(TEMPORARY)} RENAME TO {quote(TABLE)}')
    for _, _, statement in retained:
        connection.exec_driver_sql(statement)
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError('co0063 foreign key check failed')


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
