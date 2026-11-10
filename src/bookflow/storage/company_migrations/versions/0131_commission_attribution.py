"""co0131: invoice attribution switches; existing items and companies stay disabled."""
from alembic import op
import sqlalchemy as sa

revision = 'co0131'
down_revision = 'co0130'
branch_labels = depends_on = None
COMPANY_COLUMNS = ('commission_attribution_enabled',)
ITEM_COLUMNS = ('commission_enabled',)


def upgrade():
    connection = op.get_bind()
    for table, column in (('company_info', COMPANY_COLUMNS[0]), ('items', ITEM_COLUMNS[0])):
        columns = {row[1].casefold() for row in connection.exec_driver_sql(f'PRAGMA table_info({table})')}
        if column in columns:
            raise RuntimeError('co0131 reserved column already exists')
    op.add_column('company_info', sa.Column('commission_attribution_enabled', sa.Boolean, nullable=False, server_default='0'))
    op.add_column('items', sa.Column('commission_enabled', sa.Boolean, nullable=False, server_default='0'))


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
