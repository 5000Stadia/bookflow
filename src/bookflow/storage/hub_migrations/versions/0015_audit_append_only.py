"""Make the hub audit log append-only in storage, as the company ledger is.

Four BEFORE triggers abort any UPDATE or DELETE on `audit_events` and `audit_entries` from
any connection or process. hub0002 (which numbered existing events) ran long before this
revision, so replaying the chain still works. No table, column or row changes.
The DDL is literal: this revision is frozen.
"""
from alembic import op

revision = 'hub0015'
down_revision = 'hub0014'
branch_labels = None
depends_on = None

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
            raise RuntimeError('hub0015 missing managed table')
    for name in NAMES:
        for schema in ('main', 'temp'):
            if connection.exec_driver_sql(
                    f'SELECT 1 FROM {schema}.sqlite_schema WHERE lower(name)=lower(?)', (name,)).first():
                raise RuntimeError('hub0015 reserved trigger name already exists')
    for table in TABLES:
        for event in ('UPDATE', 'DELETE'):
            connection.exec_driver_sql(trigger_sql(table, event))


def downgrade():
    raise NotImplementedError('Audit history stays append-only')
