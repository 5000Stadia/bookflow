"""Typed non-posting work documents and inspectable operational history."""
from typing import Literal, Annotated
from pydantic import model_validator, Field, model_serializer
from bookflow.commands.common import CommonOut
from bookflow.company.journal_outputs import CreatedOutput, JournalMoneyOutput as MoneyOutput
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.sales_models import StrictModel
from bookflow.company.work_facts import WorkFacts, WorkLineFacts
from bookflow.company.work_tax_facts import WorkFacts2, WorkLineFacts2
from bookflow.core.models import WriteOutput
from bookflow.company.tax_attribution import TaxDetails


class WorkLineOutput(CreatedOutput):
    tax_ordinal: int | None = None

    @model_serializer(mode='wrap')
    def legacy_ordinal(self,handler):
        result=handler(self)
        if self.tax_ordinal is None:result.pop('tax_ordinal',None)
        return result

    document_id: str
    revision_id: str
    line_id: str
    position: int
    root_document_id: str
    root_line_id: str
    source_line_id: str | None
    facts: Annotated[WorkLineFacts | WorkLineFacts2, Field(discriminator="schema_version", description="Version1 keeps independent component arithmetic; version2 contains cells allocated by the complete document calculation.")]
    quantity: str
    completed_quantity: str
    unit_price: MoneyOutput | None
    net: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    estimated_unit_cost: MoneyOutput | None
    estimated_cost: MoneyOutput | None


class WorkRevisionSummary(CreatedOutput):
    tax_calculation_details: TaxDetails | None = None

    @model_serializer(mode='wrap')
    def historical_tax_projection(self, handler):
        result = handler(self)
        if self.tax_calculation_details is None:
            result.pop('tax_calculation_details', None)
        return result

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
    facts: Annotated[WorkFacts | WorkFacts2, Field(discriminator="schema_version", description="Work root1 requires profile1; root2 requires profile2 with captured tax policy and origin.")]
    custom_fields_snapshot: dict[str, SnapshotField]
    custom_fields: list[SnapshotField]
    lines: list[WorkLineOutput]
    known_cost_total: MoneyOutput
    cost_complete: bool
    estimated_profit: MoneyOutput | None

    @model_validator(mode='after')
    def line_version_matrix(self):
        from bookflow.company.tax_policy import effective, LEGACY
        if self.facts.schema_version==1 and any(line.facts.schema_version!=1 for line in self.lines):
            raise ValueError('legacy work revisions require legacy line facts')
        if effective(self.facts.profile)!=LEGACY and any(line.facts.schema_version!=2 for line in self.lines):
            raise ValueError('combined-policy work requires version2 line facts')
        return self


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
