"""Add note capability projection rows.

Revision ID: hub0007
Revises: hub0006
"""

import sqlalchemy as sa
from alembic import op

revision = "hub0007"
down_revision = "hub0006"

ROLE_CAPABILITY_SEED = (
    ("admin", "note", "member"),
    ("admin", "note", "standard"),
    ("hub_admin", "note", "member"),
    ("hub_admin", "note", "standard"),
    ("owner", "note", "member"),
    ("owner", "note", "standard"),
    ("readonly", "note", "member"),
    ("standard", "note", "member"),
    ("standard", "note", "standard"),
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
