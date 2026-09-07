"""Bounded durable recovery receipts and separately refreshed lifecycle."""
from __future__ import annotations
from typing import Literal
from pydantic import Field, JsonValue
from bookflow.company.sales_models import StrictModel
from bookflow.core.models import WriteOutput

class ConsumedOperation(StrictModel):
    id: str
    operation_key: str
    command: str
    payment_id: str | None

class Lifecycle(StrictModel):
    state: Literal['open', 'recovery_uploading', 'recovery_review', 'consumed']
    selection_id: str
    selection_version: int
    selection_revision_id: str
    recovery_id: str | None = None
    attempt_generation: str | None = None
    recovery_version: int | None = None
    received_entry_count: int = 0
    declared_entry_count: int = 0
    consumed_operation: ConsumedOperation | None = None

class Receipt(StrictModel):
    action: Literal['begin', 'upload', 'seal', 'apply', 'abort', 'replace']
    request_hash: str
    recovery_id: str | None
    selection_id: str
    recovery_version: int
    selection_version: int
    revision_id: str | None = None
    replacement_recovery_id: str | None = None
    actor_id: str | None = None
    recorded_at: str | None = None
    audit_event_id: str | None = None
    chunk_index: int | None = None
    received_entry_count: int
    declared_entry_count: int

class RecoveryWriteOutput(WriteOutput):
    changed: bool = True
    idempotent_replay: bool = False
    original_receipt: Receipt
    current: Lifecycle
    comparison: ComparisonOutput | None = None

class RecoveryOutput(StrictModel):
    id: str
    selection_id: str
    recovery_key: str
    state: Literal['uploading','sealed','applied','aborted','superseded']
    version: int
    attempt_generation: str
    intent_hash: str
    local_baseline_revision_id: str
    anchor_revision_id: str
    anchor_selection_version: int
    header_intent: dict[str, JsonValue]
    received_entry_count: int
    declared_entry_count: int
    missing_chunk_count: int
    applied_revision_id: str | None
    begin_receipt: Receipt
    seal_receipt: Receipt | None
    terminal_receipt: Receipt | None
    current: Lifecycle

class RecoveryPageOutput(StrictModel):
    items: list[RecoveryOutput]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str

class ItemsOutput(StrictModel):
    items: list[dict[str, JsonValue]]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str

class ComparisonOutput(StrictModel):
    comparison_schema_version: Literal[1] = 1
    recovery_id: str
    attempt_generation: str
    intent_hash: str
    selection_id: str
    anchor_revision_id: str
    anchor_selection_version: int
    recovery_version: int
    facts_fingerprint: str
    amount_minor_units: int | None
    amount_origin: Literal['entered','selection_total','unresolved']
    currency: str
    selected_minor_units: int | None
    resolved_subtotal_minor_units: int
    unapplied_minor_units: int | None
    header_comparison: dict[str, JsonValue]
    item_count: int
    change_count: int
    problem_count: int
    hard_blocker_count: int
    changes_command: Literal['payment recovery compare-items'] = 'payment recovery compare-items'
    problems_command: Literal['payment recovery compare-items'] = 'payment recovery compare-items'


RecoveryWriteOutput.model_rebuild()
