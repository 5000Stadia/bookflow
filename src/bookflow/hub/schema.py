"""Hub database tables (blueprint 3.0, 4, 7)."""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()


def _common(*extra: sa.Column) -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(26), nullable=False),
        sa.Column("created_via", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.String(26), nullable=False),
        sa.Column("updated_via", sa.String(16), nullable=False),
        *extra,
    ]


users = sa.Table(
    "users", metadata,
    *_common(
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("owner_user_id", sa.String(26), nullable=True),
        sa.Column("password_hash", sa.String(256), nullable=True),
        sa.Column("hub_admin", sa.Boolean, nullable=False, default=False),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, default=True),
    ),
)

api_tokens = sa.Table(
    "api_tokens", metadata,
    *_common(
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("on_behalf_of", sa.String(26), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.String(32), nullable=True),
        sa.Column("last_used_at", sa.String(32), nullable=True),
        sa.Column("revoked_at", sa.String(32), nullable=True),
    ),
)

organizations = sa.Table(
    "organizations", metadata,
    *_common(
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False, unique=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("pending_path", sa.String(1024), nullable=True),
        sa.Column("is_demo", sa.Boolean, nullable=False, default=False),
    ),
)

companies = sa.Table(
    "companies", metadata,
    *_common(
        sa.Column("organization_id", sa.String(26), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("pending_path", sa.String(1024), nullable=True),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("home_currency", sa.String(3), nullable=False),
        sa.Column("schema_revision", sa.String(32), nullable=False),
        sa.Column("is_demo", sa.Boolean, nullable=False, default=False),
    ),
    sa.UniqueConstraint("organization_id", "name_key", name="uq_company_name_in_org"),
)

memberships = sa.Table(
    "memberships", metadata,
    sa.Column("id", sa.String(26), primary_key=True),
    sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("scope_type", sa.String(12), nullable=False),
    sa.Column("scope_id", sa.String(26), nullable=False),
    sa.Column("role", sa.String(12), nullable=False),
    sa.Column("granted_by", sa.String(26), nullable=False),
    sa.Column("granted_at", sa.String(32), nullable=False),
    sa.Column("revoked_at", sa.String(32), nullable=True),
    sa.UniqueConstraint("user_id", "scope_type", "scope_id", name="uq_membership"),
)

audit_events = sa.Table(
    "audit_events", metadata,
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

idempotency_keys = sa.Table(
    "idempotency_keys", metadata,
    sa.Column("actor_id", sa.String(26), primary_key=True),
    sa.Column("key", sa.String(128), primary_key=True),
    sa.Column("command", sa.String(64), nullable=False),
    sa.Column("input_hash", sa.String(64), nullable=False),
    sa.Column("state", sa.String(12), nullable=False),
    sa.Column("request_id", sa.String(26), nullable=False),
    sa.Column("output", sa.Text, nullable=True),
    sa.Column("created_at", sa.String(32), nullable=False),
)

audit_entries = sa.Table(
    "audit_entries", metadata,
    sa.Column("id", sa.String(26), primary_key=True),
    sa.Column("event_id", sa.String(26), sa.ForeignKey("audit_events.id"), nullable=False),
    sa.Column("record_type", sa.String(32), nullable=False),
    sa.Column("record_id", sa.String(26), nullable=False),
    sa.Column("action", sa.String(12), nullable=False),
    sa.Column("version_before", sa.Integer, nullable=True),
    sa.Column("version_after", sa.Integer, nullable=True),
    sa.Column("after", sa.LargeBinary, nullable=True),
    sa.Column("before", sa.LargeBinary, nullable=True),
    sa.Index("ix_audit_entries_record", "record_type", "record_id"),
    sa.Index("ix_audit_entries_event", "event_id"),
)
