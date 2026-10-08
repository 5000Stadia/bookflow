"""Make the company audit log append-only in storage, as the ledger is.

`audit_events` and `audit_entries` are written once and never changed, but only the code's
habits said so. Four BEFORE triggers now abort any UPDATE or DELETE on either table, from
any connection or process, exactly as co0007 does for ledger history. No table, column or
row changes. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0065'
down_revision = 'co0064'
branch_labels = depends_on = None

TABLES = ('audit_events', 'audit_entries')
MESSAGE = 'audit history is append-only'
NAMES = tuple(f'{table}_no_{event}' for table in TABLES for event in ('update', 'delete'))


def trigger_sql(table, event):
    return (f"CREATE TRIGGER main.{table}_no_{event.lower()} BEFORE {event} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END")


def upgrade():
    connection = op.get_bind()
    for table in TABLES:
        if not connection.exec_driver_sql(
                "SELECT 1 FROM main.sqlite_schema WHERE type='table' AND name=?", (table,)).first():
            raise RuntimeError('co0065 missing managed table')
    for name in NAMES:
        for schema in ('main', 'temp'):
            if connection.exec_driver_sql(
                    f'SELECT 1 FROM {schema}.sqlite_schema WHERE lower(name)=lower(?)', (name,)).first():
                raise RuntimeError('co0065 reserved trigger name already exists')
    for table in TABLES:
        for event in ('UPDATE', 'DELETE'):
            connection.exec_driver_sql(trigger_sql(table, event))


def downgrade():
    raise NotImplementedError
