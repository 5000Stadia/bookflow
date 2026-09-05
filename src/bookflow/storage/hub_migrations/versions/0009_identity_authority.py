"""Add agent authority and atomically retire legacy agent credentials.

Revision ID: hub0009
Revises: hub0008
"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from ulid import ULID

revision = "hub0009"
down_revision = "hub0008"


def _snapshot(value):
    # Frozen raw audit codec. Callers supply only explicitly selected safe fields.
    return None if value is None else b"\x00" + json.dumps(value, sort_keys=True).encode("utf-8")


def _entry(conn, event_id, record_type, record_id, before, after,
           version_before=None, version_after=None):
    conn.execute(sa.text('''
        INSERT INTO audit_entries
            (id, event_id, record_type, record_id, action, version_before,
             version_after, "before", "after")
        VALUES (:id, :event_id, :record_type, :record_id, 'migrate',
                :version_before, :version_after, :before, :after)
    '''), dict(id=str(ULID()), event_id=event_id, record_type=record_type,
               record_id=record_id, version_before=version_before,
               version_after=version_after, before=_snapshot(before), after=_snapshot(after)))


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("authority_epoch", sa.Integer, nullable=True))
    op.create_table(
        "agent_principals",
        sa.Column("agent_user_id", sa.String(26), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("principal_user_id", sa.String(26), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("assigned_by", sa.String(26), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_at", sa.String(32), nullable=False),
        sa.Column("revoked_at", sa.String(32), nullable=True),
    )
    op.create_index("ix_agent_principals_principal", "agent_principals",
                    ["principal_user_id", "agent_user_id"])
    op.create_table(
        "agent_authority",
        sa.Column("agent_user_id", sa.String(26), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("epoch", sa.Integer, nullable=False),
        sa.Column("suspended_at", sa.String(32), nullable=True),
        sa.Column("suspension_reason", sa.String(140), nullable=True),
        sa.CheckConstraint("epoch >= 1", name="ck_agent_authority_epoch"),
    )
    conn = op.get_bind()
    agents = conn.execute(sa.text("SELECT id FROM users WHERE kind='agent' ORDER BY id")).scalars().all()
    if not agents:
        return

    at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    actor_id = conn.execute(sa.text("SELECT id FROM users WHERE kind='system' ORDER BY id LIMIT 1")).scalar()
    event_id = str(ULID())
    reason = "migration_requires_authorization"
    # All DDL, conversion and audit writes use the runner's Alembic transaction.
    # No ordinary post-commit migration report is needed to retain this witness.
    conn.execute(sa.text('''
        INSERT INTO audit_events
            (id, seq, at, command, actor_id, actor_kind, on_behalf_of, interface,
             client_name, client_version, client_host, session_id, request_id,
             idempotency_key, reason, directive_id, directive_code, source_ref, summary)
        VALUES (:id, (SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events), :at,
                'upgrade', :actor_id, 'system', NULL, 'system', 'bookflow-migration',
                'hub0009', '', :id, :id, NULL, :reason, NULL, NULL, NULL,
                'Suspended legacy agent authority and revoked legacy agent credentials')
    '''), dict(id=event_id, at=at, actor_id=actor_id, reason=reason))

    for agent_id in agents:
        authority = dict(agent_user_id=agent_id, epoch=1, suspended_at=at, suspension_reason=reason)
        conn.execute(sa.text('''
            INSERT INTO agent_authority (agent_user_id, epoch, suspended_at, suspension_reason)
            VALUES (:agent_user_id, :epoch, :suspended_at, :suspension_reason)
        '''), authority)
        _entry(conn, event_id, "agent_authority", agent_id, None, authority)
        tokens = conn.execute(sa.text('''
            SELECT id, user_id, on_behalf_of, kind, authority_epoch, revoked_at,
                   version, updated_at, updated_by, updated_via
            FROM api_tokens WHERE user_id=:agent_id AND revoked_at IS NULL ORDER BY id
        '''), dict(agent_id=agent_id)).mappings().all()
        for token in tokens:
            before = dict(token)
            after = {**before, "revoked_at": at, "version": before["version"] + 1,
                     "updated_at": at, "updated_by": actor_id or "system", "updated_via": "system"}
            conn.execute(sa.text('''
                UPDATE api_tokens SET revoked_at=:revoked_at, version=:version,
                    updated_at=:updated_at, updated_by=:updated_by, updated_via=:updated_via
                WHERE id=:id
            '''), after)
            _entry(conn, event_id, "api_token", token["id"], before, after,
                   before["version"], after["version"])


def downgrade() -> None:
    # Removing authority enforcement cannot safely restore legacy credentials.
    raise NotImplementedError("Agent authority security conversion cannot be downgraded")
