"""Typed connection and first-backfill commands."""
from typing import Any
from pydantic import Field
from bookflow.company.reconciliation_commands_models import Model, AccountSelector


class AddInput(Model):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=64)
    account: AccountSelector
    remote_account: str = Field(min_length=1, max_length=255)
    settings: dict[str, Any] = Field(default_factory=dict, description='Provider-validated non-secret settings.')
    secret_ref: str | None = Field(None, max_length=200, description='OS keyring reference keyring:service/name, never the secret.')


class Selector(Model):
    connection: str = Field(min_length=1, max_length=100)
    expected_version: int | None = Field(None, ge=1)


class EnableInput(Selector):
    confirm: str | None = Field(None, min_length=64, max_length=64, description='Fingerprint from the staged first-page preview; required before the first import.')


class QueryInput(Model):
    limit: int = Field(100, ge=1, le=500)


class ConnectionOutput(Model):
    id: str
    version: int
    name: str
    kind: str
    account_id: str
    remote_account: str
    secret_ref: str | None
    enabled: bool
    enabled_here: bool
    check_requested: bool
    backfill_approved: bool
    cursor: str | None = Field(description='Provider cursor, redacted below admin; never contains credentials.')
    staged_fingerprint: str | None
    approved_fingerprint: str | None
    preview: dict[str, Any] | None = Field(None, description='Staged first page, redacted below admin.')
    import_preview: dict[str, Any] | None = Field(None, description="Current shared import preview, including counts and account check; empty page has no import.")
    last_check_ok: bool | None
    last_check_at: str | None
    next: str


class QueryOutput(Model):
    rows: tuple[ConnectionOutput, ...]
    kinds_available: tuple[str, ...]
    note: str
