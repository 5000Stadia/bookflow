"""Input and output shapes for document delivery, increment 2: the queue and the transport seam (R112;
design/document-delivery.md §2, §4)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from bookflow.company.shipment_models import Selector, _Strict


class ApproveInput(_Strict):
    messages: list[Selector] | None = Field(default=None, min_length=1, max_length=100, description=(
        'Ready messages waiting for a go, by number or id.'))
    all_waiting: bool = Field(default=False, description='Every message waiting for a go (at most 100).')


class RetryInput(_Strict):
    message: Selector = Field(description='Message number (M-1001) or id.')
    expected_version: int | None = Field(default=None, ge=1)


class RunInput(_Strict):
    limit: int = Field(default=10, ge=1, le=50, description='Most messages to claim in this pass.')


class RunRow(BaseModel):
    number: str
    act: str = Field(description='claimed, expired (its lease ran out), held, or go withdrawn.')
    status: str
    attempt: int | None = None
    outcome: str | None = Field(default=None, description='accepted, temporary, permanent or unknown.')
    code: str | None = None
    text: str | None = None
    next_attempt_at: str | None = None


class RunOutput(BaseModel):
    connection: str = Field(description='The sending connection used, or none.')
    claimed: int
    sent: int = 0
    retrying: int = 0
    failed: int = 0
    held: int = 0
    go_withdrawn: int = 0
    expired: int = 0
    tracking_events: int = Field(default=0, description='delivered or bounced reports recorded from the transport.')
    messages: list[RunRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConnectionAddInput(_Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=64, description='A transport kind this build registers.')
    settings: dict[str, Any] = Field(default_factory=dict, description=(
        'Non-secret settings the kind validates (its server command, the From address). Never a password or key.'))
    secret_ref: str | None = Field(default=None, max_length=200, description=(
        'Where the secret is: env:NAME or keyring:service/name. Never the secret itself.'))
    daily_limit: int | None = Field(default=None, ge=1, le=100000, description='Most attempts in any 24 hours.')


class ConnectionSelector(_Strict):
    connection: Selector = Field(description='Connection name or id.')


class ConnectionWriteInput(ConnectionSelector):
    expected_version: int | None = Field(default=None, ge=1)


class ConnectionUpdateInput(ConnectionWriteInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    settings: dict[str, Any] | None = None
    secret_ref: str | None = Field(default=None, max_length=200)
    no_secret: bool = Field(default=False, description='Clear the secret reference.')
    daily_limit: int | None = Field(default=None, ge=1, le=100000)
    no_daily_limit: bool = Field(default=False, description='Clear the daily limit.')


class ConnectionQueryInput(_Strict):
    include_removed: bool = False


class ConnectionOutput(BaseModel):
    id: str
    version: int
    name: str
    kind: str
    kind_available: bool = Field(description='Whether this build registers its kind.')
    settings: dict[str, Any]
    secret_ref: str | None = None
    enabled: bool
    enabled_here: bool = Field(description='Enabled on this machine and company file (a copy or restore is not).')
    usable: bool = Field(description='The dispatcher may send through it now.')
    daily_limit: int | None = None
    attempts_last_24h: int = 0
    last_check_at: str | None = None
    last_check_ok: bool | None = None
    last_check_text: str | None = None
    removed_at: str | None = None
    updated_at: str
    updated_by: str
    warnings: list[str] = Field(default_factory=list)


class ConnectionPageOutput(BaseModel):
    rows: list[ConnectionOutput]
    count: int
    kinds_available: list[str] = Field(description='Transport kinds this build registers (none: nothing can send).')
    note: str
