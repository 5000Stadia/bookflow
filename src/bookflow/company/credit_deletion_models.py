"""Exact-version deletion of a customer credit memo."""
from typing import Literal
from pydantic import Field
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.core.models import WriteOutput


class CreditMemoDeleteInput(_Input):
    credit_memo: _Selector
    expected_version: _Version
    operation_key: str | None = Field(default=None, min_length=1, max_length=200,
        description='Permanent retry identity; otherwise the context idempotency key is retained.')


class CreditMemoDeleteOutput(WriteOutput):
    id: str
    family: Literal['credit_memo'] = 'credit_memo'
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    number: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    # What the person has to know before confirming: how much of a customer's
    # returned invoice quantity comes back to the invoice that issued it. A credit
    # that anything still stands on refuses instead, so this is never a cascade.
    released_source_claims: int
    source_invoice_ids: list[str] = Field(default_factory=list)
    changed: bool = True
    idempotent_replay: bool = False


class CreditMemoDeletionInfo(_Input):
    created_by_name: str | None = None
    principal_name: str | None = None
    created_at: str
    created_by: str
    principal_id: str | None
    created_via: str
    reason: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
