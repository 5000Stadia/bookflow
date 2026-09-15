"""Exact-version deletion of a captured vendor bill."""
from typing import Literal
from pydantic import Field
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.core.models import WriteOutput


class BillDeleteInput(_Input):
    bill: _Selector
    expected_version: _Version
    operation_key: str | None = Field(default=None, min_length=1, max_length=200,
        description='Permanent retry identity; otherwise the context idempotency key is retained.')


class BillDeleteOutput(WriteOutput):
    id: str
    family: Literal['bill'] = 'bill'
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    number: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    cancelled_stock_movements: int
    # What the person has to know before confirming: the received lines this bill
    # gives back, and the order it stays recorded against. A purchase order is not
    # freed by deletion, exactly as it is not freed by void, so it is named rather
    # than silently left behind.
    released_receipt_claims: int
    purchase_order_id: str | None = None
    changed: bool = True
    idempotent_replay: bool = False


class BillDeletionInfo(_Input):
    created_by_name: str | None = None
    principal_name: str | None = None
    created_at: str
    created_by: str
    principal_id: str | None
    created_via: str
    reason: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
