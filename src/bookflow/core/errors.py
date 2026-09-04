"""Error codes and the exception every command raises.

Codes are stable strings. Infrastructure codes may come from any command;
command-specific codes are declared per command in the registry.
"""

from __future__ import annotations

from typing import Any

INFRASTRUCTURE_CODES: dict[str, str] = {
    "E_USAGE": "Invalid command syntax.",
    "E_VALIDATION": "Invalid input.",
    "E_CONTEXT_IN_INPUT": "Input contains a context field.",
    "E_NOT_INITIALIZED": "The data root is not initialized; run `bookflow init`.",
    "E_NO_ACTOR": "This login is not mapped to a Bookflow user.",
    "E_PERMISSION": "The acting user may not run this command here.",
    "E_COMPANY_NOT_FOUND": "No such company.",
    "E_COMPANY_AMBIGUOUS": "More than one company matches; use the id or Organization/Company.",
    "E_ORGANIZATION_NOT_FOUND": "No such organization.",
    "E_DB_BUSY": "Another Bookflow command is running on this data root.",
    "E_NETWORK_SHARE": "The path is on a network filesystem, which Bookflow refuses to use.",
    "E_FS_UNKNOWN": "The filesystem type of the path could not be determined.",
    "E_SCHEMA_UNKNOWN": "The database schema revision is not known to this version of Bookflow; upgrade Bookflow.",
    "E_SCHEMA_BEHIND": "The database schema is behind this version of Bookflow; run `bookflow upgrade`.",
    "E_CONFIG_INVALID": "The configuration file could not be read.",
    "E_MIGRATION_FAILED": "A schema migration failed; the database was backed up first and is unchanged.",
    "E_IO": "A filesystem operation failed.",
    "E_REASON_REQUIRED": "Writes by an agent need --reason or --directive.",
    "E_FEATURE_DISABLED": "This feature is not enabled for the company.",
    "E_UNAUTHENTICATED": "No valid credential: log in, or send a bearer token.",
    "E_INTERNAL": "Internal failure.",
}

COMMAND_CODES: dict[str, str] = {
    "E_NAME_TAKEN": "That display name is already used.",
    "E_ORGANIZATION_REQUIRED": "More than one organization is visible; name one with --organization.",
    "E_ROLLOUT_INCOMPLETE": "Company creation did not finish; a folder remains.",
    "E_RENAME_INCOMPLETE": "The name changed but the folder was not moved; rerun rename --move.",
    "E_COMPANY_MISSING": "The company's registered folder does not exist.",
    "E_NOT_IN_ORGANIZATION_DIR": "The folder is not inside a registered organization's folder.",
    "E_INCOMPLETE_COMPANY": "The folder holds an unfinished company creation.",
    "E_ALREADY_ATTACHED": "That company id is already registered.",
    "E_ATTACH_INVALID": "The folder is not a valid company folder.",
    "E_DEMO_RESET_INCOMPLETE": "The old demo could not be moved to trash; its folders remain unregistered.",
    "E_AMOUNT_PRECISION": "The amount has more decimal places than the currency allows.",
    "E_EVENT_NOT_FOUND": "No such audit event.",
    "E_INIT_CONFLICT": "The data root is already initialized for a different user.",
    "E_VERSION_CONFLICT": "The record changed since the version you read.",
    "E_DIRECTIVE_NOT_FOUND": "No such directive.",
    "E_DIRECTIVE_INACTIVE": "That directive has been deactivated.",
    "E_IDEMPOTENCY_MISMATCH": "That idempotency key was used for a different command or input.",
    "E_RECORD_NOT_FOUND": "No such record.",
    "E_ACTIVE_DEPENDENTS": "Active descendants must be deactivated first or included with cascade.",
    "E_RECORD_IN_USE": "The record is still used by active records.",
    "E_INACTIVE_REFERENCE": "A new or changed reference must name an active record.",
    "E_HIERARCHY_CYCLE": "The requested parent would create a hierarchy cycle.",
    "E_HIERARCHY_DEPTH": "The requested hierarchy would exceed the maximum depth.",
    "E_SYSTEM_RECORD": "That operation would change a protected system record.",
    "E_LIST_FILTER": "The requested list filter or sort field is not supported.",
    "E_TYPE_CHANGE": "That record type cannot be changed directly.",
    "E_VALUE_RANGE": "The value is outside its allowed range or storage bounds.",
    "E_NOT_UNDOABLE": "That audit event is not eligible for list-event undo.",
    "E_ALREADY_UNDONE": "That audit event has already been compensated.",
    "E_UNDO_CONFLICT": "Current record state overlaps the change that would be undone.",
    "E_PARTIAL_WRITE": "The write completed in one database but not the other.",
    "E_LOGIN_FAILED": "Login failed.",
    "E_WORKBENCH_HEADER": "Cookie-authenticated writes need the X-Bookflow-Workbench header.",
    "E_VERSION_MISMATCH": "The running host and this client are different Bookflow versions; stop the host or upgrade the client.",
    "E_NETWORK_NOT_ALLOWED": "Binding outside loopback needs --allow-network.",
    "E_DOCS_STALE": "Generated documentation differs from the current command and schema definitions.",
    "E_USER_NOT_FOUND": "No such user.",
    "E_TOKEN_NOT_FOUND": "No such token.",
}

ALL_CODES: dict[str, str] = {**INFRASTRUCTURE_CODES, **COMMAND_CODES}

EXIT_CODES: dict[str, int] = {"E_USAGE": 2, "E_INTERNAL": 3}


class BookflowError(Exception):
    """A named failure. ``code`` is stable; ``details`` is JSON-serializable."""

    def __init__(self, code: str, message: str | None = None, details: dict[str, Any] | None = None):
        if code not in ALL_CODES:
            raise ValueError(f"unknown error code {code!r}")
        self.code = code
        self.message = message or ALL_CODES[code]
        self.details: dict[str, Any] = details or {}
        super().__init__(f"{self.code}: {self.message}")

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.code, 1)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}
