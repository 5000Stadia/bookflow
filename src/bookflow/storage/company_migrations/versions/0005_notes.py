"""Add versioned company-local notes.

Revision ID: co0005
Revises: co0004
"""

import sqlalchemy as sa
from alembic import op

revision = "co0005"
down_revision = "co0004"


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(26), nullable=False),
        sa.Column("created_via", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.String(26), nullable=False),
        sa.Column("updated_via", sa.String(16), nullable=False),
        sa.Column("record_type", sa.String(64), nullable=False),
        sa.Column("record_id", sa.String(26), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("author_id", sa.String(26), nullable=False),
        sa.Column("interface", sa.String(16), nullable=False),
        sa.Column("at", sa.String(32), nullable=False),
        sa.Column("edited_at", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.CheckConstraint("kind IN ('comment', 'system')", name="ck_notes_kind"),
    )
    op.create_index("ix_notes_target_id", "notes", ["record_type", "record_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_notes_target_id", table_name="notes")
    op.drop_table("notes")
