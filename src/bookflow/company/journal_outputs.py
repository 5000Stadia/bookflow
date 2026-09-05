"""Typed domestic journal documents and immutable historical views."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from bookflow.commands.common import CommonOut
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.core.models import WriteOutput


class JournalMoneyOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)
    amount: str
    currency: str
    minor_units: int = Field(ge=-9223372036854775808, le=9223372036854775807)


class CreatedOutput(BaseModel):
    id: str
    created_at: str
    created_by: str
    created_via: str


class JournalLineOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['journal']
    account_id: str
    account_snapshot: dict[str, Any]
    side: Literal['debit', 'credit']
    amount: JournalMoneyOutput
    amount_minor_units: int
    currency: str
    name_type: str | None
    name_id: str | None
    party_name: str | None
    class_id: str | None
    class_name: str | None
    description: str | None
    original_minor_units: int | None
    original_amount: JournalMoneyOutput | None
    original_currency: str | None
    rate_used: str | None
    rate_source: str | None


class JournalBatchOutput(CreatedOutput):
    total: JournalMoneyOutput
    transaction_id: str
    revision_id: str
    kind: Literal['original', 'replacement', 'reversal']
    effective_date: str
    reverses_batch_id: str | None
    replaces_batch_id: str | None
    audit_event_id: str
    debit_total: JournalMoneyOutput
    credit_total: JournalMoneyOutput
    debit_minor_units: int
    credit_minor_units: int
    currency: str
    line_count: int


class JournalRevisionSummaryOutput(CreatedOutput):
    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: str | None
    name_id: str | None
    memo: str | None
    total: JournalMoneyOutput
    total_minor_units: int
    debit_total: JournalMoneyOutput
    credit_total: JournalMoneyOutput
    debit_minor_units: int
    credit_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int
    batches: list[JournalBatchOutput]


class JournalRevisionOutput(JournalRevisionSummaryOutput):
    issuer_snapshot: dict[str, Any]
    custom_fields_snapshot: dict[str, SnapshotField] = Field(description="Immutable typed values and captured definition metadata keyed by definition ID.")
    custom_fields: list[SnapshotField] = Field(description="The same captured fields ordered by position, name and definition ID, without live definition lookups.")
    lines: list[JournalLineOutput]


class JournalSummaryOutput(CommonOut):
    type: Literal['journal_entry']
    number: str
    current_revision_id: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    memo: str | None
    total: JournalMoneyOutput
    total_minor_units: int
    debit_total: JournalMoneyOutput
    credit_total: JournalMoneyOutput
    debit_minor_units: int
    credit_minor_units: int
    currency: str


class JournalOutput(JournalSummaryOutput):
    revision: JournalRevisionOutput


class JournalWriteOutput(JournalOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    idempotent_replay: bool = False


class JournalPageOutput(BaseModel):
    items: list[JournalSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class JournalHistoryOutput(BaseModel):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal['posted', 'voided']
    items: list[JournalRevisionSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
