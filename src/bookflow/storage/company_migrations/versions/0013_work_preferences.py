"""Company customer-work preferences; append without rebuilding local objects."""
from alembic import op
import sqlalchemy as sa

revision = 'co0013'
down_revision = 'co0012'


def upgrade():
    for name, default in (('estimates_enabled', '1'), ('progress_billing_enabled', '1'),
                          ('close_estimates_after_billing', '0')):
        op.add_column('company_info', sa.Column(name, sa.Boolean(), nullable=False,
                                               server_default=default))


def downgrade():
    raise NotImplementedError
