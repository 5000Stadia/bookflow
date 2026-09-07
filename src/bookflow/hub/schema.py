"""Hub database tables (blueprint 3.0, 4, 7)."""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()


def _column(name: str, type_: sa.types.TypeEngine, description: str, *constraints, **kwargs) -> sa.Column:
    return sa.Column(name, type_, *constraints, info={"description": description}, **kwargs)


def _table(name: str, *columns, description: str) -> sa.Table:
    return sa.Table(name, metadata, *columns, info={"description": description})


def _common(*extra: sa.Column) -> list[sa.Column]:
    return [
        _column("id", sa.String(26), "Stable ULID of this record.", primary_key=True),
        _column("version", sa.Integer, "Optimistic-concurrency version of this record.", nullable=False, default=1),
        _column("created_at", sa.String(32), "UTC timestamp when this record was created.", nullable=False),
        _column("created_by", sa.String(26), "User id recorded as creating this record.", nullable=False),
        _column("created_via", sa.String(16), "Interface recorded for creation of this record.", nullable=False),
        _column("updated_at", sa.String(32), "UTC timestamp of the latest update to this record.", nullable=False),
        _column("updated_by", sa.String(26), "User id recorded for the latest update to this record.", nullable=False),
        _column("updated_via", sa.String(16), "Interface recorded for the latest update to this record.", nullable=False),
        *extra,
    ]


def _administration(table: str) -> list[sa.Column]:
    return [
        _column("version", sa.Integer, "Administration edit version; migrated rows start at one.",
                sa.CheckConstraint("version >= 1", name=f"ck_{table}_version"), nullable=False, server_default=sa.text("1")),
        _column("updated_at", sa.String(32), "Last administration update time; unknown for migrated rows.", nullable=True),
        _column("updated_by", sa.String(26), "Last administration actor; unknown for migrated rows.", nullable=True),
        _column("updated_via", sa.String(16), "Last administration interface; unknown for migrated rows.", nullable=True),
    ]


users = _table(
    "users",
    *_common(
        _column("kind", sa.String(8), "Actor kind: human, agent, or system.", nullable=False),
        _column("username", sa.String(64), "Unique login name or agent handle.", nullable=False, unique=True),
        _column("display_name", sa.String(128), "Human-readable name shown in audit and user-facing output.", nullable=False),
        _column("owner_user_id", sa.String(26), "Owning human user id for an agent; null for humans and the system user.", nullable=True),
        _column("password_hash", sa.String(256), "Password-verification hash for a human user; null when no password is set.", nullable=True),
        _column("hub_admin", sa.Boolean, "Whether this user administers the entire data root.", nullable=False, default=False),
        _column("timezone", sa.String(64), "IANA timezone used to render timestamps; null selects the company timezone.", nullable=True),
        _column("active", sa.Boolean, "Whether this user may authenticate and act.", nullable=False, default=True),
    ),
    description="Users and non-human actors known to this data root.",
)

api_tokens = _table(
    "api_tokens",
    *_common(
        _column("user_id", sa.String(26), "User id authenticated by this token.", sa.ForeignKey("users.id"), nullable=False),
        _column("on_behalf_of", sa.String(26), "Fixed human principal for an agent token; null for human tokens.", nullable=True),
        _column("kind", sa.String(16), "Credential kind: bearer or browser session.", nullable=False),
        _column("token_hash", sa.String(64), "SHA-256 hash of the secret token; the secret is not stored.", nullable=False, unique=True),
        _column("label", sa.String(128), "Operator-supplied name for the token and its client.", nullable=True),
        _column("expires_at", sa.String(32), "UTC timestamp after which the token is invalid; null means no fixed expiry.", nullable=True),
        _column("last_used_at", sa.String(32), "UTC timestamp of the latest throttled liveness refresh.", nullable=True),
        _column("revoked_at", sa.String(32), "UTC timestamp when the token was revoked; null while active.", nullable=True),
        _column("authority_epoch", sa.Integer, "Agent authority epoch at issuance; null for human credentials.", nullable=True),
    ),
    description="Hashed bearer credentials and browser sessions.",
)

agent_principals = _table(
    "agent_principals",
    _column("agent_user_id", sa.String(26), "Agent assigned to act for this principal.", sa.ForeignKey("users.id"), primary_key=True),
    _column("principal_user_id", sa.String(26), "Human principal assigned to this agent.", sa.ForeignKey("users.id"), primary_key=True),
    _column("assigned_by", sa.String(26), "User that assigned this principal.", sa.ForeignKey("users.id"), nullable=False),
    _column("assigned_at", sa.String(32), "UTC timestamp when the principal was assigned.", nullable=False),
    _column("revoked_at", sa.String(32), "UTC timestamp when the assignment was revoked; null while active.", nullable=True),
    sa.Index("ix_agent_principals_principal", "principal_user_id", "agent_user_id"),
    description="Assigned human principals an agent may act on behalf of.",
)

agent_authority = _table(
    "agent_authority",
    _column("agent_user_id", sa.String(26), "Agent whose authority this row governs.", sa.ForeignKey("users.id"), primary_key=True),
    _column("epoch", sa.Integer, "Monotonically increasing authority epoch, starting at one.", nullable=False),
    _column("suspended_at", sa.String(32), "UTC timestamp of authority suspension; null while authorized.", nullable=True),
    _column("suspension_reason", sa.String(140), "Reason for the authority suspension; null while authorized.", nullable=True),
    sa.CheckConstraint("epoch >= 1", name="ck_agent_authority_epoch"),
    *_administration("agent_authority"),
    _column("authorized_at", sa.String(32), "Explicit authorization time; unknown for migrated rows.", nullable=True),
    _column("authorized_by", sa.String(26), "Explicit authorization actor; unknown for migrated rows.", nullable=True),
    _column("permitted_use_at", sa.String(32), "Permitted-use confirmation time; unknown for migrated rows.", nullable=True),
    _column("fresh_context_ack_at", sa.String(32), "Explicit fresh-context acknowledgment time; unknown for migrated rows.", nullable=True),
    _column("fresh_context_required", sa.Boolean, "Whether subsequent authorization requires fresh-context acknowledgment.",
            sa.CheckConstraint("fresh_context_required IN (0,1)", name="ck_agent_authority_fresh_context"), nullable=False, server_default=sa.text("0")),
    description="Current agent authority epoch and suspension state.",
)

organizations = _table(
    "organizations",
    *_common(
        _column("display_name", sa.String(200), "Organization name shown to users.", nullable=False),
        _column("name_key", sa.String(200), "Normalized organization name used for uniqueness and lookup.", nullable=False, unique=True),
        _column("path", sa.String(1024), "Organization directory path relative to the data root.", nullable=False),
        _column("pending_path", sa.String(1024), "Requested relative path while a directory move is incomplete; otherwise null.", nullable=True),
        _column("is_demo", sa.Boolean, "Whether this is the generated demo organization.", nullable=False, default=False),
    ),
    description="Business organizations that contain registered companies.",
)

companies = _table(
    "companies",
    *_common(
        _column("organization_id", sa.String(26), "Organization that contains this company.", sa.ForeignKey("organizations.id"), nullable=False),
        _column("display_name", sa.String(200), "Company name shown to users.", nullable=False),
        _column("name_key", sa.String(200), "Normalized company name used for uniqueness and lookup within its organization.", nullable=False),
        _column("path", sa.String(1024), "Company directory path relative to the data root.", nullable=False),
        _column("pending_path", sa.String(1024), "Requested relative path while a directory move or trash operation is incomplete; otherwise null.", nullable=True),
        _column("legal_name", sa.String(200), "Legal company name copied from the company database.", nullable=False),
        _column("home_currency", sa.String(3), "ISO 4217 home-currency code copied from the company database.", nullable=False),
        _column("schema_revision", sa.String(32), "Company database migration revision recorded by the hub.", nullable=False),
        _column("is_demo", sa.Boolean, "Whether this is the generated demo company.", nullable=False, default=False),
    ),
    sa.UniqueConstraint("organization_id", "name_key", name="uq_company_name_in_org"),
    description="Registry of company folders and their hub-visible identity fields.",
)

memberships = _table(
    "memberships",
    _column("id", sa.String(26), "Stable ULID of this membership.", primary_key=True),
    _column("user_id", sa.String(26), "User or agent that receives this membership.", sa.ForeignKey("users.id"), nullable=False),
    _column("scope_type", sa.String(12), "Membership scope kind: organization or company.", nullable=False),
    _column("scope_id", sa.String(26), "Organization or company id selected by scope_type.", nullable=False),
    _column("role", sa.String(12), "Coarse role: readonly, standard, admin, or owner.", nullable=False),
    _column("grants", sa.Text, "JSON array of capability grants that augment the role; null when unspecified.", nullable=True),
    _column("denies", sa.Text, "JSON array of capability denies that restrict the role; null when unspecified.", nullable=True),
    _column("granted_by", sa.String(26), "User id that granted this membership.", nullable=False),
    _column("granted_at", sa.String(32), "UTC timestamp when this membership was granted.", nullable=False),
    _column("revoked_at", sa.String(32), "UTC timestamp when this membership was revoked; null while active.", nullable=True),
    sa.UniqueConstraint("user_id", "scope_type", "scope_id", name="uq_membership"),
    *_administration("memberships"),
    description="Role and capability membership assigned to a user at an organization or company scope.",
)

role_capabilities = _table(
    "role_capabilities",
    _column("role", sa.String(12), "Concrete role receiving this default capability.", primary_key=True),
    _column("capability", sa.String(128), "Command capability granted by default to the role.", primary_key=True),
    _column("required_role", sa.String(16), "Registry role threshold that produced this row; authenticated represents no threshold.", primary_key=True),
    description="Frozen default capability projection for each concrete role.",
)

features = _table(
    "features",
    _column("scope_type", sa.String(12), "Feature scope kind: organization or company.", primary_key=True),
    _column("scope_id", sa.String(26), "Organization or company id selected by scope_type.", primary_key=True),
    _column("feature", sa.String(128), "Stable feature identifier governed by this row.", primary_key=True),
    _column("enabled", sa.Boolean, "Whether the feature is enabled at this scope.", nullable=False),
    _column("enabled_by", sa.String(26), "User id that set the current feature state.", sa.ForeignKey("users.id"), nullable=False),
    _column("enabled_at", sa.String(32), "UTC timestamp when the current feature state was set.", nullable=False),
    _column("source", sa.String(32), "Authority that set the feature state, such as admin or license.", nullable=False),
    description="Feature enablement state at organization or company scope.",
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
    description="Append-only hub command events with actor and request provenance.",
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
    sa.Index("ix_audit_entries_record", "record_type", "record_id"),
    sa.Index("ix_audit_entries_event", "event_id"),
    description="Record-level snapshots grouped under hub audit events.",
)

pending_config = _table(
    "pending_config",
    _column("id", sa.Integer, "Singleton key, always 1.", primary_key=True),
    _column("token", sa.String(26), "Unique generation of this pending file projection.", nullable=False),
    _column("request_id", sa.String(26), "Request whose audited transaction committed these settings.", nullable=False),
    _column("contents", sa.Text, "Complete desired config.toml contents; private to the local data root.", nullable=False),
    sa.CheckConstraint("id = 1", name="ck_pending_config_singleton"),
    description="Committed local settings awaiting durable config.toml replacement. Reads overlay this row; successful file synchronization clears it.",
)


permission_state = _table(
    "permission_state",
    _column("id", sa.Integer, "Private singleton key, always one.", primary_key=True),
    _column("generation", sa.Integer, "Private authority-input generation; never a public cursor.", nullable=False),
    _column("mode", sa.String(16), "Legacy storage or explicitly activated policy; storage migration retains legacy.", nullable=False),
    _column("catalog_version", sa.Text, "Accepted catalog version in policy mode; null in legacy mode.", nullable=True),
    _column("catalog_sha256", sa.String(64), "Canonical complete root catalog digest; null in legacy mode.", nullable=True),
    _column("catalog_json", sa.Text, "Canonical complete catalog using actual root defaults; null in legacy mode.", nullable=True),
    _column("updated_at", sa.String(32), "Policy-state update time; unknown at migration.", nullable=True),
    _column("updated_by", sa.String(26), "Policy-state update actor; unknown at migration.", nullable=True),
    _column("updated_via", sa.String(16), "Policy-state update interface; unknown at migration.", nullable=True),
    sa.CheckConstraint("id = 1", name="ck_permission_state_singleton"),
    sa.CheckConstraint("generation >= 1", name="ck_permission_state_generation"),
    sa.CheckConstraint("mode IN ('legacy','policy_v1')", name="ck_permission_state_mode"),
    sa.CheckConstraint("(mode='legacy' AND catalog_version IS NULL AND catalog_sha256 IS NULL AND catalog_json IS NULL) OR (mode='policy_v1' AND catalog_version IS NOT NULL AND catalog_sha256 IS NOT NULL AND length(catalog_sha256)=64 AND catalog_json IS NOT NULL)", name="ck_permission_state_catalog"),
    description="Private permission-storage state. Its presence does not activate policy evaluation.",
)
