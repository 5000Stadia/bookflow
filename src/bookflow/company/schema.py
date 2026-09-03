"""Company database tables for row 1 (blueprint 4.1a, 9.1)."""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(26), nullable=False),
        sa.Column("created_via", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.String(26), nullable=False),
        sa.Column("updated_via", sa.String(16), nullable=False),
    ]


def _address(prefix: str) -> list[sa.Column]:
    return [sa.Column(f"{prefix}_{f}", sa.String(200), nullable=True) for f in ("line1", "line2", "city", "state", "postal_code", "country")]


company_info = sa.Table(
    "company_info", metadata,
    *_common(),
    sa.Column("legal_name", sa.String(200), nullable=False),
    sa.Column("display_name", sa.String(200), nullable=False),
    sa.Column("tax_id_kind", sa.String(3), nullable=False, default="ein"),
    sa.Column("tax_id", sa.String(16), nullable=True),
    sa.Column("entity_type", sa.String(24), nullable=False, default="other"),
    sa.Column("income_tax_form", sa.String(24), nullable=False, default="other"),
    sa.Column("industry", sa.String(128), nullable=True),
    sa.Column("contact_name", sa.String(128), nullable=True),
    *_address("address"),
    *_address("legal_address"),
    *_address("ship_address"),
    sa.Column("phone", sa.String(64), nullable=True),
    sa.Column("fax", sa.String(64), nullable=True),
    sa.Column("email", sa.String(254), nullable=True),
    sa.Column("website", sa.String(254), nullable=True),
    sa.Column("fiscal_year_start_month", sa.Integer, nullable=False, default=1),
    sa.Column("tax_year_start_month", sa.Integer, nullable=False, default=1),
    sa.Column("report_basis", sa.String(8), nullable=False, default="accrual"),
    sa.Column("home_currency", sa.String(3), nullable=False),
    sa.Column("timezone", sa.String(64), nullable=False),
    sa.Column("closing_date", sa.String(10), nullable=True),
    sa.Column("recent_activity_window_seconds", sa.Integer, nullable=False, default=60),
    sa.Column("default_chart", sa.String(64), nullable=True),
)

principals = sa.Table(
    "principals", metadata,
    sa.Column("user_id", sa.String(26), primary_key=True),
    sa.Column("username", sa.String(64), nullable=False),
    sa.Column("display_name", sa.String(128), nullable=False),
    sa.Column("kind", sa.String(8), nullable=False),
    sa.Column("first_seen_at", sa.String(32), nullable=False),
    sa.Column("last_seen_at", sa.String(32), nullable=False),
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
    sa.Index("ix_co_audit_entries_record", "record_type", "record_id"),
    sa.Index("ix_co_audit_entries_event", "event_id"),
)

presence = sa.Table(
    "presence", metadata,
    sa.Column("record_type", sa.String(32), primary_key=True),
    sa.Column("record_id", sa.String(26), primary_key=True),
    sa.Column("user_id", sa.String(26), primary_key=True),
    sa.Column("interface", sa.String(16), primary_key=True),
    sa.Column("started_at", sa.String(32), nullable=False),
    sa.Column("heartbeat_at", sa.String(32), nullable=False),
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

directives = sa.Table(
    "directives", metadata,
    *_common(),
    sa.Column("code", sa.String(16), nullable=False, unique=True),
    sa.Column("text", sa.String(1000), nullable=False),
    sa.Column("given_by", sa.String(26), nullable=False),
    sa.Column("recorded_by", sa.String(26), nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, default=True),
    sa.Column("deactivated_at", sa.String(32), nullable=True),
    sa.Column("deactivated_by", sa.String(26), nullable=True),
)

sequences = sa.Table(
    "sequences", metadata,
    sa.Column("name", sa.String(32), primary_key=True),
    sa.Column("next_number", sa.Integer, nullable=False),
)
