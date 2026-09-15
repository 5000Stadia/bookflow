"""Exact-version deletion of a banked deposit."""
from typing import Literal
from pydantic import Field
from bookflow.company.journal_models import _Input
from bookflow.core.models import WriteOutput


class DepositDeleteOutput(WriteOutput):
    id: str
    family: Literal['deposit'] = 'deposit'
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    number: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    # What the person has to know before confirming: every receipt this deposit banked
    # goes back to Undeposited Funds and can be deposited again, exactly as it does on a
    # void. That is owned handling, not a cascade -- no receipt is touched or deleted.
    released_receipt_ids: list[str] = Field(default_factory=list)
    # The deposit family checks its whole dependency graph between preview and save, so a
    # deletion is confirmed the way every other deposit write is: preview, then save with
    # the guard the preview minted.
    dependency_guard: str
    facts_fingerprint: str
    changed: bool = True
    idempotent_replay: bool = False


class DepositDeletionInfo(_Input):
    created_by_name: str | None = None
    principal_name: str | None = None
    created_at: str
    created_by: str
    principal_id: str | None
    created_via: str
    reason: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
