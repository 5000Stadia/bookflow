"""Exact-version deletion of a captured invoice or sales receipt."""
from typing import Literal
from pydantic import Field, model_serializer
from bookflow.company.journal_models import _Input, _Selector, _Version
from bookflow.core.models import WriteOutput


class _Delete(_Input):
    expected_version: _Version
    operation_key: str | None = Field(default=None, min_length=1, max_length=200,
        description='Permanent retry identity; otherwise the context idempotency key is retained.')


class InvoiceDeleteInput(_Delete):
    invoice: _Selector
    release_to_order: Literal['reopen', 'cancel'] = Field(default='reopen', description=(
        'For lines made from a sales order or work order that this correction takes back: reopen (the default) '
        'leaves that quantity open on the order again, committed and billable; cancel cancels it on the order in '
        'the same transaction. The preview and result name the order lines either way.'))

    @model_serializer(mode='wrap')
    def compatible_request(self, handler):
        values = handler(self)
        if 'release_to_order' not in self.model_fields_set:
            values.pop('release_to_order', None)
        return values


class SalesReceiptDeleteInput(_Delete):
    sales_receipt: _Selector


class SalesDeleteOutput(WriteOutput):
    id: str
    family: Literal['invoice', 'sales_receipt']
    status: Literal['deleted'] = 'deleted'
    version: int
    revision_id: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
    cancelled_stock_movements: int
    changed: bool = True
    idempotent_replay: bool = False


class SalesDeletionInfo(_Input):
    created_by_name: str | None = None
    principal_name: str | None = None
    created_at: str
    created_by: str
    principal_id: str | None
    created_via: str
    reason: str
    from_status: Literal['posted', 'voided']
    cancellation_batch_id: str | None
