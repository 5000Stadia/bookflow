"""Allow jobs to inherit their preferred delivery method.

Revision ID: co0004
Revises: co0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "co0004"
down_revision = "co0003"


def upgrade() -> None:
    # Revision-local metadata only. SQLite recreates the table while preserving
    # its rows, constraints, indexes, and foreign keys.
    with op.batch_alter_table("customers", recreate="always") as batch:
        batch.alter_column(
            "preferred_delivery_method",
            existing_type=sa.String(8),
            nullable=True,
        )


def downgrade() -> None:
    raise NotImplementedError
