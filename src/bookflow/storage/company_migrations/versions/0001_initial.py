"""company initial

Revision ID: co0001
Revises:
"""

from alembic import op

from bookflow.company.schema import metadata

revision = "co0001"
down_revision = None


def upgrade() -> None:
    metadata.create_all(op.get_bind())


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
