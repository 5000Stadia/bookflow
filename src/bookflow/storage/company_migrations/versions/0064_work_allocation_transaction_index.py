"""Index billing allocations by the sale that consumes them.

Whether a sale is linked to customer work is asked of `work_billing_allocations` by the sale's
own id: the payment publication check asks it for every sale a page names, on every frame the
page releases, and list filtering asks it for every row. Nothing indexed that column, so each
ask scanned every allocation the company ever made, and a page naming hundreds of sales from
long-billed work took minutes to release. This adds one index; no table, column or row changes.
DDL below is frozen: this migration never imports current application metadata.
"""
import sqlite3

from alembic import op
from bookflow.core.errors import BookflowError

revision = 'co0064'
down_revision = 'co0063'
branch_labels = depends_on = None

NAME, TABLE, COLUMNS = 'ix_work_billing_allocation_transaction', 'work_billing_allocations', ('transaction_id',)
DDL = 'CREATE INDEX main.' + NAME + ' ON ' + TABLE + ' (' + ', '.join(COLUMNS) + ')'


def _text_affinity(declaration):
    declaration = declaration.upper()
    return 'INT' not in declaration and any(part in declaration for part in ('CHAR', 'CLOB', 'TEXT'))


def _preflight(connection):
    # Inspect the reservation and the target before the one persistent CREATE.
    for schema in ('main', 'temp'):
        if connection.exec_driver_sql(
                f'SELECT 1 FROM {schema}.sqlite_schema WHERE lower(name)=lower(?)', (NAME,)).first():
            raise RuntimeError('co0064 reserved index name already exists')
    if connection.exec_driver_sql('SELECT 1 FROM temp.sqlite_schema WHERE lower(name)=lower(?)', (TABLE,)).first():
        raise RuntimeError('co0064 temporary managed target shadow')
    row = connection.exec_driver_sql("SELECT sql FROM main.sqlite_schema WHERE type='table' AND name=?", (TABLE,)).first()
    if not row or not row[0]:
        raise RuntimeError('co0064 missing managed table')
    columns = {r[1]: r for r in connection.exec_driver_sql(f'PRAGMA main.table_xinfo("{TABLE}")')}
    if any(column not in columns or not _text_affinity(columns[column][2]) or columns[column][6] for column in COLUMNS):
        raise RuntimeError('co0064 incompatible managed column')
    probe = sqlite3.connect(':memory:')
    try:
        try:
            # The complete stored table DDL, then the index, on a private probe.
            probe.execute(row[0])
            probe.execute(DDL)
        except sqlite3.Error:
            raise BookflowError('E_MIGRATION_FAILED', details={
                'chain': 'company', 'from': down_revision, 'to': revision,
                'cause': 'unsupported_local_ddl'}) from None
        if any(r[3] != 0 or r[4] != 'BINARY' for r in probe.execute(f'PRAGMA main.index_xinfo("{NAME}")') if r[5]):
            raise RuntimeError('co0064 incompatible index collation')
    finally:
        probe.close()


def upgrade():
    connection = op.get_bind()
    _preflight(connection)
    connection.exec_driver_sql(DDL)


def downgrade():
    raise NotImplementedError
