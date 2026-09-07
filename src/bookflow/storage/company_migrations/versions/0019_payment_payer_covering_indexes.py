"""Add two preserving indexes for historical payer reference reads."""
import sqlite3

from alembic import op
from bookflow.core.errors import BookflowError

revision = 'co0019'
down_revision = 'co0018'
branch_labels = depends_on = None

INDEXES = (
    ('ix_co19_posting_party_transactions', 'posting_lines',
     ('name_type', 'name_id', 'account_id', 'transaction_id')),
    ('ix_co19_applications_targets', 'applications',
     ('paying_transaction_id', 'paid_transaction_id')),
)
# Identifiers are the fixed owned constants above. Main qualification controls
# target resolution; SQLite stores the same canonical spelling as metadata.
DDL = tuple('CREATE INDEX main.' + name + ' ON ' + table + ' ('
            + ', '.join(columns) + ');' for name, table, columns in INDEXES)


def _text_affinity(declaration):
    declaration = declaration.upper()
    return 'INT' not in declaration and any(part in declaration for part in ('CHAR', 'CLOB', 'TEXT'))


def _preflight(connection):
    # Inspect all reservations and targets before the first persistent CREATE.
    for name, table, columns in INDEXES:
        for schema in ('main', 'temp'):
            if connection.exec_driver_sql(
                    f'SELECT 1 FROM {schema}.sqlite_schema WHERE lower(name)=lower(?)', (name,)).first():
                raise RuntimeError('co0019 reserved index name already exists')
        if connection.exec_driver_sql(
                'SELECT 1 FROM temp.sqlite_schema WHERE lower(name)=lower(?)', (table,)).first():
            raise RuntimeError('co0019 temporary managed target shadow')
    probe = sqlite3.connect(':memory:')
    try:
        for _, table, required in INDEXES:
            row = connection.exec_driver_sql(
                "SELECT sql FROM main.sqlite_schema WHERE type='table' AND name=?", (table,)).first()
            if not row or not row[0]:
                raise RuntimeError('co0019 missing managed table')
            columns = {r[1]: r for r in connection.exec_driver_sql(f'PRAGMA main.table_xinfo("{table}")')}
            if any(column not in columns or not _text_affinity(columns[column][2])
                   or columns[column][6] for column in required):
                raise RuntimeError('co0019 incompatible managed column')
            try:
                # Reproduce complete stored DDL. Private registrations are not
                # copied into the probe, and no local expression is substituted.
                probe.execute(row[0])
            except sqlite3.Error:
                raise BookflowError('E_MIGRATION_FAILED', details={
                    'chain': 'company', 'from': down_revision, 'to': revision,
                    'cause': 'unsupported_local_ddl'}) from None
        for sql, (name, _, _) in zip(DDL, INDEXES):
            try:
                probe.execute(sql)
            except sqlite3.Error:
                raise BookflowError('E_MIGRATION_FAILED', details={
                    'chain': 'company', 'from': down_revision, 'to': revision,
                    'cause': 'unsupported_local_ddl'}) from None
            if any(r[3] != 0 or r[4] != 'BINARY' for r in probe.execute(
                    f'PRAGMA main.index_xinfo("{name}")') if r[5]):
                raise RuntimeError('co0019 incompatible index collation')
    finally:
        probe.close()


def upgrade():
    connection = op.get_bind()
    _preflight(connection)
    for sql in DDL:
        connection.exec_driver_sql(sql)


def downgrade():
    raise NotImplementedError
