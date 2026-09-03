"""hub initial

Revision ID: hub0001
Revises:
"""

from alembic import op

from bookflow.hub.schema import metadata

revision = "hub0001"
down_revision = None


def upgrade() -> None:
    metadata.create_all(op.get_bind())


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
