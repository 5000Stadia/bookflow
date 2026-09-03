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
    "E_IO": "A filesystem operation failed.",
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
