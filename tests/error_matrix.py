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

# The money-out documents are the register split under their own names, so they raise the
# ledger's own codes; only the unbalanced one reads differently, because what it names is the
# difference between the expense lines and the figure on the face of the document.
for _noun in ('check', 'card-charge'):
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
for _noun, _face in (('check', 'check'), ('card-charge', 'card-charge'), ('transfer', 'transfer')):
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
MATRIX['report ar-aging'] = {
    'E_QUERY_STALE': 'posting, settlement or customer display facts changed between aging pages',
    'E_VALUE_RANGE': 'public aging column or aging total exceeds signed 64-bit range',
}
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
MATRIX['report ap-aging'] = {
    'E_QUERY_STALE': 'posting, settlement or vendor display facts changed between aging pages',
    'E_VALUE_RANGE': 'public aging column or aging total exceeds signed 64-bit range',
}
MATRIX['report unpaid-bills'] = {
    'E_QUERY_STALE': 'posting, settlement or vendor display facts changed between unpaid-bill pages',
    'E_VALUE_RANGE': 'public bill amount or unpaid-bill total exceeds signed 64-bit range',
    'E_RECORD_NOT_FOUND': 'vendor filter does not resolve',
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
    'E_REASON_REQUIRED': 'the void has no reason of 1 to 140 characters, or an agent/system write supplies neither reason nor directive',
    'E_VERSION_CONFLICT': 'stale expected_version on the receipt',
    'E_APPLICATION_INACTIVE': _UNREACHED_HERE,
    'E_APPLICATION_CAPACITY': _UNREACHED_HERE,
    'E_APPLICATION_INCOMPATIBLE': _UNREACHED_HERE,
    'E_SELECTION_CONSUMED': _UNREACHED_HERE,
    'E_DUPLICATE_NUMBER': _UNREACHED_HERE,
    'E_AMOUNT_PRECISION': _UNREACHED_HERE,
})
MATRIX['payment update'].update({
    'E_APPLIED_EXCEEDS_TOTAL': 'the corrected total drops the payer capacity below what is already applied from it',
    'E_HAS_APPLICATIONS': 'the corrected receipt date is later than the effective date of a live application',
    'E_REASON_REQUIRED': 'the correction has no reason of 1 to 140 characters, or an agent/system write supplies neither reason nor directive',
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
MATRIX["bill show"] = {"E_RECORD_NOT_FOUND": "unknown bill or revision number"}
MATRIX["bill query"] = {"E_RECORD_NOT_FOUND": "unknown vendor filter", "E_QUERY_STALE": "company audit changed between bill pages"}
MATRIX["bill history"] = {"E_RECORD_NOT_FOUND": "unknown bill", "E_QUERY_STALE": "company audit changed between history pages"}


# Paying a bill reaches across documents, so it refuses for the bill's reasons and the
# settlement's: an ineligible funding account, a stale bill, and a bill that is already paid.
MATRIX["bill pay"] = {
    "E_RECORD_NOT_FOUND": "unknown bill, funding account, method or class",
    "E_INACTIVE_REFERENCE": "deactivated funding account, method, class or payable account",
    "E_VALIDATION": "an ineligible funding account, a check number on something that is not a check, a payment dated before a bill it settles, a repeated bill selector, or an explicit number with more than one payee group",
    "E_VALUE_RANGE": "an amount of zero or less, or an exhausted payment number sequence",
    "E_AMOUNT_PRECISION": "an amount with more precision than the currency has",
    "E_PERIOD_CLOSED": "payment date on or before the closing date",
    "E_DUPLICATE_NUMBER": "explicit number already occupied by another bill payment",
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
    "E_APPLICATION_INCOMPATIBLE": "a bill owed to another vendor, from another payable account, or in another currency",
    "E_APPLICATION_INACTIVE": "a voided credit, a voided bill, or a bill with no payable",
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


# A credit memo refuses for the invoice's reasons -- it is the same resolver on the same
# accounts -- plus the two only a return can hit: asking for more of a line than is left, and
# a source line the invoice has since corrected out from under the claim.
MATRIX["credit-memo post"] = {
    "E_RECORD_NOT_FOUND": "unknown customer, item, account, class, tax item, source invoice or source line",
    "E_INACTIVE_REFERENCE": "deactivated customer, item, account, class or tax item",
    "E_VALIDATION": "no single active Accounts Receivable account, an ineligible posting account, a source invoice for another customer, a priced return, a document mixing returns with named items, or a zero total",
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
    "E_VALIDATION": "a reason longer than 140 characters, or ambiguous posting evidence",
    "E_REASON_REQUIRED": "no reason given",
    "E_PERIOD_CLOSED": "the credit memo's own date is in a closed period",
    "E_HAS_APPLICATIONS": "a live application; unapply it first",
    "E_HAS_REFUND": "a live refund consumption; void the refund first",
    "E_IDEMPOTENCY_MISMATCH": "same key, different input",
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
MATRIX["customer-refund show"] = {"E_RECORD_NOT_FOUND": "unknown refund"}
MATRIX["customer-refund query"] = {
    "E_RECORD_NOT_FOUND": "unknown customer, funding account or method filter",
    "E_QUERY_STALE": "company audit changed between refund pages",
}

MATRIX["billing-group create"] = {
    "E_RECORD_NOT_FOUND": "unknown customer or job named as a member",
    "E_NAME_TAKEN": "a billing group with that name, ignoring case",
    "E_VALIDATION": "a blank name, a name containing a colon, or the same customer twice",
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
