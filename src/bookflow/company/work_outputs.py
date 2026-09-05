"""Typed non-posting work documents and inspectable operational history."""
from typing import Literal
from pydantic import Field
from bookflow.commands.common import CommonOut
from bookflow.company.journal_outputs import CreatedOutput, JournalMoneyOutput as MoneyOutput
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.sales_models import StrictModel
from bookflow.company.work_facts import WorkFacts, WorkLineFacts
from bookflow.core.models import WriteOutput


class WorkLineOutput(CreatedOutput):
    document_id: str
    revision_id: str
    line_id: str
    position: int
    root_document_id: str
    root_line_id: str
    source_line_id: str | None
    facts: WorkLineFacts
    quantity: str
    completed_quantity: str
    unit_price: MoneyOutput | None
    net: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    estimated_unit_cost: MoneyOutput | None
    estimated_cost: MoneyOutput | None


class WorkRevisionSummary(CreatedOutput):
    document_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    title: str
    status: str
    active: bool
    customer_id: str
    currency: str
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    net: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    accepted_revision_id: str | None
    accepted_at: str | None
    accepted_by: str | None
    decision_note: str | None
    audit_event_id: str
    line_count: int


class WorkRevisionOutput(WorkRevisionSummary):
    facts: WorkFacts
    custom_fields_snapshot: dict[str, SnapshotField]
    custom_fields: list[SnapshotField]
    lines: list[WorkLineOutput]
    known_cost_total: MoneyOutput
    cost_complete: bool
    estimated_profit: MoneyOutput | None


class WorkLinkOutput(CreatedOutput):
    source_document_id: str
    source_revision_id: str
    source_version: int
    destination_document_id: str
    destination_revision_id: str
    relation: Literal['copy', 'proposal_estimate', 'estimate_work_order']
    source_kind: str
    source_number: str
    source_status: str
    source_current_revision_id: str
    destination_kind: str
    destination_number: str
    destination_status: str
    destination_current_revision_id: str


class WorkSummaryOutput(CommonOut):
    kind: Literal['proposal', 'estimate', 'work_order']
    number: str
    status: str
    active: bool
    current_revision_id: str
    estimate_group_id: str | None
    date: str
    title: str
    customer_id: str
    customer_name: str
    currency: str
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    net: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    expired: bool


class WorkOutput(WorkSummaryOutput):
    revision: WorkRevisionOutput
    links: list[WorkLinkOutput]
    links_has_more: bool = False
    next_links_cursor: str | None = None
    links_audit_watermark: int | None = None


class WorkWriteOutput(WorkOutput, WriteOutput):
    facts_fingerprint: str | None = None
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    idempotent_replay: bool = False


class WorkPageOutput(StrictModel):
    items: list[WorkSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class WorkHistoryOutput(StrictModel):
    id: str
    version: int
    number: str
    current_revision_id: str
    status: str
    items: list[WorkRevisionSummary]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
