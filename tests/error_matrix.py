"""Per-command error codes and what produces each (blueprint 5.4). Walked by test_error_matrix."""

MATRIX = {
    "init": {"E_INIT_CONFLICT": "init again with a different --username", "E_CONFIG_INVALID": "malformed config.toml"},
    "upgrade": {"E_COMPANY_MISSING": "registered folder absent (reported per company)"},
    "organization new": {"E_NAME_TAKEN": "existing name", "E_PERMISSION": "non hub admin", "E_VALIDATION": "empty or slash name"},
    "organization show": {"E_ORGANIZATION_NOT_FOUND": "absent or invisible organization"},
    "organization rename": {"E_NAME_TAKEN": "existing name", "E_RENAME_INCOMPLETE": "move failed", "E_PERMISSION": "non hub admin"},
    "company new": {"E_ORGANIZATION_REQUIRED": "several visible organizations", "E_NAME_TAKEN": "existing name", "E_ROLLOUT_INCOMPLETE": "registration failed after folder creation",
                    "E_ORGANIZATION_NOT_FOUND": "absent or invisible organization", "E_PERMISSION": "standard member", "E_VALIDATION": "bad currency, email, tax id, month, timezone, or unknown field"},
    "company use": {"E_COMPANY_NOT_FOUND": "absent or invisible company", "E_VALIDATION": "missing positional"},
    "company attach": {"E_NOT_IN_ORGANIZATION_DIR": "path outside organizations", "E_INCOMPLETE_COMPANY": "marker state creating", "E_ALREADY_ATTACHED": "id registered",
                       "E_NAME_TAKEN": "name in use", "E_ATTACH_INVALID": "marker or database missing or mismatched, wide mode", "E_SCHEMA_UNKNOWN": "unknown revision", "E_PERMISSION": "non hub admin"},
    "company detach": {"E_COMPANY_NOT_FOUND": "absent company", "E_PERMISSION": "non hub admin"},
    "company show": {"E_COMPANY_NOT_FOUND": "absent, invisible, or unselected company", "E_COMPANY_MISSING": "folder or database absent", "E_SCHEMA_BEHIND": "behind head on read", "E_IO": "corrupt database"},
    "company rename": {"E_NAME_TAKEN": "existing name", "E_RENAME_INCOMPLETE": "move failed", "E_COMPANY_MISSING": "folder absent", "E_PERMISSION": "readonly or standard member", "E_IO": "corrupt database"},
    "demo reset": {"E_DEMO_RESET_INCOMPLETE": "trash move failed", "E_NAME_TAKEN": "non-demo organization holds the seed name", "E_PERMISSION": "non hub admin"},
    "hub audit list": {"E_VALIDATION": "bad since/until"},
    "hub audit show": {"E_EVENT_NOT_FOUND": "absent or invisible event"},
}

INFRASTRUCTURE = ["E_USAGE", "E_VALIDATION", "E_CONTEXT_IN_INPUT", "E_NOT_INITIALIZED", "E_NO_ACTOR", "E_PERMISSION", "E_COMPANY_NOT_FOUND",
                  "E_COMPANY_AMBIGUOUS", "E_ORGANIZATION_NOT_FOUND", "E_DB_BUSY", "E_NETWORK_SHARE", "E_FS_UNKNOWN", "E_SCHEMA_UNKNOWN",
                  "E_SCHEMA_BEHIND", "E_CONFIG_INVALID", "E_IO", "E_REASON_REQUIRED", "E_INTERNAL"]
