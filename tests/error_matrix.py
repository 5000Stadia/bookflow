"""Per-command error codes and what produces each (blueprint 5.4). Walked by test_error_matrix."""

MATRIX = {
    "init": {"E_INIT_CONFLICT": "init again with a different --username", "E_CONFIG_INVALID": "malformed config.toml"},
    "upgrade": {"E_COMPANY_MISSING": "registered folder absent (reported per company)"},
    "organization new": {"E_NAME_TAKEN": "existing name", "E_PERMISSION": "non hub admin", "E_VALIDATION": "empty or slash name", "E_IDEMPOTENCY_MISMATCH": "same key, different input"},
    "organization show": {"E_ORGANIZATION_NOT_FOUND": "absent or invisible organization"},
    "organization rename": {"E_NAME_TAKEN": "existing name", "E_RENAME_INCOMPLETE": "move failed", "E_PERMISSION": "non hub admin"},
    "company new": {"E_ORGANIZATION_REQUIRED": "several visible organizations", "E_NAME_TAKEN": "existing name", "E_ROLLOUT_INCOMPLETE": "registration failed after folder creation", "E_IDEMPOTENCY_MISMATCH": "same key, different input",
                    "E_ORGANIZATION_NOT_FOUND": "absent or invisible organization", "E_PERMISSION": "standard member", "E_VALIDATION": "bad currency, email, tax id, month, timezone, or unknown field"},
    "chart list": {},
    "chart show": {"E_RECORD_NOT_FOUND": "unknown packaged chart id"},
    "chart apply": {"E_RECORD_NOT_FOUND": "unknown packaged chart id", "E_CHART_INVALID": "manifest conflicts with existing accounts", "E_CHART_EXISTS": "company already has a chart", "E_IDEMPOTENCY_MISMATCH": "same key, different input", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "profile list": {},
    "profile show": {"E_RECORD_NOT_FOUND": "unknown packaged profile id"},
    "profile apply": {"E_RECORD_NOT_FOUND": "unknown packaged profile id", "E_IDEMPOTENCY_MISMATCH": "same key, different input", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "company use": {"E_COMPANY_NOT_FOUND": "absent or invisible company", "E_VALIDATION": "missing positional"},
    "company attach": {"E_NOT_IN_ORGANIZATION_DIR": "path outside organizations", "E_INCOMPLETE_COMPANY": "marker state creating", "E_ALREADY_ATTACHED": "id registered",
                       "E_NAME_TAKEN": "name in use", "E_ATTACH_INVALID": "marker or database missing or mismatched, wide mode", "E_SCHEMA_UNKNOWN": "unknown revision", "E_PERMISSION": "non hub admin"},
    "company detach": {"E_COMPANY_NOT_FOUND": "absent company", "E_PERMISSION": "non hub admin"},
    "company show": {"E_COMPANY_NOT_FOUND": "absent, invisible, or unselected company", "E_COMPANY_MISSING": "folder or database absent", "E_SCHEMA_BEHIND": "behind head on read", "E_IO": "corrupt database"},
    "company rename": {"E_NAME_TAKEN": "existing name", "E_RENAME_INCOMPLETE": "move failed", "E_COMPANY_MISSING": "folder absent", "E_PERMISSION": "readonly or standard member", "E_IO": "corrupt database", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "demo reset": {"E_DEMO_RESET_INCOMPLETE": "trash move failed", "E_NAME_TAKEN": "non-demo organization holds the seed name", "E_PERMISSION": "non hub admin"},
    "hub audit list": {"E_VALIDATION": "bad since/until"},
    "hub audit show": {"E_EVENT_NOT_FOUND": "absent or invisible event"},
    "hub audit tail": {"E_VALIDATION": "bad since/until"},
    "audit list": {"E_VALIDATION": "bad since/until"},
    "audit show": {"E_EVENT_NOT_FOUND": "absent event"},
    "audit tail": {"E_VALIDATION": "bad since/until"},
    "company update": {"E_VERSION_CONFLICT": "stale expected_version with overlapping fields", "E_PARTIAL_WRITE": "hub projection failed after the company commit", "E_PERMISSION": "standard member", "E_VALIDATION": "bad merged row, unknown or non-nullable --clear", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "directive add": {"E_PERMISSION": "agent without a principal; readonly member", "E_IDEMPOTENCY_MISMATCH": "same key, different input", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "directive list": {},
    "directive show": {"E_DIRECTIVE_NOT_FOUND": "unknown id or code"},
    "directive deactivate": {"E_DIRECTIVE_NOT_FOUND": "unknown id or code", "E_DIRECTIVE_INACTIVE": "deactivated --directive", "E_PERMISSION": "readonly member"},
    "presence set": {"E_RECORD_NOT_FOUND": "unknown record id", "E_VALIDATION": "unknown record type"},
    "presence clear": {"E_VALIDATION": "unknown record type", "E_RECORD_NOT_FOUND": "unknown record id"},
    "serve": {"E_NETWORK_NOT_ALLOWED": "--bind outside loopback without --allow-network", "E_VERSION_MISMATCH": "a forwarded call from another Bookflow version",
              "E_COMPANY_MISSING": "registered folder absent (reported per company, never fatal)", "E_DB_BUSY": "another command or host holds the data-root lock",
              "E_NOT_INITIALIZED": "no hub database", "E_NO_ACTOR": "the OS login maps to no user", "E_PERMISSION": "the OS login is not a hub admin",
              "E_VALIDATION": "--bind is not host:port"},
    "user set-password": {"E_USER_NOT_FOUND": "no such username", "E_PERMISSION": "non hub admin", "E_VALIDATION": "no password given, or a non-human user"},
    "token issue": {"E_USER_NOT_FOUND": "--user names nobody (hub admins only)", "E_PERMISSION": "a non-admin naming another user", "E_VALIDATION": "--principal on a non-agent user, or a non-human principal"},
    "token list": {"E_USER_NOT_FOUND": "--user names nobody (hub admins only)", "E_PERMISSION": "a non-admin naming another user"},
    "token revoke": {"E_TOKEN_NOT_FOUND": "unknown token id", "E_PERMISSION": "another user's token, as a non-admin"},
}

_SUPPORTING_LIST_NOUNS = (
    "account", "customer", "vendor", "employee", "other-name", "item",
    "custom-field", "item-category", "class", "term", "payment-method", "price-level",
    "sales-tax-code", "unit-of-measure",
    "customer-type", "vendor-type", "job-type", "sales-rep", "ship-method",
    "customer-message",
)
_LIFECYCLE_ERRORS = {
    "create": {
        "E_NAME_TAKEN", "E_RECORD_NOT_FOUND", "E_INACTIVE_REFERENCE", "E_HIERARCHY_DEPTH",
        "E_AMOUNT_PRECISION", "E_VALUE_RANGE",
        "E_IDEMPOTENCY_MISMATCH", "E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE",
    },
    "update": {
        "E_RECORD_NOT_FOUND", "E_NAME_TAKEN", "E_VERSION_CONFLICT", "E_INACTIVE_REFERENCE",
        "E_HIERARCHY_CYCLE", "E_HIERARCHY_DEPTH", "E_TYPE_CHANGE", "E_RECORD_IN_USE",
        "E_SYSTEM_RECORD", "E_AMOUNT_PRECISION", "E_VALUE_RANGE",
        "E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE",
    },
    "show": {"E_RECORD_NOT_FOUND"},
    "list": {"E_LIST_FILTER"},
    "query": {"E_LIST_FILTER", "E_QUERY_STALE"},
    "activate": {
        "E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_INACTIVE_REFERENCE", "E_NAME_TAKEN",
        "E_SYSTEM_RECORD",
        "E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE",
    },
    "deactivate": {
        "E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_ACTIVE_DEPENDENTS", "E_RECORD_IN_USE",
        "E_SYSTEM_RECORD", "E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE",
    },
}
for _noun in _SUPPORTING_LIST_NOUNS:
    for _verb, _codes in _LIFECYCLE_ERRORS.items():
        MATRIX[f"{_noun} {_verb}"] = {code: "shared Row 5 lifecycle rule" for code in _codes}
    MATRIX[f"{_noun} query"]["E_QUERY_STALE"] = "company audit changed after the first page; restart without a cursor"

MATRIX.update({
    "customer link-vendor": {
        "E_RECORD_NOT_FOUND": "customer, vendor, or prior link is absent",
        "E_INACTIVE_REFERENCE": "customer or vendor is inactive",
        "E_RECORD_IN_USE": "either endpoint already has another active link",
        "E_VERSION_CONFLICT": "an endpoint or prior link version is stale",
        "E_IDEMPOTENCY_MISMATCH": "same key, different input",
        "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
        "E_DIRECTIVE_INACTIVE": "deactivated --directive",
    },
    "customer unlink-vendor": {
        "E_RECORD_NOT_FOUND": "customer or active link is absent",
        "E_VERSION_CONFLICT": "the customer, vendor, or link version is stale",
        "E_IDEMPOTENCY_MISMATCH": "same key, different input",
        "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
        "E_DIRECTIVE_INACTIVE": "deactivated --directive",
    },
    "other-name convert": {
        "E_RECORD_NOT_FOUND": "source or mapped reference is absent",
        "E_RECORD_IN_USE": "source was already converted",
        "E_NAME_TAKEN": "the target list already has that name",
        "E_INACTIVE_REFERENCE": "a mapped reference is inactive",
        "E_VERSION_CONFLICT": "the source version is stale",
        "E_IDEMPOTENCY_MISMATCH": "same key, different input",
        "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
        "E_DIRECTIVE_INACTIVE": "deactivated --directive",
    },
    "undo": {
        "E_EVENT_NOT_FOUND": "the company audit event is absent",
        "E_NOT_UNDOABLE": "the event or one of its entries is not eligible",
        "E_ALREADY_UNDONE": "another undo already compensates the event",
        "E_UNDO_CONFLICT": "current data or dependencies reject the inverse",
        "E_IDEMPOTENCY_MISMATCH": "same key, different input",
        "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
        "E_DIRECTIVE_INACTIVE": "deactivated --directive",
    },
})

# Item projections validate graph state during reads/activation in addition to
# the shared lifecycle rules.
MATRIX["item create"]["E_HIERARCHY_CYCLE"] = "submitted member graph is cyclic"
MATRIX["item list"]["E_RECORD_NOT_FOUND"] = "a referenced item disappeared during projection"
MATRIX["item query"]["E_RECORD_NOT_FOUND"] = "a referenced item is absent during projection"
MATRIX["item activate"]["E_HIERARCHY_CYCLE"] = "stored hierarchy or member graph is cyclic"
MATRIX["other-name activate"]["E_RECORD_IN_USE"] = "a converted source cannot be reactivated directly"

# Standalone local tooling has no actor, database role, or membership capability,
# so it is kept out of the database command matrix and its frozen capability seed.
STANDALONE_MATRIX = {
    "docs generate": {"E_DOCS_STALE": "--check found missing, extra, or changed generated documentation"},
}

INFRASTRUCTURE = ["E_USAGE", "E_VALIDATION", "E_CONTEXT_IN_INPUT", "E_NOT_INITIALIZED", "E_NO_ACTOR", "E_PERMISSION", "E_COMPANY_NOT_FOUND",
                  "E_COMPANY_AMBIGUOUS", "E_ORGANIZATION_NOT_FOUND", "E_DB_BUSY", "E_NETWORK_SHARE", "E_FS_UNKNOWN", "E_SCHEMA_UNKNOWN",
                  "E_SCHEMA_BEHIND", "E_MIGRATION_FAILED", "E_CONFIG_INVALID", "E_IO", "E_PARTIAL_WRITE", "E_REASON_REQUIRED", "E_FEATURE_DISABLED", "E_UNAUTHENTICATED", "E_INTERNAL"]
