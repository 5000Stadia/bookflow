"""hub: audit seq and directive_code, idempotency keys

Revision ID: hub0002
Revises: hub0001
"""

import sqlalchemy as sa
from alembic import op

revision = "hub0002"
down_revision = "hub0001"


def _idempotency_keys() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "idempotency_keys",
        metadata,
        sa.Column("actor_id", sa.String(26), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("request_id", sa.String(26), nullable=False),
        sa.Column("output", sa.Text, nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("seq", sa.Integer, nullable=True))
        batch.add_column(sa.Column("directive_code", sa.String(16), nullable=True))
        batch.create_unique_constraint("uq_audit_events_seq", ["seq"])
    conn = op.get_bind()
    ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM audit_events ORDER BY id")).fetchall()]
    for n, event_id in enumerate(ids, start=1):
        conn.execute(sa.text("UPDATE audit_events SET seq = :n WHERE id = :i"), {"n": n, "i": event_id})
    _idempotency_keys().create(conn, checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError
