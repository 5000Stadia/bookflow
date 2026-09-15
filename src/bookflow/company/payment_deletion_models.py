"""Exact-version deletion of a captured customer payment."""
from typing import Literal
from pydantic import Field
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.core.models import WriteOutput


class PaymentDeleteInput(_Input):
    payment: _Selector
    expected_version: _Version
    operation_key: str | None = Field(default=None, min_length=1, max_length=200,
        description='Permanent retry identity; otherwise the context idempotency key is retained.')


class PaymentDeleteOutput(WriteOutput):
    id: str
    family: Literal['payment'] = 'payment'
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    number: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    cancelled_posting_lines: int
    changed: bool = True
    idempotent_replay: bool = False


class PaymentDeletionInfo(_Input):
    created_by_name: str | None = None
    principal_name: str | None = None
    created_at: str
    created_by: str
    principal_id: str | None
    created_via: str
    reason: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
