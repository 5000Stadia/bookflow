"""Company database tables for row 1 (blueprint 4.1a, 9.1)."""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()


def _column(name: str, type_: sa.types.TypeEngine, description: str, *constraints, **kwargs) -> sa.Column:
    return sa.Column(name, type_, *constraints, info={"description": description}, **kwargs)


def _table(name: str, *columns, description: str) -> sa.Table:
    return sa.Table(name, metadata, *columns, info={"description": description})


def _common() -> list[sa.Column]:
    return [
        _column("id", sa.String(26), "Stable ULID of this record.", primary_key=True),
        _column("version", sa.Integer, "Optimistic-concurrency version of this record.", nullable=False, default=1),
        _column("created_at", sa.String(32), "UTC timestamp when this record was created.", nullable=False),
        _column("created_by", sa.String(26), "User id recorded as creating this record.", nullable=False),
        _column("created_via", sa.String(16), "Interface recorded for creation of this record.", nullable=False),
        _column("updated_at", sa.String(32), "UTC timestamp of the latest update to this record.", nullable=False),
        _column("updated_by", sa.String(26), "User id recorded for the latest update to this record.", nullable=False),
        _column("updated_via", sa.String(16), "Interface recorded for the latest update to this record.", nullable=False),
    ]


def _address(prefix: str) -> list[sa.Column]:
    names = {"address": "Primary company", "legal_address": "Legal", "ship_address": "Shipping"}
    fields = {
        "line1": "street line 1",
        "line2": "street line 2",
        "city": "city",
        "state": "state or province",
        "postal_code": "postal code",
        "country": "country",
    }
    return [
        _column(f"{prefix}_{field}", sa.String(200), f"{names[prefix]} address {meaning}.", nullable=True)
        for field, meaning in fields.items()
    ]


company_info = _table(
    "company_info",
    *_common(),
    _column("legal_name", sa.String(200), "Company name used on legal and tax records.", nullable=False),
    _column("display_name", sa.String(200), "Company name shown to users.", nullable=False),
    _column("tax_id_kind", sa.String(3), "Tax identifier kind: ein or ssn.", nullable=False, default="ein"),
    _column("tax_id", sa.String(16), "Formatted tax identifier; null when not recorded.", nullable=True),
    _column("entity_type", sa.String(24), "Legal entity classification used for company setup.", nullable=False, default="other"),
    _column("income_tax_form", sa.String(24), "Default federal income-tax form classification.", nullable=False, default="other"),
    _column("industry", sa.String(128), "Company line of business; null when not recorded.", nullable=True),
    _column("contact_name", sa.String(128), "Primary contact person; null when not recorded.", nullable=True),
    *_address("address"),
    *_address("legal_address"),
    *_address("ship_address"),
    _column("phone", sa.String(64), "Main company phone number; null when not recorded.", nullable=True),
    _column("fax", sa.String(64), "Company fax number; null when not recorded.", nullable=True),
    _column("email", sa.String(254), "Main company email address; null when not recorded.", nullable=True),
    _column("website", sa.String(254), "Company website; null when not recorded.", nullable=True),
    _column("fiscal_year_start_month", sa.Integer, "First month of the fiscal year, from 1 through 12.", nullable=False, default=1),
    _column("tax_year_start_month", sa.Integer, "First month of the tax year, from 1 through 12.", nullable=False, default=1),
    _column("report_basis", sa.String(8), "Default report basis: accrual or cash.", nullable=False, default="accrual"),
    _column("home_currency", sa.String(3), "Immutable ISO 4217 home-currency code.", nullable=False),
    _column("timezone", sa.String(64), "IANA timezone used to interpret and render company-local time.", nullable=False),
    _column("closing_date", sa.String(10), "Latest date through which the books are closed; null when open.", nullable=True),
    _column("recent_activity_window_seconds", sa.Integer, "Age in seconds within which a prior write triggers a concurrency warning.", nullable=False, default=60),
    _column("default_chart", sa.String(64), "Identifier of the chart template selected at rollout; null when none was seeded.", nullable=True),
    description="Authoritative company identity, contact, calendar, currency, and accounting settings.",
)

principals = _table(
    "principals",
    _column("user_id", sa.String(26), "Hub user id represented by this local identity copy.", primary_key=True),
    _column("username", sa.String(64), "Username copied from the hub for local provenance rendering.", nullable=False),
    _column("display_name", sa.String(128), "Display name copied from the hub for local provenance rendering.", nullable=False),
    _column("kind", sa.String(8), "Actor kind copied from the hub: human, agent, or system.", nullable=False),
    _column("first_seen_at", sa.String(32), "UTC timestamp when this identity first appeared in the company.", nullable=False),
    _column("last_seen_at", sa.String(32), "UTC timestamp when this identity most recently appeared in the company.", nullable=False),
    description="Company-local identity copies for rendering provenance without the hub database.",
)


audit_events = _table(
    "audit_events",
    _column("id", sa.String(26), "Stable ULID of this audit event.", primary_key=True),
    _column("seq", sa.Integer, "Monotonic database-local cursor assigned to this event.", nullable=True, unique=True),
    _column("at", sa.String(32), "UTC timestamp when the event was recorded.", nullable=False),
    _column("command", sa.String(64), "Registered command that produced the event.", nullable=False),
    _column("actor_id", sa.String(26), "User id that performed the command; null before an actor is available.", nullable=True),
    _column("actor_kind", sa.String(8), "Actor kind recorded for the command; null before an actor is available.", nullable=True),
    _column("on_behalf_of", sa.String(26), "Human principal id for an agent action; otherwise null.", nullable=True),
    _column("interface", sa.String(16), "Interface through which the command was received.", nullable=False),
    _column("client_name", sa.String(64), "Client application name supplied by the command context.", nullable=False),
    _column("client_version", sa.String(32), "Client application version supplied by the command context.", nullable=False),
    _column("client_host", sa.String(255), "Host name supplied by the command context.", nullable=False),
    _column("session_id", sa.String(26), "Session identifier that groups related requests.", nullable=False),
    _column("request_id", sa.String(26), "Identifier of the request that produced this event.", nullable=False),
    _column("idempotency_key", sa.String(128), "Caller-supplied idempotency key; null when absent.", nullable=True),
    _column("reason", sa.String(140), "Short reason supplied for the write; null when absent.", nullable=True),
    _column("directive_id", sa.String(26), "Standing-instruction id cited by the write; null when absent.", nullable=True),
    _column("directive_code", sa.String(16), "Standing-instruction code captured with the event; null when absent.", nullable=True),
    _column("source_ref", sa.String(512), "Caller-supplied reference to the source of the write; null when absent.", nullable=True),
    _column("summary", sa.String(512), "Human-readable summary of what the command did.", nullable=False),
    description="Append-only company command events with actor and request provenance.",
)

audit_entries = _table(
    "audit_entries",
    _column("id", sa.String(26), "Stable ULID of this audit entry.", primary_key=True),
    _column("event_id", sa.String(26), "Audit event that groups this record change.", sa.ForeignKey("audit_events.id"), nullable=False),
    _column("record_type", sa.String(32), "Stable type name of the changed record.", nullable=False),
    _column("record_id", sa.String(26), "Identifier of the changed record.", nullable=False),
    _column("action", sa.String(12), "Change action, such as create, update, deactivate, migrate, or baseline.", nullable=False),
    _column("version_before", sa.Integer, "Record version before the change; null when no prior record existed.", nullable=True),
    _column("version_after", sa.Integer, "Record version after the change; null when no record remains.", nullable=True),
    _column("after", sa.LargeBinary, "Encoded post-change snapshot; null when no post-change state exists.", nullable=True),
    _column("before", sa.LargeBinary, "Encoded pre-change snapshot when recorded; otherwise null.", nullable=True),
    sa.Index("ix_co_audit_entries_record", "record_type", "record_id"),
    sa.Index("ix_co_audit_entries_event", "event_id"),
    description="Record-level snapshots grouped under company audit events.",
)

presence = _table(
    "presence",
    _column("record_type", sa.String(32), "Stable type name of the record being edited.", primary_key=True),
    _column("record_id", sa.String(26), "Identifier of the record being edited.", primary_key=True),
    _column("user_id", sa.String(26), "User id whose editor reports this presence.", primary_key=True),
    _column("interface", sa.String(16), "Interface from which this presence is reported.", primary_key=True),
    _column("started_at", sa.String(32), "UTC timestamp when this editing session first reported presence.", nullable=False),
    _column("heartbeat_at", sa.String(32), "UTC timestamp of the most recent presence heartbeat.", nullable=False),
    description="Short-lived advisory records showing who is editing a company record.",
)

idempotency_keys = _table(
    "idempotency_keys",
    _column("actor_id", sa.String(26), "User id whose idempotency namespace contains the key.", primary_key=True),
    _column("key", sa.String(128), "Caller-supplied key unique within the actor namespace.", primary_key=True),
    _column("command", sa.String(64), "Registered command first run with this key.", nullable=False),
    _column("input_hash", sa.String(64), "SHA-256 digest of the canonical command input.", nullable=False),
    _column("state", sa.String(12), "Execution state: in_progress or complete.", nullable=False),
    _column("request_id", sa.String(26), "Request id that first claimed this key.", nullable=False),
    _column("output", sa.Text, "Canonical JSON output returned by a completed command; null while in progress.", nullable=True),
    _column("created_at", sa.String(32), "UTC timestamp when the key was first claimed.", nullable=False),
    description="Retry records that bind an actor and key to one command input and output.",
)

directives = _table(
    "directives",
    *_common(),
    _column("code", sa.String(16), "Company-local human-readable directive code.", nullable=False, unique=True),
    _column("text", sa.String(1000), "Standing instruction text attributed to its principal.", nullable=False),
    _column("given_by", sa.String(26), "Human principal id that gave the instruction.", nullable=False),
    _column("recorded_by", sa.String(26), "User or agent id that recorded the instruction.", nullable=False),
    _column("active", sa.Boolean, "Whether the directive may be cited by new writes.", nullable=False, default=True),
    _column("deactivated_at", sa.String(32), "UTC timestamp when the directive was deactivated; null while active.", nullable=True),
    _column("deactivated_by", sa.String(26), "User id that deactivated the directive; null while active.", nullable=True),
    description="Versioned standing instructions that company writes may cite.",
)

sequences = _table(
    "sequences",
    _column("name", sa.String(32), "Name of the company-local numbered series.", primary_key=True),
    _column("next_number", sa.Integer, "Next integer to allocate from this series.", nullable=False),
    description="Company-local counters used to allocate stable human-readable codes.",
)
