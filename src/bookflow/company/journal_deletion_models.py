"""Exact-version deletion of a journal entry."""
from typing import Literal
from pydantic import Field
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.core.models import WriteOutput


class JournalDeleteInput(_Input):
    journal: _Selector
    expected_version: _Version
    operation_key: str | None = Field(default=None, min_length=1, max_length=200,
        description='Permanent retry identity; otherwise the context idempotency key is retained.')


class JournalDeleteOutput(WriteOutput):
    id: str
    family: Literal['journal_entry'] = 'journal_entry'
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    number: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    changed: bool = True
    idempotent_replay: bool = False


class JournalDeletionInfo(_Input):
    created_by_name: str | None = None
    principal_name: str | None = None
    created_at: str
    created_by: str
    principal_id: str | None
    created_via: str
    reason: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
