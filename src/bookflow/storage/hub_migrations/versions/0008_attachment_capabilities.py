"""Add attachment and activity capability projection rows.

Revision ID: hub0008
Revises: hub0007
"""

import sqlalchemy as sa
from alembic import op

revision = "hub0008"
down_revision = "hub0007"

ROLE_CAPABILITY_SEED = (
    ('admin', 'attachment', 'member'),
    ('hub_admin', 'attachment', 'member'),
    ('owner', 'attachment', 'member'),
    ('readonly', 'attachment', 'member'),
    ('standard', 'attachment', 'member'),
    ('admin', 'attachment', 'standard'),
    ('hub_admin', 'attachment', 'standard'),
    ('owner', 'attachment', 'standard'),
    ('standard', 'attachment', 'standard'),
    ('admin', 'attachment', 'admin'),
    ('hub_admin', 'attachment', 'admin'),
    ('owner', 'attachment', 'admin'),
    ('admin', 'activity', 'member'),
    ('hub_admin', 'activity', 'member'),
    ('owner', 'activity', 'member'),
    ('readonly', 'activity', 'member'),
    ('standard', 'activity', 'member'),
)


def _table():
    return sa.table(
        "role_capabilities",
        sa.column("role", sa.String(12)),
        sa.column("capability", sa.String(128)),
        sa.column("required_role", sa.String(16)),
    )


def upgrade() -> None:
    op.bulk_insert(_table(), [
        {"role": role, "capability": capability, "required_role": required_role}
        for role, capability, required_role in ROLE_CAPABILITY_SEED
    ])


def downgrade() -> None:
    table = _table()
    for role, capability, required_role in ROLE_CAPABILITY_SEED:
        op.execute(table.delete().where(
            (table.c.role == role)
            & (table.c.capability == capability)
            & (table.c.required_role == required_role)
        ))
