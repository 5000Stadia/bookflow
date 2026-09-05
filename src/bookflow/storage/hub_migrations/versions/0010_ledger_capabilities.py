"""Add domestic journal and financial report capability defaults.

Revision ID: hub0010
Revises: hub0009
"""

import sqlalchemy as sa
from alembic import op

revision = 'hub0010'
down_revision = 'hub0009'
ROLE_CAPABILITY_SEED = (
    ('admin', 'ledger.read', 'member'), ('hub_admin', 'ledger.read', 'member'),
    ('owner', 'ledger.read', 'member'), ('readonly', 'ledger.read', 'member'),
    ('standard', 'ledger.read', 'member'),
    ('admin', 'ledger.post', 'standard'), ('hub_admin', 'ledger.post', 'standard'),
    ('owner', 'ledger.post', 'standard'), ('standard', 'ledger.post', 'standard'),
    ('admin', 'reports', 'member'), ('hub_admin', 'reports', 'member'),
    ('owner', 'reports', 'member'), ('readonly', 'reports', 'member'),
    ('standard', 'reports', 'member'),
)


def upgrade() -> None:
    table = sa.table('role_capabilities', sa.column('role'), sa.column('capability'), sa.column('required_role'))
    op.bulk_insert(table, [dict(role=role, capability=capability, required_role=required)
                          for role, capability, required in ROLE_CAPABILITY_SEED])


def downgrade() -> None:
    raise NotImplementedError
