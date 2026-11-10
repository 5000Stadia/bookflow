"""Preserve business error contracts while withholding infrastructure diagnostics."""
from bookflow.core.errors import BookflowError

DIAGNOSTIC_CODES = frozenset(("E_IO", "E_INTERNAL", "E_CONFIG_INVALID", "E_GUARD_REFUSED", "E_USAGE"))
SECRET_FIELDS = frozenset(("password", "password_hash", "secret", "token_hash", "key_material", "private_key",
                           "authorization", "cookie", "setup_url", "owner_link", "secret_handle", "enrollment_proof"))


def redact_fields(value):
    if isinstance(value, dict):
        return {key: "[redacted]" if str(key).lower() in SECRET_FIELDS else redact_fields(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_fields(item) for item in value]
    return value


def public_error(error: BookflowError) -> BookflowError:
    if error.code in DIAGNOSTIC_CODES:
        return BookflowError(error.code)
    # Reason discriminators and version/field facts are functional contracts.
    # They must survive redaction (including workbench recovery branches).
    return BookflowError(error.code, message=error.message, details=redact_fields(error.details))
