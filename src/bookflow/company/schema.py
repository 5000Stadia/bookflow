"""Company database tables (blueprint sections 4, 6, 7, 9, 11, and 12)."""

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


def _list_common() -> list[sa.Column]:
    """Columns shared by versioned Row 5 master-data records."""
    return [
        *_common(),
        _column("active", sa.Boolean, "Whether this record is available for new references.", nullable=False, default=True),
        _column("seed_key", sa.String(128), "Immutable manifest key; null for records not created by a seed service.", nullable=True),
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


def _record_address(prefix: str, label: str) -> list[sa.Column]:
    fields = {
        "line1": "street line 1",
        "line2": "street line 2",
        "city": "city",
        "state": "state or province",
        "postal_code": "postal code",
        "country": "country",
    }
    return [
        _column(f"{prefix}_{field}", sa.String(200), f"{label} {meaning}; null when not recorded.", nullable=True)
        for field, meaning in fields.items()
    ]


def _person_identity() -> list[sa.Column]:
    return [
        _column("salutation", sa.String(32), "Person salutation; null when not recorded.", nullable=True),
        _column("first_name", sa.String(128), "Person first name; null when not recorded.", nullable=True),
        _column("middle_name", sa.String(128), "Person middle name; null when not recorded.", nullable=True),
        _column("last_name", sa.String(128), "Person last name; null when not recorded.", nullable=True),
        _column("job_title", sa.String(128), "Person job title; null when not recorded.", nullable=True),
    ]


def _money(prefix: str, label: str, *, nullable: bool = True) -> list[sa.Column]:
    return [
        _column(f"{prefix}_minor_units", sa.BigInteger, f"{label} as signed integer minor units; null with its currency.", nullable=nullable),
        _column(f"{prefix}_currency", sa.String(3), f"ISO 4217 currency for {label.lower()}; null with its amount.", nullable=nullable),
    ]


def _money_pair_check(prefix: str, *, nullable: bool = True) -> sa.CheckConstraint:
    if nullable:
        expression = (
            f"({prefix}_minor_units IS NULL AND {prefix}_currency IS NULL) OR "
            f"({prefix}_minor_units IS NOT NULL AND {prefix}_currency IS NOT NULL)"
        )
    else:
        expression = f"{prefix}_minor_units IS NOT NULL AND {prefix}_currency IS NOT NULL"
    return sa.CheckConstraint(expression, name=f"ck_{prefix}_money_pair")


def _hierarchy(table_name: str) -> list:
    return [
        _column("parent_id", sa.String(26), "Parent record id; null for a root record.", sa.ForeignKey(f"{table_name}.id", ondelete="RESTRICT"), nullable=True),
        _column("full_name", sa.String(1004), "Materialized colon-delimited name from the root through this record.", nullable=False),
        _column("full_name_key", sa.String(2004), "NFC-normalized and case-folded full-name key.", nullable=False),
        _column("depth", sa.Integer, "One-based hierarchy depth from one through five.", nullable=False),
        _column("path", sa.String(136), "Binary-collated internal path encoded as slash-delimited ULIDs.", nullable=False),
        sa.CheckConstraint("depth BETWEEN 1 AND 5", name=f"ck_{table_name}_depth"),
        sa.UniqueConstraint("full_name_key", name=f"uq_{table_name}_full_name_key"),
        sa.Index(f"ux_{table_name}_root_name", "name_key", unique=True, sqlite_where=sa.text("parent_id IS NULL AND active = 1")),
        sa.Index(f"ux_{table_name}_sibling_name", "parent_id", "name_key", unique=True, sqlite_where=sa.text("parent_id IS NOT NULL AND active = 1")),
    ]


def _contact_columns() -> list[sa.Column]:
    return [
        _column("role", sa.String(12), "Contact role: primary, alternate, or additional.", nullable=False),
        *_person_identity(),
        _column("work_phone", sa.String(64), "Work telephone number; null when not recorded.", nullable=True),
        _column("home_phone", sa.String(64), "Home telephone number; null when not recorded.", nullable=True),
        _column("mobile_phone", sa.String(64), "Mobile telephone number; null when not recorded.", nullable=True),
        _column("other_phone", sa.String(64), "Other telephone number; null when not recorded.", nullable=True),
        _column("work_fax", sa.String(64), "Work fax number; null when not recorded.", nullable=True),
        _column("home_fax", sa.String(64), "Home fax number; null when not recorded.", nullable=True),
        _column("primary_email", sa.String(254), "Primary email address; null when not recorded.", nullable=True),
        _column("secondary_email", sa.String(254), "Secondary email address; null when not recorded.", nullable=True),
        _column("website", sa.String(254), "Website address; null when not recorded.", nullable=True),
        _column("external_handle", sa.String(128), "External contact handle; null when not recorded.", nullable=True),
        _column("display_name", sa.String(200), "Contact display name used by convenience projections; null when not recorded.", nullable=True),
    ]


CONTACT_POINT_KINDS = (
    "main_phone", "work_phone", "home_phone", "mobile_phone", "other_phone", "fax", "work_fax", "home_fax",
    "pager", "main_email", "additional_email", "cc_email", "website", "url_1", "url_2", "linked_in",
    "facebook", "twitter", "skype_id", "instant_messaging", "other_1", "other_2", "other_3", "other_4",
)


def _contact_point_columns() -> list:
    quoted = ",".join(f"'{kind}'" for kind in CONTACT_POINT_KINDS)
    return [
        _column("kind", sa.String(24), "Contact-point kind selected from the declared contact catalogue.", nullable=False),
        _column("custom_label", sa.String(64), "Optional user-facing relabeling for this contact point.", nullable=True),
        _column("value", sa.String(512), "Contact-point value such as a telephone number, email address, URL, or handle.", nullable=False),
        sa.CheckConstraint(f"kind IN ({quoted})", name="ck_contact_points_kind"),
        sa.CheckConstraint("length(value) > 0", name="ck_contact_points_value"),
    ]


def _owned_child(owner_column: str, owner_table: str) -> list[sa.Column]:
    return [
        _column("id", sa.String(26), "Stable ULID of this owned child record.", primary_key=True),
        _column(owner_column, sa.String(26), "Id of the aggregate record that owns this child.", sa.ForeignKey(f"{owner_table}.id", ondelete="RESTRICT"), nullable=False),
        _column("position", sa.Integer, "Zero-based position derived from the submitted array order.", nullable=False),
        _column("active", sa.Boolean, "Whether this child is in the owner's current collection.", nullable=False, default=True),
        sa.CheckConstraint("position >= 0", name=f"ck_{owner_table}_{owner_column}_position"),
    ]


company_info = _table(
    "company_info",
    *_common(),
    _column("attachment_max_bytes", sa.Integer, "Maximum attachment bytes, from 1 through 100,000,000.", nullable=False, server_default="25000000"),
    sa.CheckConstraint("attachment_max_bytes BETWEEN 1 AND 100000000", name="ck_company_attachment_max_bytes"),
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
    _column("default_chart_version", sa.Integer, "Version of the applied chart manifest; null while the company is chartless.", nullable=True),
    _column("use_account_numbers", sa.Boolean, "Whether account numbers appear in account labels and selectors.", nullable=False, default=True),
    _column("show_lowest_subaccount_only", sa.Boolean, "Whether account selectors abbreviate hierarchy labels to the lowest subaccount.", nullable=False, default=False),
    _column("required_employee_profile_fields", sa.Text, "Canonical JSON array of alternative field-path groups required for employee profile completeness.", nullable=False),
    _column("use_classes", sa.Boolean, "Whether class controls are enabled for later forms.", nullable=False, default=False),
    _column("prompt_for_class", sa.Boolean, "Whether later forms require or warn for a class when classes are enabled.", nullable=False, default=False),
    _column("enable_price_levels", sa.Boolean, "Whether price-level selectors are enabled for later sales forms.", nullable=False, default=False),
    _column("units_of_measure_mode", sa.String(24), "Unit mode: disabled, single_unit_per_item, or multiple_related_units.", nullable=False, default="disabled"),
    _column("sales_tax_calculation", sa.String(32), "Captured sales-tax algorithm default; upgraded companies retain legacy rounding.", sa.CheckConstraint("sales_tax_calculation IN ('line_component_half_even','line_combined_half_up','invoice_combined_half_up')", name="ck_company_tax_calculation"), nullable=False, server_default="line_component_half_even"),
    _column("sales_tax_enabled", sa.Boolean, "Whether sales-tax controls are enabled for later forms.", nullable=False, default=False),
    _column("default_sales_tax_item_id", sa.String(26), "Default active sales-tax item or group id; null when unset.", sa.ForeignKey("items.id", ondelete="RESTRICT"), nullable=True),
    _column("sales_tax_liability_basis", sa.String(20), "Sales-tax liability basis: invoice_date or payment_receipt.", nullable=False, default="invoice_date"),
    _column("sales_tax_remittance_frequency", sa.String(12), "Sales-tax remittance frequency: monthly, quarterly, or annually.", nullable=False, default="quarterly"),
    _column("default_ship_method_id", sa.String(26), "Default active shipping-method id; null when unset.", sa.ForeignKey("ship_methods.id", ondelete="RESTRICT"), nullable=True),
    _column("free_on_board", sa.String(128), "Default free-on-board text for later sales forms; null when unset.", nullable=True),
    _column("order_printable_checks", sa.Boolean, "Company default for ordering printable checks.", nullable=False, default=False),
    _column("estimates_enabled", sa.Boolean, "Enable new estimates; existing estimate workflows remain available.", nullable=False, server_default="1"),
    _column("progress_billing_enabled", sa.Boolean, "Enable partial quantity, amount, percentage and exact rebill selections.", nullable=False, server_default="1"),
    _column("close_estimates_after_billing", sa.Boolean, "Make estimates inactive after final positive net billing, effective only with progress billing disabled.", nullable=False, server_default="0"),
    _column("automatically_apply_payments", sa.Boolean, "Suggest exact-match then oldest invoice allocations for new cash.", nullable=False, server_default="0"),
    _column("automatically_calculate_payments", sa.Boolean, "Derive amounts for selected invoices while preserving explicitly entered cash.", nullable=False, server_default="0"),
    _column("use_undeposited_funds_for_payments", sa.Boolean, "Default new receipts to Undeposited Funds unless explicitly overridden.", nullable=False, server_default="1"),
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
    _column("undo_of_event_id", sa.String(26), "Original company audit event compensated by this event; null for ordinary events.", sa.ForeignKey("audit_events.id", ondelete="RESTRICT"), nullable=True),
    _column("summary", sa.String(512), "Human-readable summary of what the command did.", nullable=False),
    sa.Index("ux_co_audit_events_undo", "undo_of_event_id", unique=True, sqlite_where=sa.text("undo_of_event_id IS NOT NULL")),
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
    _column("prefix", sa.String(16), "Text prefix used for automatic document numbers.", nullable=False, server_default=""),
    description="Company-local counters used to allocate stable human-readable codes.",
)


accounts = _table(
    "accounts",
    *_list_common(),
    _column("name", sa.String(200), "Account leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded account leaf-name key.", nullable=False),
    *_hierarchy("accounts"),
    _column("number", sa.String(7), "Company-unique account number of one through seven ASCII digits; null when unnumbered.", nullable=True),
    _column("number_key", sa.String(7), "Normalized account-number key; identical to the ASCII-digit number or null.", nullable=True),
    _column("type", sa.String(32), "Standard account type used for posting and statements.", nullable=False),
    _column("description", sa.String(200), "Account description; null when not recorded.", nullable=True),
    _column("currency", sa.String(3), "ISO 4217 account currency.", nullable=False),
    _column("tax_line", sa.String(128), "Compatible tax-line classification; null when unset.", nullable=True),
    _column("institution_name", sa.String(200), "Financial-institution name; null when not applicable.", nullable=True),
    _column("institution_account_last4", sa.String(4), "Safe last four characters of the institution account identifier; null when unavailable.", nullable=True),
    _column("routing_number_last4", sa.String(4), "Safe last four characters of the bank routing number; null when unavailable.", nullable=True),
    _column("provider_profile_ref", sa.String(255), "Opaque protected provider-profile reference; remains null in Row 5.", nullable=True),
    _column("next_check_number", sa.String(64), "Next printable check number; null when not configured.", nullable=True),
    _column("check_reorder_number", sa.String(64), "Check reorder identifier; null when not applicable.", nullable=True),
    _column("order_printable_checks", sa.Boolean, "Nullable account override for the company printable-check preference.", nullable=True),
    _column("default_class_id", sa.String(26), "Default active class id; null when unset.", sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=True),
    _column("track_reimbursable_expenses", sa.Boolean, "Whether eligible expenses are tracked for reimbursement.", nullable=False, default=False),
    _column("reimbursable_income_account_id", sa.String(26), "Income account used for reimbursable expenses; null when tracking is disabled.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("note", sa.Text, "Account note; null when not recorded.", nullable=True),
    _column("system_role", sa.String(64), "Chart-assigned company-unique system role; null for ordinary accounts.", nullable=True),
    sa.CheckConstraint(
        "type IN ('bank','accounts_receivable','other_current_asset','fixed_asset','other_asset','accounts_payable','credit_card','other_current_liability','long_term_liability','equity','income','cost_of_goods_sold','expense','other_income','other_expense','non_posting')",
        name="ck_accounts_type",
    ),
    sa.CheckConstraint("number IS NULL OR (length(number) BETWEEN 1 AND 7 AND number NOT GLOB '*[^0-9]*')", name="ck_accounts_number"),
    sa.CheckConstraint("(number IS NULL AND number_key IS NULL) OR number_key = number", name="ck_accounts_number_key"),
    sa.UniqueConstraint("number_key", name="uq_accounts_number_key"),
    sa.UniqueConstraint("seed_key", name="uq_accounts_seed_key"),
    sa.UniqueConstraint("system_role", name="uq_accounts_system_role"),
    description="Versioned chart-of-accounts records with materialized hierarchy and account profile settings.",
)


customers = _table(
    "customers",
    *_list_common(),
    _column("name", sa.String(200), "Customer or job leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded customer leaf-name key.", nullable=False),
    *_hierarchy("customers"),
    _column("company_name", sa.String(200), "Customer business name; null when not recorded.", nullable=True),
    *_person_identity(),
    *_record_address("billing", "Billing address"),
    _column("terms_id", sa.String(26), "Default active term id; null when unset.", sa.ForeignKey("terms.id", ondelete="RESTRICT"), nullable=True),
    _column("sales_tax_code_id", sa.String(26), "Default active sales-tax code id; null when unset.", sa.ForeignKey("sales_tax_codes.id", ondelete="RESTRICT"), nullable=True),
    _column("sales_tax_item_id", sa.String(26), "Default active sales-tax item or group id; null when unset.", sa.ForeignKey("items.id", ondelete="RESTRICT"), nullable=True),
    _column("price_level_id", sa.String(26), "Default active price-level id; null when unset.", sa.ForeignKey("price_levels.id", ondelete="RESTRICT"), nullable=True),
    _column("customer_type_id", sa.String(26), "Active customer-type id; null when unset.", sa.ForeignKey("customer_types.id", ondelete="RESTRICT"), nullable=True),
    _column("sales_rep_id", sa.String(26), "Default active sales-representative id; null when unset.", sa.ForeignKey("sales_reps.id", ondelete="RESTRICT"), nullable=True),
    _column("preferred_payment_method_id", sa.String(26), "Preferred active payment-method id; null when unset.", sa.ForeignKey("payment_methods.id", ondelete="RESTRICT"), nullable=True),
    _column("preferred_ship_method_id", sa.String(26), "Preferred active shipping-method id; null when unset.", sa.ForeignKey("ship_methods.id", ondelete="RESTRICT"), nullable=True),
    _column("resale_number", sa.String(128), "Customer resale or exemption number; null when not recorded.", nullable=True),
    *_money("credit_limit", "Customer credit limit"),
    _column("preferred_delivery_method", sa.String(8), "Preferred delivery method override: none, email, or mail; null on a job means inherit.", nullable=True),
    _column("account_number", sa.String(128), "Customer account number; null when not recorded.", nullable=True),
    _column("payment_profile_ref", sa.String(255), "Opaque protected payment-profile reference; remains null in Row 5.", nullable=True),
    _column("payment_brand", sa.String(32), "Safe payment brand display metadata; null when unavailable.", nullable=True),
    _column("payment_last4", sa.String(4), "Safe last four payment-profile characters; null when unavailable.", nullable=True),
    _column("payment_expiry_month", sa.Integer, "Safe payment expiry month; null when unavailable.", nullable=True),
    _column("payment_expiry_year", sa.Integer, "Safe payment expiry year; null when unavailable.", nullable=True),
    *_record_address("payment_billing", "Protected payment-profile billing address"),
    _column("notes", sa.Text, "Customer or job notes; null when not recorded.", nullable=True),
    _column("default_class_id", sa.String(26), "Default active class id; null when unset.", sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=True),
    _column("job_status", sa.String(16), "Job status, or none for an ordinary customer.", nullable=False, default="none"),
    _column("job_type_id", sa.String(26), "Active job-type id; null when unset.", sa.ForeignKey("job_types.id", ondelete="RESTRICT"), nullable=True),
    _column("job_start", sa.String(10), "ISO job start date; null when unset.", nullable=True),
    _column("job_projected_end", sa.String(10), "ISO projected job end date; null when unset.", nullable=True),
    _column("job_end", sa.String(10), "ISO actual job end date; null when unset.", nullable=True),
    _column("job_description", sa.Text, "Job description; null when unset.", nullable=True),
    _column("job_sales_rep_id", sa.String(26), "Job-specific active sales-representative id; null when inherited or unset.", sa.ForeignKey("sales_reps.id", ondelete="RESTRICT"), nullable=True),
    _column("address_mode", sa.String(7), "Address collection mode: inherit or own.", nullable=False, default="own"),
    _column("contact_mode", sa.String(7), "Contact collection mode: inherit or own.", nullable=False, default="own"),
    _money_pair_check("credit_limit"),
    sa.CheckConstraint("preferred_delivery_method IN ('none','email','mail')", name="ck_customers_delivery_method"),
    sa.CheckConstraint("job_status IN ('none','pending','awarded','in_progress','closed','not_awarded')", name="ck_customers_job_status"),
    sa.CheckConstraint("address_mode IN ('inherit','own')", name="ck_customers_address_mode"),
    sa.CheckConstraint("contact_mode IN ('inherit','own')", name="ck_customers_contact_mode"),
    sa.CheckConstraint("job_projected_end IS NULL OR job_start IS NULL OR job_projected_end >= job_start", name="ck_customers_projected_end"),
    sa.CheckConstraint("job_end IS NULL OR job_start IS NULL OR job_end >= job_start", name="ck_customers_job_end"),
    sa.UniqueConstraint("seed_key", name="uq_customers_seed_key"),
    description="Versioned customers and jobs with materialized hierarchy, profile defaults, and inheritance modes.",
)


customer_addresses = _table(
    "customer_addresses",
    *_owned_child("customer_id", "customers"),
    _column("label", sa.String(128), "Shipping-address label unique among the owner's active addresses.", nullable=False),
    _column("label_key", sa.String(256), "NFC-normalized and case-folded shipping-address label key.", nullable=False),
    _column("is_default", sa.Boolean, "Whether this is the owner's default active shipping address.", nullable=False, default=False),
    *_record_address("address", "Shipping address"),
    sa.Index("ux_customer_addresses_label", "customer_id", "label_key", unique=True, sqlite_where=sa.text("active = 1")),
    sa.Index("ux_customer_addresses_default", "customer_id", unique=True, sqlite_where=sa.text("active = 1 AND is_default = 1")),
    description="Stable ordered shipping-address children owned and versioned by a customer or job.",
)


customer_contacts = _table(
    "customer_contacts",
    *_owned_child("customer_id", "customers"),
    *_contact_columns(),
    sa.CheckConstraint("role IN ('primary','alternate','additional')", name="ck_customer_contacts_role"),
    sa.Index("ux_customer_contacts_primary", "customer_id", unique=True, sqlite_where=sa.text("active = 1 AND role = 'primary'")),
    sa.Index("ux_customer_contacts_alternate", "customer_id", unique=True, sqlite_where=sa.text("active = 1 AND role = 'alternate'")),
    description="Stable ordered contact children owned and versioned by a customer or job.",
)


customer_contact_points = _table(
    "customer_contact_points",
    *_owned_child("contact_id", "customer_contacts"),
    *_contact_point_columns(),
    description="Stable ordered relabelable contact points owned through a customer contact aggregate.",
)


vendors = _table(
    "vendors",
    *_list_common(),
    _column("name", sa.String(200), "Vendor display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded vendor-name key.", nullable=False),
    _column("company_name", sa.String(200), "Vendor business name; null when not recorded.", nullable=True),
    *_person_identity(),
    *_record_address("address", "Vendor address"),
    _column("terms_id", sa.String(26), "Default active term id; null when unset.", sa.ForeignKey("terms.id", ondelete="RESTRICT"), nullable=True),
    _column("vendor_type_id", sa.String(26), "Active vendor-type id; null when unset.", sa.ForeignKey("vendor_types.id", ondelete="RESTRICT"), nullable=True),
    _column("default_class_id", sa.String(26), "Default active class id; null when unset.", sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=True),
    _column("billing_rate_level_id", sa.String(255), "Opaque time-billing rate-level reference; remains null in Row 5.", nullable=True),
    _column("account_number", sa.String(128), "Vendor account number; null when not recorded.", nullable=True),
    _column("print_name_on_check_as", sa.String(200), "Vendor name printed on checks; null to use the display name.", nullable=True),
    *_money("credit_limit", "Vendor credit limit"),
    _column("eligible_1099", sa.Boolean, "Whether the vendor is eligible for later 1099 reporting.", nullable=False, default=False),
    _column("is_tax_agency", sa.Boolean, "Whether the vendor may be referenced by a sales-tax item.", nullable=False, default=False),
    _column("recall_last_transaction", sa.Boolean, "Nullable vendor override for the later recall-last-transaction preference.", nullable=True),
    _column("notes", sa.Text, "Vendor notes; null when not recorded.", nullable=True),
    _column("tax_id_kind", sa.String(3), "Safe tax identifier kind, ein or ssn; null before a protected profile exists.", nullable=True),
    _column("tax_id_last4", sa.String(4), "Safe last four tax-identifier characters; null when unavailable.", nullable=True),
    _column("tax_profile_ref", sa.String(255), "Opaque protected tax-profile reference; remains null in Row 5.", nullable=True),
    _money_pair_check("credit_limit"),
    sa.CheckConstraint("tax_id_kind IS NULL OR tax_id_kind IN ('ein','ssn')", name="ck_vendors_tax_id_kind"),
    sa.UniqueConstraint("name_key", name="uq_vendors_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_vendors_seed_key"),
    description="Versioned vendor master records with purchasing, tax-agency, and profile defaults.",
)


vendor_contacts = _table(
    "vendor_contacts",
    *_owned_child("vendor_id", "vendors"),
    *_contact_columns(),
    sa.CheckConstraint("role IN ('primary','alternate','additional')", name="ck_vendor_contacts_role"),
    sa.Index("ux_vendor_contacts_primary", "vendor_id", unique=True, sqlite_where=sa.text("active = 1 AND role = 'primary'")),
    sa.Index("ux_vendor_contacts_alternate", "vendor_id", unique=True, sqlite_where=sa.text("active = 1 AND role = 'alternate'")),
    description="Stable ordered contact children owned and versioned by a vendor.",
)


vendor_contact_points = _table(
    "vendor_contact_points",
    *_owned_child("contact_id", "vendor_contacts"),
    *_contact_point_columns(),
    description="Stable ordered relabelable contact points owned through a vendor contact aggregate.",
)


vendor_expense_accounts = _table(
    "vendor_expense_accounts",
    *_owned_child("vendor_id", "vendors"),
    _column("account_id", sa.String(26), "Active expense or cost-of-goods-sold account id.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
    sa.Index("ux_vendor_expense_accounts_active", "vendor_id", "account_id", unique=True, sqlite_where=sa.text("active = 1")),
    description="Ordered default expense-account references owned and versioned by a vendor.",
)


customer_vendor_links = _table(
    "customer_vendor_links",
    *_common(),
    _column("customer_id", sa.String(26), "Customer endpoint of this explicit relationship.", sa.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False),
    _column("vendor_id", sa.String(26), "Vendor endpoint of this explicit relationship.", sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False),
    _column("active", sa.Boolean, "Whether this customer/vendor relationship is current.", nullable=False, default=True),
    sa.UniqueConstraint("customer_id", "vendor_id", name="uq_customer_vendor_links_pair"),
    sa.Index("ux_customer_vendor_links_customer", "customer_id", unique=True, sqlite_where=sa.text("active = 1")),
    sa.Index("ux_customer_vendor_links_vendor", "vendor_id", unique=True, sqlite_where=sa.text("active = 1")),
    description="Versioned explicit one-to-one relationships between customer and vendor records.",
)


employees = _table(
    "employees",
    *_list_common(),
    _column("name", sa.String(200), "Employee display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded employee-name key.", nullable=False),
    *_person_identity(),
    _column("print_name_on_check_as", sa.String(200), "Employee name printed on checks; null to use the display name.", nullable=True),
    _column("employment_type", sa.String(12), "Employment type; null when not classified.", nullable=True),
    *_record_address("address", "Employee address"),
    _column("phone", sa.String(64), "Employee telephone number; null when not recorded.", nullable=True),
    _column("email", sa.String(254), "Employee email address; null when not recorded.", nullable=True),
    _column("hire_date", sa.String(10), "ISO hire date; null when not recorded.", nullable=True),
    _column("release_date", sa.String(10), "ISO release date; null while not released.", nullable=True),
    _column("emergency_contact_name", sa.String(200), "Emergency-contact name; null when not recorded.", nullable=True),
    _column("emergency_contact_relationship", sa.String(128), "Emergency-contact relationship; null when not recorded.", nullable=True),
    _column("emergency_contact_phone", sa.String(64), "Emergency-contact telephone number; null when not recorded.", nullable=True),
    _column("emergency_contact_email", sa.String(254), "Emergency-contact email address; null when not recorded.", nullable=True),
    _column("default_class_id", sa.String(26), "Default active class id; null when unset.", sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=True),
    _column("notes", sa.Text, "Employee notes; null when not recorded.", nullable=True),
    _column("tax_id_last4", sa.String(4), "Safe last four tax-identifier characters; null when unavailable.", nullable=True),
    sa.CheckConstraint("employment_type IS NULL OR employment_type IN ('full_time','part_time','seasonal','temporary','other')", name="ck_employees_employment_type"),
    sa.CheckConstraint("release_date IS NULL OR hire_date IS NULL OR release_date >= hire_date", name="ck_employees_release_date"),
    sa.UniqueConstraint("name_key", name="uq_employees_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_employees_seed_key"),
    description="Versioned employee profiles containing nonsecret contact and employment master data.",
)


other_names = _table(
    "other_names",
    *_list_common(),
    _column("name", sa.String(200), "Other-name display key.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded other-name key.", nullable=False),
    _column("company_name", sa.String(200), "Business name; null when not recorded.", nullable=True),
    *_person_identity(),
    *_record_address("address", "Other-name address"),
    _column("phone", sa.String(64), "Telephone number; null when not recorded.", nullable=True),
    _column("email", sa.String(254), "Email address; null when not recorded.", nullable=True),
    _column("contact", sa.String(200), "Contact name; null when not recorded.", nullable=True),
    _column("account_number", sa.String(128), "Counterparty account number; null when not recorded.", nullable=True),
    _column("default_class_id", sa.String(26), "Default active class id; null when unset.", sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=True),
    _column("notes", sa.Text, "Other-name notes; null when not recorded.", nullable=True),
    _column("converted_to_type", sa.String(8), "Target type after explicit conversion; null while unconverted.", nullable=True),
    _column("converted_to_id", sa.String(26), "Stable target id after explicit conversion; null while unconverted.", nullable=True),
    sa.CheckConstraint("converted_to_type IS NULL OR converted_to_type IN ('customer','vendor','employee')", name="ck_other_names_converted_type"),
    sa.CheckConstraint("(converted_to_type IS NULL) = (converted_to_id IS NULL)", name="ck_other_names_conversion_pair"),
    sa.UniqueConstraint("name_key", name="uq_other_names_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_other_names_seed_key"),
    description="Versioned noncustomer and nonvendor payee, owner, and partner master records.",
)


item_categories = _table(
    "item_categories",
    *_list_common(),
    _column("name", sa.String(200), "Item-category leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded category leaf-name key.", nullable=False),
    *_hierarchy("item_categories"),
    sa.UniqueConstraint("seed_key", name="uq_item_categories_seed_key"),
    description="Versioned hierarchical categories that classify items independently of item hierarchy.",
)


classes = _table(
    "classes",
    *_list_common(),
    _column("name", sa.String(200), "Class leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded class leaf-name key.", nullable=False),
    *_hierarchy("classes"),
    sa.UniqueConstraint("seed_key", name="uq_classes_seed_key"),
    description="Versioned hierarchical classes used to classify later transaction headers and lines.",
)


terms = _table(
    "terms",
    *_list_common(),
    _column("name", sa.String(200), "Term display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded term-name key.", nullable=False),
    _column("kind", sa.String(12), "Term kind: standard or date_driven.", nullable=False),
    _column("due_days", sa.Integer, "Days after transaction date for standard terms; null for date-driven terms.", nullable=True),
    _column("discount_days", sa.Integer, "Early-discount days for standard terms; null when absent.", nullable=True),
    _column("due_day_of_month", sa.Integer, "Calendar due day for date-driven terms; null for standard terms.", nullable=True),
    _column("due_next_month_if_within_days", sa.Integer, "Threshold moving a date-driven due date to the next month.", nullable=True),
    _column("discount_day_of_month", sa.Integer, "Calendar discount day for date-driven terms; null when absent.", nullable=True),
    _column("discount_percent_millionths", sa.BigInteger, "Early-discount percent in millionths of one percentage point; null when absent.", nullable=True),
    sa.CheckConstraint("kind IN ('standard','date_driven')", name="ck_terms_kind"),
    sa.CheckConstraint("due_days IS NULL OR due_days BETWEEN 0 AND 365", name="ck_terms_due_days"),
    sa.CheckConstraint("discount_days IS NULL OR discount_days BETWEEN 0 AND 365", name="ck_terms_discount_days"),
    sa.CheckConstraint("due_day_of_month IS NULL OR due_day_of_month BETWEEN 1 AND 31", name="ck_terms_due_day"),
    sa.CheckConstraint("due_next_month_if_within_days IS NULL OR due_next_month_if_within_days BETWEEN 0 AND 31", name="ck_terms_next_month"),
    sa.CheckConstraint("discount_day_of_month IS NULL OR discount_day_of_month BETWEEN 1 AND 31", name="ck_terms_discount_day"),
    sa.CheckConstraint("discount_percent_millionths IS NULL OR discount_percent_millionths BETWEEN 0 AND 100000000", name="ck_terms_discount_percent"),
    sa.CheckConstraint("(discount_percent_millionths IS NULL) = (CASE WHEN kind='standard' THEN discount_days IS NULL ELSE discount_day_of_month IS NULL END)", name="ck_terms_discount_pair"),
    sa.UniqueConstraint("name_key", name="uq_terms_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_terms_seed_key"),
    description="Versioned standard and date-driven customer and vendor payment terms.",
)


payment_methods = _table(
    "payment_methods",
    *_list_common(),
    _column("name", sa.String(200), "Payment-method display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded payment-method key.", nullable=False),
    _column("kind", sa.String(16), "Payment-method kind without credential data.", nullable=False),
    sa.CheckConstraint("kind IN ('cash','check','credit_card','debit_card','gift_card','e_check','ach','other')", name="ck_payment_methods_kind"),
    sa.UniqueConstraint("name_key", name="uq_payment_methods_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_payment_methods_seed_key"),
    description="Versioned payment-method classifications that contain no credentials.",
)


sales_tax_codes = _table(
    "sales_tax_codes",
    *_list_common(),
    _column("code", sa.String(3), "One-to-three-character sales-tax code used as this list's display key.", nullable=False),
    _column("code_key", sa.String(6), "NFC-normalized and case-folded sales-tax code key.", nullable=False),
    _column("description", sa.String(200), "Sales-tax code description; null when not recorded.", nullable=True),
    _column("taxable", sa.Boolean, "Whether this code delegates to taxable item behavior.", nullable=False),
    sa.CheckConstraint("length(code) BETWEEN 1 AND 3", name="ck_sales_tax_codes_length"),
    sa.UniqueConstraint("code_key", name="uq_sales_tax_codes_code_key"),
    sa.UniqueConstraint("seed_key", name="uq_sales_tax_codes_seed_key"),
    description="Versioned taxable and nontaxable classification codes for customers and items.",
)


customer_types = _table(
    "customer_types",
    *_list_common(),
    _column("name", sa.String(200), "Customer-type leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded customer-type leaf-name key.", nullable=False),
    *_hierarchy("customer_types"),
    sa.UniqueConstraint("seed_key", name="uq_customer_types_seed_key"),
    description="Versioned hierarchical customer segmentation types.",
)


vendor_types = _table(
    "vendor_types",
    *_list_common(),
    _column("name", sa.String(200), "Vendor-type leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded vendor-type leaf-name key.", nullable=False),
    *_hierarchy("vendor_types"),
    sa.UniqueConstraint("seed_key", name="uq_vendor_types_seed_key"),
    description="Versioned hierarchical vendor segmentation types.",
)


job_types = _table(
    "job_types",
    *_list_common(),
    _column("name", sa.String(200), "Job-type leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded job-type leaf-name key.", nullable=False),
    *_hierarchy("job_types"),
    sa.UniqueConstraint("seed_key", name="uq_job_types_seed_key"),
    description="Versioned hierarchical job segmentation types.",
)


sales_reps = _table(
    "sales_reps",
    *_list_common(),
    _column("name", sa.String(200), "Sales-representative list display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded sales-representative name key.", nullable=False),
    _column("initials", sa.String(5), "Company-unique sales-representative initials.", nullable=False),
    _column("initials_key", sa.String(10), "NFC-normalized and case-folded initials key.", nullable=False),
    _column("name_type", sa.String(10), "Source list type: employee, vendor, or other_name.", nullable=False),
    _column("name_id", sa.String(26), "Stable id of the source record selected by name_type.", nullable=False),
    sa.CheckConstraint("name_type IN ('employee','vendor','other_name')", name="ck_sales_reps_name_type"),
    sa.UniqueConstraint("name_key", name="uq_sales_reps_name_key"),
    sa.UniqueConstraint("initials_key", name="uq_sales_reps_initials_key"),
    sa.UniqueConstraint("seed_key", name="uq_sales_reps_seed_key"),
    description="Versioned sales-representative aliases backed by an employee, vendor, or other-name id.",
)


ship_methods = _table(
    "ship_methods",
    *_list_common(),
    _column("name", sa.String(200), "Shipping-method display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded shipping-method key.", nullable=False),
    _column("display_order", sa.Integer, "Nonnegative default display order.", nullable=False, default=0),
    sa.CheckConstraint("display_order >= 0", name="ck_ship_methods_display_order"),
    sa.UniqueConstraint("name_key", name="uq_ship_methods_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_ship_methods_seed_key"),
    description="Versioned reusable shipping methods for company and customer defaults.",
)


customer_messages = _table(
    "customer_messages",
    *_list_common(),
    _column("name", sa.String(200), "Customer-message display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded customer-message key.", nullable=False),
    _column("text", sa.String(101), "Reusable customer-facing message text of at most 101 characters.", nullable=False),
    _column("display_order", sa.Integer, "Nonnegative default display order.", nullable=False, default=0),
    sa.CheckConstraint("length(text) BETWEEN 1 AND 101", name="ck_customer_messages_text"),
    sa.CheckConstraint("display_order >= 0", name="ck_customer_messages_display_order"),
    sa.UniqueConstraint("name_key", name="uq_customer_messages_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_customer_messages_seed_key"),
    description="Versioned reusable customer messages for later sales forms.",
)


items = _table(
    "items",
    *_list_common(),
    _column("name", sa.String(200), "Item leaf name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded item leaf-name key.", nullable=False),
    *_hierarchy("items"),
    _column("type", sa.String(24), "Discriminated standard item type.", nullable=False),
    _column("category_id", sa.String(26), "Independent active item-category id; null when unclassified.", sa.ForeignKey("item_categories.id", ondelete="RESTRICT"), nullable=True),
    _column("description", sa.Text, "Sales description; null when not recorded.", nullable=True),
    _column("purchase_description", sa.Text, "Purchase description; null when not recorded.", nullable=True),
    _column("sales_enabled", sa.Boolean, "Whether the sales profile is enabled.", nullable=False, default=False),
    _column("purchase_enabled", sa.Boolean, "Whether the purchase profile is enabled.", nullable=False, default=False),
    *_money("price", "Standard sales price"),
    *_money("cost", "Standard purchase cost"),
    _column("income_account_id", sa.String(26), "Active income account id; null when the type does not use one.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("expense_account_id", sa.String(26), "Active expense account id; null when the type does not use one.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("cogs_account_id", sa.String(26), "Active cost-of-goods-sold account id; null when the type does not use one.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("asset_account_id", sa.String(26), "Active inventory or fixed-asset account id; null when the type does not use one.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("deposit_account_id", sa.String(26), "Active deposit account id; null when the type does not use one.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("liability_account_id", sa.String(26), "Active liability account id; null when the type does not use one.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("default_class_id", sa.String(26), "Default active class id; null when unset.", sa.ForeignKey("classes.id", ondelete="RESTRICT"), nullable=True),
    _column("sales_tax_code_id", sa.String(26), "Default active sales-tax code id; null when unset.", sa.ForeignKey("sales_tax_codes.id", ondelete="RESTRICT"), nullable=True),
    _column("manufacturer_part_number", sa.String(128), "Manufacturer part number; null when not recorded.", nullable=True),
    _column("barcode", sa.String(128), "Barcode value; null when not recorded.", nullable=True),
    _column("unit_of_measure_set_id", sa.String(26), "Active unit-of-measure set id; null when units are disabled or unset.", sa.ForeignKey("units_of_measure.id", ondelete="RESTRICT"), nullable=True),
    _column("reorder_point_min_microunits", sa.BigInteger, "Minimum reorder point in quantity micro-units; null when unset.", nullable=True),
    _column("reorder_point_max_microunits", sa.BigInteger, "Maximum reorder point in quantity micro-units; null when unset.", nullable=True),
    _column("notes", sa.Text, "Item notes; null when not recorded.", nullable=True),
    _column("preferred_vendor_id", sa.String(26), "Stored preferred-vendor id synchronized with the active rank-one vendor profile.", sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=True),
    _column("print_members", sa.Boolean, "Whether group members print on later forms; null for other item types.", nullable=True),
    _column("other_charge_percent_millionths", sa.BigInteger, "Other-charge percentage in millionths of one percentage point; null for other types.", nullable=True),
    _column("assembly_build_point_microunits", sa.BigInteger, "Nonnegative inventory-assembly build point in quantity micro-units; null when unset.", nullable=True),
    *_money("discount_amount", "Fixed discount amount"),
    _column("discount_percent_millionths", sa.BigInteger, "Discount percent in millionths of one percentage point; null when not percentage-based.", nullable=True),
    _column("payment_method_id", sa.String(26), "Payment-method id for a payment item; null for other item types.", sa.ForeignKey("payment_methods.id", ondelete="RESTRICT"), nullable=True),
    _column("use_undeposited_funds", sa.Boolean, "Whether a payment item uses the system undeposited-funds account.", nullable=True),
    _column("tax_percent_millionths", sa.BigInteger, "Sales-tax percent in millionths of one percentage point; null for other item types.", nullable=True),
    _column("tax_agency_vendor_id", sa.String(26), "Active flagged tax-agency vendor id; null for other item types.", sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=True),
    _column("asset_number", sa.String(128), "Fixed-asset tracking number; null for other item types.", nullable=True),
    _column("purchase_date", sa.String(10), "ISO fixed-asset purchase date; null for other item types.", nullable=True),
    *_money("original_cost", "Fixed-asset original cost"),
    _column("vendor_id", sa.String(26), "Fixed-asset vendor id; null when unset.", sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=True),
    _column("location", sa.String(200), "Fixed-asset location; null for other item types.", nullable=True),
    _column("serial_number", sa.String(128), "Fixed-asset serial number; null when not recorded.", nullable=True),
    _column("warranty_expiration", sa.String(10), "ISO fixed-asset warranty-expiration date; null when unset.", nullable=True),
    _column("disposal_status", sa.String(10), "Fixed-asset status: in_service, sold, or disposed; null for other types.", nullable=True),
    _column("disposal_date", sa.String(10), "ISO fixed-asset disposal date; null while in service.", nullable=True),
    *_money("disposal_proceeds", "Fixed-asset disposal proceeds"),
    *_money("disposal_costs", "Fixed-asset disposal costs"),
    _column("accumulated_depreciation_account_id", sa.String(26), "Accumulated-depreciation account id; null when unset.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("depreciation_expense_account_id", sa.String(26), "Depreciation-expense account id; null when unset.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("gain_loss_account_id", sa.String(26), "Gain-or-loss account id; null when unset.", sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True),
    _column("depreciation_method", sa.String(24), "Fixed-asset depreciation method; null for other types.", nullable=True),
    _column("useful_life_months", sa.Integer, "Positive fixed-asset useful life in months; null when unset.", nullable=True),
    *_money("book_basis", "Fixed-asset book basis"),
    *_money("tax_basis", "Fixed-asset tax basis"),
    _money_pair_check("price"),
    _money_pair_check("cost"),
    _money_pair_check("discount_amount"),
    _money_pair_check("original_cost"),
    _money_pair_check("disposal_proceeds"),
    _money_pair_check("disposal_costs"),
    _money_pair_check("book_basis"),
    _money_pair_check("tax_basis"),
    sa.CheckConstraint(
        "type IN ('service','inventory_part','non_inventory_part','other_charge','subtotal','group','discount','payment','sales_tax_item','sales_tax_group','inventory_assembly','fixed_asset')",
        name="ck_items_type",
    ),
    sa.CheckConstraint("reorder_point_min_microunits IS NULL OR reorder_point_min_microunits >= 0", name="ck_items_reorder_min"),
    sa.CheckConstraint("reorder_point_max_microunits IS NULL OR reorder_point_max_microunits >= 0", name="ck_items_reorder_max"),
    sa.CheckConstraint("reorder_point_min_microunits IS NULL OR reorder_point_max_microunits IS NULL OR reorder_point_max_microunits >= reorder_point_min_microunits", name="ck_items_reorder_order"),
    sa.CheckConstraint("discount_amount_minor_units IS NULL OR discount_amount_minor_units >= 0", name="ck_items_discount_amount"),
    sa.CheckConstraint("other_charge_percent_millionths IS NULL OR other_charge_percent_millionths BETWEEN 0 AND 100000000", name="ck_items_other_charge_percent"),
    sa.CheckConstraint("assembly_build_point_microunits IS NULL OR assembly_build_point_microunits >= 0", name="ck_items_assembly_build_point"),
    sa.CheckConstraint("discount_percent_millionths IS NULL OR discount_percent_millionths BETWEEN 0 AND 100000000", name="ck_items_discount_percent"),
    sa.CheckConstraint("tax_percent_millionths IS NULL OR tax_percent_millionths BETWEEN 0 AND 100000000", name="ck_items_tax_percent"),
    sa.CheckConstraint("disposal_status IS NULL OR disposal_status IN ('in_service','sold','disposed')", name="ck_items_disposal_status"),
    sa.CheckConstraint("depreciation_method IS NULL OR depreciation_method IN ('none','straight_line','declining_balance','sum_of_years_digits','units_of_production','other')", name="ck_items_depreciation_method"),
    sa.CheckConstraint("useful_life_months IS NULL OR useful_life_months > 0", name="ck_items_useful_life"),
    sa.UniqueConstraint("seed_key", name="uq_items_seed_key"),
    description="Versioned discriminated master records for standard product, service, charge, tax, payment, assembly, and fixed-asset items.",
)


item_members = _table(
    "item_members",
    *_owned_child("owner_item_id", "items"),
    _column("component_item_id", sa.String(26), "Active component item id.", sa.ForeignKey("items.id", ondelete="RESTRICT"), nullable=False),
    _column("quantity_microunits", sa.BigInteger, "Component quantity in signed integer micro-units.", nullable=False),
    _column("unit_id", sa.String(26), "Active unit-conversion id from the component's set; null when unscaled.", sa.ForeignKey("unit_conversions.id", ondelete="RESTRICT"), nullable=True),
    sa.CheckConstraint("quantity_microunits >= 0", name="ck_item_members_quantity"),
    sa.Index("ux_item_members_component", "owner_item_id", "component_item_id", unique=True, sqlite_where=sa.text("active = 1")),
    description="Stable ordered component rows owned and versioned by a group or inventory assembly item.",
)


item_vendor_profiles = _table(
    "item_vendor_profiles",
    *_owned_child("item_id", "items"),
    _column("vendor_id", sa.String(26), "Active vendor id for this purchasing profile.", sa.ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False),
    _column("preferred_rank", sa.Integer, "Positive preference rank; rank one is the derived preferred vendor.", nullable=False),
    _column("vendor_item_name", sa.String(200), "Vendor-specific item name; null when not recorded.", nullable=True),
    *_money("purchase_cost", "Vendor purchase cost"),
    _column("minimum_quantity_microunits", sa.BigInteger, "Nonnegative vendor minimum quantity in micro-units; null when unset.", nullable=True),
    _column("lead_time_days", sa.Integer, "Nonnegative vendor lead time in days; null when unset.", nullable=True),
    _column("manufacturer_part_number", sa.String(128), "Vendor-specific manufacturer part number; null when unset.", nullable=True),
    _column("availability_notes", sa.Text, "Vendor availability notes; null when unset.", nullable=True),
    _money_pair_check("purchase_cost"),
    sa.CheckConstraint("preferred_rank > 0", name="ck_item_vendor_profiles_rank"),
    sa.CheckConstraint("minimum_quantity_microunits IS NULL OR minimum_quantity_microunits >= 0", name="ck_item_vendor_profiles_minimum"),
    sa.CheckConstraint("lead_time_days IS NULL OR lead_time_days >= 0", name="ck_item_vendor_profiles_lead_time"),
    sa.Index("ux_item_vendor_profiles_vendor", "item_id", "vendor_id", unique=True, sqlite_where=sa.text("active = 1")),
    sa.Index("ux_item_vendor_profiles_rank", "item_id", "preferred_rank", unique=True, sqlite_where=sa.text("active = 1")),
    description="Stable ordered item purchasing profiles linking items to preferred and alternate vendors.",
)


price_levels = _table(
    "price_levels",
    *_list_common(),
    _column("name", sa.String(200), "Price-level display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded price-level key.", nullable=False),
    _column("kind", sa.String(16), "Price-level kind: fixed_percent or per_item.", nullable=False),
    _column("currency", sa.String(3), "Optional ISO 4217 level currency; null selects company home currency.", nullable=True),
    _column("rounding_mode", sa.String(8), "Rounding mode: nearest, up, or down.", nullable=False, default="nearest"),
    *_money("rounding_increment", "Positive price-level rounding increment", nullable=False),
    *_money("rounding_offset", "Signed price-level rounding offset", nullable=False),
    _column("percent_millionths", sa.BigInteger, "Fixed adjustment percent in millionths of one percentage point; null for per-item levels.", nullable=True),
    _money_pair_check("rounding_increment", nullable=False),
    _money_pair_check("rounding_offset", nullable=False),
    sa.CheckConstraint("kind IN ('fixed_percent','per_item')", name="ck_price_levels_kind"),
    sa.CheckConstraint("rounding_mode IN ('nearest','up','down')", name="ck_price_levels_rounding_mode"),
    sa.CheckConstraint("rounding_increment_minor_units >= 1", name="ck_price_levels_rounding_increment"),
    sa.CheckConstraint("percent_millionths IS NULL OR percent_millionths BETWEEN -100000000 AND 1000000000000", name="ck_price_levels_percent"),
    sa.UniqueConstraint("name_key", name="uq_price_levels_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_price_levels_seed_key"),
    description="Versioned fixed-percent and per-item price levels with exact currency rounding.",
)


price_level_items = _table(
    "price_level_items",
    *_owned_child("price_level_id", "price_levels"),
    _column("item_id", sa.String(26), "Item receiving this per-item adjustment.", sa.ForeignKey("items.id", ondelete="RESTRICT"), nullable=False),
    *_money("price", "Per-item fixed price"),
    _column("percent_millionths", sa.BigInteger, "Per-item signed adjustment percent in millionths of one percentage point; null for fixed price.", nullable=True),
    _column("adjustment_basis", sa.String(20), "Adjustment basis: standard_price, cost, or current_custom_price.", nullable=False),
    _money_pair_check("price"),
    sa.CheckConstraint("(price_minor_units IS NOT NULL) != (percent_millionths IS NOT NULL)", name="ck_price_level_items_adjustment"),
    sa.CheckConstraint("adjustment_basis IN ('standard_price','cost','current_custom_price')", name="ck_price_level_items_basis"),
    sa.Index("ux_price_level_items_item", "price_level_id", "item_id", unique=True, sqlite_where=sa.text("active = 1")),
    description="Stable ordered per-item price adjustments owned and versioned by a price level.",
)


units_of_measure = _table(
    "units_of_measure",
    *_list_common(),
    _column("name", sa.String(200), "Unit-of-measure set display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded unit-set key.", nullable=False),
    _column("default_purchase_unit_id", sa.String(26), "Default active purchase-unit child id; null when unset.", sa.ForeignKey("unit_conversions.id", ondelete="RESTRICT"), nullable=True),
    _column("default_sales_unit_id", sa.String(26), "Default active sales-unit child id; null when unset.", sa.ForeignKey("unit_conversions.id", ondelete="RESTRICT"), nullable=True),
    _column("default_shipping_unit_id", sa.String(26), "Default active shipping-unit child id; null when unset.", sa.ForeignKey("unit_conversions.id", ondelete="RESTRICT"), nullable=True),
    sa.UniqueConstraint("name_key", name="uq_units_of_measure_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_units_of_measure_seed_key"),
    description="Versioned related-unit sets with explicit purchase, sales, and shipping defaults.",
)


unit_conversions = _table(
    "unit_conversions",
    *_owned_child("unit_of_measure_id", "units_of_measure"),
    _column("name", sa.String(200), "Unit display name unique among active children in this set.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded unit-name key.", nullable=False),
    _column("abbreviation", sa.String(32), "Unit abbreviation unique among active children in this set.", nullable=False),
    _column("abbreviation_key", sa.String(64), "NFC-normalized and case-folded unit-abbreviation key.", nullable=False),
    _column("is_base", sa.Boolean, "Whether this child is the set's single active base unit.", nullable=False, default=False),
    _column("base_factor_nanounits", sa.BigInteger, "Positive factor in nano-units, expressed as base units per one unit.", nullable=False),
    sa.CheckConstraint("base_factor_nanounits > 0", name="ck_unit_conversions_factor"),
    sa.CheckConstraint("is_base = 0 OR base_factor_nanounits = 1000000000", name="ck_unit_conversions_base_factor"),
    sa.Index("ux_unit_conversions_name", "unit_of_measure_id", "name_key", unique=True, sqlite_where=sa.text("active = 1")),
    sa.Index("ux_unit_conversions_abbreviation", "unit_of_measure_id", "abbreviation_key", unique=True, sqlite_where=sa.text("active = 1")),
    sa.Index("ux_unit_conversions_base", "unit_of_measure_id", unique=True, sqlite_where=sa.text("active = 1 AND is_base = 1")),
    description="Stable ordered conversion-unit children owned and versioned by a unit-of-measure set.",
)


custom_field_defs = _table(
    "custom_field_defs",
    *_list_common(),
    _column("name", sa.String(200), "Custom-field display name.", nullable=False),
    _column("name_key", sa.String(400), "NFC-normalized and case-folded custom-field name key.", nullable=False),
    _column("kind", sa.String(8), "Typed custom-field kind: text, number, date, bool, or choice.", nullable=False),
    _column("position", sa.Integer, "Nonnegative default form and column position.", nullable=False, default=0),
    _column("required", sa.Boolean, "Whether new applicable records require a populated value.", nullable=False, default=False),
    _column("default_canonical_text", sa.Text, "Canonical typed default representation; null when no default exists.", nullable=True),
    sa.CheckConstraint("kind IN ('text','number','date','bool','choice')", name="ck_custom_field_defs_kind"),
    sa.CheckConstraint("position >= 0", name="ck_custom_field_defs_position"),
    sa.UniqueConstraint("name_key", name="uq_custom_field_defs_name_key"),
    sa.UniqueConstraint("seed_key", name="uq_custom_field_defs_seed_key"),
    description="Versioned typed custom-field definitions projected into one or more record scopes.",
)


custom_field_scopes = _table(
    "custom_field_scopes",
    *_owned_child("definition_id", "custom_field_defs"),
    _column("record_type", sa.String(32), "Supported list or transaction record type for this definition.", nullable=False),
    _column("definition_name", sa.String(200), "Synchronized current definition display name.", nullable=False),
    _column("definition_name_key", sa.String(400), "Synchronized normalized current definition-name key.", nullable=False),
    _column("definition_active", sa.Boolean, "Synchronized current definition active state.", nullable=False),
    sa.CheckConstraint(
        "record_type IN ('customer','vendor','employee','other_name','item','journal_entry','invoice','sales_receipt','credit_memo','payment','deposit','bill','bill_payment','check','credit_card_charge','transfer','inventory_adjustment','vendor_credit','proposal','work_order','estimate','sales_order','purchase_order','item_receipt','statement')",
        name="ck_custom_field_scopes_record_type",
    ),
    sa.Index("ux_custom_field_scopes_record_name", "record_type", "definition_name_key", unique=True, sqlite_where=sa.text("active = 1 AND definition_active = 1")),
    sa.Index("ux_custom_field_scopes_definition_record", "definition_id", "record_type", unique=True, sqlite_where=sa.text("active = 1")),
    description="Stable ordered definition scopes with synchronized name and activity projections.",
)


custom_field_choices = _table(
    "custom_field_choices",
    *_owned_child("definition_id", "custom_field_defs"),
    _column("value", sa.String(200), "Choice label presented and returned for this definition.", nullable=False),
    _column("value_key", sa.String(400), "NFC-normalized and case-folded choice-label key.", nullable=False),
    sa.Index("ux_custom_field_choices_value", "definition_id", "value_key", unique=True, sqlite_where=sa.text("active = 1")),
    description="Stable ordered choice options owned and versioned by a choice custom-field definition.",
)


custom_field_values = _table(
    "custom_field_values",
    _column("id", sa.String(26), "Stable ULID reused when a cleared custom-field value is set again.", primary_key=True),
    _column("def_id", sa.String(26), "Custom-field definition id governing this value.", sa.ForeignKey("custom_field_defs.id", ondelete="RESTRICT"), nullable=False),
    _column("record_type", sa.String(32), "Stable owner record type for this value.", nullable=False),
    _column("record_id", sa.String(26), "Stable owner record id for this value.", nullable=False),
    _column("active", sa.Boolean, "Whether this value appears in the owner's current custom-field projection.", nullable=False, default=True),
    _column("canonical_text", sa.Text, "Canonical kind-dependent value representation preserved across clear and restore.", nullable=False),
    sa.UniqueConstraint("def_id", "record_type", "record_id", name="uq_custom_field_values_slot"),
    sa.Index("ix_custom_field_values_owner", "record_type", "record_id"),
    description="Stable typed custom-field value slots attached polymorphically to supported aggregate records.",
)


ROW5_TABLES = (
    accounts,
    customers,
    customer_addresses,
    customer_contacts,
    customer_contact_points,
    vendors,
    vendor_contacts,
    vendor_contact_points,
    vendor_expense_accounts,
    customer_vendor_links,
    employees,
    other_names,
    items,
    item_categories,
    item_members,
    item_vendor_profiles,
    classes,
    terms,
    payment_methods,
    sales_tax_codes,
    customer_types,
    vendor_types,
    job_types,
    sales_reps,
    ship_methods,
    customer_messages,
    price_levels,
    price_level_items,
    units_of_measure,
    unit_conversions,
    custom_field_defs,
    custom_field_scopes,
    custom_field_choices,
    custom_field_values,
)


for _table_with_foreign_keys in (*ROW5_TABLES, company_info):
    for _foreign_key in _table_with_foreign_keys.foreign_keys:
        _foreign_key.ondelete = "RESTRICT"
        _foreign_column = _foreign_key.parent.name
        _index_name = f"ix_{_table_with_foreign_keys.name}_{_foreign_column}"
        if _index_name not in {_index.name for _index in _table_with_foreign_keys.indexes}:
            sa.Index(_index_name, _foreign_key.parent)


notes = _table(
    "notes",
    *_common(),
    _column("record_type", sa.String(64), "Canonical company-local type of the annotated record.", nullable=False),
    _column("record_id", sa.String(26), "Stable id of the annotated record.", nullable=False),
    _column("body", sa.Text, "Preserved nonblank note text, limited to 65,536 UTF-8 bytes by commands.", nullable=False),
    _column("author_id", sa.String(26), "Original author's company-local principal id.", nullable=False),
    _column("interface", sa.String(16), "Interface through which the note was originally added.", nullable=False),
    _column("at", sa.String(32), "UTC timestamp when the note was originally added.", nullable=False),
    _column("edited_at", sa.String(32), "UTC timestamp of the latest body edit; null before any edit.", nullable=True),
    _column("kind", sa.String(16), "Note kind: comment or reserved system note.", nullable=False),
    sa.CheckConstraint("kind IN ('comment', 'system')", name="ck_notes_kind"),
    sa.Index("ix_notes_target_id", "record_type", "record_id", "id"),
    description="Versioned company-local notes attached to persistent records.",
)


attachments = _table(
    "attachments", *_common(),
    _column("sha256", sa.String(64), "Lowercase SHA-256 of the verified body.", nullable=False, unique=True),
    _column("size_bytes", sa.BigInteger, "Verified body size in bytes.", nullable=False),
    _column("media_type", sa.String(127), "Original validated media type.", nullable=False),
    _column("original_filename", sa.String(255), "Original basename, at most 255 UTF-8 bytes.", nullable=False),
    _column("uploaded_by", sa.String(26), "Original uploading principal.", nullable=False),
    _column("uploaded_at", sa.String(32), "Original upload UTC timestamp.", nullable=False),
    _column("collected_at", sa.String(32), "Collection UTC timestamp; null while available.", nullable=True),
    sa.CheckConstraint("size_bytes >= 0", name="ck_attachments_size"),
    sa.CheckConstraint("length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'", name="ck_attachments_sha256"),
    sa.CheckConstraint("length(CAST(original_filename AS BLOB)) BETWEEN 1 AND 255", name="ck_attachments_filename"),
    description="Content-addressed attachment metadata retaining original provenance after collection.",
)
attachment_links = _table(
    "attachment_links", *_common(),
    _column("attachment_id", sa.String(26), "Linked attachment identity.", sa.ForeignKey("attachments.id", ondelete="RESTRICT"), nullable=False),
    _column("record_type", sa.String(64), "Canonical annotation target type.", nullable=False),
    _column("record_id", sa.String(26), "Stable annotation target id.", nullable=False),
    _column("linked_by", sa.String(26), "Principal creating this link occurrence.", nullable=False),
    _column("linked_at", sa.String(32), "Original link UTC timestamp.", nullable=False),
    _column("caption", sa.Text, "Association caption, at most 2048 UTF-8 bytes.", nullable=False),
    _column("active", sa.Boolean, "Whether this occurrence remains linked.", nullable=False),
    sa.CheckConstraint("length(CAST(caption AS BLOB)) <= 2048", name="ck_attachment_links_caption"),
    sa.Index("ix_attachment_links_attachment_id", "attachment_id"),
    sa.Index("ix_attachment_links_target_id", "record_type", "record_id", "id"),
    sa.Index("uq_attachment_links_active", "attachment_id", "record_type", "record_id", unique=True, sqlite_where=sa.text("active = 1")),
    description="Versioned soft-unlinked attachment association occurrences.",
)
attachment_collection = _table(
    "attachment_collection",
    _column("id", sa.String(26), "Collection operation ULID.", primary_key=True),
    _column("payload", sa.Text, "Operational JSON containing bounded candidates and original invocation context.", nullable=False),
    sa.CheckConstraint("length(CAST(payload AS BLOB)) <= 262144 AND json_valid(payload)", name="ck_attachment_collection_payload"),
    description="Internal durable attachment collection intents, bounded to 262144 UTF-8 bytes.",
)

# Shared company metadata includes the journal history tables.
from bookflow.company.ledger_schema import define_tables as _define_ledger_tables

globals().update(_define_ledger_tables(metadata, _column, _table, _common))

from bookflow.company.rate_schema import define_tables as _define_rate_tables

globals().update(_define_rate_tables(metadata, _column, _table))

from bookflow.company.sales_schema import define_tables as _define_sales_tables

globals().update(_define_sales_tables(metadata, _column, _table))

from bookflow.company.work_schema import define_tables as _define_work_tables

globals().update(_define_work_tables(metadata, _column, _table, _common))

from bookflow.company.billing_schema import define_tables as _define_billing_tables

globals().update(_define_billing_tables(metadata, _column, _table))

from bookflow.company.payment_schema import define_tables as _define_payment_tables

globals().update(_define_payment_tables(metadata, _column, _table, _common))

from bookflow.company.tax_schema import define_tables as _define_tax_tables
globals().update(_define_tax_tables(metadata, _column, _table))

from bookflow.company.read_indexes import define_indexes as _define_read_indexes
_define_read_indexes(metadata)

from bookflow.company.payment_recovery_schema import define_tables as _define_recovery_tables
globals().update(_define_recovery_tables(metadata, _column, _table, _common))

from bookflow.company.deposit_schema import define_tables as _define_deposit_tables
globals().update(_define_deposit_tables(metadata, _column, _table, _common))

from bookflow.company.reconciliation_schema import define_tables as _define_reconciliation_tables
globals().update(_define_reconciliation_tables(metadata, _column, _table, _common))

from bookflow.company.deposit_draft_schema import define_tables as _define_deposit_draft_tables
globals().update(_define_deposit_draft_tables(metadata, _column, _table, _common))
