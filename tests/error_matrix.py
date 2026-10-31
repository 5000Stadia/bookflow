"""Per-command error codes and what produces each (blueprint 5.4). Walked by test_error_matrix."""

MATRIX = {
    "owner-link": {"E_VALIDATION": "the owner already has a password", "E_NOT_INITIALIZED": "no books at the data root", "E_NETWORK_NOT_ALLOWED": "a bind address outside loopback"},
    "setup": {"E_INIT_CONFLICT": "a different --owner than the data root's", "E_VALIDATION": "a company kept in another currency, or a .mcp.json that is not JSON", "E_PERMISSION": "a login that may not administer the root", "E_IO": "the background host stops while starting", "E_NETWORK_NOT_ALLOWED": "a bind address outside loopback"},
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
    "company backup": {"E_IO": "backups folder unwritable or disk full", "E_BACKUP_INVALID": "the written archive fails its read-back check",
                       "E_PERMISSION": "standard member or readonly", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "company restore": {"E_BACKUP_INVALID": "missing file, not a zip, bad manifest, member hash or size mismatch, damaged database", "E_SCHEMA_UNKNOWN": "backup from a newer Bookflow",
                        "E_ALREADY_ATTACHED": "backed-up company registered here without --as-copy", "E_NAME_TAKEN": "name in use in the organization",
                        "E_ORGANIZATION_REQUIRED": "several visible organizations", "E_ORGANIZATION_NOT_FOUND": "absent organization", "E_VALIDATION": "relative path or slash in name",
                        "E_MIGRATION_FAILED": "older backup whose migration fails", "E_IO": "organization folder unwritable", "E_PERMISSION": "non hub admin"},
    "company show": {"E_COMPANY_NOT_FOUND": "absent, invisible, or unselected company", "E_COMPANY_MISSING": "folder or database absent", "E_SCHEMA_BEHIND": "behind head on read", "E_IO": "corrupt database"},
    "company rename": {"E_NAME_TAKEN": "existing name", "E_RENAME_INCOMPLETE": "move failed", "E_COMPANY_MISSING": "folder absent", "E_PERMISSION": "readonly or standard member", "E_IO": "corrupt database", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "demo reset": {"E_DEMO_RESET_INCOMPLETE": "trash move failed", "E_NAME_TAKEN": "non-demo organization holds the seed name", "E_PERMISSION": "non hub admin"},
    "hub audit list": {"E_VALIDATION": "bad since/until"},
    "hub audit show": {"E_EVENT_NOT_FOUND": "absent or invisible event"},
    "hub audit tail": {"E_VALIDATION": "bad since/until"},
    "audit list": {"E_VALIDATION": "bad since/until"},
    "audit show": {"E_EVENT_NOT_FOUND": "absent event"},
    "audit tail": {"E_VALIDATION": "bad since/until"},
    "company update": {"E_VERSION_CONFLICT": "stale expected_version with overlapping fields", "E_PARTIAL_WRITE": "hub projection failed after the company commit", "E_PERMISSION": "standard member", "E_VALIDATION": "bad merged row, unknown or non-nullable --clear", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive", "E_WORK_DEPENDENCY": "turning picking off while a pick list is open"},
    "directive add": {"E_PERMISSION": "agent without a principal; readonly member", "E_IDEMPOTENCY_MISMATCH": "same key, different input", "E_DIRECTIVE_NOT_FOUND": "unknown --directive", "E_DIRECTIVE_INACTIVE": "deactivated --directive"},
    "directive list": {},
    "activity": {"E_RECORD_NOT_FOUND": "target absent from selected company", "E_VALIDATION": "invalid date, kinds, or scoped cursor"},
    "attachment add": {"E_RECORD_NOT_FOUND": "target absent from selected company", "E_IO": "interrupted input or failed publication", "E_VALUE_RANGE": "actual bytes exceed company limit", "E_DB_BUSY": "capacity or collection recovery pending", "E_IDEMPOTENCY_MISMATCH": "same key with different metadata or actual bytes", "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive"},
    "attachment link": {"E_RECORD_NOT_FOUND": "attachment or target absent", "E_IO": "collected or corrupt body", "E_DB_BUSY": "collection requires recovery", "E_IDEMPOTENCY_MISMATCH": "same key with different association", "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive"},
    "attachment unlink": {"E_RECORD_NOT_FOUND": "link absent", "E_VERSION_CONFLICT": "stale link version", "E_DB_BUSY": "collection requires recovery", "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive"},
    "attachment list": {"E_RECORD_NOT_FOUND": "target absent from selected company"},
    "attachment get": {"E_RECORD_NOT_FOUND": "attachment absent", "E_VALUE_RANGE": "invalid company byte limit", "E_IO": "missing, collected, corrupt or unsafe body", "E_DB_BUSY": "collection pending"},
    "company compact": {"E_IO": "unsafe entry or incomplete durable collection", "E_DB_BUSY": "active transfer or pending dry-run recovery", "E_VALIDATION": "limit outside 1 through 200", "E_IDEMPOTENCY_MISMATCH": "same key with a different collection limit", "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive"},
    "note add": {"E_RECORD_NOT_FOUND": "target absent from selected company", "E_IDEMPOTENCY_MISMATCH": "same key, different input", "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive"},
    "note show": {"E_RECORD_NOT_FOUND": "note absent from selected company"},
    "note list": {"E_RECORD_NOT_FOUND": "target absent from selected company"},
    "note edit": {"E_RECORD_NOT_FOUND": "note absent from selected company", "E_VERSION_CONFLICT": "stale expected version", "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive"},
    "directive show": {"E_DIRECTIVE_NOT_FOUND": "unknown id or code"},
    "directive deactivate": {"E_DIRECTIVE_NOT_FOUND": "unknown id or code", "E_DIRECTIVE_INACTIVE": "deactivated --directive", "E_PERMISSION": "readonly member"},
    "presence set": {"E_RECORD_NOT_FOUND": "unknown record id", "E_VALIDATION": "unknown record type"},
    "presence clear": {"E_VALIDATION": "unknown record type", "E_RECORD_NOT_FOUND": "unknown record id"},
    "serve": {"E_NETWORK_NOT_ALLOWED": "--bind outside loopback without --allow-network", "E_VERSION_MISMATCH": "a forwarded call from another Bookflow version",
              "E_COMPANY_MISSING": "registered folder absent (reported per company, never fatal)", "E_DB_BUSY": "another command or host holds the data-root lock",
              "E_NOT_INITIALIZED": "no hub database", "E_NO_ACTOR": "the OS login maps to no user", "E_PERMISSION": "the OS login is not a hub admin",
              "E_VALIDATION": "--bind is not host:port"},
    "user set-password": {"E_USER_NOT_FOUND": "no such username", "E_PERMISSION": "non hub admin", "E_VALIDATION": "no password given, or a non-human user"},
    "user add": {"E_PERMISSION": "not a human hub administrator, or not an administrator of the scope being granted",
                 "E_VALIDATION": "username already in use or reserved, or a role with no scope to apply to",
                 "E_COMPANY_NOT_FOUND": "--company names nothing this user can see", "E_COMPANY_AMBIGUOUS": "--company names two companies",
                 "E_ORGANIZATION_NOT_FOUND": "--organization names nothing this user can see"},
    "membership grant": {"E_USER_NOT_FOUND": "no such username", "E_PERMISSION": "not a human administrator of that scope; owner is needed to grant or move an owner",
                         "E_VALIDATION": "neither or both of --company and --organization, or the system user",
                         "E_COMPANY_NOT_FOUND": "--company names nothing this user can see", "E_COMPANY_AMBIGUOUS": "--company names two companies",
                         "E_ORGANIZATION_NOT_FOUND": "--organization names nothing this user can see"},
    "membership revoke": {"E_USER_NOT_FOUND": "no such username", "E_RECORD_NOT_FOUND": "that user has no membership at that scope",
                          "E_PERMISSION": "not a human administrator of that scope; owner is needed to revoke an owner",
                          "E_VALIDATION": "neither or both of --company and --organization, or the system user",
                          "E_COMPANY_NOT_FOUND": "--company names nothing this user can see", "E_COMPANY_AMBIGUOUS": "--company names two companies",
                          "E_ORGANIZATION_NOT_FOUND": "--organization names nothing this user can see"},
    "user list": {"E_VALIDATION": "both --company and --organization named at once",
                  "E_PERMISSION": "the publication re-check finds the listed scope no longer administered",
                  "E_COMPANY_NOT_FOUND": "--company names nothing this user can see", "E_COMPANY_AMBIGUOUS": "--company names two companies",
                  "E_ORGANIZATION_NOT_FOUND": "--organization names nothing this user can see"},
    "membership list": {"E_USER_NOT_FOUND": "--user names nobody this caller can already see",
                        "E_VALIDATION": "both --company and --organization named at once",
                        "E_PERMISSION": "the publication re-check finds the listed scope no longer administered",
                        "E_COMPANY_NOT_FOUND": "--company names nothing this user can see", "E_COMPANY_AMBIGUOUS": "--company names two companies",
                        "E_ORGANIZATION_NOT_FOUND": "--organization names nothing this user can see"},
    "token issue": {"E_USER_NOT_FOUND": "--user names nobody (hub admins only)", "E_PERMISSION": "a non-admin naming another user", "E_VALIDATION": "--principal on a non-agent user, or a non-human principal"},
    "token list": {"E_USER_NOT_FOUND": "--user names nobody (hub admins only)", "E_PERMISSION": "a non-admin naming another user"},
    "agent create": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                     "E_VALIDATION": "username already in use or reserved, or a non-human owner", "E_USER_NOT_FOUND": "--owner names nobody"},
    "agent show": {"E_PERMISSION": "not a human hub administrator", "E_USER_NOT_FOUND": "no agent by that name or id"},
    "agent list": {"E_PERMISSION": "not a human hub administrator", "E_USER_NOT_FOUND": "--principal names nobody",
                   "E_VALIDATION": "--principal names a non-human user"},
    "agent assign": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                     "E_USER_NOT_FOUND": "no such agent or principal", "E_VALIDATION": "no --confirm-permitted-use, or a non-human principal",
                     "E_VERSION_CONFLICT": "stale --expected-version", "E_AGENT_PRINCIPAL_MISMATCH": "the principals would not hold identical permissions"},
    "agent unassign": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                       "E_USER_NOT_FOUND": "no such agent or principal", "E_RECORD_NOT_FOUND": "that person is not assigned to the agent",
                       "E_VALIDATION": "a non-human principal", "E_VERSION_CONFLICT": "stale --expected-version"},
    "agent authorize": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                        "E_USER_NOT_FOUND": "no such agent", "E_VALIDATION": "no --confirm-permitted-use, no principals, or a required --acknowledge-fresh-context missing",
                        "E_VERSION_CONFLICT": "stale --expected-version", "E_AGENT_PRINCIPAL_MISMATCH": "the assigned principals do not hold identical permissions"},
    "agent deactivate": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                         "E_USER_NOT_FOUND": "no such agent", "E_VALIDATION": "malformed input", "E_VERSION_CONFLICT": "stale --expected-version"},
    "agent activate": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                       "E_USER_NOT_FOUND": "no such agent", "E_VALIDATION": "malformed input", "E_VERSION_CONFLICT": "stale --expected-version"},
    "user deactivate": {"E_PERMISSION": "not a human hub administrator, a never-activated installation, or the last active installation administrator",
                        "E_USER_NOT_FOUND": "no such person", "E_VALIDATION": "an agent (use agent deactivate), or yourself",
                        "E_VERSION_CONFLICT": "stale --expected-version"},
    "opening-draft create": {"E_DIRECTIVE_INACTIVE": "the cited directive is no longer active", "E_DIRECTIVE_NOT_FOUND": "no such directive", "E_RECORD_NOT_FOUND": "no such opening draft"},
    "opening-draft update": {"E_DIRECTIVE_INACTIVE": "the cited directive is no longer active", "E_DIRECTIVE_NOT_FOUND": "no such directive", "E_RECORD_NOT_FOUND": "no such opening draft", "E_VERSION_CONFLICT": "stale --expected-version"},
    "opening-draft discard": {"E_DIRECTIVE_INACTIVE": "the cited directive is no longer active", "E_DIRECTIVE_NOT_FOUND": "no such directive", "E_RECORD_NOT_FOUND": "no such opening draft"},
    "opening-draft plan": {"E_DIRECTIVE_INACTIVE": "the cited directive is no longer active", "E_DIRECTIVE_NOT_FOUND": "no such directive", "E_RECORD_NOT_FOUND": "no such opening draft"},
    "opening-draft apply": {"E_CUTOVER_BLOCKED": "the plan has blockers, or the opening does not tie without accept_differences", "E_CUTOVER_INCOMPLETE": "a step was refused part way; what this call posted was rolled back", "E_DIRECTIVE_INACTIVE": "the cited directive is no longer active", "E_DIRECTIVE_NOT_FOUND": "no such directive", "E_RECORD_NOT_FOUND": "no such opening draft", "E_VERSION_CONFLICT": "stale --expected-version"},
    "opening-draft reverse": {"E_BATCH_BLOCKED": "something later depends on what the opening posted", "E_DIRECTIVE_INACTIVE": "the cited directive is no longer active", "E_DIRECTIVE_NOT_FOUND": "no such directive", "E_RECORD_NOT_FOUND": "no such opening draft", "E_VERSION_CONFLICT": "stale --expected-version"},
    "opening-draft show": {"E_RECORD_NOT_FOUND": "no such opening draft"},
    "opening-draft tie-out": {"E_QUERY_STALE": "the draft changed since the tie-out was read", "E_RECORD_NOT_FOUND": "no such opening draft"},
    "user home set": {"E_PERMISSION": "another person's Home without user.manage, or a Home for an agent",
                      "E_USER_NOT_FOUND": "no such person", "E_VALIDATION": "not a workbench page, or one the person may not open",
                      "E_VERSION_CONFLICT": "stale --expected-version", "E_COMPANY_NOT_FOUND": "no such company",
                      "E_COMPANY_AMBIGUOUS": "the company name matches more than one"},
    "user home show": {"E_PERMISSION": "another person's Home without user.manage", "E_USER_NOT_FOUND": "no such person",
                       "E_VALIDATION": "an unreadable --user"},
    "user home clear": {"E_PERMISSION": "another person's Home without user.manage", "E_USER_NOT_FOUND": "no such person",
                        "E_VALIDATION": "an agent's Home", "E_VERSION_CONFLICT": "stale --expected-version"},
    "user activate": {"E_PERMISSION": "not a human hub administrator, or a never-activated installation",
                      "E_USER_NOT_FOUND": "no such person", "E_VALIDATION": "an agent (use agent activate)",
                      "E_VERSION_CONFLICT": "stale --expected-version"},
    "report export": {"E_VALIDATION": "not an exportable report, or the report's own filters are invalid (named filters.<field>)",
                      "E_QUERY_STALE": "the books changed on every attempt to read the report through"},
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
# R117: a merged-away account or item changes only by undoing its merge.
for _merged in ("account activate", "item activate", "item update"):
    MATRIX[_merged]["E_RECORD_IN_USE"] = "the entry is merged into another; undo the merge first"

# Standalone local tooling has no actor, database role, or membership capability,
# so it is kept out of the database command matrix and its frozen capability seed.
STANDALONE_MATRIX = {
    "docs generate": {"E_DOCS_STALE": "--check found missing, extra, or changed generated documentation"},
}

# Bank reconciliation's own reasons, described once. The `reconcile ...` rows at the end of this
# file copy these rather than restating them; the set itself is read from
# `core.errors.RECONCILIATION_CODES`, never retyped.
RECONCILIATION_MATRIX = {
    "E_RECONCILIATION_ATTEMPT_STATE": "a second attempt on one draft, a chunk out of order, or seal/apply on an attempt that is not uploading/sealed",
    "E_RECONCILIATION_CHAIN_STALE": "a draft whose base chain version, opening or head certificate is no longer the account's",
    "E_RECONCILIATION_DATE": "a statement date not after the previous one, a marked movement dated after the cutoff, or a proposal dated past the statement",
    "E_RECONCILIATION_DEPENDENCY": "an opening draft on an account that already has one, or an amendment whose head certificate moved",
    "E_RECONCILIATION_DIFFERENCE": "finishing a statement whose cleared balance does not equal the entered ending balance",
    "E_RECONCILIATION_DRAFT_STATE": "a draft that is consumed or canceled, or the wrong kind for the step",
    "E_RECONCILIATION_MANIFEST": "a selection that splits a movement group, repeats a key, mixes actions, or does not match the proposals it declares",
    "E_RECONCILIATION_MEMBERSHIP_CONFLICT": "marking a movement already covered by the opening or held by another certificate",
    "E_RECONCILIATION_OPENING_UNPROVEN": "an opening that leaves an eligible movement unclassified, or whose covered sum misses the entered balance",
    "E_RECONCILIATION_OPERATION_KEY_REUSED": "a permanent operation or chunk key replayed with different content",
    "E_RECONCILIATION_SELECTION_STALE": "a selected version that is no longer the movement's current one, or a candidate page whose fingerprint moved",
    "E_RECONCILIATION_SOURCE_INVALID": "stored effects that do not sum to the general ledger for the account, or source rows the adapters cannot read",
    "E_RECONCILIATION_UNSUPPORTED": "a foreign-currency or non-bank account, or a document the statement adapters cannot represent",
}

INFRASTRUCTURE = ["E_USAGE", "E_VALIDATION", "E_CONTEXT_IN_INPUT", "E_NOT_INITIALIZED", "E_NO_ACTOR", "E_PERMISSION", "E_COMPANY_NOT_FOUND",
                  "E_COMPANY_AMBIGUOUS", "E_ORGANIZATION_NOT_FOUND", "E_DB_BUSY", "E_NETWORK_SHARE", "E_FS_UNKNOWN", "E_SCHEMA_UNKNOWN",
                  "E_SCHEMA_BEHIND", "E_MIGRATION_FAILED", "E_CONFIG_INVALID", "E_IO", "E_PARTIAL_WRITE", "E_REASON_REQUIRED", "E_FEATURE_DISABLED", "E_UNAUTHENTICATED", "E_GUARD_REFUSED", "E_INTERNAL"]

_LEDGER_WRITE_ERRORS = {
    'E_RECORD_NOT_FOUND': 'journal, account, party or class absent from selected company',
    'E_VERSION_CONFLICT': 'stale whole-journal expected version, including no-op and repeated void',
    'E_UNBALANCED_ENTRY': 'entered or generated batch debit and credit totals differ',
    'E_PERIOD_CLOSED': 'original or replacement accounting date is closed',
    'E_DUPLICATE_NUMBER': 'supplied number belongs to another journal, including a voided one',
    'E_INACTIVE_REFERENCE': 'new posting references an inactive account, party or class',
    'E_VALUE_RANGE': 'amount or journal side exceeds signed 64-bit range',
    'E_AMOUNT_PRECISION': 'decimal input has more places than home currency supports',
    'E_REASON_REQUIRED': 'void has no reason, or agent/system write lacks reason or directive',
    'E_IDEMPOTENCY_MISMATCH': 'retry key is reused with different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown context directive',
    'E_DIRECTIVE_INACTIVE': 'inactive context directive',
}
for _verb in ('post', 'update', 'void'):
    MATRIX['journal ' + _verb] = dict(_LEDGER_WRITE_ERRORS)
MATRIX.update({
    'journal show': {'E_RECORD_NOT_FOUND': 'journal or requested revision absent'},
    'journal query': {'E_QUERY_STALE': 'company audit changed between journal pages'},
    'journal history': {'E_RECORD_NOT_FOUND': 'journal absent', 'E_QUERY_STALE': 'company audit changed between history pages'},
    'report trial-balance': {'E_QUERY_STALE': 'relevant posting or account display facts changed between pages', 'E_VALUE_RANGE': 'public account balance or report total exceeds signed 64-bit range'},
    'report general-ledger': {'E_QUERY_STALE': 'relevant posting or account display facts changed between pages', 'E_VALUE_RANGE': 'public running balance or report total exceeds signed 64-bit range', 'E_RECORD_NOT_FOUND': 'account filter does not resolve'},
})

for _verb in ('post', 'update'):
    MATRIX['register ' + _verb] = dict(_LEDGER_WRITE_ERRORS)
MATRIX['register calculate'] = {code: _LEDGER_WRITE_ERRORS[code] for code in (
    'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALUE_RANGE', 'E_AMOUNT_PRECISION')}
MATRIX['register query'] = dict(MATRIX['report general-ledger'])
MATRIX['register query'].update({code: _LEDGER_WRITE_ERRORS[code] for code in ('E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION')})

# The money-out documents are the register split under their own names, so they raise the
# ledger's own codes; only the unbalanced one reads differently, because what it names is the
# difference between the expense lines and the figure on the face of the document.
for _noun in ('check', 'card-charge', 'card-credit'):
    MATRIX[_noun + ' post'] = {code: _LEDGER_WRITE_ERRORS[code] for code in (
        'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_PERIOD_CLOSED', 'E_DUPLICATE_NUMBER',
        'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED', 'E_IDEMPOTENCY_MISMATCH',
        'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE')}
    MATRIX[_noun + ' post'].update({
        'E_VALIDATION': 'account is not the kind that funds this document, or a line names an unusable account',
        'E_UNBALANCED_ENTRY': 'expense lines do not add up to the amount on the face of the document',
    })

# A transfer is the same register entry with a single category, so it raises the same ledger
# codes. It never reports an unbalanced entry, because both legs are the one amount, and it
# never reports a duplicate number, because it takes no number of its own.
MATRIX['transfer post'] = {code: _LEDGER_WRITE_ERRORS[code] for code in (
    'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_PERIOD_CLOSED',
    'E_VALUE_RANGE', 'E_AMOUNT_PRECISION', 'E_REASON_REQUIRED', 'E_IDEMPOTENCY_MISMATCH',
    'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE')}
MATRIX['transfer post']['E_VALIDATION'] = (
    'an end of the transfer is not a balance-sheet account the company owns, the same account '
    'is named at both ends, or an account is kept in another currency')

# The rest of each money-out document's lifecycle. A correction raises everything entering one
# raises plus a stale version; a void reads no accounts, allocates no number and cannot be out
# of balance, so it raises far less; the reads can only fail to find the document, refuse to
# describe an entry that is no longer one, or be walked with an expired cursor.
_MONEY_OUT_READ_ERRORS = {
    'E_RECORD_NOT_FOUND': 'no document of this kind carries that id or number',
    'E_VALIDATION': 'the stored entry no longer has this document shape, or the cursor belongs '
                    'to another query contract',
    'E_QUERY_STALE': 'the company audit log moved while the page was being walked',
}
# A cheque number belongs to one bank account, so its duplicate is a different fact from a
# document number already taken in the shared series, and the refusal says which cheque holds
# it. A card charge and a transfer have no cheque number and still raise only the document one.
MATRIX['check post']['E_DUPLICATE_NUMBER'] = (
    'the typed check number is already on another cheque drawn on the same bank account, '
    'including a voided one; 1001 and 01001 are one number')

for _noun, _face in (('check', 'check'), ('card-charge', 'card-charge'), ('card-credit', 'card-credit'),
                     ('transfer', 'transfer')):
    MATRIX[_noun + ' update'] = dict(MATRIX[_noun + ' post'])
    MATRIX[_noun + ' update']['E_VERSION_CONFLICT'] = (
        'expected_version is stale: the document changed since it was read')
    MATRIX[_noun + ' update']['E_RECORD_NOT_FOUND'] = (
        'no document of this kind carries that id or number, or a named reference is absent')
    MATRIX[_noun + ' void'] = {
        'E_RECORD_NOT_FOUND': MATRIX[_noun + ' update']['E_RECORD_NOT_FOUND'],
        'E_VERSION_CONFLICT': MATRIX[_noun + ' update']['E_VERSION_CONFLICT'],
        'E_VALIDATION': 'the stored entry no longer has this document shape',
        'E_REASON_REQUIRED': 'a void carries no reason, or an agent write carries no directive',
        'E_PERIOD_CLOSED': "the document's own accounting date is closed",
        'E_IDEMPOTENCY_MISMATCH': 'retry key reused with different original input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }
    MATRIX[_noun + ' show'] = {code: text for code, text in _MONEY_OUT_READ_ERRORS.items()
                               if code != 'E_QUERY_STALE'}
    MATRIX[_noun + ' query'] = dict(_MONEY_OUT_READ_ERRORS)
    MATRIX[_noun + ' history'] = dict(_MONEY_OUT_READ_ERRORS)

# Deleting a check or card charge cancels its stock and accounting at their original dates, so
# it refuses where that cancellation would reach something it cannot detach.
for _noun, _face in (('check', 'check'), ('card-charge', 'card charge')):
    MATRIX[_noun + ' delete'] = {
        'E_RECORD_NOT_FOUND': 'no ' + _face + ' carries that id or number',
        'E_VERSION_CONFLICT': 'stale expected_version',
        'E_VALIDATION': 'the ' + _face + ' is already deleted, a settlement source depends on it, '
                        'or its statement effects cannot be safely cancelled',
        'E_REASON_REQUIRED': 'no reason, or an agent write carries no directive',
        'E_PERIOD_CLOSED': 'an affected accounting date is on or before the closing date',
        'E_RECONCILIATION_DEPENDENCY': 'a reconciliation still holds an effect of this ' + _face,
        'E_DEPOSIT_DEPENDENCY': 'a deposit still claims this ' + _face,
        'E_IDEMPOTENCY_MISMATCH': 'same operation_key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }

# A cheque is addressed by the number on its face, which belongs to one bank account, so that
# number can name a cheque on each of two accounts. Every command that takes the selector says
# so rather than resolving one of them.
for _verb in ('show', 'update', 'void', 'history'):
    MATRIX['check ' + _verb]['E_VALIDATION'] = (
        MATRIX['check ' + _verb]['E_VALIDATION']
        + ', or the check number names a cheque on more than one bank account')

for _verb in ('show', 'list', 'query', 'activate', 'deactivate'):
    MATRIX['customer ' + _verb]['E_VALUE_RANGE'] = 'exact own or family receivable balance exceeds signed 64-bit range'

_SALES_WRITE_ERRORS = {
    'E_RECORD_NOT_FOUND': 'sale, customer, item, unit or referenced rule absent from selected company',
    'E_VERSION_CONFLICT': 'whole-document expected version is stale, including no-op and repeated void',
    'E_PERIOD_CLOSED': 'original or replacement accounting date is closed',
    'E_DUPLICATE_NUMBER': 'number already reserved by this document type, including voided documents',
    'E_INACTIVE_REFERENCE': 'newly selected reference or replacement posting account is inactive',
    'E_VALUE_RANGE': 'exact amount, quantity, unit conversion or accumulated total exceeds supported range',
    'E_AMOUNT_PRECISION': 'money input has more fractional digits than the home currency supports',
    'E_REASON_REQUIRED': 'void lacks a reason or agent/system write lacks reason or directive',
    'E_IDEMPOTENCY_MISMATCH': 'retry key reused with different original input',
    'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
    'E_DIRECTIVE_INACTIVE': 'context directive inactive',
}
for _noun in ('invoice', 'sales-receipt'):
    for _verb in ('post', 'update', 'void'):
        MATRIX[f'{_noun} {_verb}'] = dict(_SALES_WRITE_ERRORS)
        if _verb != 'void':
            MATRIX[f'{_noun} {_verb}']['E_PREVIEW_STALE'] = 'resolved commercial facts differ from the successful preview fingerprint'
    MATRIX[f'{_noun} show'] = {'E_RECORD_NOT_FOUND': 'document of the requested type or revision absent'}
    MATRIX[f'{_noun} query'] = {
        'E_RECORD_NOT_FOUND': 'customer filter does not resolve',
        'E_QUERY_STALE': 'company audit changed between query pages',
    }
    MATRIX[f'{_noun} history'] = {
        'E_RECORD_NOT_FOUND': 'document of the requested type absent',
        'E_QUERY_STALE': 'company audit changed between immutable revision pages',
    }
for _report in ('profit-and-loss', 'balance-sheet', 'profit-and-loss-by-job', 'profit-and-loss-by-class'):
    MATRIX['report ' + _report] = {
        'E_QUERY_STALE': 'posting or display facts changed between statement pages',
        'E_VALUE_RANGE': 'public account amount or statement total exceeds signed 64-bit range',
    }
MATRIX['report cash-flows'] = {
    'E_QUERY_STALE': 'posting or display facts changed between statement pages',
    'E_VALUE_RANGE': 'public account change, cash balance or statement total exceeds signed 64-bit range',
}
MATRIX['report income-tax-summary'] = {
    'E_QUERY_STALE': 'posting, tax-line assignment or display facts changed between summary pages',
    'E_VALUE_RANGE': 'public account amount, tax-line total or report total exceeds signed 64-bit range',
}
MATRIX['report ar-aging'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between aging pages',
    'E_VALUE_RANGE': 'public aging column or aging total exceeds signed 64-bit range',
}
MATRIX['report sales-by-customer'] = {
    'E_QUERY_STALE': 'posting or customer display facts changed between summary pages',
    'E_VALUE_RANGE': 'public income for a customer or the summary total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'a class or customer filter that names nothing',
}
MATRIX['report sales-by-item'] = {
    'E_QUERY_STALE': 'posting, sales-line or item display facts changed between summary pages',
    'E_VALUE_RANGE': 'public income, quantity or average price for an item exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'a class or customer filter that names nothing',
}
MATRIX['report sales-by-rep'] = {
    'E_QUERY_STALE': 'posting or sales-representative display facts changed between summary pages',
    'E_VALUE_RANGE': 'public income for a representative or the summary total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'a class or customer filter that names nothing',
}
MATRIX['report expenses-by-vendor'] = {
    'E_QUERY_STALE': 'posting or vendor display facts changed between summary pages',
    'E_VALUE_RANGE': 'public expense for a vendor or the summary total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'a class filter that names nothing',
}
for _report in ('profit-and-loss', 'balance-sheet', 'profit-and-loss-by-job',
                'profit-and-loss-by-class', 'cash-flows', 'income-tax-summary',
                'sales-by-customer', 'sales-by-item', 'sales-by-rep', 'expenses-by-vendor'):
    MATRIX['report ' + _report]['E_CASH_BASIS_EVIDENCE'] = 'retained commercial or settlement evidence cannot reconcile for cash reporting'
MATRIX['report statement'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between statement pages',
    'E_VALUE_RANGE': 'public statement amount, running balance or statement total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'customer filter does not resolve',
}
MATRIX['report open-invoices'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between open-invoice pages',
    'E_VALUE_RANGE': 'public invoice amount or open-invoice total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'customer filter does not resolve',
}
MATRIX['report customer-balance-summary'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between summary pages',
    'E_VALUE_RANGE': 'public customer balance or report total exceeds signed 64-bit range',
}
MATRIX['report customer-balance-detail'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between detail pages',
    'E_VALUE_RANGE': 'public amount, running balance or report total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'customer filter does not resolve',
}
MATRIX['report vendor-balance-summary'] = {
    'E_QUERY_STALE': 'posting, settlement or vendor display facts changed between summary pages',
    'E_VALUE_RANGE': 'public vendor balance or report total exceeds signed 64-bit range',
}
MATRIX['report vendor-balance-detail'] = {
    'E_QUERY_STALE': 'posting, settlement or vendor display facts changed between detail pages',
    'E_VALUE_RANGE': 'public amount, running balance or report total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'vendor filter does not resolve',
}
MATRIX['report open-purchase-orders'] = {
    'E_QUERY_STALE': 'an order, a receipt against one or vendor display facts changed between pages',
    'E_VALUE_RANGE': 'public order amount or report total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'vendor filter does not resolve',
}
MATRIX['report purchases-by-vendor'] = {
    'E_QUERY_STALE': 'posting, stock movement or vendor display facts changed between pages',
    'E_VALUE_RANGE': 'public purchase amount or report total exceeds signed 64-bit range',
}
MATRIX['report purchases-by-item'] = {
    'E_QUERY_STALE': 'posting, stock movement or item display facts changed between pages',
    'E_VALUE_RANGE': 'public purchase amount, average cost or report total exceeds signed 64-bit range',
}
MATRIX['report deposit-detail'] = {
    'E_QUERY_STALE': 'a deposit, its sources or account display facts changed between pages',
    'E_VALUE_RANGE': 'public deposit amount or report total exceeds signed 64-bit range',
}
MATRIX['report transaction-list-by-date'] = {
    'E_QUERY_STALE': 'a posting or account display facts changed between pages',
    'E_VALUE_RANGE': 'public transaction amount exceeds signed 64-bit range',
}
MATRIX['report vendor-1099-summary'] = {
    'E_QUERY_STALE': 'a posting or vendor facts changed between pages',
    'E_VALUE_RANGE': 'public payment total exceeds signed 64-bit range',
}
MATRIX['report ap-aging'] = {
    'E_QUERY_STALE': 'posting, settlement or vendor display facts changed between aging pages',
    'E_VALUE_RANGE': 'public aging column or aging total exceeds signed 64-bit range',
}
MATRIX['report unpaid-bills'] = {
    'E_QUERY_STALE': 'posting, settlement or vendor display facts changed between unpaid-bill pages',
    'E_VALUE_RANGE': 'public bill amount or unpaid-bill total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'vendor filter does not resolve',
}
MATRIX['report transaction-detail'] = {
    'E_QUERY_STALE': 'relevant posting or account display facts changed between pages',
    'E_VALUE_RANGE': 'public running balance or report total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'an account named in the accounts filter does not resolve',
}
MATRIX['report missing-checks'] = {
    'E_QUERY_STALE': 'company audit changed between check-sequence pages',
    'E_VALUE_RANGE': 'public check amount exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'account filter does not resolve',
    'E_VALIDATION': 'account filter names an account that is not a bank account',
}
MATRIX['report reconciliation-discrepancy'] = {
    'E_QUERY_STALE': 'company audit changed between discrepancy pages',
    'E_VALUE_RANGE': 'a reconciled or current balance exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'account does not resolve',
    'E_VALIDATION': 'account names an account that is neither a bank nor a credit card account',
}
MATRIX['report entries-to-review'] = {
    'E_QUERY_STALE': 'company audit changed between review pages',
    'E_VALUE_RANGE': 'a listed entry amount exceeds signed 64-bit range',
    'E_VALIDATION': 'as_of is not a date, or the continuation does not match',
}
MATRIX['report prior-balances'] = {
    'E_QUERY_STALE': 'company audit changed between prior-balance pages',
    'E_VALUE_RANGE': 'a reviewed or current balance exceeds signed 64-bit range',
    'E_VALIDATION': 'as_of is not a date, or the continuation does not match',
}
MATRIX['review mark'] = {
    'E_RECORD_NOT_FOUND': 'transaction does not resolve',
    'E_PERMISSION': 'an agent tries to mark an entry reviewed; marking is a person\'s step',
    'E_VALIDATION': 'the transaction is not on the entries-to-review list',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown directive',
    'E_DIRECTIVE_INACTIVE': 'inactive directive',
}
MATRIX['report inventory-valuation'] = {
    'E_QUERY_STALE': 'a movement, posting or item display fact changed between valuation pages',
    'E_VALUE_RANGE': 'an item asset value or the valuation total exceeds signed 64-bit range',
}
MATRIX['report stock-status'] = {
    'E_QUERY_STALE': 'a movement, posting or item display fact changed between stock-status pages',
    'E_VALUE_RANGE': 'an on-hand quantity, asset value or the total exceeds signed 64-bit range',
}
MATRIX['inventory adjust'] = {
    'E_RECORD_NOT_FOUND': 'unknown item, adjustment account or class',
    'E_INACTIVE_REFERENCE': 'inactive item, account or class',
    'E_VALIDATION': 'an item that carries no stock, no change at all, a quantity decrease carrying a value, a line already at its new_quantity, stock left worth nothing, or a movement worth nothing at the current average',
    'E_VALUE_RANGE': 'quantity or value outside signed 64-bit storage',
    'E_AMOUNT_PRECISION': 'value_change with more decimal places than the currency allows',
    'E_UNBALANCED_ENTRY': 'the generated posting does not balance',
    'E_PERIOD_CLOSED': 'the adjustment date or any correction date falls in a closed period',
    'E_DUPLICATE_NUMBER': 'the supplied number is already used in the journal series',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['inventory void'] = {
    'E_RECORD_NOT_FOUND': 'no inventory adjustment with that id or number',
    'E_VERSION_CONFLICT': 'stale expected_version',
    'E_VALIDATION': 'the reversal would leave value with no quantity behind it',
    'E_VALUE_RANGE': 'a reversed or corrected value outside signed 64-bit storage',
    'E_REASON_REQUIRED': 'void without a reason',
    'E_PERIOD_CLOSED': 'the adjustment date or any correction date falls in a closed period',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['inventory show'] = {
    'E_RECORD_NOT_FOUND': 'no inventory adjustment with that id or number, or no revision with that number',
    'E_VALIDATION': 'missing or malformed selector',
}
MATRIX['report unbilled-costs'] = {
    'E_QUERY_STALE': 'work, billing allocation or customer display facts changed between unbilled-cost pages',
    'E_VALUE_RANGE': 'public remaining amount, subtotal or unbilled total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'customer filter does not resolve',
}
MATRIX['report collections'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between collection pages',
    'E_VALUE_RANGE': 'public aging column, overdue balance or collection total exceeds signed 64-bit range',
}

for _verb in ('post', 'update'):
    MATRIX['journal ' + _verb]['E_NO_EXCHANGE_RATE'] = 'no exact-date original-to-home rate and no manual override'
MATRIX.update({
    'rate set': {'E_VERSION_CONFLICT': 'expected version is not the exact current pair version', 'E_VALUE_RANGE': 'next rate version exceeds integer storage', 'E_IDEMPOTENCY_MISMATCH': 'retry key reused with a different rate', 'E_DIRECTIVE_NOT_FOUND': 'unknown directive', 'E_DIRECTIVE_INACTIVE': 'inactive directive'},
    'rate show': {'E_RECORD_NOT_FOUND': 'id or exact date pair absent in selected company'},
    'rate query': {'E_QUERY_STALE': 'company audit changed between rate pages'},
})

_WORK_WRITE_ERRORS = {
    'E_RECORD_NOT_FOUND': 'work, historical revision or company-local referenced record does not exist',
    'E_VERSION_CONFLICT': 'expected whole-work version differs, including stale no-op or competing conversion',
    'E_DUPLICATE_NUMBER': 'number belongs to another work document of this kind',
    'E_INACTIVE_REFERENCE': 'new selection or conversion source is inactive; carried historical facts stay captured',
    'E_VALUE_RANGE': 'exact quote quantity, price, cost, tax or total exceeds supported bounds',
    'E_AMOUNT_PRECISION': 'home-currency quote amount has excess fractional precision',
    'E_REASON_REQUIRED': 'decision revocation, cancellation, supersession or reopening lacks required reason',
    'E_PREVIEW_STALE': 'resolved source/default/custom facts differ from preview',
    'E_WORK_DEPENDENCY': 'another alternative is accepted, agreed scope is frozen or the work-order destination already exists',
    'E_CONVERSION_KEY_REUSED': 'permanent company conversion key identifies a different canonical intent',
    'E_IDEMPOTENCY_MISMATCH': 'ordinary request key reused with different command or input',
    'E_DIRECTIVE_NOT_FOUND': 'context directive does not exist',
    'E_DIRECTIVE_INACTIVE': 'context directive is inactive',
}
for _noun, _conversion in (('proposal', 'estimate'), ('estimate', 'work-order'), ('work-order', 'complete')):
    for _verb in ('create', 'update', 'copy', _conversion):
        MATRIX[f'{_noun} {_verb}'] = dict(_WORK_WRITE_ERRORS)
    MATRIX[f'{_noun} show'] = {'E_RECORD_NOT_FOUND': 'document kind or selected revision does not exist',
        'E_QUERY_STALE': 'company audit changed between source-link pages'}
    for _verb in ('query', 'history'):
        MATRIX[f'{_noun} {_verb}'] = {
            'E_RECORD_NOT_FOUND': 'document or customer filter absent from selected company',
            'E_QUERY_STALE': 'company audit changed between bounded pages',
        }

# Withdrawing a quote posts nothing, so it refuses for the quote's own reasons only: a stale
# expected version, a missing reason, and the dependency owners refusing to let work that a
# work order or a bill already consumes be taken back.
MATRIX['estimate void'] = dict(_WORK_WRITE_ERRORS,
    E_WORK_DEPENDENCY='the estimate already has its work order, or a sale consumes its billing roots')

for _noun in ('estimate', 'work-order', 'time-activity'):
    for _verb in ('invoice', 'sales-receipt'):
        MATRIX[f'{_noun} {_verb}'] = {
            **_SALES_WRITE_ERRORS, **_WORK_WRITE_ERRORS,
            'E_WORK_DEPENDENCY': 'source not accepted, redirected to work order, cancelled, consumed, or linked economics changed',
            'E_CONVERSION_KEY_REUSED': 'permanent key already names a different operational or financial intent',
        }
    MATRIX[f'{_noun} billing'] = {
        'E_RECORD_NOT_FOUND': 'source absent from selected company',
        'E_QUERY_STALE': 'company audit changed between linked destination pages',
    }
# Recorded time is a customer-work document with a much smaller input, so it refuses for the
# work reasons and two of its own: a correction to somebody's hours always carries a reason,
# and neither a correction nor a withdrawal may move hours a sale is already standing on.
MATRIX['time-activity create'] = dict(_WORK_WRITE_ERRORS)
MATRIX['time-activity update'] = dict(_WORK_WRITE_ERRORS,
    E_REASON_REQUIRED='a correction that moves recorded time carries no reason',
    E_WORK_DEPENDENCY='a sale already consumes these hours, so the entry is frozen at what it was billed as')
MATRIX['time-activity void'] = dict(_WORK_WRITE_ERRORS,
    E_REASON_REQUIRED='the withdrawal carries no reason',
    E_WORK_DEPENDENCY='a sale consumes these hours; void the sale before withdrawing the time')
MATRIX['time-activity show'] = {'E_RECORD_NOT_FOUND': 'recorded time or selected revision does not exist',
    'E_QUERY_STALE': 'company audit changed between source-link pages'}
for _verb in ('query', 'history'):
    MATRIX[f'time-activity {_verb}'] = {
        'E_RECORD_NOT_FOUND': 'recorded time or customer filter absent from selected company',
        'E_QUERY_STALE': 'company audit changed between bounded pages',
    }

for _noun in ('invoice', 'sales-receipt'):
    for _verb in ('post', 'update', 'void'):
        MATRIX[f'{_noun} {_verb}']['E_WORK_DEPENDENCY'] = 'retained source-linked line or commercial scope changed'

_DEPOSIT_WRITE_ERRORS = {
    'E_RECORD_NOT_FOUND': 'deposit, receipt, account or party absent from the selected company',
    'E_VERSION_CONFLICT': 'expected_version behind the current deposit or receipt header',
    'E_PREVIEW_STALE': 'resolved facts, dependency guard or draft pin changed since the preview',
    'E_PERIOD_CLOSED': 'deposit or reversal date inside the closing date',
    'E_INACTIVE_REFERENCE': 'inactive bank, offset, cash-back account, party, class or payment method',
    'E_DUPLICATE_NUMBER': 'deposit number already used',
    'E_AMOUNT_PRECISION': 'amount with more decimals than the currency allows',
    'E_VALUE_RANGE': 'posting total, leg or header version outside signed 64-bit range',
    'E_REASON_REQUIRED': 'correction, void or coordination without a reason',
    'E_SCHEMA_BEHIND': 'company database behind the deposit feature revision',
    'E_DEPOSIT_SOURCE_INELIGIBLE': 'receipt is not posted undeposited home-currency cash',
    'E_DEPOSIT_SOURCE_CLAIMED': 'receipt already belongs to another active deposit',
    'E_DEPOSIT_SOURCE_INVALID': 'captured receipt cash provenance unsupported or inconsistent',
    'E_DEPOSIT_DATE_BEFORE_SOURCE': 'deposit date earlier than a selected receipt',
    'E_DEPOSIT_TOTAL': 'nonpositive subtotal, cash back above subtotal, or negative bank total',
    'E_DEPOSIT_OPERATION_KEY_REUSED': 'permanent key already names a different deposit intent',
    'E_DEPOSIT_DRAFT_STATE': 'pinned draft consumed, abandoned or pointing at another deposit',
    'E_DEPOSIT_DEPENDENCY': 'receipt claimed by a deposit; an atomic coordinated correction is required',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
for _verb in ('post', 'update', 'void'):
    MATRIX[f'deposit {_verb}'] = dict(_DEPOSIT_WRITE_ERRORS)
# Deleting takes the same document a void takes and writes no new revision, so the
# refusals that belong to composing one -- an ineligible receipt, a duplicate number, an
# amount, a draft -- cannot arise. What it adds is the reconciliation that will not let
# the bank line go, and the retained history it refuses to edit a second time.
MATRIX['deposit delete'] = {
    **{code: text for code, text in _DEPOSIT_WRITE_ERRORS.items()
       if code in ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_PREVIEW_STALE', 'E_PERIOD_CLOSED',
                   'E_VALUE_RANGE', 'E_REASON_REQUIRED', 'E_SCHEMA_BEHIND', 'E_DEPOSIT_SOURCE_INVALID',
                   'E_DEPOSIT_OPERATION_KEY_REUSED', 'E_IDEMPOTENCY_MISMATCH',
                   'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE')},
    'E_VALIDATION': 'a reason longer than 140 characters, or the deposit is already deleted',
    'E_RECONCILIATION_DEPENDENCY': 'a bank reconciliation still holds this deposit',
}
MATRIX['deposit sources'] = {
    'E_RECORD_NOT_FOUND': 'for_deposit names no deposit in the selected company',
    'E_QUERY_STALE': 'candidate facts changed between bounded pages',
    'E_VALIDATION': 'inverted receipt date range or a cursor from another filter',
    'E_SCHEMA_BEHIND': 'company database behind the deposit feature revision',
    'E_DEPOSIT_SOURCE_INVALID': 'captured receipt cash provenance unsupported or inconsistent',
}

# --- Row 5 bounded discovery: option catalogs and owned collections ----------
# Both commands page through query.page_state, so their cursor guard is the same
# company audit sequence the ordinary `query` verb pins.
_OPTIONS_ERRORS = {
    'E_LIST_FILTER': 'kind=choices names a definition this list does not own, or one whose kind is not choice',
    'E_QUERY_STALE': 'company audit changed after the first options page; restart without a cursor',
}
_CHILDREN_ERRORS = {
    'E_LIST_FILTER': 'column names no owned collection of this list',
    'E_QUERY_STALE': 'company audit changed after the first collection page; restart without a cursor',
    'E_RECORD_NOT_FOUND': 'the owning record selector resolves to no row in this list',
}
for _noun in _SUPPORTING_LIST_NOUNS:
    MATRIX[f'{_noun} query options'] = dict(_OPTIONS_ERRORS)
for _noun in ('custom-field', 'item', 'price-level', 'unit-of-measure', 'vendor'):
    MATRIX[f'{_noun} query children'] = dict(_CHILDREN_ERRORS)

# --- Settlement: sale-side declarations --------------------------------------
# The six sales writes share one declaration list, but only `invoice update`
# routes into payment_invoice_corrections/payment_restatement, and only
# InvoiceUpdateInput carries operation_key. The other five inherit these three
# codes with no path that raises them; the declarations, not the rows, are the
# drift.
_SALES_SETTLEMENT_UNREACHED = {
    'E_HAS_APPLICATIONS': 'inherited from the shared sales-write list; no path in this command raises it',
    'E_APPLIED_EXCEEDS_TOTAL': 'inherited from the shared sales-write list; only invoice update restates settlement',
    'E_PAYMENT_OPERATION_KEY_REUSED': 'inherited from the shared sales-write list; only invoice update accepts operation_key',
}
MATRIX['invoice update'].update({
    'E_HAS_APPLICATIONS': 'the correction moves the settlement owner (payer, receivable account or currency) or dates the invoice after a live application',
    'E_APPLIED_EXCEEDS_TOTAL': 'the corrected invoice total is below the amount already applied to it',
    'E_PAYMENT_OPERATION_KEY_REUSED': 'operation_key already names a different original invoice correction',
})
MATRIX['invoice post'].update(_SALES_SETTLEMENT_UNREACHED)
MATRIX['invoice void'].update(_SALES_SETTLEMENT_UNREACHED)
MATRIX['invoice void']['E_HAS_APPLICATIONS'] = 'the invoice still carries active applications; unapply them before voiding'
for _verb in ('post', 'update', 'void'):
    MATRIX[f'sales-receipt {_verb}'].update(_SALES_SETTLEMENT_UNREACHED)
for _verb in ('update', 'void'):
    MATRIX[f'sales-receipt {_verb}']['E_DEPOSIT_DEPENDENCY'] = 'a deposit claims this receipt; correct the receipt and the deposit in one coordinated write'

# --- Settlement: payment writes ----------------------------------------------
# receive/apply run payments.prepare; unapply/void divert to payment_cancellation
# and update to payment_corrections, so the five verbs reach quite different
# parts of the shared declaration.
_PAYMENT_WRITE_ERRORS = {
    'E_RECORD_NOT_FOUND': 'receipt, invoice, customer, receivable or deposit-to account, payment method or shared draft absent from the selected company',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt, on a selected invoice, or on the shared draft',
    'E_APPLICATION_CAPACITY': 'an application exceeds the cash received, the credit still owned by that party, or the invoice amount due',
    'E_APPLICATION_INCOMPATIBLE': 'a selected invoice has a different customer family, receivable account or currency than the credit',
    'E_APPLICATION_INACTIVE': 'the receipt is not posted, or a named application was already reversed',
    'E_PAYMENT_OPERATION_KEY_REUSED': 'operation_key already names a different original settlement request',
    'E_SELECTION_CONSUMED': 'the named shared draft was already consumed by a settlement operation',
    'E_PREVIEW_STALE': 'expected_facts_fingerprint, or the facts re-resolved inside the write transaction, differ from the previewed settlement',
    'E_PERIOD_CLOSED': 'the receipt, replacement or application date is on or before the closing date',
    'E_INACTIVE_REFERENCE': 'inactive customer, receivable account, deposit-to account or payment method',
    'E_DUPLICATE_NUMBER': 'the supplied receipt number belongs to another payment',
    'E_AMOUNT_PRECISION': 'a money input has more decimal places than the home currency supports',
    'E_VALUE_RANGE': 'a posting amount, component capacity or accumulated total exceeds signed 64-bit range',
    'E_REASON_REQUIRED': 'a void or correction has no reason, or an agent/system write supplies neither reason nor directive',
    'E_HAS_APPLICATIONS': 'active applications block this change; unapply them first',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
_UNREACHED_HERE = 'inherited from the shared payment-write list; no path in this verb raises it'
for _verb in ('receive', 'apply', 'unapply', 'void', 'update'):
    MATRIX[f'payment {_verb}'] = dict(_PAYMENT_WRITE_ERRORS)
for _verb in ('receive', 'apply'):
    MATRIX[f'payment {_verb}']['E_RECOVERY_PENDING'] = 'the named shared draft has an unresolved recovery attempt; settle it with payment recovery first'
    MATRIX[f'payment {_verb}']['E_HAS_APPLICATIONS'] = _UNREACHED_HERE
for _verb in ('update', 'void'):
    MATRIX[f'payment {_verb}']['E_DEPOSIT_DEPENDENCY'] = 'a deposit claims this receipt; correct the receipt and the deposit in one coordinated write'
MATRIX['payment receive'].update({
    'E_VERSION_CONFLICT': 'stale expected_version on a selected invoice or on the named shared draft',
    'E_APPLICATION_CAPACITY': 'the applications total more than the cash received, or one exceeds its invoice amount due',
    'E_DUPLICATE_NUMBER': 'the supplied receipt number is already allocated to another payment',
    'E_APPLICATION_INACTIVE': _UNREACHED_HERE,
})
MATRIX['payment apply'].update({
    'E_APPLICATION_CAPACITY': 'the application exceeds the credit still available on the receipt, or the invoice amount due',
    'E_APPLICATION_INACTIVE': 'the receipt being applied is not posted',
    'E_DUPLICATE_NUMBER': _UNREACHED_HERE,
})
MATRIX['payment unapply'].update({
    'E_DEPOSIT_DEPENDENCY': 'an unapplied invoice took a discount or write-off, which leaves with it and restates the receipt, and the receipt is banked on a deposit',
    'E_APPLICATION_INACTIVE': 'a named application is absent or already reversed, or the receipt is not posted',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt or on an invoice named by invoice_expected_version',
    'E_HAS_APPLICATIONS': _UNREACHED_HERE,
    'E_APPLICATION_CAPACITY': _UNREACHED_HERE,
    'E_APPLICATION_INCOMPATIBLE': _UNREACHED_HERE,
    'E_SELECTION_CONSUMED': _UNREACHED_HERE,
    'E_DUPLICATE_NUMBER': _UNREACHED_HERE,
    'E_AMOUNT_PRECISION': _UNREACHED_HERE,
})
MATRIX['payment void'].update({
    'E_HAS_APPLICATIONS': 'the receipt still carries active applications; unapply them before voiding',
    'E_HAS_REFUND': 'a live refund paid this receipt\'s overpayment back; void the refund before voiding the receipt',
    'E_REASON_REQUIRED': 'the void has no reason, or an agent/system write supplies neither reason nor directive',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt',
    'E_APPLICATION_INACTIVE': _UNREACHED_HERE,
    'E_APPLICATION_CAPACITY': _UNREACHED_HERE,
    'E_APPLICATION_INCOMPATIBLE': _UNREACHED_HERE,
    'E_SELECTION_CONSUMED': _UNREACHED_HERE,
    'E_DUPLICATE_NUMBER': _UNREACHED_HERE,
    'E_AMOUNT_PRECISION': _UNREACHED_HERE,
})
# Delete is its own grant and its own owner, so its reasons are not the posting verbs'. Two of
# them are the things that still stand on a receipt and are cancelled by whoever owns them: the
# applications it settled, and a refund that paid its overpayment back.
MATRIX['payment delete'] = {
    'E_RECORD_NOT_FOUND': 'unknown receipt, or one already deleted and not asked for by include_deleted',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt',
    'E_VALIDATION': 'a reason outside 1 to 140 characters, or incomplete cancellation evidence',
    'E_REASON_REQUIRED': 'no reason given',
    'E_PERIOD_CLOSED': "the receipt's own posting dates are on or before the closing date",
    'E_RECONCILIATION_DEPENDENCY': 'a finished bank reconciliation holds this receipt; undo it first',
    'E_DEPOSIT_DEPENDENCY': 'a deposit claims this receipt; cancel source and deposit in one coordinated write',
    'E_HAS_APPLICATIONS': 'active applications settle invoices from this receipt; unapply them first',
    'E_HAS_REFUND': "a live refund paid this receipt's overpayment back; void the refund first",
    'E_VALUE_RANGE': 'the resulting version exceeds signed 64-bit range',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
# A returned check is four writes in one transaction (the receipt's unapply, a customer refund, a register
# entry for the bank's fee and an invoice for the customer's), so it can be refused for what any of them can.
MATRIX['payment bounce'] = {
    'E_RECORD_NOT_FOUND': 'unknown receipt, fee account or fee item',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt',
    'E_VALIDATION': 'the receipt is still in Undeposited Funds, already bounced, took no cash, paid more than one customer or job, or is dated after the return; a fee is not more than zero, its account is the wrong type, or the customer fee names neither or both of item and account',
    'E_VALUE_RANGE': 'a fee amount is zero or negative, or a resulting version exceeds signed 64-bit range',
    'E_AMOUNT_PRECISION': 'a fee amount has more decimal places than the currency allows',
    'E_REASON_REQUIRED': 'the bounce has no reason, or an agent/system write supplies neither reason nor directive',
    'E_PERIOD_CLOSED': 'the return date, or the date the receipt was applied to an invoice, is on or before the closing date',
    'E_INACTIVE_REFERENCE': 'the bank account, fee account, fee item or the customer named for a fee invoice is inactive',
    'E_APPLICATION_INACTIVE': 'the receipt is voided',
    'E_APPLICATION_INCOMPATIBLE': 'the receipt still holds money for more than one customer or job',
    'E_CREDIT_UNAVAILABLE': 'the cash to take back is no longer on the receipt',
    'E_HAS_REFUND': 'part of the receipt was already paid back to the customer; void that refund first',
    'E_DUPLICATE_NUMBER': 'the supplied fee invoice number is already used',
    'E_PREVIEW_STALE': 'expected_facts_fingerprint no longer matches the books',
    'E_PAYMENT_OPERATION_KEY_REUSED': 'the operation_key belongs to a different receipt',
    'E_IDEMPOTENCY_MISMATCH': 'same idempotency key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['payment update'].update({
    'E_APPLIED_EXCEEDS_TOTAL': 'the corrected total drops the payer capacity below what is already applied from it',
    'E_HAS_APPLICATIONS': 'the corrected receipt date is later than the effective date of a live application',
    'E_REASON_REQUIRED': 'the correction has no reason, or an agent/system write supplies neither reason nor directive',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt or on a related invoice named by invoice_versions',
    'E_PREVIEW_STALE': 'the supplied settlement_guard no longer matches, or invoice_versions omits a related invoice; a fresh guard is returned',
    'E_DUPLICATE_NUMBER': 'the corrected receipt number belongs to another payment',
    'E_APPLICATION_INACTIVE': _UNREACHED_HERE,
    'E_APPLICATION_CAPACITY': _UNREACHED_HERE,
    'E_APPLICATION_INCOMPATIBLE': _UNREACHED_HERE,
    'E_SELECTION_CONSUMED': _UNREACHED_HERE,
})

# --- Settlement: shared nonposting drafts ------------------------------------
_SELECTION_WRITE_ERRORS = {
    'E_RECORD_NOT_FOUND': 'draft, funding receipt, selected invoice, customer or receivable account absent from the selected company',
    'E_VERSION_CONFLICT': 'stale expected_version on the draft, on a selected invoice, or on the adopted funding receipt',
    'E_SELECTION_CONSUMED': 'the draft was already consumed by a settlement operation and no longer accepts edits',
    'E_APPLICATION_INCOMPATIBLE': 'a selected invoice has a different customer family, receivable account or currency than the draft context',
    'E_PREVIEW_STALE': "a selected invoice's amount due changed, or the manifest re-derived inside the write transaction differs from the previewed one",
    'E_INACTIVE_REFERENCE': 'inactive customer or receivable account',
    'E_AMOUNT_PRECISION': 'a header or row amount has more decimal places than the home currency supports',
    'E_RECOVERY_PENDING': 'this draft has an unresolved recovery attempt; resolve it before editing',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
_UNREACHED_DRAFT = 'inherited from the shared draft-write list; no path in this verb raises it'
for _verb in ('create', 'update', 'clear'):
    MATRIX[f'payment selection {_verb}'] = dict(_SELECTION_WRITE_ERRORS)
MATRIX['payment selection create'].update({
    'E_RECORD_NOT_FOUND': 'funding receipt, customer or receivable account absent from the selected company',
    'E_PREVIEW_STALE': 'the draft context re-derived inside the write transaction differs from the previewed one',
    'E_AMOUNT_PRECISION': 'the header amount has more decimal places than the home currency supports',
    'E_VERSION_CONFLICT': _UNREACHED_DRAFT,
    'E_SELECTION_CONSUMED': _UNREACHED_DRAFT,
    'E_APPLICATION_INCOMPATIBLE': _UNREACHED_DRAFT,
    'E_RECOVERY_PENDING': _UNREACHED_DRAFT,
})
MATRIX['payment selection clear'].update({
    'E_APPLICATION_INCOMPATIBLE': _UNREACHED_DRAFT,
    'E_AMOUNT_PRECISION': _UNREACHED_DRAFT,
})
MATRIX['payment selection show'] = {
    'E_RECORD_NOT_FOUND': 'draft or requested revision absent from the selected company',
    'E_RECOVERY_PENDING': "the draft's recovery barrier is inconsistent, or a consumed draft has no readable operation",
}
MATRIX['payment selection items'] = {
    'E_RECORD_NOT_FOUND': 'draft or pinned revision absent from the selected company',
    'E_QUERY_STALE': 'the pinned revision manifest changed between manifest pages; restart without a cursor',
}
MATRIX['payment selection query'] = {
    'E_QUERY_STALE': 'company audit changed between draft pages; restart without a cursor',
    'E_RECOVERY_PENDING': "a listed draft's recovery barrier is inconsistent, or a consumed draft has no readable operation",
}

# --- Settlement: preparation reads -------------------------------------------
# One declaration list covers four reads that do very different work: only
# `calculate` resolves caller-supplied applications, so only it checks invoice
# versions, compatibility and row amounts.
_PREPARATION_UNREACHED = 'inherited from the shared preparation-read list; no path in this command raises it'
MATRIX['payment invoices'] = {
    'E_APPLICATION_INACTIVE': 'an existing_credit context names a receipt that is not posted; mode and payment come straight from the caller, so this is reachable on a read',
    'E_RECORD_NOT_FOUND': 'customer, funding receipt or receivable account absent from the selected company',
    'E_QUERY_STALE': 'candidate facts changed between candidate pages; restart without a cursor',
    'E_INACTIVE_REFERENCE': 'inactive customer or receivable account',
    'E_VERSION_CONFLICT': _PREPARATION_UNREACHED,
    'E_APPLICATION_INCOMPATIBLE': _PREPARATION_UNREACHED,
    'E_AMOUNT_PRECISION': _PREPARATION_UNREACHED,
}
MATRIX['payment suggest'] = {
    'E_APPLICATION_INACTIVE': 'an existing_credit context names a receipt that is not posted; mode and payment come straight from the caller, so this is reachable on a read',
    'E_RECORD_NOT_FOUND': 'customer, funding receipt or receivable account absent from the selected company',
    'E_QUERY_STALE': 'candidate facts changed between suggestion pages; restart without a cursor',
    'E_INACTIVE_REFERENCE': 'inactive customer or receivable account',
    'E_AMOUNT_PRECISION': 'the suggested amount has more decimal places than the home currency supports',
    'E_VERSION_CONFLICT': _PREPARATION_UNREACHED,
    'E_APPLICATION_INCOMPATIBLE': _PREPARATION_UNREACHED,
}
MATRIX['payment calculate'] = {
    'E_APPLICATION_INACTIVE': 'an existing_credit context names a receipt that is not posted; mode and payment come straight from the caller, so this is reachable on a read',
    'E_RECORD_NOT_FOUND': 'customer, funding receipt, receivable account, draft or a named invoice absent from the selected company',
    'E_VERSION_CONFLICT': 'stale expected_version on a named invoice or on the referenced draft',
    'E_QUERY_STALE': "a selected invoice's amount due or the funding receipt version moved under the calculation, or the pinned facts changed between pages",
    'E_APPLICATION_INCOMPATIBLE': 'a named invoice has a different customer family, receivable account or currency than the calculation context',
    'E_INACTIVE_REFERENCE': 'inactive customer or receivable account',
    'E_AMOUNT_PRECISION': 'a header or row amount has more decimal places than the home currency supports',
}
MATRIX['payment query'] = {
    'E_RECORD_NOT_FOUND': 'a customer, component customer or payment method filter resolves to nothing',
    'E_QUERY_STALE': 'company audit changed between receipt pages; restart without a cursor',
    'E_PAYMENT_PROFILE_INVALID': 'a stored receipt profile snapshot no longer decodes into a payment profile',
    'E_VERSION_CONFLICT': _PREPARATION_UNREACHED,
    'E_APPLICATION_INCOMPATIBLE': _PREPARATION_UNREACHED,
    'E_INACTIVE_REFERENCE': _PREPARATION_UNREACHED,
    'E_AMOUNT_PRECISION': _PREPARATION_UNREACHED,
}

# --- Settlement: reads over committed effect ---------------------------------
MATRIX['payment show'] = {'E_RECORD_NOT_FOUND': 'receipt or requested revision absent from the selected company'}
MATRIX['payment history'] = {
    'E_RECORD_NOT_FOUND': 'receipt, or a profile or audit event its history cites, absent from the selected company',
    'E_QUERY_STALE': 'company audit changed between history pages; restart without a cursor',
}
MATRIX['payment settlement'] = {
    'E_RECORD_NOT_FOUND': 'receipt absent from the selected company',
    'E_QUERY_STALE': 'company audit changed between component or application pages; restart without a cursor',
}
MATRIX['invoice settlement'] = {
    'E_RECORD_NOT_FOUND': 'invoice absent from the selected company',
    'E_QUERY_STALE': 'company audit changed between settlement pages; restart without a cursor',
}
MATRIX['payment settlement changes'] = {
    'E_RECORD_NOT_FOUND': 'the guard names an owner absent from the selected company',
    'E_PREVIEW_STALE': 'the settlement_guard is unsigned, malformed, or issued for another company or owner',
    'E_QUERY_STALE': 'the intervening-change set moved between pages; restart without a cursor',
}
MATRIX['application show'] = {'E_RECORD_NOT_FOUND': 'application or its exact inverse absent from the selected company'}
MATRIX['application history'] = {
    'E_RECORD_NOT_FOUND': 'application, or an audit event its history cites, absent from the selected company',
    'E_QUERY_STALE': 'company audit changed between history pages; restart without a cursor',
}
MATRIX['payment operation show'] = {'E_RECORD_NOT_FOUND': 'operation_key names no permanent operation in the selected company'}
MATRIX['payment operation items'] = {
    'E_RECORD_NOT_FOUND': 'operation_key names no permanent operation in the selected company',
    'E_QUERY_STALE': 'the stored operation effect changed between effect pages; restart without a cursor',
}
# payment preview items re-runs the write preparation purely. It converts the
# four codes below into E_PREVIEW_STALE before returning, so they are declared
# but never surface from this command.
_PREVIEW_CONVERTED = 'raised by the re-run preparation but converted to E_PREVIEW_STALE before it leaves this command'
MATRIX['payment preview items'] = {
    'E_RECORD_NOT_FOUND': 'receipt, invoice, draft or referenced record absent from the selected company',
    'E_PREVIEW_STALE': 'the intent is already committed, the recomputed facts fingerprint differs from the supplied one, the cursor does not belong to this page contract, or a converted staleness cause (see below)',
    'E_PERIOD_CLOSED': 'the prospective receipt or application date is on or before the closing date',
    'E_INACTIVE_REFERENCE': 'the prospective write references an inactive customer, account or payment method',
    'E_APPLICATION_INCOMPATIBLE': 'a prospective application targets a different customer family, receivable account or currency',
    'E_VERSION_CONFLICT': _PREVIEW_CONVERTED,
    'E_APPLICATION_CAPACITY': _PREVIEW_CONVERTED,
    'E_SELECTION_CONSUMED': _PREVIEW_CONVERTED,
    'E_QUERY_STALE': _PREVIEW_CONVERTED,
}

# --- Settlement: durable recovery of an interrupted draft edit ---------------
# All twelve commands are declared from one ERRORS list in
# commands/payment_recovery_cmds.py, so each row below narrows that list to what
# the command's own path can produce.
_RECOVERY_MEANINGS = {
    'E_RECORD_NOT_FOUND': 'recovery, draft, draft revision or a named invoice absent from the selected company',
    'E_VERSION_CONFLICT': 'stale expected_recovery_version, or the draft moved off the anchor version and revision this attempt was opened against',
    'E_RECOVERY_PENDING': "the draft's recovery barrier is inconsistent, or another attempt is already live on it",
    'E_RECOVERY_INCOMPLETE': 'the declared entries are not all uploaded in contiguous chunks, or the stored entries do not rebuild the declared intent hash',
    'E_RECOVERY_KEY_REUSED': 'recovery_key already names a different attempt, a retried action carries a different request, or a chunk repeats an invoice already stored',
    'E_RECOVERY_FINALIZED': 'the attempt is no longer the live one for its draft, or has left the state this action needs',
    'E_PREVIEW_STALE': 'expected_facts_fingerprint differs from the recomputed comparison',
    'E_QUERY_STALE': 'the comparison generation or intent hash moved, or the pinned facts changed between pages',
    'E_SELECTION_CONSUMED': 'the draft was consumed by a settlement operation and cannot open or take an attempt',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
_UNREACHED_RECOVERY = 'inherited from the shared recovery declaration list; no path in this command raises it'
_RECOVERY_REACHABLE = {
    'begin': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_KEY_REUSED',
              'E_RECOVERY_FINALIZED', 'E_SELECTION_CONSUMED'),
    'upload': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_INCOMPLETE',
               'E_RECOVERY_KEY_REUSED', 'E_RECOVERY_FINALIZED'),
    'seal': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_INCOMPLETE',
             'E_RECOVERY_KEY_REUSED', 'E_RECOVERY_FINALIZED'),
    'apply': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_INCOMPLETE',
              'E_RECOVERY_KEY_REUSED', 'E_RECOVERY_FINALIZED', 'E_PREVIEW_STALE', 'E_QUERY_STALE'),
    'abort': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_KEY_REUSED',
              'E_RECOVERY_FINALIZED'),
    'replace': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_KEY_REUSED',
                'E_RECOVERY_FINALIZED', 'E_SELECTION_CONSUMED'),
    'show': ('E_RECORD_NOT_FOUND', 'E_RECOVERY_PENDING'),
    'compare': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_INCOMPLETE',
                'E_RECOVERY_FINALIZED', 'E_QUERY_STALE'),
    'compare-items': ('E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECOVERY_PENDING', 'E_RECOVERY_INCOMPLETE',
                      'E_RECOVERY_FINALIZED', 'E_QUERY_STALE'),
    'items': ('E_RECORD_NOT_FOUND', 'E_QUERY_STALE'),
    'query': ('E_RECORD_NOT_FOUND', 'E_RECOVERY_PENDING', 'E_QUERY_STALE'),
}
_PIPELINE = ('E_IDEMPOTENCY_MISMATCH', 'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE')
for _verb, _reachable in _RECOVERY_REACHABLE.items():
    _write = _verb in ('begin', 'upload', 'seal', 'apply', 'abort', 'replace')
    # The three pipeline codes are added to every write by the registry and are
    # raised by dispatch, so they reach every write verb below.
    MATRIX[f'payment recovery {_verb}'] = {
        code: _RECOVERY_MEANINGS[code] if code in _reachable or code in _PIPELINE else _UNREACHED_RECOVERY
        for code in _RECOVERY_MEANINGS
        if _write or code not in _PIPELINE
    }
MATRIX['payment recovery begin']['E_RECOVERY_KEY_REUSED'] = 'recovery_key already names another attempt, or a retry of this begin carries a different request'
MATRIX['payment recovery upload']['E_RECOVERY_INCOMPLETE'] = 'the chunk is not the exact declared length for its index'
MATRIX['payment recovery upload']['E_RECOVERY_KEY_REUSED'] = 'a retry of this chunk carries a different request, or the chunk repeats an invoice already stored'
MATRIX['payment recovery seal']['E_RECOVERY_INCOMPLETE'] = 'entries are missing, out of range, disagree with their stored chunks, or do not rebuild the declared intent hash'
MATRIX['payment recovery apply']['E_RECOVERY_INCOMPLETE'] = 'the sealed attempt is incomplete, or the comparison still holds hard blockers'
MATRIX['payment recovery apply']['E_QUERY_STALE'] = 'the supplied attempt generation or intent hash no longer matches the stored attempt'
MATRIX['payment recovery replace']['E_SELECTION_CONSUMED'] = 'the draft was consumed by a settlement operation and cannot take a replacement attempt'
MATRIX['payment recovery show']['E_RECOVERY_PENDING'] = "the draft's recovery barrier is inconsistent, or a consumed draft has no readable operation"
MATRIX['payment recovery query']['E_RECOVERY_PENDING'] = "a listed draft's recovery barrier is inconsistent, or a consumed draft has no readable operation"
MATRIX['payment recovery query']['E_RECORD_NOT_FOUND'] = 'a listed draft named by a live attempt is absent from the selected company'
MATRIX['payment recovery query']['E_QUERY_STALE'] = 'company audit changed between attempt pages; restart without a cursor'
MATRIX['payment recovery items']['E_QUERY_STALE'] = 'the attempt intent hash or version changed between entry, chunk or gap pages; restart without a cursor'

# Public deposit reads. E_DEPOSIT_SOURCE_INVALID has three distinct producers and the
# distinction matters: a candidate whose authority lookup refuses is converted here rather
# than skipped, so a record the reader cannot resolve can never silently drop out of a page
# and quietly change a total.
_DEPOSIT_READ_ERRORS = {
    'E_RECORD_NOT_FOUND': 'absent deposit, or one whose connected graph the reader is not admitted to; the refusal is deliberately identical for both so it never distinguishes them',
    'E_DEPOSIT_SOURCE_INVALID': 'captured provenance the reader layer cannot decode (an allocation bucket that is neither a known name nor additional:<row>), a candidate whose authority or graph lookup refused during admission, or stored shape that no longer matches the pinned owners, tables and codecs',
}
for _name in ('deposit show', 'deposit items', 'deposit history', 'deposit query'):
    MATRIX.setdefault(_name, {}).update(_DEPOSIT_READ_ERRORS)
for _name in ('deposit items', 'deposit history', 'deposit query'):
    MATRIX[_name]['E_QUERY_STALE'] = 'the page fingerprint moved under the cursor; restart without one'

# The bill is the payables mirror of the invoice, so it refuses for the invoice's reasons on
# the other side of the books: an ineligible or ambiguous payable account, an expense line that
# names an account no expense can go to, and a due date before the bill's own.
MATRIX["bill post"] = {
    "E_RECORD_NOT_FOUND": "unknown vendor, account, terms, class or job",
    "E_INACTIVE_REFERENCE": "deactivated vendor, account, terms, class or job",
    "E_VALIDATION": "no single active Accounts Payable account, an ineligible account on the header or a line, or a due date before the bill date",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "bill date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit --number already used by another bill",
    "E_WORK_DEPENDENCY": "--purchase-order names an order another bill already consumed",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["bill update"] = dict(MATRIX["bill post"], **{
    "E_RECORD_NOT_FOUND": "unknown bill, vendor, account, terms, class or job",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_PERIOD_CLOSED": "original or new bill date on or before the closing date",
    "E_HAS_APPLICATIONS": "a payment has been applied to this bill",
    "E_VALIDATION": "a voided bill, a retired line identity, an ineligible account, or a due date before the bill date",
})
MATRIX["bill void"] = {
    "E_RECORD_NOT_FOUND": "unknown bill",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_REASON_REQUIRED": "no --reason",
    "E_VALIDATION": "--reason longer than 140 characters",
    "E_PERIOD_CLOSED": "bill date on or before the closing date",
    "E_HAS_APPLICATIONS": "a payment has been applied to this bill",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["bill delete"] = {
    "E_RECORD_NOT_FOUND": "unknown bill",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_REASON_REQUIRED": "no --reason",
    "E_VALIDATION": "--reason longer than 140 characters, or the bill is already deleted",
    "E_PERIOD_CLOSED": "bill date on or before the closing date",
    "E_HAS_APPLICATIONS": "a bill payment or vendor credit is still applied to this bill",
    "E_RECONCILIATION_DEPENDENCY": "a reconciliation still holds an effect of this bill",
    "E_IDEMPOTENCY_MISMATCH": "same operation_key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["bill show"] = {"E_RECORD_NOT_FOUND": "unknown bill or revision number"}
MATRIX["bill query"] = {"E_RECORD_NOT_FOUND": "unknown vendor filter", "E_QUERY_STALE": "company audit changed between bill pages"}
MATRIX["bill history"] = {"E_RECORD_NOT_FOUND": "unknown bill", "E_QUERY_STALE": "company audit changed between history pages"}


# Paying a bill reaches across documents, so it refuses for the bill's reasons and the
# settlement's: an ineligible funding account, a stale bill, and a bill that is already paid.
MATRIX["bill pay"] = {
    "E_RECORD_NOT_FOUND": "unknown bill, funding account, method or class",
    "E_INACTIVE_REFERENCE": "deactivated funding account, method, class or payable account",
    "E_VALIDATION": "an ineligible funding account, a check number on something that is not a check, a check number named for more than one payee group, a chequebook whose next check number names no place in a sequence, a payment dated before a bill it settles, a repeated bill selector, or an explicit number with more than one payee group",
    "E_VALUE_RANGE": "an amount of zero or less, an exhausted payment number sequence, or an exhausted chequebook",
    "E_AMOUNT_PRECISION": "an amount with more precision than the currency has",
    "E_PERIOD_CLOSED": "payment date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit payment number already occupied by another bill payment, or a check number already on another cheque drawn on the same bank account, whichever form wrote it",
    "E_VERSION_CONFLICT": "stale expected_version on a selected bill",
    "E_APPLICATION_CAPACITY": "more than the bill has open, including a concurrent payment that took it first",
    "E_APPLICATION_INCOMPATIBLE": "a bill owed in another currency",
    "E_APPLICATION_INACTIVE": "a voided bill, or one with no payable",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
# Applying capacity that already exists refuses for both sides of the edge: what the payment
# has free, and what the bill still has open.
MATRIX["bill payment apply"] = {
    "E_RECORD_NOT_FOUND": "unknown payment or bill",
    "E_VALIDATION": "a settlement dated before the payment or before a bill it settles, a repeated bill selector, or a row reached with nothing left to apply",
    "E_AMOUNT_PRECISION": "an amount with more precision than the currency has",
    "E_VALUE_RANGE": "an amount of zero or less",
    "E_PERIOD_CLOSED": "settlement date on or before the closing date",
    "E_VERSION_CONFLICT": "stale expected_version on the payment or on a selected bill",
    "E_APPLICATION_CAPACITY": "more than the payment has free or more than the bill has open, including a concurrent write that took either first",
    "E_APPLICATION_INCOMPATIBLE": "a bill owed to another vendor, from another payable account, or in another currency",
    "E_APPLICATION_INACTIVE": "a voided payment, a voided bill, or a bill with no payable",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["bill payment unapply"] = {
    "E_RECORD_NOT_FOUND": "unknown payment or bill",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_APPLICATION_INACTIVE": "a voided payment, or a named bill this payment has nothing applied to",
    "E_PERIOD_CLOSED": "an application being taken back is effective on or before the closing date",
    "E_VALIDATION": "a repeated bill selector",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["bill payment void"] = {
    "E_RECORD_NOT_FOUND": "unknown payment",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters",
    "E_REASON_REQUIRED": "void without a reason",
    "E_PERIOD_CLOSED": "payment date on or before the closing date",
    "E_APPLICATION_INACTIVE": "an already voided payment",
    "E_HAS_APPLICATIONS": "the payment still settles a bill",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["bill payment show"] = {"E_RECORD_NOT_FOUND": "unknown payment"}
MATRIX["bill payment query"] = {
    "E_RECORD_NOT_FOUND": "unknown vendor, bill, funding account or method filter",
    "E_QUERY_STALE": "company audit changed between payment pages",
}
MATRIX["bill payment history"] = {
    "E_RECORD_NOT_FOUND": "unknown payment",
    "E_QUERY_STALE": "company audit changed between history pages",
}


# A vendor credit refuses for the bill's reasons -- it is the same resolver on the same
# accounts -- and applying one refuses for both sides of the same settlement edge a bill
# payment uses: what the credit has free, and what the bill still has open.
MATRIX["vendor-credit post"] = {
    "E_RECORD_NOT_FOUND": "unknown vendor, account, class or job",
    "E_INACTIVE_REFERENCE": "deactivated vendor, account, class or job",
    "E_VALIDATION": "no single active Accounts Payable account, or an ineligible account on the header or a line",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "credit date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit --number already used by another vendor credit",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["vendor-credit update"] = {
    "E_RECORD_NOT_FOUND": "unknown credit, vendor, account, class or job",
    "E_INACTIVE_REFERENCE": "a deactivated account, class or job on the corrected header or a line",
    "E_VALIDATION": "an ineligible account on the header or a line, a retired or repeated line identity, a reason longer than 140 characters, a correction dated after a settlement the credit already made, or ambiguous current posting evidence",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "the superseded revision's date or the corrected one in a closed period",
    "E_DUPLICATE_NUMBER": "explicit --number already used by another vendor credit",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_REASON_REQUIRED": "no reason given for a correction",
    "E_APPLICATION_INACTIVE": "a voided credit",
    "E_APPLICATION_INCOMPATIBLE": "a correction naming another vendor or another payable account",
    "E_APPLICATION_CAPACITY": "a correction worth less than what the credit already answers, including a concurrent write that took it first",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["vendor-credit void"] = {
    "E_RECORD_NOT_FOUND": "unknown credit",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_REASON_REQUIRED": "void without a reason",
    "E_VALIDATION": "a reason longer than 140 characters",
    "E_PERIOD_CLOSED": "credit date on or before the closing date",
    "E_HAS_APPLICATIONS": "the credit still settles a bill",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["vendor-credit apply"] = {
    "E_RECORD_NOT_FOUND": "unknown credit or bill",
    "E_VALIDATION": "a settlement dated before the credit or before a bill it settles, a repeated bill selector, or a row reached with nothing left to apply",
    "E_AMOUNT_PRECISION": "an amount with more precision than the currency has",
    "E_VALUE_RANGE": "an amount of zero or less",
    "E_PERIOD_CLOSED": "settlement date on or before the closing date",
    "E_VERSION_CONFLICT": "stale expected_version on the credit or on a selected bill",
    "E_APPLICATION_CAPACITY": "more than the credit has free or more than the bill has open, including a concurrent write that took either first",
    "E_APPLICATION_INCOMPATIBLE": "a bill owed to another vendor, from another payable account, or in another currency; a deposit with no Accounts Payable line naming the credit's vendor",
    "E_APPLICATION_INACTIVE": "a voided credit, a voided bill or refund deposit, or a bill with no payable",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["vendor-credit unapply"] = {
    "E_RECORD_NOT_FOUND": "unknown credit or bill",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_APPLICATION_INACTIVE": "a voided credit, or a named bill this credit has nothing applied to",
    "E_PERIOD_CLOSED": "an application being taken back is effective on or before the closing date",
    "E_VALIDATION": "a repeated bill selector",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["vendor-credit show"] = {"E_RECORD_NOT_FOUND": "unknown credit or revision number"}
MATRIX["vendor-credit query"] = {
    "E_RECORD_NOT_FOUND": "unknown vendor or bill filter",
    "E_QUERY_STALE": "company audit changed between credit pages",
}
MATRIX["vendor-credit history"] = {
    "E_RECORD_NOT_FOUND": "unknown credit",
    "E_QUERY_STALE": "company audit changed between revision pages",
}


# A credit memo refuses for the invoice's reasons -- it is the same resolver on the same
# accounts -- plus the two only a return can hit: asking for more of a line than is left, and
# a source line the invoice has since corrected out from under the claim. It also refuses what
# it cannot value: a line that *names* a stock-carrying item rather than returning the invoice
# line it was sold on, because a price is not a cost and nothing here derives one. Returning a
# stocked line is posted, and refuses in turn if the stock it moves cannot stand on some date.
MATRIX["credit-memo post"] = {
    "E_RECORD_NOT_FOUND": "unknown customer, item, account, class, tax item, source invoice or source line",
    "E_INACTIVE_REFERENCE": "deactivated customer, item, account, class or tax item",
    "E_VALIDATION": "no single active Accounts Receivable account, an ineligible posting account, a source invoice for another customer, a priced return, a line naming a stock-carrying item instead of returning one, a return that would leave stock below zero on some date, a document mixing returns with named items, or a zero total",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "credit date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit --number already used by an invoice or another credit memo",
    "E_PREVIEW_STALE": "expected_facts_fingerprint no longer matches the resolved facts",
    "E_RETURN_EXHAUSTED": "more of the source line asked for than is still returnable",
    "E_SOURCE_CORRECTION_CONFLICT": "the source line's quantity or net changed after it was claimed",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["credit-memo update"] = {
    **MATRIX["credit-memo post"],
    "E_VERSION_CONFLICT": "expected_version does not match the current credit header",
    "E_REASON_REQUIRED": "a used credit correction has no reason",
    "E_APPLICATION_INCOMPATIBLE": "a used credit changes customer, receivable or currency",
    "E_APPLIED_EXCEEDS_TOTAL": "corrected capacity is less than applications plus refunds",
    "E_HAS_APPLICATIONS": "corrected credit date is later than its earliest use",
}
MATRIX["credit-memo show"] = {"E_RECORD_NOT_FOUND": "unknown credit memo or revision number"}
MATRIX["credit-memo history"] = {
    "E_RECORD_NOT_FOUND": "unknown credit memo",
    "E_QUERY_STALE": "company audit changed between history pages",
}


# Remitting sales tax refuses for three families: the funding side a bill payment already
# refuses for, the agency side that keeps a remittance off a vendor who is not one, and the
# basis refusal -- the company policy under which nothing recorded a liability to report.
MATRIX["sales-tax liability"] = {
    "E_RECORD_NOT_FOUND": "unknown agency filter",
    "E_QUERY_STALE": "company audit changed between liability pages",
    "E_VALUE_RANGE": "an amount outside signed 64-bit minor units",
    "E_TAX_BASIS_UNSUPPORTED": "the company's sales tax liability basis is payment_receipt",
}
MATRIX["sales-tax pay"] = {
    "E_RECORD_NOT_FOUND": "unknown agency, funding account, method or class, or no sales tax payable account in the chart",
    "E_INACTIVE_REFERENCE": "deactivated agency, funding account, method, class or liability account",
    "E_VALIDATION": "a vendor that is not a flagged tax agency, an ineligible funding account, a check number on something that is not a check, a through_date after the payment date, or an amount of zero",
    "E_VALUE_RANGE": "an exhausted remittance number sequence",
    "E_AMOUNT_PRECISION": "an amount with more precision than the currency has",
    "E_PERIOD_CLOSED": "payment date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit number already occupied by another remittance",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_APPLICATION_CAPACITY": "more than the agency is owed through that date, including a concurrent remittance that took it first",
    "E_TAX_BASIS_UNSUPPORTED": "the company's sales tax liability basis is payment_receipt",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["sales-tax payment void"] = {
    "E_RECORD_NOT_FOUND": "unknown remittance",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters",
    "E_REASON_REQUIRED": "void without a reason",
    "E_PERIOD_CLOSED": "remittance date on or before the closing date",
    "E_APPLICATION_INACTIVE": "an already voided remittance",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["sales-tax payment show"] = {"E_RECORD_NOT_FOUND": "unknown remittance"}
MATRIX["sales-tax adjust"] = {
    "E_RECORD_NOT_FOUND": "unknown agency, adjustment account or class, or no sales tax payable account in the chart",
    "E_INACTIVE_REFERENCE": "deactivated agency, adjustment account, class or liability account",
    "E_VALIDATION": "a vendor that is not a flagged tax agency, an adjustment account that is the sales tax payable account, a bank, a credit card, receivable, payable, inventory or non-posting account",
    "E_VALUE_RANGE": "an amount of zero, or an exhausted adjustment number sequence",
    "E_AMOUNT_PRECISION": "an amount with more precision than the currency has",
    "E_PERIOD_CLOSED": "adjustment date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit number already occupied by another adjustment",
    "E_TAX_BASIS_UNSUPPORTED": "the company's sales tax liability basis is payment_receipt",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["sales-tax adjustment void"] = {
    "E_RECORD_NOT_FOUND": "unknown adjustment",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters",
    "E_REASON_REQUIRED": "void without a reason",
    "E_PERIOD_CLOSED": "adjustment date on or before the closing date",
    "E_APPLICATION_INACTIVE": "an already voided adjustment",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["sales-tax adjustment show"] = {"E_RECORD_NOT_FOUND": "unknown adjustment"}
MATRIX["sales-tax adjustment query"] = {
    "E_RECORD_NOT_FOUND": "unknown agency or adjustment account filter",
    "E_QUERY_STALE": "company audit changed between adjustment pages",
}
MATRIX["sales-tax payment query"] = {
    "E_RECORD_NOT_FOUND": "unknown agency, funding account or method filter",
    "E_QUERY_STALE": "company audit changed between remittance pages",
}


# What can go wrong when a credit is used. Two codes are this family's own: a credit that has
# already been spent (applied or refunded) is `E_CREDIT_UNAVAILABLE`, and a credit something
# still stands on is `E_HAS_APPLICATIONS` or `E_HAS_REFUND`. Everything else is the invoice's
# own vocabulary, because the target of an application is an invoice.
MATRIX["customer-credit apply"] = {
    "E_RECORD_NOT_FOUND": "unknown credit memo or invoice",
    "E_VERSION_CONFLICT": "stale expected_version on the credit memo or an invoice",
    "E_VALIDATION": "the same invoice twice, a non-positive amount, or a date before the credit memo",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "settlement date in a closed period",
    "E_PREVIEW_STALE": "expected_facts_fingerprint no longer matches the live settlement",
    "E_APPLICATION_CAPACITY": "more than the invoice still owes",
    "E_APPLICATION_INCOMPATIBLE": "another customer, receivable account or currency",
    "E_APPLICATION_INACTIVE": "voided credit memo",
    "E_CREDIT_UNAVAILABLE": "more than the credit is still worth after applications and refunds",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["customer-credit unapply"] = {
    "E_RECORD_NOT_FOUND": "unknown credit memo",
    "E_VERSION_CONFLICT": "stale expected_version on the credit memo or an invoice",
    "E_VALIDATION": "the same application twice, or incomplete allocation evidence",
    "E_PERIOD_CLOSED": "the original application's own date is in a closed period",
    "E_PREVIEW_STALE": "expected_facts_fingerprint no longer matches the live settlement",
    "E_APPLICATION_INACTIVE": "unknown or already-undone application, or a voided credit memo",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["credit-memo void"] = {
    "E_RECORD_NOT_FOUND": "unknown credit memo",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters, ambiguous posting evidence, or returned stock that is no longer on hand to take back out",
    "E_REASON_REQUIRED": "no reason given",
    "E_PERIOD_CLOSED": "the credit memo's own date is in a closed period",
    "E_HAS_APPLICATIONS": "a live application; unapply it first",
    "E_HAS_REFUND": "a live refund consumption; void the refund first",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["credit-memo delete"] = {
    "E_RECORD_NOT_FOUND": "unknown credit memo",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters, or the credit memo is already deleted",
    "E_REASON_REQUIRED": "no reason given",
    "E_PERIOD_CLOSED": "the credit memo's own date is in a closed period",
    "E_HAS_APPLICATIONS": "a live application; unapply it first, and the invoices are named",
    "E_HAS_REFUND": "a live refund consumption; void the refund first, and the refunds are named",
    "E_RECONCILIATION_DEPENDENCY": "a reconciliation still holds an effect of this credit memo",
    "E_IDEMPOTENCY_MISMATCH": "same operation_key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["credit-memo query"] = {
    "E_RECORD_NOT_FOUND": "unknown customer or receivable account filter",
    "E_QUERY_STALE": "company audit changed between credit pages",
}
MATRIX["customer-refund post"] = {
    "E_RECORD_NOT_FOUND": "unknown credit memo, customer, funding account or method",
    "E_INACTIVE_REFERENCE": "deactivated funding account, method or class",
    "E_VALIDATION": "a funding account that is not a bank account, a check number on something that is not a check, the same credit twice, a guard customer the credits do not belong to, or a date before a credit memo",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "refund date in a closed period",
    "E_DUPLICATE_NUMBER": "explicit --number already used by another refund",
    "E_APPLICATION_INACTIVE": "a voided credit memo",
    "E_APPLICATION_INCOMPATIBLE": "credits belonging to different customers, receivable accounts or currencies",
    "E_CREDIT_UNAVAILABLE": "more than a credit is still worth after applications and refunds",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["customer-refund update"] = {
    "E_RECORD_NOT_FOUND": "unknown refund, credit memo, customer, funding account or method",
    "E_INACTIVE_REFERENCE": "deactivated funding account, method or class",
    "E_VALIDATION": "a funding account that is not a bank account, a check number on something that is not a check, the same credit twice, a guard customer the credits do not belong to, a date before a credit memo, a reason longer than 140 characters, or ambiguous current posting evidence",
    "E_VALUE_RANGE": "amount outside signed 64-bit minor units",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_PERIOD_CLOSED": "the superseded revision's date or the corrected one in a closed period",
    "E_DUPLICATE_NUMBER": "explicit --number already used by another refund",
    "E_APPLICATION_INACTIVE": "a voided refund, or a voided credit memo named as a source",
    "E_APPLICATION_INCOMPATIBLE": "credits belonging to different customers, receivable accounts or currencies, or a correction that would pay back somebody else",
    "E_CREDIT_UNAVAILABLE": "more than a credit is worth once this refund's own consumptions are handed back",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_REASON_REQUIRED": "no reason given for a correction",
    "E_RECONCILIATION_DEPENDENCY": "a finished bank reconciliation holds the refund",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["customer-refund void"] = {
    "E_RECORD_NOT_FOUND": "unknown refund",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters",
    "E_REASON_REQUIRED": "no reason given",
    "E_PERIOD_CLOSED": "the refund's own date is in a closed period",
    "E_APPLICATION_INACTIVE": "already voided",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["customer-refund show"] = {"E_RECORD_NOT_FOUND": "unknown refund or revision number"}
MATRIX["customer-refund query"] = {
    "E_RECORD_NOT_FOUND": "unknown customer, funding account or method filter",
    "E_QUERY_STALE": "company audit changed between refund pages",
}
MATRIX["statement-charge post"] = {
    "E_RECORD_NOT_FOUND": "unknown customer, item, class, tax code or receivable account",
    "E_INACTIVE_REFERENCE": "an inactive customer, item or account",
    "E_VALIDATION": "no positive amount, rate and amount together, a non-sales item, or no single active AR account",
    "E_VALUE_RANGE": "a quantity or amount past the exact-integer bound",
    "E_AMOUNT_PRECISION": "a rate or amount with more decimals than the currency has",
    "E_PERIOD_CLOSED": "the charge date is on or before the closing date",
    "E_DUPLICATE_NUMBER": "another statement charge already holds that number",
    "E_PREVIEW_STALE": "expected_facts_fingerprint no longer matches the resolved facts",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}


# A purchase order posts nothing, so it has no closing-date gate and no payable to refuse for:
# what is left is the bill's own list of unresolvable and ineligible references, plus the two
# refusals that belong to an order alone -- a line that is neither an item nor an account, and
# an order a bill has already been entered from.
MATRIX["purchase-order post"] = {
    "E_RECORD_NOT_FOUND": "unknown vendor, item, account, terms, class or job",
    "E_INACTIVE_REFERENCE": "deactivated vendor, item, account, terms, class or job",
    "E_VALIDATION": "a line naming both or neither of item and account, an item with no purchase account, an ineligible account, an amount that contradicts quantity times rate, or an expected date before the order date",
    "E_VALUE_RANGE": "quantity or amount outside signed 64-bit scaled integers",
    "E_AMOUNT_PRECISION": "more decimals than the home currency has",
    "E_DUPLICATE_NUMBER": "explicit --number already used by another purchase order",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}

MATRIX["billing-group create"] = {
    "E_RECORD_NOT_FOUND": "unknown customer or job named as a member",
    "E_NAME_TAKEN": "a billing group with that name, ignoring case",
    "E_VALIDATION": "a blank name, a name containing a colon, or the same customer twice",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["statement-charge void"] = {
    "E_RECORD_NOT_FOUND": "unknown statement charge",
    "E_HAS_APPLICATIONS": "the charge still carries active applications; unapply them before voiding",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a reason longer than 140 characters",
    "E_REASON_REQUIRED": "no reason given",
    "E_PERIOD_CLOSED": "the charge's own date is in a closed period",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["purchase-order update"] = dict(MATRIX["purchase-order post"], **{
    "E_RECORD_NOT_FOUND": "unknown purchase order, vendor, item, account, terms, class or job",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_WORK_DEPENDENCY": "a bill has already been entered from this purchase order",
    "E_VALIDATION": "a withdrawn order, a retired line identity, a line naming both or neither of item and account, or an ineligible account",
})
MATRIX["purchase-order void"] = {
    "E_RECORD_NOT_FOUND": "unknown purchase order",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_REASON_REQUIRED": "no --reason",
    "E_VALIDATION": "--reason longer than 140 characters",
    "E_WORK_DEPENDENCY": "a bill has already been entered from this purchase order",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["purchase-order close-lines"] = {
    "E_RECORD_NOT_FOUND": "unknown purchase order",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "a voided order, or a line that is not on the order",
    "E_WORK_DEPENDENCY": "the order already became one bill in full",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["billing-group rename"] = {
    "E_RECORD_NOT_FOUND": "unknown billing group",
    "E_NAME_TAKEN": "another billing group already has that name",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["statement-charge show"] = {"E_RECORD_NOT_FOUND": "unknown charge or revision number"}
MATRIX["statement-charge query"] = {
    "E_RECORD_NOT_FOUND": "unknown customer filter",
    "E_QUERY_STALE": "company audit changed between charge pages",
}
MATRIX["purchase-order show"] = {"E_RECORD_NOT_FOUND": "unknown purchase order or revision number"}
MATRIX["purchase-order query"] = {
    "E_RECORD_NOT_FOUND": "unknown vendor filter",
    "E_QUERY_STALE": "company audit changed between purchase order pages",
}
MATRIX["purchase-order history"] = {
    "E_RECORD_NOT_FOUND": "unknown purchase order",
    "E_QUERY_STALE": "company audit changed between history pages",
}
MATRIX["billing-group delete"] = {
    "E_RECORD_NOT_FOUND": "unknown billing group",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["billing-group add"] = {
    "E_RECORD_NOT_FOUND": "unknown billing group, customer or job",
    "E_VALIDATION": "the same customer twice in one list",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["billing-group remove"] = {
    "E_RECORD_NOT_FOUND": "unknown billing group, customer or job",
    "E_VALIDATION": "the same customer twice in one list",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["billing-group show"] = {"E_RECORD_NOT_FOUND": "unknown billing group"}
MATRIX["billing-group list"] = {"E_LIST_FILTER": "a continuation naming a group that is gone"}
# A batch never raises for one customer: an invoice that is refused becomes a failed row on the
# batch carrying that customer's own code. These are the codes the batch itself raises, before
# any invoice is attempted.
MATRIX["batch-invoice post"] = {
    "E_RECORD_NOT_FOUND": "unknown billing group, customer or job",
    "E_VALIDATION": "neither or both of billing_group and customers, an empty group, or the same customer twice",
    "E_INACTIVE_REFERENCE": "reported per customer on the batch, never raised for the run",
    "E_PERIOD_CLOSED": "reported per customer on the batch, never raised for the run",
    "E_DUPLICATE_NUMBER": "reported per customer on the batch, never raised for the run",
    "E_VALUE_RANGE": "reported per customer on the batch, never raised for the run",
    "E_AMOUNT_PRECISION": "reported per customer on the batch, never raised for the run",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["batch-invoice retry"] = {
    "E_RECORD_NOT_FOUND": "unknown batch",
    "E_VALIDATION": "a batch whose customers were all invoiced",
    "E_INACTIVE_REFERENCE": "reported per customer on the batch, never raised for the run",
    "E_PERIOD_CLOSED": "reported per customer on the batch, never raised for the run",
    "E_DUPLICATE_NUMBER": "reported per customer on the batch, never raised for the run",
    "E_VALUE_RANGE": "reported per customer on the batch, never raised for the run",
    "E_AMOUNT_PRECISION": "reported per customer on the batch, never raised for the run",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown --directive",
    "E_DIRECTIVE_INACTIVE": "deactivated --directive",
}
MATRIX["batch-invoice show"] = {"E_RECORD_NOT_FOUND": "unknown batch"}
MATRIX["batch-invoice query"] = {"E_RECORD_NOT_FOUND": "unknown billing group filter"}


# Memorized transactions. A failed entry is a blocked occurrence, not a raised error, so the
# rows below are about the template and the schedule rather than about what an entry posts.
MATRIX["memorized create"] = {
    "E_VALIDATION": "a command that is not a create/post one, a payload that command rejects, a schedule with no start date, or a group member carrying its own schedule",
    "E_NAME_TAKEN": "another memorized transaction already has that name",
    "E_RECORD_NOT_FOUND": "unknown group",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized update"] = {
    "E_VALIDATION": "a payload the command rejects, a schedule with no start date, a group member carrying its own schedule, or an unknown or non-nullable --clear",
    "E_NAME_TAKEN": "another memorized transaction already has that name",
    "E_RECORD_NOT_FOUND": "unknown template or group",
    "E_VERSION_CONFLICT": "stale expected_version",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized delete"] = {
    "E_RECORD_NOT_FOUND": "unknown template", "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "empty selector", "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized enter"] = {
    "E_RECORD_NOT_FOUND": "unknown template", "E_VALIDATION": "a deleted template or a bad date",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized process"] = {
    "E_RECORD_NOT_FOUND": "unknown template named by --memorized", "E_VALIDATION": "a bad as-of date or limit",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized retry"] = {
    "E_RECORD_NOT_FOUND": "unknown occurrence", "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "an occurrence that is already entered or skipped",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized skip"] = {
    "E_RECORD_NOT_FOUND": "unknown occurrence", "E_VERSION_CONFLICT": "stale expected_version",
    "E_VALIDATION": "an occurrence that is already entered or skipped",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized show"] = {"E_RECORD_NOT_FOUND": "unknown template", "E_VALIDATION": "empty selector"}
MATRIX["memorized list"] = {"E_VALIDATION": "a bad cursor, as-of date or limit"}
MATRIX["memorized-group create"] = {
    "E_VALIDATION": "a schedule with no start date", "E_NAME_TAKEN": "another group already has that name",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized-group update"] = {
    "E_VALIDATION": "a shared schedule while a member carries its own, or an unknown or non-nullable --clear",
    "E_NAME_TAKEN": "another group already has that name", "E_RECORD_NOT_FOUND": "unknown group",
    "E_VERSION_CONFLICT": "stale expected_version", "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized-group delete"] = {
    "E_RECORD_NOT_FOUND": "unknown group", "E_VERSION_CONFLICT": "stale expected_version",
    "E_ACTIVE_DEPENDENTS": "the group still has members", "E_VALIDATION": "empty selector",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized-group enter"] = {
    "E_RECORD_NOT_FOUND": "unknown group", "E_VALIDATION": "a deleted or empty group, or a bad date",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
MATRIX["memorized-group show"] = {"E_RECORD_NOT_FOUND": "unknown group", "E_VALIDATION": "empty selector"}
MATRIX["memorized-group list"] = {"E_VALIDATION": "a bad cursor or limit"}

# All four writes load, prepare and re-prove the same account aggregate, so any of them can answer
# with any of the family's reasons; which step raises first is what differs between them.
_RECONCILE_ERRORS = {
    **RECONCILIATION_MATRIX,
    "E_RECORD_NOT_FOUND": "unknown account, draft or certificate in the selected company",
    "E_VERSION_CONFLICT": "stale expected draft or proposal version",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
for _verb in ("opening start", "start", "mark", "finish", "discard"):
    MATRIX["reconcile " + _verb] = dict(_RECONCILE_ERRORS)
# `reconcile import` starts and marks through the same aggregate, reading a file or the bank inbox.
# Inbox validation names statement_date for an invalid period, ending_balance when supplied with a draft,
# and from_inbox while the source import is reversing or blocked.
MATRIX["reconcile import"] = {
    **_RECONCILE_ERRORS,
    "E_VALIDATION": "invalid statement input, mixed file/inbox sources, invalid inbox period, missing date/balance, ending_balance with an inbox draft, or reversing/blocked inbox source",
    "E_AMOUNT_PRECISION": "a statement amount with more decimal places than the account's currency has",
}

# The two reads walk that same aggregate, so they carry the family's reasons too -- but neither
# takes an idempotency key or a directive, and each has one page-level reason the other cannot give.
_RECONCILE_READ_ERRORS = {_code: _RECONCILE_ERRORS[_code]
                          for _code in (*RECONCILIATION_MATRIX, "E_RECORD_NOT_FOUND")}
MATRIX["reconcile candidates"] = {
    **_RECONCILE_READ_ERRORS,
    "E_QUERY_STALE": "a continuation whose fingerprint no longer matches the candidate population, or an offset past its end",
}
MATRIX["reconcile preview"] = {
    **_RECONCILE_READ_ERRORS,
    "E_VERSION_CONFLICT": _RECONCILE_ERRORS["E_VERSION_CONFLICT"],
}

# Rows for commands added after this matrix was last completed.
MATRIX['membership grant']['E_VERSION_CONFLICT'] = 'stale --expected-version on the permission policy'
MATRIX['membership revoke']['E_VERSION_CONFLICT'] = 'stale --expected-version on the permission policy'
MATRIX['permission activate'] = {
    'E_VERSION_CONFLICT': 'expected_generation or expected_catalog_sha256 no longer names the reviewed policy',
}
MATRIX['customer-refund history'] = {
    'E_RECORD_NOT_FOUND': 'unknown refund',
    'E_QUERY_STALE': 'company audit changed between history pages',
}
_ITEM_RECEIPT_WRITE = {
    'E_RECORD_NOT_FOUND': 'unknown vendor, item, account, purchase order or receipt',
    'E_INACTIVE_REFERENCE': 'an inactive vendor, item or account',
    'E_PERIOD_CLOSED': 'the receipt date, or the date it would change from, is in a closed period',
    'E_DUPLICATE_NUMBER': 'the number is already used in the item-receipt number series',
    'E_VALUE_RANGE': 'a quantity, cost or total past the exact-integer bound',
    'E_AMOUNT_PRECISION': 'an amount finer than the currency allows',
    'E_VERSION_CONFLICT': 'stale expected_version or purchase_order_version',
    'E_WORK_DEPENDENCY': 'a linked bill, or stock already consumed from this receipt, prevents the change',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
for _verb in ('post', 'update', 'void'):
    MATRIX['item-receipt ' + _verb] = dict(_ITEM_RECEIPT_WRITE)
MATRIX['item-receipt show'] = {'E_RECORD_NOT_FOUND': 'unknown receipt or revision number',
                               'E_QUERY_STALE': 'company audit changed while the receipt was read'}
MATRIX['item-receipt query'] = {'E_RECORD_NOT_FOUND': 'unknown vendor or item filter',
                                'E_QUERY_STALE': 'company audit changed between receipt pages'}
MATRIX['item-receipt history'] = {'E_RECORD_NOT_FOUND': 'unknown receipt',
                                  'E_QUERY_STALE': 'company audit changed between history pages'}
# The sale and journal deletions share the purchase deletions' owner and refusals, plus the
# settlements a sale can carry and a credit memo line claimed from it.
for _name, _face in (('invoice delete', 'invoice'), ('sales-receipt delete', 'sales receipt'),
                     ('journal delete', 'journal entry')):
    MATRIX[_name] = {
        'E_RECORD_NOT_FOUND': 'no ' + _face + ' carries that id or number',
        'E_VERSION_CONFLICT': 'stale expected_version',
        'E_VALIDATION': 'the ' + _face + ' is already deleted, or its effects cannot be safely cancelled',
        'E_REASON_REQUIRED': 'no reason, or an agent write carries no directive',
        'E_PERIOD_CLOSED': 'an affected accounting date is on or before the closing date',
        'E_RECONCILIATION_DEPENDENCY': 'a reconciliation still holds an effect of this ' + _face,
        'E_DEPOSIT_DEPENDENCY': 'a deposit still claims this ' + _face,
        'E_HAS_APPLICATIONS': 'an active settlement applies to this ' + _face + '; unapply it first',
        'E_IDEMPOTENCY_MISMATCH': 'same operation_key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }
for _name in ('invoice delete', 'sales-receipt delete'):
    MATRIX[_name]['E_SOURCE_CORRECTION_CONFLICT'] = 'a credit memo line claimed from this sale prevents its cancellation'

# Moving a company in (R166): plan and tie-out read the export files; apply writes through the
# ordinary commands and stops at the first failure with what it made kept in.
_CUTOVER_FILE = "an attachment id in `files` that names no attachment"
MATRIX["cutover plan"] = {"E_RECORD_NOT_FOUND": _CUTOVER_FILE}
MATRIX["cutover tie-out"] = {"E_RECORD_NOT_FOUND": _CUTOVER_FILE,
                             "E_QUERY_STALE": "a continuation cursor whose tie-out no longer matches the books"}
MATRIX["cutover apply"] = {
    "E_RECORD_NOT_FOUND": _CUTOVER_FILE,
    "E_CUTOVER_BLOCKED": "the plan still has blocking exceptions (unmapped account, clearing not 0.00, missing inventory file)",
    "E_CUTOVER_INCOMPLETE": "a write failed part way; what was made stays and a re-run continues",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
    "E_DIRECTIVE_NOT_FOUND": "unknown directive", "E_DIRECTIVE_INACTIVE": "inactive directive",
}
# R174: a person may finish with a labelled adjustment; an agent may not, and the adjustment
# journal is dated the statement date, so a closed period refuses it.
MATRIX["reconcile finish"]["E_PERMISSION"] = "an agent asking for the discrepancy adjustment (a person's step)"
MATRIX["reconcile finish"]["E_PERIOD_CLOSED"] = "the adjustment journal's statement date is on or before the closing date"
# Restoring a deleted document posts a new one through its family's post writer (R151).
for _name, _face in (('journal restore', 'journal entry'), ('invoice restore', 'invoice')):
    MATRIX[_name] = {
        'E_RECORD_NOT_FOUND': 'no ' + _face + ' carries that id or number',
        'E_VALIDATION': 'the ' + _face + ' is not deleted, was void when deleted, or would not post the same today',
        'E_PERMISSION': 'an agent asked; restoring is a person\'s act',
        'E_PERIOD_CLOSED': 'its date is on or before the closing date; give an open date',
        'E_DUPLICATE_NUMBER': 'the number given is taken',
        'E_INACTIVE_REFERENCE': 'an account, name or item it used is inactive',
        'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }
MATRIX['journal restore']['E_UNBALANCED_ENTRY'] = 'the retained lines no longer balance'
MATRIX['journal restore']['E_NO_EXCHANGE_RATE'] = 'no stored rate for a foreign line on the restore date'

# Merging duplicate customers and vendors (R117): an audited alias, never a re-pointing of revisions.
for _kind in ('customer', 'vendor'):
    MATRIX[f'{_kind} merge'] = {
        'E_RECORD_NOT_FOUND': f'no {_kind} carries either name or id',
        'E_MERGE_REFUSED': f'the same {_kind} twice, one already merged or a survivor of other merges, a job or linked or open-balance {_kind}, or a currency the survivor never used (the details say which)',
        'E_REASON_REQUIRED': 'a merge is always given a reason',
        'E_ACTIVE_DEPENDENTS': f'deactivating the merged {_kind} finds active members still under it',
        'E_RECORD_IN_USE': f'deactivating the merged {_kind} finds it still in use',
        'E_PERMISSION': 'an agent asked; merging is a person\'s act',
        'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }
    MATRIX[f'{_kind} unmerge'] = {
        'E_RECORD_NOT_FOUND': f'no {_kind} carries that name or id',
        'E_MERGE_REFUSED': f'the {_kind} is not merged into another one',
        'E_REASON_REQUIRED': 'an undo is always given a reason',
        'E_INACTIVE_REFERENCE': f'the survivor was made inactive since, so the {_kind} cannot stand beside it again',
        'E_PERMISSION': 'an agent asked; undoing a merge is a person\'s act',
        'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }

# Merging duplicate accounts and items (R117, second part): the same alias, editable references re-pointed.
for _kind in ('account', 'item'):
    MATRIX[f'{_kind} merge'] = {
        'E_RECORD_NOT_FOUND': f'no {_kind} carries either name or id',
        'E_MERGE_REFUSED': (f'the same {_kind} twice, a different type, one already merged or a survivor of other merges, '
                            'children of its own, a system or uncategorized account, a bank, card, A/R or A/P account with '
                            'postings, a tax or stocked item, a group listing both in different units, a price level pricing them differently, a member cycle '
                            '(the details say which)'),
        'E_REASON_REQUIRED': 'a merge is always given a reason',
        'E_PERMISSION': 'an agent asked; merging is a person\'s act',
        'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }
    MATRIX[f'{_kind} unmerge'] = {
        'E_RECORD_NOT_FOUND': f'no {_kind} carries that name or id',
        'E_MERGE_REFUSED': f'the {_kind} is not merged into another one',
        'E_REASON_REQUIRED': 'an undo is always given a reason',
        'E_PERMISSION': 'an agent asked; undoing a merge is a person\'s act',
        'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
        'E_DIRECTIVE_INACTIVE': 'context directive inactive',
    }

# What a 1099 vendor was paid before the books began here (R179): one figure per vendor and year.
MATRIX['vendor 1099-opening'] = {
    'E_RECORD_NOT_FOUND': 'no vendor carries that name or id',
    'E_VALIDATION': 'a vendor not marked eligible for a 1099, an as_of outside the year, or a negative amount',
    'E_VERSION_CONFLICT': 'expected_version is not the amount\'s current version',
    'E_AMOUNT_PRECISION': 'more decimal places than the home currency has',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
    'E_DIRECTIVE_INACTIVE': 'context directive inactive',
}

# A small text attachment read as text (R170 blind July): the summary box of a statement, kept as an attachment.
MATRIX['transaction find'] = {
    'E_VALIDATION': 'bad date, amount, type or cursor; a name on two lists without name_type',
    'E_RECORD_NOT_FOUND': 'no name or account matches',
    'E_QUERY_STALE': 'the books changed between pages',
    'E_VALUE_RANGE': 'a stored amount outside the exact range',
}

MATRIX['attachment text'] = {
    'E_RECORD_NOT_FOUND': 'attachment absent',
    'E_VALIDATION': 'not a text file by its media type or name, or a body holding binary data',
    'E_VALUE_RANGE': 'larger than the 64 KiB attachment text reads, or an invalid company byte limit',
    'E_IO': 'missing, collected, corrupt or unsafe body',
    'E_DB_BUSY': 'collection pending',
}

# Converting a non-inventory part or other charge to an inventory part (R154): one-way, people only.
MATRIX['item convert'] = {
    'E_RECORD_NOT_FOUND': 'no item carries that name or id',
    'E_TYPE_CHANGE': 'already an inventory part (one-way), a type that cannot convert, a percentage charge, or an item in a branch of the item list',
    'E_RECORD_IN_USE': 'an open purchase order, estimate, proposal or work order still names the item',
    'E_VALIDATION': 'a date before the last document using the item, or no cost-of-goods or income account to name',
    'E_REASON_REQUIRED': 'a conversion is always given a reason',
    'E_INACTIVE_REFERENCE': 'a named account is inactive',
    'E_AMOUNT_PRECISION': 'a price or cost with more decimal places than the home currency has',
    'E_PERMISSION': 'an agent asked; converting an item is a person\'s act',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'context directive absent',
    'E_DIRECTIVE_INACTIVE': 'context directive inactive',
}
# Finance charges (R120): assessed by the company policy, each written as a statement charge.
MATRIX['finance-charge assess'] = {
    'E_VALIDATION': 'finance charges are off (no account, or neither rate nor minimum), a named customer has nothing overdue, a changed amount is not above 0.00, or a customer is named twice',
    'E_RECORD_NOT_FOUND': 'unknown customer',
    'E_INACTIVE_REFERENCE': 'a selected customer or job is inactive',
    'E_AMOUNT_PRECISION': 'a changed amount has more decimal places than the currency allows',
    'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
    'E_PERIOD_CLOSED': 'the assessment date is on or before the closing date',
    'E_PREVIEW_STALE': 'the books or the policy changed since the preview whose fingerprint was given',
    'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
# A vendor's refund check for an open credit (R149): a deposit and the credit applied to it, in one write.
MATRIX['vendor-credit refund'] = {
    'E_RECORD_NOT_FOUND': 'unknown credit, deposit-to account or payment method',
    'E_INACTIVE_REFERENCE': 'the deposit-to account or payment method is inactive',
    'E_VALIDATION': 'a refund dated before the credit, an amount of zero or less, or a deposit-to account that is not a bank',
    'E_VALUE_RANGE': 'a resulting version exceeds signed 64-bit range',
    'E_AMOUNT_PRECISION': 'an amount with more precision than the currency has',
    'E_PERIOD_CLOSED': 'refund date on or before the closing date',
    'E_DUPLICATE_NUMBER': 'the supplied deposit number is already used',
    'E_VERSION_CONFLICT': 'stale expected_version on the credit',
    'E_APPLICATION_CAPACITY': 'more than the credit still has free, or nothing free at all',
    'E_APPLICATION_INACTIVE': 'a voided credit',
    'E_APPLICATION_INCOMPATIBLE': 'the deposit line does not match the credit vendor and payable account',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
# Budgets, budget and comparison reports, memorized reports (R109).
_BUDGET_CELL = {
    'E_VALIDATION': 'an account of the wrong kind, a month outside the budget, a customer or class missing on a dimensional budget or given on another, more amounts than months remain',
    'E_RECORD_NOT_FOUND': 'unknown budget, account, customer or class',
    'E_INACTIVE_REFERENCE': 'an inactive account',
    'E_AMOUNT_PRECISION': 'an amount with more decimal places than the currency allows',
    'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
    'E_VERSION_CONFLICT': 'stale expected_version',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
_WRITE_TAIL = {k: _BUDGET_CELL[k] for k in ('E_IDEMPOTENCY_MISMATCH', 'E_DIRECTIVE_NOT_FOUND', 'E_DIRECTIVE_INACTIVE')}
MATRIX['budget set'] = dict(_BUDGET_CELL)
MATRIX['budget fill'] = dict(_BUDGET_CELL)
MATRIX['budget clear'] = {k: _BUDGET_CELL[k] for k in ('E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VERSION_CONFLICT')} | _WRITE_TAIL
MATRIX['budget create'] = {'E_VALIDATION': 'a balance sheet budget kept per customer or class', 'E_NAME_TAKEN': 'a budget already has the name',
                           'E_VALUE_RANGE': 'an actual amount beyond signed 64-bit range', 'E_CASH_BASIS_EVIDENCE': 'cash basis evidence missing for from_actuals'} | _WRITE_TAIL
MATRIX['budget rename'] = {'E_RECORD_NOT_FOUND': 'unknown budget', 'E_NAME_TAKEN': 'another budget has the name',
                           'E_VERSION_CONFLICT': 'stale expected_version', 'E_VALIDATION': 'a blank name'} | _WRITE_TAIL
MATRIX['budget delete'] = {'E_RECORD_NOT_FOUND': 'unknown budget', 'E_VERSION_CONFLICT': 'stale expected_version'} | _WRITE_TAIL
MATRIX['budget show'] = {'E_VALIDATION': 'a customer or class on a budget not kept that way', 'E_RECORD_NOT_FOUND': 'unknown budget, customer or class',
                         'E_VALUE_RANGE': 'a row total beyond signed 64-bit range'}
MATRIX['budget list'] = {}
MATRIX['report budget-overview'] = {'E_QUERY_STALE': 'budget or books changed between pages', 'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
                                    'E_VALIDATION': 'a customer or class on a budget not kept that way', 'E_RECORD_NOT_FOUND': 'unknown budget, customer or class'}
MATRIX['report budget-vs-actual'] = MATRIX['report budget-overview'] | {'E_CASH_BASIS_EVIDENCE': 'cash basis evidence missing'}
MATRIX['report profit-and-loss-comparison'] = {'E_CASH_BASIS_EVIDENCE': 'cash basis evidence missing', 'E_QUERY_STALE': 'books changed between pages',
                                               'E_VALUE_RANGE': 'an amount beyond signed 64-bit range'}
MATRIX['report balance-sheet-comparison'] = dict(MATRIX['report profit-and-loss-comparison'])
MATRIX['memorized-report create'] = {'E_VALIDATION': 'not a report, or options the report refuses', 'E_NAME_TAKEN': 'a memorized report already has the name',
                                     'E_RECORD_NOT_FOUND': 'a name in the options matches nothing'} | _WRITE_TAIL
MATRIX['memorized-report update'] = MATRIX['memorized-report create'] | {'E_VERSION_CONFLICT': 'stale expected_version'}
MATRIX['memorized-report delete'] = {'E_RECORD_NOT_FOUND': 'unknown memorized report', 'E_VERSION_CONFLICT': 'stale expected_version'} | _WRITE_TAIL
MATRIX['memorized-report show'] = {'E_RECORD_NOT_FOUND': 'unknown memorized report'}
MATRIX['memorized-report list'] = {}
MATRIX['memorized-report run'] = {'E_RECORD_NOT_FOUND': 'unknown memorized report', 'E_VALIDATION': 'kept options the report now refuses',
                                  'E_QUERY_STALE': 'books changed between pages', 'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
                                  'E_CASH_BASIS_EVIDENCE': 'cash basis evidence missing'}

# Customer prepayments (R124): deposits held on a liability until the work bills.
_PREPAYMENT_WRITE = {
    'E_RECORD_NOT_FOUND': 'unknown customer, document, receipt, invoice, account or payment method',
    'E_VALIDATION': 'no or two targets, an unaccepted, voided or cancelled document, another customer\'s document, nothing held, not a prepayment receipt or draw',
    'E_PERIOD_CLOSED': 'the date, or a draw\'s application date when undoing it, is on or before the closing date',
    'E_INACTIVE_REFERENCE': 'an inactive customer, account, item or payment method',
    'E_AMOUNT_PRECISION': 'an amount with more decimal places than the home currency has',
    'E_VALUE_RANGE': 'an amount outside the exact range',
    'E_DUPLICATE_NUMBER': 'the receipt number is already used',
    'E_APPLIED_EXCEEDS_TOTAL': 'more than is held, or more than the invoice owes',
    'E_APPLICATION_INACTIVE': 'a voided receipt, invoice or draw',
    'E_VERSION_CONFLICT': 'a document changed between reading and writing',
    'E_PREVIEW_STALE': 'a document the step wrote changed under it',
    'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive',
    'E_IDEMPOTENCY_MISMATCH': 'same idempotency key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['prepayment receive'] = dict(_PREPAYMENT_WRITE)
MATRIX['prepayment attach'] = dict(_PREPAYMENT_WRITE)
MATRIX['prepayment apply'] = dict(_PREPAYMENT_WRITE)
MATRIX['prepayment unapply'] = dict(_PREPAYMENT_WRITE, E_REASON_REQUIRED='an unapply always takes a reason',
                                    E_HAS_APPLICATIONS='the draw\'s credit memo still has an application it cannot release')
MATRIX['prepayment refund'] = dict(_PREPAYMENT_WRITE, E_APPLICATION_INCOMPATIBLE='the refund cannot pay out that credit',
                                   E_CREDIT_UNAVAILABLE='the drawing credit memo is no longer worth the amount')
MATRIX['prepayment show'] = {
    'E_RECORD_NOT_FOUND': 'unknown receipt, document or customer',
    'E_VALIDATION': 'not exactly one of receipt, document, customer; a sales receipt that is not a prepayment',
}
MATRIX['prepayment query'] = {
    'E_RECORD_NOT_FOUND': 'unknown customer or document',
    'E_VALIDATION': 'bad date or cursor',
}
MATRIX['report customer-prepayments'] = {
    'E_RECORD_NOT_FOUND': 'unknown customer',
    'E_INTERNAL': 'the Customer Prepayments account differs from what the receipts hold (an unattributed posting)',
}
# R148: moving credit between jobs is an invoice, the source credit's applications to it and a credit
# memo (and its applications) in one transaction, so it can be refused for what any of them can.
MATRIX['customer-credit transfer'] = {
    'E_RECORD_NOT_FOUND': 'unknown customer or job, credit memo, receipt or target invoice',
    'E_VERSION_CONFLICT': 'stale expected_version on a target invoice in apply_to',
    'E_VALIDATION': 'the two are the same or not jobs of one customer, the amount is not more than zero, both a credit memo and a receipt are named, a named credit is not the source\'s, an apply_to invoice is not the target\'s or takes more than it owes or than is moved, or the Credit Transfer item or clearing account cannot carry it',
    'E_VALUE_RANGE': 'an amount outside signed 64-bit minor units',
    'E_AMOUNT_PRECISION': 'more decimals than the home currency has',
    'E_REASON_REQUIRED': 'the transfer has no reason, or an agent/system write supplies neither reason nor directive',
    'E_PERIOD_CLOSED': 'the transfer date is on or before the closing date',
    'E_PREVIEW_STALE': 'expected_facts_fingerprint no longer matches the books',
    'E_INACTIVE_REFERENCE': 'the source or target customer or job is inactive',
    'E_APPLICATION_CAPACITY': 'a step would apply more than a document holds',
    'E_APPLICATION_INCOMPATIBLE': 'a credit on another receivable account or currency',
    'E_APPLICATION_INACTIVE': 'the named credit memo or receipt is voided',
    'E_CREDIT_UNAVAILABLE': 'the source has less credit dated on or before the transfer date than the amount',
    'E_PAYMENT_OPERATION_KEY_REUSED': 'the operation_key belongs to a transfer between other jobs',
    'E_DUPLICATE_NUMBER': 'a document number is already used',
    'E_IDEMPOTENCY_MISMATCH': 'same idempotency key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['customer-credit transfer-void'] = {
    'E_RECORD_NOT_FOUND': 'unknown transfer id',
    'E_VERSION_CONFLICT': 'a document of the transfer changed while it was being voided',
    'E_VALIDATION': 'a document of the transfer cannot be voided as it stands',
    'E_REASON_REQUIRED': 'the void has no reason, or an agent/system write supplies neither reason nor directive',
    'E_PERIOD_CLOSED': 'the transfer, or a use of the moved credit, is dated on or before the closing date',
    'E_PREVIEW_STALE': 'the transfer\'s documents changed between preview and write',
    'E_HAS_REFUND': 'part of the moved credit was refunded to the customer; void that refund first',
    'E_HAS_APPLICATIONS': 'something still stands on the charge invoice or the credit memo',
    'E_APPLICATION_INACTIVE': 'a document of the transfer is already voided',
    'E_DEPOSIT_DEPENDENCY': 'a receipt the transfer drew on is held by a deposit change',
    'E_IDEMPOTENCY_MISMATCH': 'same idempotency key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}

# The sales order (R101): a fifth customer-work kind. It refuses for the family's reasons, plus its own:
# new orders while the company has them off, and a void or a consumed-line change while an invoice
# consumes the order (or, once prepayments land, while one is held on it).
_ORDER_WRITE_ERRORS = dict(_WORK_WRITE_ERRORS,
    E_WORK_DEPENDENCY='an invoice consumes the order: its customer and invoiced lines are fixed and it cannot be voided; '
                      'or a customer prepayment is held on it',
    E_FEATURE_DISABLED='new sales orders are turned off for this company (sales_orders_enabled)',
    E_REASON_REQUIRED='closing, reopening or voiding an order lacks its reason')
for _verb in ('create', 'update', 'copy', 'void'):
    MATRIX[f'sales-order {_verb}'] = dict(_ORDER_WRITE_ERRORS)
MATRIX['sales-order show'] = {'E_RECORD_NOT_FOUND': 'order or selected revision does not exist',
    'E_QUERY_STALE': 'company audit changed between source-link pages'}
for _verb in ('query', 'history'):
    MATRIX[f'sales-order {_verb}'] = {
        'E_RECORD_NOT_FOUND': 'order or customer filter absent from selected company',
        'E_QUERY_STALE': 'company audit changed between bounded pages',
    }
MATRIX['estimate sales-order'] = dict(_WORK_WRITE_ERRORS,
    E_WORK_DEPENDENCY='the estimate is not accepted, is voided, or already has its work order or sales order',
    E_FEATURE_DISABLED='new sales orders are turned off for this company (sales_orders_enabled)')
# Fixed-asset depreciation and loans (R119).
_ASSET_TAIL = {
    'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['fixed-asset setup'] = {
    'E_VALIDATION': 'not a fixed-asset item, no method the first time, declining_balance without a percent, salvage not below cost, no cost basis, or changing the in-service date or period once periods are posted',
    'E_RECORD_NOT_FOUND': 'unknown item or account',
    'E_INACTIVE_REFERENCE': 'an inactive account',
    'E_AMOUNT_PRECISION': 'a salvage value with more decimal places than the currency has',
    'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
    'E_VERSION_CONFLICT': 'stale expected_version',
    **_ASSET_TAIL,
}
MATRIX['fixed-asset schedule'] = {'E_VALIDATION': 'not a fixed-asset item', 'E_RECORD_NOT_FOUND': 'unknown item'}
MATRIX['fixed-asset list'] = {}
_ASSET_POST = {
    'E_RECORD_NOT_FOUND': 'unknown item or account',
    'E_INACTIVE_REFERENCE': 'an inactive account',
    'E_AMOUNT_PRECISION': 'an amount with more decimal places than the currency has',
    'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
    'E_PERIOD_CLOSED': 'an entry date on or before the closing date',
    **_ASSET_TAIL,
}
MATRIX['fixed-asset depreciate'] = dict(_ASSET_POST, E_VALIDATION='an asset named twice, not set up, without a cost basis or without its depreciation accounts')
MATRIX['fixed-asset dispose'] = dict(_ASSET_POST, E_VALIDATION='already disposed of, depreciation through the date not posted, no cost basis or accounts, or proceeds with no deposit_to')
_LOAN = {
    'E_VALIDATION': 'an account of the wrong type, a rate outside 0 through 100, a balloon not below the principal, escrow without an escrow account, or a payment that does not cover the interest',
    'E_RECORD_NOT_FOUND': 'unknown loan, account or vendor',
    'E_INACTIVE_REFERENCE': 'an inactive account or vendor',
    'E_AMOUNT_PRECISION': 'an amount with more decimal places than the currency has',
    'E_VALUE_RANGE': 'an amount beyond signed 64-bit range',
    'E_NAME_TAKEN': 'another loan has that name',
    **_ASSET_TAIL,
}
MATRIX['loan create'] = dict(_LOAN)
MATRIX['loan update'] = dict(_LOAN, E_VERSION_CONFLICT='stale expected_version')
MATRIX['loan show'] = {'E_VALIDATION': 'a payment that does not cover the interest', 'E_RECORD_NOT_FOUND': 'unknown loan'}
MATRIX['loan list'] = {'E_VALIDATION': 'a loan whose payment does not cover the interest'}
MATRIX['loan pay'] = dict(_ASSET_POST, E_VALIDATION='a paid-off loan, extra principal above the balance, extra_only without an amount or date, no payment account, or a number on a card charge',
                          E_DUPLICATE_NUMBER='the check number is already used on the account')
# Assembly builds (R103): an assembly built from its components, posted, pending, edited, voided or deleted.
_BUILD_COMMON = {
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
    'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive',
}
MATRIX['assembly build'] = {
    'E_RECORD_NOT_FOUND': 'unknown assembly item',
    'E_INACTIVE_REFERENCE': 'the assembly item is inactive',
    'E_VALIDATION': 'not an inventory assembly, no bill of materials, a quantity not above zero, components short of the build without pending, a component on hand at no cost, or a change that leaves a later build short',
    'E_VALUE_RANGE': 'a quantity or cost outside signed 64-bit storage',
    'E_PERIOD_CLOSED': 'the build date or a correction date falls in a closed period',
    **_BUILD_COMMON,
}
for _verb, _extra in (('update', 'a deleted or voided build, or components short of the new quantity'),
                      ('finalize', 'a build that is not pending, or components still short'),
                      ('void', 'a build that is not built (a pending build is deleted instead)'),
                      ('delete', 'a build already deleted')):
    MATRIX['assembly ' + _verb] = {
        'E_RECORD_NOT_FOUND': 'no build with that id or journal number',
        'E_INACTIVE_REFERENCE': 'the assembly item is inactive',
        'E_VALIDATION': _extra,
        'E_VALUE_RANGE': 'a quantity or cost outside signed 64-bit storage',
        'E_PERIOD_CLOSED': 'the build date, old or new, or a correction date falls in a closed period',
        'E_VERSION_CONFLICT': 'stale expected_version',
        **_BUILD_COMMON,
    }
MATRIX['assembly show'] = {'E_RECORD_NOT_FOUND': 'no build with that id or journal number'}
MATRIX['assembly list'] = {
    'E_RECORD_NOT_FOUND': 'unknown item filter',
    'E_VALIDATION': 'bad status or limit',
}

# Committed stock (R102): the open-sales-orders report.
MATRIX['report open-sales-orders'] = {
    'E_RECORD_NOT_FOUND': 'unknown customer or item filter',
    'E_QUERY_STALE': 'company audit changed between pages',
}

# Mileage (R118): vehicles, mileage rates, trips, trip billing and reimbursement, mileage reports.
_MILEAGE_TAIL = {'E_IDEMPOTENCY_MISMATCH': 'same key, different input', 'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
                 'E_DIRECTIVE_INACTIVE': 'deactivated --directive', 'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive'}
MATRIX['vehicle create'] = {'E_VALIDATION': 'a blank name', 'E_NAME_TAKEN': 'a vehicle already has the name'} | _MILEAGE_TAIL
MATRIX['vehicle update'] = {'E_VALIDATION': 'a blank name', 'E_RECORD_NOT_FOUND': 'unknown vehicle',
                            'E_NAME_TAKEN': 'another vehicle has the name', 'E_VERSION_CONFLICT': 'stale expected_version'} | _MILEAGE_TAIL
MATRIX['vehicle show'] = {'E_RECORD_NOT_FOUND': 'unknown vehicle'}
MATRIX['vehicle list'] = {}
MATRIX['mileage-rate set'] = {'E_VALIDATION': 'not a rate from 0 through 1000', 'E_AMOUNT_PRECISION': 'more than four decimal places'} | _MILEAGE_TAIL
MATRIX['mileage-rate delete'] = {'E_VALIDATION': 'not a date', 'E_RECORD_NOT_FOUND': 'no rate starts on the date'} | _MILEAGE_TAIL
MATRIX['mileage-rate list'] = {}
_TRIP_WRITE = {'E_VALIDATION': 'one odometer reading, odometer end not above start, total_miles disagreeing with the readings, no miles, a start after the end, or billable without a customer:job or item; a non-service, non-other-charge item',
               'E_RECORD_NOT_FOUND': 'unknown vehicle, customer, item or class',
               'E_INACTIVE_REFERENCE': 'a newly named vehicle, customer, item or class is inactive',
               'E_VALUE_RANGE': 'miles beyond range', 'E_AMOUNT_PRECISION': 'a billing rate finer than the currency allows',
               'E_PREVIEW_STALE': 'the books changed since the preview'} | _MILEAGE_TAIL
MATRIX['trip create'] = dict(_TRIP_WRITE)
MATRIX['trip update'] = _TRIP_WRITE | {'E_VERSION_CONFLICT': 'stale expected_version',
                                       'E_WORK_DEPENDENCY': 'an invoice carries the trip, or a posted check paid it back, and the change would move what they stand on'}
MATRIX['trip void'] = {'E_RECORD_NOT_FOUND': 'unknown trip', 'E_VALIDATION': 'a reason over 140 characters',
                       'E_VERSION_CONFLICT': 'stale expected_version',
                       'E_WORK_DEPENDENCY': 'an invoice carries the trip, or a posted check paid it back'} | _MILEAGE_TAIL
MATRIX['trip show'] = {'E_RECORD_NOT_FOUND': 'unknown trip'}
MATRIX['trip query'] = {'E_RECORD_NOT_FOUND': 'unknown vehicle or customer', 'E_VALIDATION': 'a cursor not from the previous page'}
_TRIP_BILL = {'E_RECORD_NOT_FOUND': 'unknown trip', 'E_VERSION_CONFLICT': 'stale trip expected_version',
              'E_WORK_DEPENDENCY': 'the trip is voided, not billable, or already billed', 'E_CONVERSION_KEY_REUSED': 'the key was used for other work or input',
              'E_PERIOD_CLOSED': 'the sale date is on or before the closing date', 'E_DUPLICATE_NUMBER': 'the sale number is taken',
              'E_INACTIVE_REFERENCE': 'an inactive customer or item', 'E_VALUE_RANGE': 'an amount beyond range',
              'E_AMOUNT_PRECISION': 'an amount finer than the currency allows', 'E_PREVIEW_STALE': 'the trip billing changed since the preview',
              'E_FEATURE_DISABLED': 'a partial billing mode the company turned off'} | _MILEAGE_TAIL
MATRIX['trip invoice'] = dict(_TRIP_BILL)
MATRIX['trip sales-receipt'] = dict(_TRIP_BILL)
MATRIX['trip billing'] = {'E_RECORD_NOT_FOUND': 'unknown trip', 'E_QUERY_STALE': 'billing changed between pages',
                          'E_WORK_DEPENDENCY': 'the trip is voided or not billable'}
MATRIX['trip reimburse'] = {'E_VALIDATION': 'a trip named twice, a voided trip, no mileage rate on a trip end date, or a zero amount',
                            'E_RECORD_NOT_FOUND': 'unknown trip, payee or account', 'E_INACTIVE_REFERENCE': 'an inactive payee or account',
                            'E_WORK_DEPENDENCY': 'a trip already paid back by a posted check', 'E_PERIOD_CLOSED': 'the check date is on or before the closing date',
                            'E_AMOUNT_PRECISION': 'a rate with more than four decimal places', 'E_VALUE_RANGE': 'an amount beyond range',
                            'E_DUPLICATE_NUMBER': 'the check number is taken on the account',
                            'E_PREVIEW_STALE': 'the trips or rates changed since the preview whose fingerprint was given'} | _MILEAGE_TAIL
MATRIX['report mileage-by-vehicle'] = {'E_VALIDATION': 'date_from after date_to', 'E_RECORD_NOT_FOUND': 'unknown vehicle'}
MATRIX['report mileage-by-job'] = {'E_VALIDATION': 'date_from after date_to', 'E_RECORD_NOT_FOUND': 'unknown customer'}

# Price rules (R110).
_PRICE_TAIL = {'E_IDEMPOTENCY_MISMATCH': 'same key, different input', 'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
               'E_DIRECTIVE_INACTIVE': 'deactivated --directive', 'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive'}
_PRICE_WRITE = {'E_VALIDATION': 'no value or breaks, both, breaks not rising, date_to before date_from, a zero min_quantity, or x.99 rounding without cents',
                'E_RECORD_NOT_FOUND': 'an unknown item, category, customer, customer type, class or sales rep',
                'E_INACTIVE_REFERENCE': 'a named item, customer, class or rep is inactive',
                'E_VALUE_RANGE': 'a discount over 100 percent', 'E_AMOUNT_PRECISION': 'an amount finer than the currency allows',
                'E_NAME_TAKEN': 'another rule has the name'} | _PRICE_TAIL
MATRIX['price-rule create'] = dict(_PRICE_WRITE)
MATRIX['price-rule update'] = _PRICE_WRITE | {'E_VERSION_CONFLICT': 'stale expected_version'}
MATRIX['price-rule activate'] = {'E_RECORD_NOT_FOUND': 'unknown rule', 'E_VERSION_CONFLICT': 'stale expected_version'} | _PRICE_TAIL
MATRIX['price-rule deactivate'] = {'E_RECORD_NOT_FOUND': 'unknown rule', 'E_VERSION_CONFLICT': 'stale expected_version'} | _PRICE_TAIL
MATRIX['price-rule show'] = {'E_RECORD_NOT_FOUND': 'unknown rule'}
MATRIX['price-rule list'] = {'E_RECORD_NOT_FOUND': 'unknown item or customer filter'}
MATRIX['price-rule preview'] = {'E_VALIDATION': 'a zero quantity, or an item with no price that no rule prices',
                                'E_RECORD_NOT_FOUND': 'unknown item, customer, class, rep or price level'}
MATRIX['price-rule preferences'] = {}
MATRIX['price-rule set-preferences'] = {'E_VALIDATION': 'not lowest_price or priority'} | _PRICE_TAIL
# Invoicing from sales orders (R101 increment 4).
MATRIX['sales-order invoice'] = {
    **_SALES_WRITE_ERRORS, **_WORK_WRITE_ERRORS,
    'E_VALIDATION': 'nothing on the order can ship by default (each line\'s open quantity and availability are given), a percent or net-amount selection, or an item that no single open line carries',
    'E_WORK_DEPENDENCY': 'the order is voided, closed or inactive, a selected line is already invoiced, or a quantity exceeds what is open',
    'E_CONVERSION_KEY_REUSED': 'permanent key already names a different operational or financial intent',
}
MATRIX['sales-order billing'] = {
    'E_RECORD_NOT_FOUND': 'order absent from selected company',
    'E_QUERY_STALE': 'company audit changed between linked destination pages',
}

# Shortfall coverage (R101 increment 5).
for _noun in ('sales-order', 'work-order'):
    MATRIX[f'{_noun} purchase-order'] = {
        'E_RECORD_NOT_FOUND': 'document, vendor or item absent from selected company',
        'E_VALIDATION': 'nothing is short for this vendor (a retry orders nothing), short lines come from several vendors and none is named, explicit lines without a vendor, or an item with no purchase cost',
        'E_WORK_DEPENDENCY': 'the document is closed, cancelled, voided or inactive and commits nothing',
        'E_INACTIVE_REFERENCE': 'the vendor or an item is inactive',
        'E_VALUE_RANGE': 'a quantity or amount beyond supported bounds',
        'E_AMOUNT_PRECISION': 'a rate with more precision than the currency has',
        'E_DUPLICATE_NUMBER': 'the purchase order number is already used',
        'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
        'E_DIRECTIVE_NOT_FOUND': 'context directive does not exist',
        'E_DIRECTIVE_INACTIVE': 'context directive is inactive',
    }

# Timekeeping (R106): weekly timesheets, the time clock and the job schedule.
_TIME_TAIL = {'E_IDEMPOTENCY_MISMATCH': 'same key, different input', 'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
              'E_DIRECTIVE_INACTIVE': 'deactivated --directive', 'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive'}
_TIME_REFS = {'E_VALIDATION': 'no employee named and the login is not linked to one; an unparseable time',
              'E_RECORD_NOT_FOUND': 'unknown employee, customer, item or class',
              'E_INACTIVE_REFERENCE': 'an inactive employee, customer or item',
              'E_VALUE_RANGE': 'more than 24 hours in a day'}
MATRIX['timesheet show'] = {'E_RECORD_NOT_FOUND': 'unknown employee', 'E_VALIDATION': 'no employee named and the login is not linked to one'}
MATRIX['timesheet copy-last'] = dict(MATRIX['timesheet show'])
MATRIX['timesheet save'] = _TIME_REFS | {'E_VALIDATION': 'two rows with the same customer:job, item, class, billable and notes; hours that are not a decimal; no employee and no linked login',
                                         'E_VERSION_CONFLICT': 'expected_sheet_version no longer matches the week',
                                         'E_WORK_DEPENDENCY': 'a change to hours a posted sale carries'} | _TIME_TAIL
MATRIX['timesheet preferences'] = {}
MATRIX['timesheet set-preferences'] = {'E_VALIDATION': 'a week day, rounding or maximum shift outside the choices'} | _TIME_TAIL
MATRIX['clock in'] = _TIME_REFS | {'E_VALIDATION': 'already clocked in; a start in the future or longer ago than the maximum shift'} | _TIME_TAIL
MATRIX['clock out'] = _TIME_REFS | {'E_VALIDATION': 'past the maximum shift with no stated end; an end before clock-in, in the future or longer than the maximum shift; no customer:job or item',
                                    'E_RECORD_NOT_FOUND': 'the person is not clocked in'} | _TIME_TAIL
MATRIX['clock cancel'] = {'E_VALIDATION': 'a reason over 140 characters', 'E_RECORD_NOT_FOUND': 'the person is not clocked in'} | _TIME_TAIL
MATRIX['clock status'] = {'E_RECORD_NOT_FOUND': 'unknown employee'}
MATRIX['clock link'] = {'E_VALIDATION': 'an agent login, or a login already linked to another employee',
                        'E_RECORD_NOT_FOUND': 'unknown employee or user'} | _TIME_TAIL
MATRIX['schedule create'] = {'E_VALIDATION': 'no customer:job or work order, a time not YYYY-MM-DDTHH:MM, an end not after the start',
                             'E_RECORD_NOT_FOUND': 'unknown employee, customer or work order',
                             'E_INACTIVE_REFERENCE': 'an inactive employee'} | _TIME_TAIL
MATRIX['schedule update'] = MATRIX['schedule create'] | {'E_VERSION_CONFLICT': 'stale expected_version'}
MATRIX['schedule cancel'] = {'E_RECORD_NOT_FOUND': 'unknown entry', 'E_VERSION_CONFLICT': 'stale expected_version'} | _TIME_TAIL
MATRIX['schedule show'] = {'E_RECORD_NOT_FOUND': 'unknown entry'}
MATRIX['schedule calendar'] = {'E_RECORD_NOT_FOUND': 'unknown employee or customer', 'E_VALIDATION': 'not a date'}
# Inventory sites and transfers (R104 increment 1).
_SITE_COMMON = {
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'context directive does not exist',
    'E_DIRECTIVE_INACTIVE': 'context directive is inactive',
}
MATRIX['inventory-site create'] = {'E_VALIDATION': 'a blank name', 'E_NAME_TAKEN': 'another site has the name', **_SITE_COMMON}
for _verb in ('update', 'activate', 'deactivate'):
    MATRIX[f'inventory-site {_verb}'] = {
        'E_VALIDATION': 'the default site deactivated, or an inactive site made the default',
        'E_RECORD_NOT_FOUND': 'site absent from selected company',
        'E_NAME_TAKEN': 'another site has the name',
        'E_VERSION_CONFLICT': 'the site changed since expected_version',
        **_SITE_COMMON}
MATRIX['inventory-site deactivate']['E_RECORD_IN_USE'] = 'the site still holds stock'
MATRIX['inventory-site show'] = {'E_RECORD_NOT_FOUND': 'site absent from selected company', 'E_VALIDATION': 'an unknown site name'}
MATRIX['inventory-site list'] = {}
for _verb in ('post', 'update', 'void'):
    MATRIX[f'inventory-transfer {_verb}'] = {
        'E_VALIDATION': 'an unknown site (the sites are listed), the same site twice, or a non-stock item',
        'E_RECORD_NOT_FOUND': 'transfer or item absent from selected company',
        'E_INACTIVE_REFERENCE': 'an inactive site or item',
        'E_VALUE_RANGE': 'a quantity beyond supported bounds',
        'E_PERIOD_CLOSED': 'a date on or before the closing date',
        'E_DUPLICATE_NUMBER': 'the transfer number is already used',
        **_SITE_COMMON}
MATRIX['inventory-transfer update']['E_VERSION_CONFLICT'] = 'the transfer changed since expected_version'
MATRIX['inventory-transfer void']['E_VERSION_CONFLICT'] = 'the transfer changed since expected_version'
MATRIX['inventory-transfer void']['E_REASON_REQUIRED'] = 'no reason given'
for _verb in ('show', 'history'):
    MATRIX[f'inventory-transfer {_verb}'] = {'E_RECORD_NOT_FOUND': 'transfer absent from selected company'}
MATRIX['inventory-transfer query'] = {'E_RECORD_NOT_FOUND': 'item absent from selected company', 'E_VALIDATION': 'an unknown site name'}
MATRIX['report quantity-by-site'] = {
    'E_QUERY_STALE': 'the books changed between pages',
    'E_VALUE_RANGE': 'a limit beyond supported bounds',
    'E_VALIDATION': 'an unknown site name',
}
# Accountant review (R121).
_ACCT_TAIL = {'E_DIRECTIVE_NOT_FOUND': 'unknown --directive', 'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
_ACCT_KEY = {'E_IDEMPOTENCY_MISMATCH': 'same key, different input'}
_ACCT_PERSON = {'E_PERMISSION': 'an agent: a person\'s act'}
_ACCT_ONLY = {'E_PERMISSION': 'not the designated external accountant, and not working in an accountant\'s copy'}
_ACCT_JOURNAL = {'E_RECORD_NOT_FOUND': 'unknown transaction or account', 'E_UNBALANCED_ENTRY': 'the entry does not balance',
                 'E_PERIOD_CLOSED': 'dated in a closed period, after the review period or dividing date, or on or before the dividing date while a copy is out',
                 'E_DUPLICATE_NUMBER': 'the entry number is taken', 'E_INACTIVE_REFERENCE': 'an account, name or class is inactive',
                 'E_VALUE_RANGE': 'an amount beyond supported bounds', 'E_AMOUNT_PRECISION': 'an amount finer than the currency allows'}
MATRIX['accountant add'] = {'E_RECORD_NOT_FOUND': 'unknown user', 'E_VALIDATION': 'an agent, an inactive user, or a role on the company other than standard (none, read-only, admin or owner)'} | _ACCT_PERSON | _ACCT_KEY | _ACCT_TAIL
MATRIX['accountant remove'] = {'E_RECORD_NOT_FOUND': 'unknown user, or never designated'} | _ACCT_PERSON | _ACCT_KEY | _ACCT_TAIL
MATRIX['accountant list'] = {}
MATRIX['accountant reclassify'] = _ACCT_JOURNAL | {'E_VALIDATION': 'a voided entry, the same account twice, an entry posting nothing to from_account, or an amount over what it posts there'} | _ACCT_ONLY | _ACCT_KEY | _ACCT_TAIL
MATRIX['accountant mark-entry'] = {'E_RECORD_NOT_FOUND': 'unknown transaction', 'E_VALIDATION': 'not on the entries-to-review list'} | {'E_PERMISSION': 'an agent, or not the external accountant'} | _ACCT_KEY | _ACCT_TAIL
MATRIX['accountant reviewed-through'] = {'E_VALIDATION': 'after the review period end or the dividing date'} | _ACCT_ONLY | _ACCT_KEY | _ACCT_TAIL
MATRIX['accountant-copy export'] = {'E_VALIDATION': 'a copy is already out, the dividing date is on or before the closing date, or no single designated accountant',
                                    'E_PERMISSION': "these books are an accountant's copy", 'E_IO': 'the archive could not be written',
                                    'E_BACKUP_INVALID': 'the written archive did not verify'} | _ACCT_TAIL
MATRIX['accountant-copy status'] = {}
MATRIX['accountant-copy cancel'] = {'E_RECORD_NOT_FOUND': 'no copy is out', 'E_REASON_REQUIRED': 'no reason given'} | _ACCT_PERSON | _ACCT_TAIL
MATRIX['accountant-copy changes'] = {'E_PERMISSION': "these books are not an accountant's copy", 'E_IO': 'the change file could not be written'} | _ACCT_TAIL
MATRIX['accountant-copy preview'] = {'E_VALIDATION': "no such file, not a change file, another company's, an unknown copy, tampered, its copy cancelled or returned, or its key not in these books"}
MATRIX['accountant-copy import'] = _ACCT_JOURNAL | {'E_VALIDATION': "the file is refused (see preview), a chosen change is unknown or cannot be taken, or it takes nothing without close_copy"} | _ACCT_PERSON | _ACCT_TAIL
MATRIX['report accountant-review'] = {'E_VALIDATION': 'as_of is not a date'}
MATRIX['report accountant-changes'] = {}
# Check printing (R152): the print queue, print runs, "Did checks print OK?", reprints and alignment.
_PRINT_TAIL = {'E_IDEMPOTENCY_MISMATCH': 'same key, different input', 'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
               'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
MATRIX['check print-queue'] = {'E_RECORD_NOT_FOUND': 'unknown account', 'E_VALIDATION': 'not a bank account'}
MATRIX['check print'] = {'E_RECORD_NOT_FOUND': 'unknown account', 'E_VALIDATION': 'nothing queued, a check not queued on the account, or a partial first sheet of voucher checks',
                         'E_DUPLICATE_NUMBER': 'a number in the run is already used, retired, spoiled or in the printer',
                         'E_PERIOD_CLOSED': 'a queued check dated on or before the closing date',
                         'E_VALUE_RANGE': 'the run would pass the largest check number'} | _PRINT_TAIL
MATRIX['check print-confirm'] = {'E_RECORD_NOT_FOUND': 'unknown run', 'E_VALIDATION': 'already answered, or a failed number not in the run',
                                 'E_PERMISSION': 'an agent: what printed is a person\'s to say'} | _PRINT_TAIL
MATRIX['check print-cancel'] = {'E_RECORD_NOT_FOUND': 'unknown run', 'E_VALIDATION': 'already answered',
                                'E_PERMISSION': 'an agent'} | _PRINT_TAIL
MATRIX['check reprint'] = {'E_RECORD_NOT_FOUND': 'not a check or bill payment', 'E_VALIDATION': 'no number yet, voided, or in a print run',
                           'E_DUPLICATE_NUMBER': 'the new number is used on the account', 'E_PERIOD_CLOSED': 'a new number for a closed-period check',
                           'E_VERSION_CONFLICT': 'stale expected_version', 'E_VALUE_RANGE': 'past the largest check number',
                           'E_PERMISSION': 'an agent'} | _PRINT_TAIL
MATRIX['check print-run'] = {'E_RECORD_NOT_FOUND': 'unknown run'}
MATRIX['check print-pdf'] = {'E_RECORD_NOT_FOUND': 'unknown run'}
MATRIX['check print-setup'] = {'E_RECORD_NOT_FOUND': 'unknown account', 'E_VALIDATION': 'not a bank account, or an offset beyond 2 inches',
                               'E_VERSION_CONFLICT': 'stale expected_version'} | _PRINT_TAIL
MATRIX['check print-alignment'] = {'E_RECORD_NOT_FOUND': 'unknown account', 'E_VALIDATION': 'not a bank account, or a partial voucher sheet'}
# Barcodes (R104 increment 3): what a code names, and numbering items that have none.
MATRIX['barcode lookup'] = {'E_VALIDATION': 'no code, or as_of is not a date'}
MATRIX['barcode generate'] = {'E_RECORD_NOT_FOUND': 'a named item is unknown', 'E_INACTIVE_REFERENCE': 'a named item is inactive',
                              'E_VALIDATION': 'a named code is on two items', 'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
                              'E_DIRECTIVE_NOT_FOUND': 'unknown --directive', 'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
# Physical counts (R104 increment 2).
MATRIX['inventory-count create'] = {
    'E_VALIDATION': 'an unknown site (the sites are listed), a non-stock item, an unknown bin or a site that does not track bins, a cycle count with nothing due, or nothing to count',
    'E_RECORD_NOT_FOUND': 'item or category absent from selected company',
    'E_INACTIVE_REFERENCE': 'an inactive site',
    'E_PERIOD_CLOSED': 'a count date on or before the closing date',
    'E_DUPLICATE_NUMBER': 'the count number is already used',
    **_SITE_COMMON}
MATRIX['inventory-count enter'] = {
    'E_VALIDATION': 'a count that is not open, a non-stock item, an unknown or ambiguous code, a negative quantity, an unknown bin, a lot or serial number of a kind the item does not track, a serial number the books hold at another site, or a serial number counted other than 0 or 1',
    'E_RECORD_NOT_FOUND': 'count or item absent from selected company',
    **_SITE_COMMON}
MATRIX['inventory-count post'] = {
    'E_VALIDATION': 'a count that is not open, nothing counted, an unusable adjustment account, a place another recorded count of that date counted, or differences worth nothing',
    'E_RECORD_NOT_FOUND': 'count, account or class absent from selected company',
    'E_INACTIVE_REFERENCE': 'an inactive account or class',
    'E_PERIOD_CLOSED': 'a count date on or before the closing date',
    'E_VERSION_CONFLICT': 'the count changed since expected_version',
    'E_PREVIEW_STALE': 'no preview_fingerprint, or what would post changed since the dry run',
    'E_UNBALANCED_ENTRY': 'never in practice: the difference entry balances by construction',
    **_SITE_COMMON}
MATRIX['inventory-count cancel'] = {
    'E_VALIDATION': 'a count that is not open',
    'E_RECORD_NOT_FOUND': 'count absent from selected company',
    'E_VERSION_CONFLICT': 'the count changed since expected_version',
    'E_REASON_REQUIRED': 'no reason given',
    **_SITE_COMMON}
MATRIX['inventory-count void'] = MATRIX['inventory-count cancel'] | {
    'E_VALIDATION': 'a count that is not recorded',
    'E_PERIOD_CLOSED': 'a count date on or before the closing date'}
for _verb in ('show', 'sheet'):
    MATRIX[f'inventory-count {_verb}'] = {'E_RECORD_NOT_FOUND': 'count absent from selected company',
                                          'E_VALIDATION': 'a malformed selector'}
MATRIX['inventory-count query'] = {'E_RECORD_NOT_FOUND': 'site absent from selected company', 'E_VALIDATION': 'an unknown site name or a malformed date'}
MATRIX['report physical-inventory-worksheet'] = {
    'E_QUERY_STALE': 'the books changed between pages',
    'E_VALUE_RANGE': 'a limit beyond supported bounds',
    'E_VALIDATION': 'an unknown site name',
}
MATRIX['report inventory-valuation-detail'] = {
    'E_QUERY_STALE': 'the books changed between pages',
    'E_VALUE_RANGE': 'a limit beyond supported bounds',
    'E_VALIDATION': 'a malformed date',
    'E_RECORD_NOT_FOUND': 'item absent from selected company',
}
# Unattended scheduling and reminders (R111): to-dos, overdue notices and the Reminders list.
_SCHED_TAIL = {
    'E_REASON_REQUIRED': 'an agent/system write supplies neither reason nor directive',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['todo create'] = {
    'E_VALIDATION': 'a blank title, an assignee who is not a member of the company, or link without link_type',
    'E_RECORD_NOT_FOUND': 'unknown assignee or linked record',
    **_SCHED_TAIL,
}
MATRIX['todo update'] = {
    'E_VALIDATION': 'a blank title, a null due date, nothing to change, or an assignee who is not a member',
    'E_RECORD_NOT_FOUND': 'unknown to-do, assignee or linked record',
    'E_VERSION_CONFLICT': 'stale expected_version',
    **_SCHED_TAIL,
}
MATRIX['todo complete'] = {
    'E_VALIDATION': 'the to-do is already done',
    'E_RECORD_NOT_FOUND': 'unknown to-do',
    'E_VERSION_CONFLICT': 'stale expected_version',
    **_SCHED_TAIL,
}
MATRIX['todo show'] = {'E_VALIDATION': 'malformed selector', 'E_RECORD_NOT_FOUND': 'unknown to-do'}
MATRIX['todo list'] = {'E_VALIDATION': 'link without link_type', 'E_RECORD_NOT_FOUND': 'unknown assignee or linked record'}
MATRIX['notice generate'] = {'E_VALIDATION': 'overdue notices are off (no overdue_notice_days)', **_SCHED_TAIL}
MATRIX['notice list'] = {'E_VALIDATION': 'malformed filter', 'E_RECORD_NOT_FOUND': 'unknown customer'}
MATRIX['notice show'] = {'E_VALIDATION': 'malformed selector', 'E_RECORD_NOT_FOUND': 'unknown notice'}
_NOTICE_WRITE = {
    'E_VALIDATION': 'the notice is not ready to send (already sent or dismissed)',
    'E_WORK_DEPENDENCY': 'an email message carrying the notice is still in the outbox',
    'E_RECORD_NOT_FOUND': 'unknown notice',
    'E_VERSION_CONFLICT': 'stale expected_version',
    **_SCHED_TAIL,
}
MATRIX['notice mark-sent'] = dict(_NOTICE_WRITE)
MATRIX['notice dismiss'] = dict(_NOTICE_WRITE)
MATRIX['reminders'] = {'E_VALIDATION': 'a malformed as_of date'}
# R111 review X6: a person's entry names the revision it will post; a template changed since the preview is refused.
for _name in ('memorized enter', 'memorized process', 'memorized retry', 'memorized-group enter'):
    MATRIX[_name]['E_PREVIEW_STALE'] = 'a template was changed after the preview that named the revision to post'
# Serial and lot numbers (R104 increment 4).
MATRIX['inventory assign'] = {
    'E_VALIDATION': 'an untracked item, numbers of the wrong kind, a serial number named twice, already in stock or already gone, more units than the place (or the document line) holds unassigned, an unknown or ambiguous document, or a number not held there to unassign',
    'E_RECORD_NOT_FOUND': 'item or site absent from selected company',
    'E_INACTIVE_REFERENCE': 'an inactive site',
    'E_PERIOD_CLOSED': 'a date on or before the closing date',
    **_SITE_COMMON}
for _noun in ('lot', 'serial-number'):
    MATRIX[f'{_noun} update'] = {
        'E_VALIDATION': 'a number two items share without the item named',
        'E_RECORD_NOT_FOUND': 'no such number',
        'E_VERSION_CONFLICT': 'the number changed since expected_version',
        **_SITE_COMMON}
    for _verb in ('show', 'list'):
        MATRIX[f'{_noun} {_verb}'] = {'E_RECORD_NOT_FOUND': 'number, item or site absent from selected company',
                                      'E_VALIDATION': 'a number two items share without the item named, or an unknown site'}
for _report in ('serial-lot-stock', 'serial-lot-history', 'lot-trace'):
    MATRIX[f'report {_report}'] = {'E_RECORD_NOT_FOUND': 'number, item or site absent from selected company',
                                   'E_VALIDATION': 'a number that is both a serial number and a lot without kind, or an unknown site'}
# Bins and cycle counts (R104 increment 5).
MATRIX['bin create'] = {
    'E_VALIDATION': 'a blank name or one with a colon, a site that does not track bins, a fourth level, or a label another active bin carries',
    'E_NAME_TAKEN': 'a bin of that full name at the site',
    'E_RECORD_NOT_FOUND': 'site absent from selected company',
    'E_INACTIVE_REFERENCE': 'an inactive site or parent bin',
    **_SITE_COMMON}
MATRIX['bin update'] = {
    'E_VALIDATION': 'an unknown or ambiguous bin, a blank name, or a label another active bin carries',
    'E_NAME_TAKEN': 'a bin of that full name at the site',
    'E_RECORD_NOT_FOUND': 'site absent from selected company',
    'E_VERSION_CONFLICT': 'the bin changed since expected_version',
    **_SITE_COMMON}
for _verb in ('activate', 'deactivate'):
    MATRIX[f'bin {_verb}'] = {
        'E_VALIDATION': 'an unknown or ambiguous bin, a parent that is inactive, or a label another active bin now carries',
        'E_RECORD_NOT_FOUND': 'site absent from selected company',
        'E_VERSION_CONFLICT': 'the bin changed since expected_version',
        **({'E_RECORD_IN_USE': 'the bin or a bin inside it holds stock, or active bins sit inside it'} if _verb == 'deactivate' else {}),
        **_SITE_COMMON}
MATRIX['bin show'] = {'E_VALIDATION': 'an unknown or ambiguous bin', 'E_RECORD_NOT_FOUND': 'site absent from selected company'}
MATRIX['bin list'] = {'E_VALIDATION': 'an unknown site'}
MATRIX['report items-by-bin'] = {'E_VALIDATION': 'an unknown site or bin', 'E_RECORD_NOT_FOUND': 'item absent from selected company',
                                 'E_INTERNAL': 'placements disagree with the stock ledger (the quantity tie)'}
MATRIX['cycle-class create'] = {'E_VALIDATION': 'a blank name or an interval outside 1-3660 days', 'E_NAME_TAKEN': 'another class has the name', **_SITE_COMMON}
MATRIX['cycle-class update'] = {'E_VALIDATION': 'an unknown class, a blank name or an interval outside 1-3660 days',
                                'E_NAME_TAKEN': 'another class has the name', 'E_RECORD_NOT_FOUND': 'class absent',
                                'E_VERSION_CONFLICT': 'the class changed since expected_version', **_SITE_COMMON}
for _verb in ('activate', 'deactivate'):
    MATRIX[f'cycle-class {_verb}'] = {
        'E_VALIDATION': 'an unknown class', 'E_RECORD_NOT_FOUND': 'class absent',
        'E_VERSION_CONFLICT': 'the class changed since expected_version',
        **({'E_RECORD_IN_USE': 'items are still in the class'} if _verb == 'deactivate' else {}),
        **_SITE_COMMON}
MATRIX['cycle-class show'] = {'E_VALIDATION': 'an unknown class', 'E_RECORD_NOT_FOUND': 'class absent'}
MATRIX['cycle-count classify'] = {
    'E_VALIDATION': 'a share outside 0-100, shares passing 100, a period that ends before it starts, or an unknown class',
    'E_RECORD_NOT_FOUND': 'class absent', 'E_INACTIVE_REFERENCE': 'an inactive class', **_SITE_COMMON}
MATRIX['cycle-count due'] = {'E_VALIDATION': 'an unknown site or a malformed date', 'E_RECORD_NOT_FOUND': 'site absent from selected company'}

# Landed cost (R104 increment 6).
_LANDED_WRITE = {
    'E_VALIDATION': 'no clearing account, a charge or target not found among what may take part, nothing left to '
                    'allocate, more than is unallocated, charges and credits mixed, a target weighing nothing, a date '
                    'before a target receipt, a freight credit taking a receipt below zero, stock that cannot stand',
    'E_RECORD_NOT_FOUND': 'unknown allocation, item or account',
    'E_INACTIVE_REFERENCE': 'inactive clearing or target account',
    'E_PERIOD_CLOSED': 'the allocation date or a true-up or void date falls in a closed period',
    'E_WORK_DEPENDENCY': 'voiding an allocation a later freight credit needs',
    'E_DUPLICATE_NUMBER': 'the supplied allocation number is already used',
    'E_REASON_REQUIRED': 'a write without --reason',
    'E_VALUE_RANGE': 'an amount outside signed 64-bit storage',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['landed-cost setup'] = {'E_VALIDATION': 'invalid input', 'E_REASON_REQUIRED': 'a write without --reason',
                               'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
                               'E_DIRECTIVE_NOT_FOUND': 'unknown --directive', 'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
MATRIX['landed-cost charges'] = {'E_VALIDATION': 'the clearing account does not equal the unallocated charges on a date',
                                 'E_RECORD_NOT_FOUND': 'unknown vendor'}
MATRIX['landed-cost post'] = dict(_LANDED_WRITE)
MATRIX['landed-cost void'] = dict(_LANDED_WRITE)
MATRIX['landed-cost show'] = {'E_RECORD_NOT_FOUND': 'unknown allocation', 'E_VALIDATION': 'invalid input'}
MATRIX['landed-cost query'] = {'E_RECORD_NOT_FOUND': 'unknown document', 'E_VALIDATION': 'invalid input'}

# FIFO costing (R104 increment 7).
_COSTING_WRITE = {
    'E_VALIDATION': 'a date not after the last switch, the method already in force on that date, a switch whose date '
                    'has come (withdraw), a correction that cannot stand',
    'E_RECORD_NOT_FOUND': 'unknown switch (withdraw), an item a correction names',
    'E_PERIOD_CLOSED': 'the switch date, or a correction date, on or before the closing date',
    'E_PERMISSION': 'an agent: changing the costing method is a person\'s act',
    'E_REASON_REQUIRED': 'a write without --reason',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['costing-method set'] = dict(_COSTING_WRITE)
MATRIX['costing-method withdraw'] = dict(_COSTING_WRITE)
MATRIX['costing-method show'] = {'E_VALIDATION': 'invalid input'}
MATRIX['report inventory-layers'] = {'E_VALIDATION': 'invalid input', 'E_RECORD_NOT_FOUND': 'unknown item'}

# The verified history watermark (R180 stage 2).
MATRIX['company verify'] = {'E_RECORD_NOT_FOUND': 'unknown account', 'E_VALIDATION': 'invalid input'}
# Shipments (R181 increment 1).
_SHIPMENT_WRITE = {
    'E_VALIDATION': 'a line not on the order, more than it has to ship, another site, nothing to ship, units worth nothing',
    'E_RECORD_NOT_FOUND': 'unknown order, shipment, line, item, bin, site or ship method',
    'E_INACTIVE_REFERENCE': 'an inactive site or item',
    'E_VALUE_RANGE': 'a quantity outside storage bounds',
    'E_PERIOD_CLOSED': 'the ship date, or a cost correction or cost move it owes, on or before the closing date',
    'E_VERSION_CONFLICT': 'a stale expected_version',
    'E_WORK_DEPENDENCY': 'a closed or voided order, an invoice billing the shipment (below its quantity, or a void), or a return giving back its units',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['shipment post'] = dict(_SHIPMENT_WRITE, E_FEATURE_DISABLED='--pick-list, --shipment or --packages while pick and pack is off',
                               E_CONVERSION_KEY_REUSED='--invoice: the derived invoice key reused for other input',
                               E_AMOUNT_PRECISION='freight amount with too many decimals',
                               E_DUPLICATE_NUMBER='--invoice: the invoice number taken')
MATRIX['shipment update'] = dict(_SHIPMENT_WRITE)
MATRIX['shipment void'] = dict(_SHIPMENT_WRITE, E_REASON_REQUIRED='a void without --reason')
# Shipment returns (R181 increment 2).
_SHIPMENT_RETURN_WRITE = {
    'E_VALIDATION': 'more than is out and not invoiced, a date before the ship date, a serial number the shipment did not take, nothing to return, units worth nothing',
    'E_RECORD_NOT_FOUND': 'unknown shipment, return, line, item or bin',
    'E_INACTIVE_REFERENCE': 'an inactive site or item',
    'E_VALUE_RANGE': 'a quantity outside storage bounds',
    'E_PERIOD_CLOSED': 'the return date, or a cost move it owes, on or before the closing date',
    'E_VERSION_CONFLICT': 'a stale expected_version',
    'E_WORK_DEPENDENCY': 'the reopened units shipped or billed since, or an order no longer carrying the cancellation',
    'E_REASON_REQUIRED': 'a void without --reason',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['shipment return'] = dict(_SHIPMENT_RETURN_WRITE)
MATRIX['shipment-return void'] = dict(_SHIPMENT_RETURN_WRITE)
MATRIX['shipment-return show'] = {'E_RECORD_NOT_FOUND': 'unknown shipment return'}
MATRIX['shipment-return query'] = {'E_RECORD_NOT_FOUND': 'unknown shipment or customer', 'E_VALIDATION': 'invalid input'}
MATRIX['shipment show'] = {'E_RECORD_NOT_FOUND': 'unknown shipment'}
MATRIX['shipment history'] = {'E_RECORD_NOT_FOUND': 'unknown shipment'}
MATRIX['shipment query'] = {'E_RECORD_NOT_FOUND': 'unknown order or customer', 'E_VALIDATION': 'invalid input'}
# Draft shipments and packing (R181 increment 4): an optional layer, off until the company turns it on.
_PACK_WRITE = {
    'E_VALIDATION': 'nothing to ship, a draft not open, more in boxes than a line ships, a serial number the line does not ship',
    'E_RECORD_NOT_FOUND': 'unknown shipment, order, item or site',
    'E_FEATURE_DISABLED': 'pick and pack is off for the company (company update --pick-and-pack-enabled true)',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['shipment create'] = dict(_PACK_WRITE, E_INACTIVE_REFERENCE='an inactive site or item',
                                 E_VALUE_RANGE='a quantity out of range', E_WORK_DEPENDENCY='a closed order, a pick list holding it')
MATRIX['shipment pack'] = dict(_PACK_WRITE, E_VERSION_CONFLICT='a stale expected_version')
MATRIX['shipment cancel'] = dict(_PACK_WRITE, E_VERSION_CONFLICT='a stale expected_version')
# Pick lists (R181 increment 3): an optional layer, off until the company turns it on.
_PICK_WRITE = {
    'E_VALIDATION': 'nothing to pick, an order not open, an item not on the list, a serial number held elsewhere or picked on another list, a list not open',
    'E_RECORD_NOT_FOUND': 'unknown pick list, order, item, bin or site',
    'E_INACTIVE_REFERENCE': 'an inactive site or item',
    'E_FEATURE_DISABLED': 'picking is off for the company (company update --pick-and-pack-enabled true)',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['pick-list create'] = dict(_PICK_WRITE)
MATRIX['pick-list enter'] = dict(_PICK_WRITE)
MATRIX['pick-list close'] = dict(_PICK_WRITE, E_VERSION_CONFLICT='a stale expected_version')
MATRIX['pick-list cancel'] = dict(_PICK_WRITE, E_VERSION_CONFLICT='a stale expected_version',
                                  E_REASON_REQUIRED='a cancel without --reason')
MATRIX['pick-list show'] = {'E_RECORD_NOT_FOUND': 'unknown pick list', 'E_FEATURE_DISABLED': 'picking is off'}
MATRIX['pick-list query'] = {'E_RECORD_NOT_FOUND': 'unknown site or order', 'E_VALIDATION': 'invalid input',
                             'E_FEATURE_DISABLED': 'picking is off'}
# Imports and reversible batches (R107 increments 0 and 1).
_IMPORT_WRITE = {
    'E_VALIDATION': 'no file or two, an unknown kind or column or path, an unreadable file, a run cancelled or done',
    'E_RECORD_NOT_FOUND': 'unknown run or attachment',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['import preview'] = dict(_IMPORT_WRITE, E_IO='an attachment body collected',
                                E_AMOUNT_PRECISION='more decimals than the currency allows (per row, as a refusal)')
MATRIX['import run'] = dict(_IMPORT_WRITE)
MATRIX['import cancel'] = dict(_IMPORT_WRITE)
MATRIX['import reverse'] = dict(_IMPORT_WRITE, E_BATCH_BLOCKED='a record edited since or in use, a later live batch, '
                                'or an inverse command the caller may not run',
                                E_VERSION_CONFLICT='the batch changed since the dry run that gave --fingerprint')
MATRIX['import kinds'] = {}
MATRIX['import template'] = {'E_VALIDATION': 'unknown kind'}
for _name in ('import show', 'import query', 'import rows', 'import errors', 'batch show', 'batch query'):
    MATRIX[_name] = {'E_VALIDATION': 'invalid input', 'E_RECORD_NOT_FOUND': 'unknown run or batch'}
# R107 increment 2 and Kabe's rules.
MATRIX['import check'] = {'E_VALIDATION': 'no file or two, an unknown kind or column, an unreadable or non-text file',
                          'E_RECORD_NOT_FOUND': 'unknown attachment or saved mapping', 'E_IO': 'an attachment body collected'}
MATRIX['import decide'] = dict(_IMPORT_WRITE)
MATRIX['import-mapping save'] = dict(_IMPORT_WRITE, E_VERSION_CONFLICT='the source was saved meanwhile')
MATRIX['import-mapping deactivate'] = dict(_IMPORT_WRITE, E_VERSION_CONFLICT='the source changed meanwhile')
for _name in ('import-mapping show', 'import-mapping history'):
    MATRIX[_name] = {'E_VALIDATION': 'invalid input', 'E_RECORD_NOT_FOUND': 'unknown source or version'}
MATRIX['import-mapping query'] = {}
MATRIX['import-mapping match'] = dict(MATRIX['import check'])
MATRIX['report shipped-not-invoiced'] = {'E_RECORD_NOT_FOUND': 'unknown customer', 'E_VALIDATION': 'invalid input'}
MATRIX['report order-fulfillment'] = {'E_RECORD_NOT_FOUND': 'unknown site or customer',
                                     'E_VALIDATION': 'an unknown status', 'E_QUERY_STALE': 'books changed between pages'}
MATRIX['report shipments'] = {'E_RECORD_NOT_FOUND': 'unknown customer, site or ship method',
                              'E_VALIDATION': 'date_from after date_to', 'E_QUERY_STALE': 'books changed between pages'}
MATRIX['shipment invoice'] = {
    'E_VALIDATION': 'an invoice dated before the ship date, nothing left to invoice',
    'E_RECORD_NOT_FOUND': 'unknown shipment or account',
    'E_VERSION_CONFLICT': 'the order changed while the invoice was made',
    'E_WORK_DEPENDENCY': 'a voided or closed order',
    'E_CONVERSION_KEY_REUSED': 'a conversion key used for another request',
    'E_PERIOD_CLOSED': 'the invoice date on or before the closing date',
    'E_DUPLICATE_NUMBER': 'the supplied invoice number is already used',
    'E_INACTIVE_REFERENCE': 'an inactive customer, item or account',
    'E_VALUE_RANGE': 'an amount outside storage bounds',
    'E_AMOUNT_PRECISION': 'an amount with too many decimals',
    'E_PREVIEW_STALE': 'a stale expected_facts_fingerprint',
    'E_FEATURE_DISABLED': 'a disabled billing mode',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
# Document delivery (R112 increment 1): messages and the outbox.
_DELIVERY_TAIL = {'E_IDEMPOTENCY_MISMATCH': 'same key, different input', 'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
                  'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
MATRIX['delivery draft'] = {
    'E_VALIDATION': 'a voided document, a notice not ready, a statement without its period, a bad address',
    'E_RECORD_NOT_FOUND': 'unknown document, customer, notice or template',
    'E_INACTIVE_REFERENCE': 'an inactive template',
    'E_WORK_DEPENDENCY': 'the document is already in a live message or was sent (use --again)', **_DELIVERY_TAIL}
MATRIX['delivery update'] = {'E_VALIDATION': 'a bad address or nothing to change', 'E_RECORD_NOT_FOUND': 'unknown message',
                             'E_WORK_DEPENDENCY': 'the message is not a draft or held',
                             'E_VERSION_CONFLICT': 'stale expected_version', **_DELIVERY_TAIL}
MATRIX['delivery send'] = {'E_VALIDATION': 'a message with no To address, or batch with messages',
                           'E_RECORD_NOT_FOUND': 'unknown message',
                           'E_WORK_DEPENDENCY': 'the message is not a draft, or the batch is cancelled',
                           'E_VERSION_CONFLICT': 'changed while releasing', **_DELIVERY_TAIL}
MATRIX['delivery cancel'] = {'E_RECORD_NOT_FOUND': 'unknown message', 'E_WORK_DEPENDENCY': 'already sent or cancelled',
                             'E_VERSION_CONFLICT': 'stale expected_version', 'E_REASON_REQUIRED': 'no --reason',
                             **_DELIVERY_TAIL}
MATRIX['delivery mark-sent'] = {'E_VALIDATION': 'sent_at in the future or malformed',
                                'E_RECORD_NOT_FOUND': 'unknown message',
                                'E_WORK_DEPENDENCY': 'already sent or cancelled',
                                'E_VERSION_CONFLICT': 'stale expected_version',
                                'E_PERMISSION': "an agent its principal has not granted delivery.mark-sent", **_DELIVERY_TAIL}
MATRIX['delivery export'] = {'E_RECORD_NOT_FOUND': 'unknown message', 'E_WORK_DEPENDENCY': 'a cancelled message',
                             'E_VERSION_CONFLICT': 'changed while exporting', **_DELIVERY_TAIL}
MATRIX['delivery show'] = {'E_RECORD_NOT_FOUND': 'unknown message'}
MATRIX['delivery history'] = {'E_RECORD_NOT_FOUND': 'unknown message'}
MATRIX['delivery query'] = {'E_RECORD_NOT_FOUND': 'unknown customer', 'E_VALIDATION': 'invalid filter'}
MATRIX['delivery-template create'] = {'E_VALIDATION': 'an unknown merge field, nested {{if}}, or a name in use',
                                      **_DELIVERY_TAIL}
MATRIX['delivery-template update'] = {'E_VALIDATION': 'an unknown merge field or nothing to change',
                                      'E_RECORD_NOT_FOUND': 'unknown template',
                                      'E_VERSION_CONFLICT': 'stale expected_version', **_DELIVERY_TAIL}
MATRIX['delivery-template deactivate'] = {'E_VALIDATION': 'the default, or already inactive',
                                          'E_RECORD_NOT_FOUND': 'unknown template',
                                          'E_VERSION_CONFLICT': 'stale expected_version', **_DELIVERY_TAIL}
MATRIX['delivery-template show'] = {'E_RECORD_NOT_FOUND': 'unknown template', 'E_VALIDATION': 'a name of two kinds'}
MATRIX['delivery-template query'] = {}
MATRIX['delivery-template fields'] = {}
MATRIX['delivery-template preview'] = {'E_VALIDATION': 'a voided document or missing identity',
                                       'E_RECORD_NOT_FOUND': 'unknown document or template',
                                       'E_INACTIVE_REFERENCE': 'an inactive template'}
# Document delivery (R112 increment 2): the queue and the transport seam.
MATRIX['delivery approve'] = {'E_VALIDATION': 'no messages and not all_waiting, or more than one of them',
                              'E_RECORD_NOT_FOUND': 'unknown message',
                              'E_WORK_DEPENDENCY': 'the message is not ready, or the batch is cancelled', 'E_VERSION_CONFLICT': 'changed while approving',
                              'E_PERMISSION': "an agent its principal has not granted delivery.send", **_DELIVERY_TAIL}
MATRIX['delivery retry'] = {'E_VALIDATION': 'no To address', 'E_RECORD_NOT_FOUND': 'unknown message',
                            'E_WORK_DEPENDENCY': 'not held or failed, or held because the document changed',
                            'E_VERSION_CONFLICT': 'stale expected_version', **_DELIVERY_TAIL}
MATRIX['delivery run'] = {**_DELIVERY_TAIL}
_CONNECTION_WRITE = {'E_PERMISSION': "an agent its principal has not granted delivery.connect", **_DELIVERY_TAIL}
MATRIX['delivery-connection add'] = {'E_VALIDATION': 'no transport of that kind, bad settings or secret_ref, name in use',
                                     **_CONNECTION_WRITE}
MATRIX['delivery-connection update'] = {'E_VALIDATION': 'bad settings or secret_ref, or nothing to change',
                                        'E_RECORD_NOT_FOUND': 'unknown connection',
                                        'E_VERSION_CONFLICT': 'stale expected_version', **_CONNECTION_WRITE}
MATRIX['delivery-connection enable'] = {'E_VALIDATION': 'its kind is not available here',
                                        'E_RECORD_NOT_FOUND': 'unknown connection',
                                        'E_VERSION_CONFLICT': 'stale expected_version', **_CONNECTION_WRITE}
MATRIX['delivery-connection disable'] = {'E_RECORD_NOT_FOUND': 'unknown connection',
                                         'E_VERSION_CONFLICT': 'stale expected_version', **_CONNECTION_WRITE}
MATRIX['delivery-connection remove'] = {'E_RECORD_NOT_FOUND': 'unknown connection',
                                        'E_VERSION_CONFLICT': 'stale expected_version', **_CONNECTION_WRITE}
MATRIX['delivery-connection check'] = {'E_VALIDATION': 'its kind is not available here',
                                       'E_RECORD_NOT_FOUND': 'unknown connection', **_CONNECTION_WRITE}
MATRIX['delivery-connection show'] = {'E_RECORD_NOT_FOUND': 'unknown connection'}
MATRIX['delivery-connection query'] = {}
# Document delivery (R112 increment 3): flags, the queue and batches.
_FLAG = {'E_VALIDATION': 'a voided document', 'E_RECORD_NOT_FOUND': 'unknown document',
         'E_VERSION_CONFLICT': 'flagged meanwhile', **_DELIVERY_TAIL}
MATRIX['delivery flag'] = dict(_FLAG)
MATRIX['delivery unflag'] = dict(_FLAG)
MATRIX['delivery queue'] = {'E_RECORD_NOT_FOUND': 'unknown customer', 'E_VALIDATION': 'invalid filter'}
MATRIX['delivery batch'] = {'E_VALIDATION': 'no selection, or a missing kind, period or ready; a filter for another kind',
                            'E_RECORD_NOT_FOUND': 'unknown batch, customer or customer type',
                            'E_VERSION_CONFLICT': 'the batch moved meanwhile',
                            'E_WORK_DEPENDENCY': 'the set changed since the preview, or another batch fingerprint',
                            **_DELIVERY_TAIL}
MATRIX['delivery batch-show'] = {'E_RECORD_NOT_FOUND': 'unknown batch'}
MATRIX['delivery batch-query'] = {'E_VALIDATION': 'invalid state or limit'}
MATRIX['delivery batch-cancel'] = {'E_RECORD_NOT_FOUND': 'unknown batch', 'E_VERSION_CONFLICT': 'stale expected_version',
                                   'E_REASON_REQUIRED': 'no --reason', **_DELIVERY_TAIL}
# Document delivery (R112 increment 4): recurring schedules.
_SCHEDULE_ERRORS = {'E_VALIDATION': 'a missing frequency, start, selection or period; a filter for another kind; a '
                                    'taken name', 'E_RECORD_NOT_FOUND': 'unknown schedule, template or customer',
                    **_DELIVERY_TAIL}
MATRIX['delivery-schedule create'] = dict(_SCHEDULE_ERRORS)
MATRIX['delivery-schedule update'] = dict(_SCHEDULE_ERRORS, E_VERSION_CONFLICT='stale expected_version',
                                          E_WORK_DEPENDENCY="confirm is not the changed schedule's check",
                                          E_PERMISSION='an agent passing confirm (automate.delivery), or a person '
                                                       'who could not give a go now')
MATRIX['delivery-schedule live'] = {'E_VALIDATION': 'no confirm (show the check first)',
                                    'E_RECORD_NOT_FOUND': 'unknown schedule',
                                    'E_VERSION_CONFLICT': 'stale expected_version',
                                    'E_WORK_DEPENDENCY': 'the details or recipients changed since the check, or no '
                                                         'slot left',
                                    'E_PERMISSION': "an agent its principal has not granted automate.delivery, or a person "
                                                    'who could not give a go now',
                                    **_DELIVERY_TAIL}
MATRIX['delivery-schedule pause'] = {'E_RECORD_NOT_FOUND': 'unknown schedule',
                                     'E_VERSION_CONFLICT': 'stale expected_version', **_DELIVERY_TAIL}
MATRIX['delivery-schedule resume'] = dict(MATRIX['delivery-schedule pause'])
MATRIX['delivery-schedule run'] = {'E_VALIDATION': 'a selection the batch refuses now',
                                   'E_RECORD_NOT_FOUND': 'unknown schedule or batch',
                                   'E_VERSION_CONFLICT': 'the run or batch moved meanwhile',
                                   'E_WORK_DEPENDENCY': 'a document refused while drafting (recorded as a skip)',
                                   **_DELIVERY_TAIL}
MATRIX['delivery-schedule show'] = {'E_RECORD_NOT_FOUND': 'unknown schedule', 'E_VALIDATION': 'invalid runs'}
MATRIX['delivery-schedule query'] = {'E_VALIDATION': 'invalid kind or limit'}
# The bank inbox (R108 increment 1).
MATRIX['bank-feed import'] = {
    'E_VALIDATION': 'no file or two, not a bank or card account, a file with unreadable lines (every one listed), '
                    'another currency, no transactions',
    'E_RECORD_NOT_FOUND': 'unknown account or attachment',
    'E_AMOUNT_PRECISION': 'more decimals than the currency allows (listed with the file refusal)',
    'E_ACCOUNT_MISMATCH': "an OFX file for another account number, or a card statement into a bank account",
    'E_ALREADY_IMPORTED': 'the same file again without --again',
    'E_IO': 'an attachment body collected',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
for _name in ('bank-feed show', 'bank-feed query', 'bank-line query'):
    MATRIX[_name] = {'E_VALIDATION': 'invalid input or cursor', 'E_RECORD_NOT_FOUND': 'unknown import or account'}
# Reverse and move (R108 increment 2).
_BANK_REVERSE = {
    'E_VALIDATION': 'already reversed, the account frozen by a reversal under way, a move to the same account or '
                    'another currency, an import holding no lines',
    'E_RECORD_NOT_FOUND': 'unknown import or account',
    'E_BATCH_BLOCKED': 'a later live import, an entry edited, linked, reconciled, deposited, applied, in a closed '
                       'period or without the delete grant (every blocker named)',
    'E_VERSION_CONFLICT': 'the fingerprint of an older dry run',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['bank-feed reverse'] = dict(_BANK_REVERSE)
MATRIX['bank-feed move'] = {**_BANK_REVERSE, 'E_ACCOUNT_MISMATCH': "the import's account number or kind differs "
                                                                  "from the target's"}
# Sorting the inbox (R108 increment 3).
_BANK_SORT = {
    'E_VALIDATION': 'a line not waiting (or not live, or its import being reversed), no payee or account given (ask), '
                    'several candidates or pairs fitting equally, amounts that differ, a line named twice',
    'E_RECORD_NOT_FOUND': 'unknown line, entry, account or payee',
    'E_VERSION_CONFLICT': 'the line changed meanwhile',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['bank-line match'] = dict(_BANK_SORT)
MATRIX['bank-line unmatch'] = dict(_BANK_SORT)
MATRIX['bank-line ignore'] = dict(_BANK_SORT)
_BANK_POST = {**_BANK_SORT, 'E_AMOUNT_PRECISION': 'a split with more places than the currency has',
              'E_PERIOD_CLOSED': "the line's date is in a closed period (the posting command's own refusal)",
              'E_INACTIVE_REFERENCE': 'an inactive account, payee or class (the posting command\'s own refusal)',
              'E_DUPLICATE_NUMBER': "a check number already used on the account (the line's number)"}
MATRIX['bank-line add'] = dict(_BANK_POST)
MATRIX['bank-line transfer'] = dict(_BANK_POST)
MATRIX['bank-line undo'] = {
    'E_VALIDATION': 'not a bank-sort batch, or already undone',
    'E_RECORD_NOT_FOUND': 'unknown batch',
    'E_BATCH_BLOCKED': 'a line sorted again since, an entry edited, linked, reconciled, deposited, applied, in a '
                       'closed period or without the delete grant (every blocker named)',
    'E_VERSION_CONFLICT': 'the fingerprint of an older dry run, or a line changed meanwhile',
    'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}

# R114 increment 1: the item picture.
_ITEM_PICTURE = {
    'E_RECORD_NOT_FOUND': 'no such item, or no such attachment to use or as the thumbnail',
    'E_VERSION_CONFLICT': 'the item changed since its expected_version',
    'E_DB_BUSY': 'attachment collection requires recovery',
    'E_IDEMPOTENCY_MISMATCH': 'the key was used for a different picture write',
    'E_DIRECTIVE_NOT_FOUND': 'unknown directive', 'E_DIRECTIVE_INACTIVE': 'inactive directive',
    'E_RECORD_IN_USE': 'the item is merged into another; undo the merge first',
}
MATRIX['item picture set'] = dict(_ITEM_PICTURE, E_IO='the attachment body could not be read or published',
                                  E_VALUE_RANGE='the picture is larger than 20,000,000 bytes or the company limit')
MATRIX['item picture use'] = dict(_ITEM_PICTURE, E_IO='the attachment body was collected or cannot be verified',
                                  E_VALUE_RANGE='the picture or thumbnail is larger than its limit')
MATRIX['item picture clear'] = dict(_ITEM_PICTURE)
MATRIX['item picture get'] = {
    'E_RECORD_NOT_FOUND': 'no item shows this attachment as its picture or thumbnail',
    'E_IO': 'the picture body was collected, missing or corrupt', 'E_DB_BUSY': 'attachment collection requires recovery',
    'E_VALUE_RANGE': 'invalid company byte limit'}
# Grid edits (R107 increment 6).
_BULK_WRITE = {
    'E_VALIDATION': 'an unknown column or kind, a value the column cannot hold, nothing asked, an operation on the '
                    'wrong type, over 5,000 records, all or nothing with a refused record, already applied, a header-less paste '
                    'without the selection fingerprint, undo or cancel while not allowed',
    'E_RECORD_NOT_FOUND': 'unknown grid edit, or a named or pasted record the list lacks',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
}
MATRIX['bulk-edit preview'] = dict(_BULK_WRITE)
MATRIX['bulk-edit apply'] = dict(_BULK_WRITE, E_VERSION_CONFLICT='applied meanwhile by another call')
MATRIX['bulk-edit reverse'] = dict(_BULK_WRITE, E_BATCH_BLOCKED='a record edited again since (each named; '
                                   '--skip-changed leaves them), or an update the caller may not run',
                                   E_VERSION_CONFLICT='the batch changed since the dry run that gave --fingerprint')
MATRIX['bulk-edit columns'] = {'E_VALIDATION': 'unknown kind'}
for _name in ('bulk-edit grid', 'bulk-edit show'):
    MATRIX[_name] = {'E_VALIDATION': 'invalid input or column', 'E_RECORD_NOT_FOUND': 'unknown grid edit or record'}
MATRIX['bulk-edit query'] = {'E_VALIDATION': 'invalid input or cursor'}
# Add more details (R108 increment 5): a deposit of Undeposited Funds payments, bills marked paid.
_BANK_DETAILS = {**_BANK_POST,
                 'E_VALIDATION': 'a line not waiting, not money in (deposit) or out (bills), a selection that does not '
                                 'come to the line (the difference shown), bills of two vendors, no method or '
                                 'adjustment payee given (ask)',
                 'E_VALUE_RANGE': "an amount out of range for the posting command",
                 'E_DEPOSIT_SOURCE_INELIGIBLE': 'a receipt not waiting in Undeposited Funds (deposit post\'s own)',
                 'E_DEPOSIT_SOURCE_CLAIMED': 'a payment already deposited (deposit post\'s own refusal)',
                 'E_DEPOSIT_SOURCE_INVALID': 'a receipt that cannot be deposited (deposit post\'s own)',
                 'E_DEPOSIT_DATE_BEFORE_SOURCE': 'the line is dated before a payment it banks',
                 'E_APPLICATION_CAPACITY': 'more than is open on a bill (bill pay\'s own refusal)',
                 'E_APPLICATION_INCOMPATIBLE': 'a bill of another currency or payable account',
                 'E_APPLICATION_INACTIVE': 'a bill voided or deleted'}
MATRIX['bank-line deposit'] = dict(_BANK_DETAILS)
MATRIX['bank-line pay-bills'] = dict(_BANK_DETAILS)
# Automations (R187 increment 2).
MATRIX['automation list'] = {'E_VALIDATION': 'unknown kind, state or limit'}
MATRIX['automation show'] = {'E_VALIDATION': 'unknown kind', 'E_RECORD_NOT_FOUND': 'unknown rule',
                             'E_PERMISSION': "a caller the kind's own show does not admit"}
MATRIX['automation pause'] = {'E_VALIDATION': 'unknown kind', 'E_RECORD_NOT_FOUND': 'unknown rule',
                              'E_PERMISSION': "a caller the kind's own update does not admit",
                              'E_VERSION_CONFLICT': 'the rule changed meanwhile',
                              'E_IDEMPOTENCY_MISMATCH': 'a reused key with other input',
                              'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
                              'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
# Bank rules and learning (R108 increment 4).
_BANK_RULE = {'E_VALIDATION': 'a field the action does not take, an auto-add with no payee or category, a condition '
                              'that cannot be read, a suggestion edited by status, a retired rule, making live '
                              'without the check confirmed or a rule that only proposes',
              'E_RECORD_NOT_FOUND': 'unknown rule, account, payee or class',
              'E_VERSION_CONFLICT': 'the rule or a line changed meanwhile',
              'E_IDEMPOTENCY_MISMATCH': 'same key, different input',
              'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
              'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}
MATRIX['bank-rule create'] = {**_BANK_RULE, 'E_NAME_TAKEN': 'a rule of that name exists',
                              'E_AMOUNT_PRECISION': 'an amount condition with more places than the currency has'}
MATRIX['bank-rule update'] = {**MATRIX['bank-rule create'],
                              'E_PERMISSION': 'making it live without automate.bank-rule',
                              'E_DB_BUSY': 'the establisher\'s authority cannot be read now'}
for _name in ('bank-rule deactivate', 'bank-rule accept', 'bank-rule decline', 'bank-rule apply'):
    MATRIX[_name] = dict(_BANK_RULE)
MATRIX['bank-rule show'] = {'E_VALIDATION': 'invalid input', 'E_RECORD_NOT_FOUND': 'unknown rule'}
MATRIX['bank-rule query'] = {'E_VALIDATION': 'invalid input or cursor', 'E_RECORD_NOT_FOUND': 'unknown account'}
# The POS layer (R114 increment 4): registers.
_POS_WRITE = {
    'E_VALIDATION': 'a deposit account that is not Undeposited Funds or a bank, a refund account that is not a bank, a '
                    'payment method named twice, an unknown decoder',
    'E_RECORD_NOT_FOUND': 'unknown register, site, account, payment method or customer',
    'E_NAME_TAKEN': 'another register has the name',
    'E_INACTIVE_REFERENCE': 'an inactive account, payment method or customer',
    'E_FEATURE_DISABLED': 'POS is off for the company',
    'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
    'E_DIRECTIVE_INACTIVE': 'deactivated --directive',
    'E_IDEMPOTENCY_MISMATCH': 'a reused operation key with other input',
}
MATRIX['pos-register create'] = dict(_POS_WRITE)
MATRIX['pos-register update'] = dict(_POS_WRITE, E_VERSION_CONFLICT='changed since the version read')
MATRIX['pos-register show'] = {'E_RECORD_NOT_FOUND': 'unknown register', 'E_FEATURE_DISABLED': 'POS is off'}
MATRIX['pos-register list'] = {'E_FEATURE_DISABLED': 'POS is off for the company'}
# Ringing up (R114 increment 5).
_POS_SALE = {
    'E_VALIDATION': 'an empty cart, a register to name when there are several, a payment method the register does not '
                    'offer, cash short of the total, a card for part of it, a field the register fixes',
    'E_RECORD_NOT_FOUND': 'unknown register, customer, item or payment method',
    'E_INACTIVE_REFERENCE': 'an inactive customer, item or payment method',
    'E_FEATURE_DISABLED': 'POS is off for the company',
    'E_PERIOD_CLOSED': 'today is on or before the closing date',
    'E_VALUE_RANGE': 'a quantity or amount out of range',
    'E_AMOUNT_PRECISION': 'a price with more places than the currency',
    'E_PERMISSION': 'a price typed over an item\'s own price without pos.price-override',
}
MATRIX['pos sale preview'] = dict(_POS_SALE)
MATRIX['pos sale complete'] = dict(_POS_SALE, E_PREVIEW_STALE='the sale now totals other than the total shown',
                                   E_IDEMPOTENCY_MISMATCH='the ticket already completed with another cart',
                                   E_DIRECTIVE_NOT_FOUND='unknown --directive',
                                   E_DIRECTIVE_INACTIVE='deactivated --directive')
MATRIX['pos customer search'] = {'E_FEATURE_DISABLED': 'POS is off for the company'}
MATRIX['pos customer add'] = {'E_VALIDATION': 'an empty name', 'E_NAME_TAKEN': 'a customer has the name',
                              'E_IDEMPOTENCY_MISMATCH': 'the idempotency key was used with another input',
                              'E_FEATURE_DISABLED': 'POS is off for the company',
                              'E_DIRECTIVE_NOT_FOUND': 'unknown --directive',
                              'E_DIRECTIVE_INACTIVE': 'deactivated --directive'}

# R108 increment 8: connection writes only authorize bounded host work.
for _name in ('bank-feed-connection add', 'bank-feed-connection enable', 'bank-feed-connection disable',
              'bank-feed-connection check', 'bank-feed fetch'):
    MATRIX[_name] = {
        'E_VALIDATION': 'unknown provider, invalid keyring reference or settings, missing or stale backfill approval',
        'E_RECORD_NOT_FOUND': 'unknown connection or account',
        'E_VERSION_CONFLICT': 'connection changed since the supplied version',
        'E_PERMISSION': 'bank-feed.connect is not held or explicitly granted',
        'E_IDEMPOTENCY_MISMATCH': 'same key with different input',
        'E_DIRECTIVE_NOT_FOUND': 'unknown directive',
        'E_DIRECTIVE_INACTIVE': 'deactivated directive',
    }
MATRIX['bank-feed-connection query'] = {}

MATRIX['bank-feed fetch']['E_ACCOUNT_MISMATCH'] = 'the staged page differs from the selected account hint'

MATRIX['bank-feed-connection disable']['E_PERMISSION'] = 'bank-feed at standard is not held'
