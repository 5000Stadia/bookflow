"""co0125: device identity for audited POS ticket take-over."""
from alembic import op
revision = 'co0125'
down_revision = 'co0124'
branch_labels = depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql("ALTER TABLE pos_tickets ADD COLUMN device VARCHAR(128) NOT NULL DEFAULT 'unknown'")


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
