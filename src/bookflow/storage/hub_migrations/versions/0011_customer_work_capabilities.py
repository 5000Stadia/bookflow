"""Add customer-work capability defaults for all company member roles."""
import sqlalchemy as sa
from alembic import op

revision = 'hub0011'
down_revision = 'hub0010'
branch_labels = None
depends_on = None

ROLE_CAPABILITY_SEED = (
    ('admin', 'customer-work', 'member'),
    ('hub_admin', 'customer-work', 'member'),
    ('owner', 'customer-work', 'member'),
    ('readonly', 'customer-work', 'member'),
    ('standard', 'customer-work', 'member'),
)


def upgrade() -> None:
    table = sa.table('role_capabilities', sa.column('role'), sa.column('capability'), sa.column('required_role'))
    op.bulk_insert(table, [dict(role=role, capability=capability, required_role=required)
                          for role, capability, required in ROLE_CAPABILITY_SEED])


def downgrade() -> None:
    raise NotImplementedError
