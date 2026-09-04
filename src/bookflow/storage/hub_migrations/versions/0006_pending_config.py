"""Recoverable local configuration projection.

Revision ID: hub0006
Revises: hub0005
"""

import sqlalchemy as sa
from alembic import op

revision = "hub0006"
down_revision = "hub0005"


def upgrade() -> None:
    op.create_table(
        "pending_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String(26), nullable=False),
        sa.Column("request_id", sa.String(26), nullable=False),
        sa.Column("contents", sa.Text, nullable=False),
        sa.CheckConstraint("id = 1", name="ck_pending_config_singleton"),
    )


def downgrade() -> None:
    raise NotImplementedError
