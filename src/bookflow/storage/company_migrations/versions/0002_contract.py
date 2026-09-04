"""company: audit tables, presence, idempotency keys, directives, sequences; baseline entry

Revision ID: co0002
Revises: co0001
"""

import json
import sqlalchemy as sa
from alembic import op

revision = "co0002"
down_revision = "co0001"


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(26), nullable=False),
        sa.Column("created_via", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.String(26), nullable=False),
        sa.Column("updated_via", sa.String(16), nullable=False),
    ]


def _tables() -> tuple[sa.Table, ...]:
    """Return the exact co0002 tables without importing mutable live metadata."""
    metadata = sa.MetaData()
    audit_events = sa.Table(
        "audit_events",
        metadata,
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("seq", sa.Integer, nullable=True, unique=True),
        sa.Column("at", sa.String(32), nullable=False),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(26), nullable=True),
        sa.Column("actor_kind", sa.String(8), nullable=True),
        sa.Column("on_behalf_of", sa.String(26), nullable=True),
        sa.Column("interface", sa.String(16), nullable=False),
        sa.Column("client_name", sa.String(64), nullable=False),
        sa.Column("client_version", sa.String(32), nullable=False),
        sa.Column("client_host", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(26), nullable=False),
        sa.Column("request_id", sa.String(26), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("reason", sa.String(140), nullable=True),
        sa.Column("directive_id", sa.String(26), nullable=True),
        sa.Column("directive_code", sa.String(16), nullable=True),
        sa.Column("source_ref", sa.String(512), nullable=True),
        sa.Column("summary", sa.String(512), nullable=False),
    )
    audit_entries = sa.Table(
        "audit_entries",
        metadata,
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("event_id", sa.String(26), sa.ForeignKey("audit_events.id"), nullable=False),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("record_id", sa.String(26), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("version_before", sa.Integer, nullable=True),
        sa.Column("version_after", sa.Integer, nullable=True),
        sa.Column("after", sa.LargeBinary, nullable=True),
        sa.Column("before", sa.LargeBinary, nullable=True),
        sa.Index("ix_co_audit_entries_record", "record_type", "record_id"),
        sa.Index("ix_co_audit_entries_event", "event_id"),
    )
    presence = sa.Table(
        "presence",
        metadata,
        sa.Column("record_type", sa.String(32), primary_key=True),
        sa.Column("record_id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), primary_key=True),
        sa.Column("interface", sa.String(16), primary_key=True),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("heartbeat_at", sa.String(32), nullable=False),
    )
    idempotency_keys = sa.Table(
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
    directives = sa.Table(
        "directives",
        metadata,
        *_common(),
        sa.Column("code", sa.String(16), nullable=False, unique=True),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("given_by", sa.String(26), nullable=False),
        sa.Column("recorded_by", sa.String(26), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("deactivated_at", sa.String(32), nullable=True),
        sa.Column("deactivated_by", sa.String(26), nullable=True),
    )
    sequences = sa.Table(
        "sequences",
        metadata,
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("next_number", sa.Integer, nullable=False),
    )
    return audit_events, audit_entries, presence, idempotency_keys, directives, sequences


def upgrade() -> None:
    conn = op.get_bind()
    for table in _tables():
        table.create(conn, checkfirst=True)
    conn.execute(sa.text("INSERT INTO sequences (name, next_number) VALUES ('directive', 1)"))
    # the baseline entry for company_info is written by the migration caller, which knows the actor (blueprint 7)


def downgrade() -> None:
    raise NotImplementedError
