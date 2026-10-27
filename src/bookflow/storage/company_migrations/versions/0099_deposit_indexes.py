"""Deposit lookup indexes (R180 increment 3, design/performance.md "Increment 3").

Deposit sources and every deposit write walk deposit membership and permanent operations by
transaction (``deposit_draft_validation.admit``): which deposits a receipt was claimed into, which
receipts a deposit claimed, and which receipts a deposit's operations targeted. None of those tables
had an index led by the transaction column, so each step scanned the whole table. This migration adds
the three indexes. Indexes only: no table, column, trigger or row is added or changed.
DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0099'
down_revision = 'co0098'
branch_labels = depends_on = None

INDEXES = (
    'CREATE INDEX ix_deposit_memberships_source ON deposit_memberships (source_transaction_id)',
    'CREATE INDEX ix_deposit_memberships_transaction ON deposit_memberships (transaction_id)',
    'CREATE INDEX ix_deposit_operations_transaction ON deposit_operations (transaction_id)',
)
# What a test rewinding a head file to co0098 drops.
INDEX_NAMES = tuple(statement.split()[2] for statement in INDEXES)


def upgrade():
    connection = op.get_bind()
    names = {row[0].casefold() for row in connection.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & {name.casefold() for name in INDEX_NAMES}:
        raise RuntimeError('co0099 reserved object exists')
    for statement in INDEXES:
        connection.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
