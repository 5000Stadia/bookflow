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
for _report in ('profit-and-loss', 'balance-sheet'):
    MATRIX['report ' + _report] = {
        'E_QUERY_STALE': 'posting or display facts changed between statement pages',
        'E_VALUE_RANGE': 'public account amount or statement total exceeds signed 64-bit range',
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

for _noun in ('estimate', 'work-order'):
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
MATRIX['deposit sources'] = {
    'E_RECORD_NOT_FOUND': 'for_deposit names no deposit in the selected company',
    'E_QUERY_STALE': 'candidate facts changed between bounded pages',
    'E_VALIDATION': 'inverted receipt date range or a cursor from another filter',
    'E_SCHEMA_BEHIND': 'company database behind the deposit feature revision',
    'E_DEPOSIT_SOURCE_INVALID': 'captured receipt cash provenance unsupported or inconsistent',
}
