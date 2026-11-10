"""Saved native bill proposal contracts."""
from typing import Literal, Any
from pydantic import Field, model_validator
from bookflow.company.journal_models import _Input, _Selector, _Date, _Version, _Number
from bookflow.company.bill_models import BillExpenseInput, SupplierReference, Text
from bookflow.company.journal_outputs import JournalMoneyOutput


from bookflow.company.bill_models import _BillFields, Expenses, Items, OrderLineSelection, ReceiptBillingInput

State = Literal['draft', 'pending', 'approved', 'rejected', 'committed', 'cancelled']

class Payload(_BillFields):
    date: _Date
    vendor: _Selector | None = None
    expenses: Expenses | None = None
    items: Items | None = None
    purchase_order: _Selector | None = None
    order_lines: list[OrderLineSelection] | None = None
    receipts: list[ReceiptBillingInput] | None = None


class CreateInput(Payload):
    kind: Literal['bill'] = 'bill'
    operation: Literal['create', 'correct', 'restore'] = 'create'
    original_id: _Selector | None = None
    original_version: _Version | None = None
    operation_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode='after')
    def original_target(self):
        if self.operation == 'create' and (self.original_id is not None or self.original_version is not None):
            raise ValueError('create does not accept an original bill')
        if self.operation != 'create' and (self.original_id is None or self.original_version is None):
            raise ValueError('correct and restore require an original bill and exact version')
        return self


class UpdateInput(CreateInput):
    draft: _Selector
    expected_version: _Version


class DraftInput(_Input):
    draft: _Selector


class CancelInput(DraftInput):
    expected_version: _Version
    operation_key: str = Field(min_length=1, max_length=128)


class ListInput(_Input):
    state: State | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=200)


class Named(_Input):
    id: str
    name: str


class ExpensePreview(_Input):
    account: Named
    amount: JournalMoneyOutput
    memo: str | None
    customer: Named | None
    class_id: Named | None
    billable: bool


class Preview(_Input):
    total: JournalMoneyOutput
    expenses: list[ExpensePreview]
    vendor: Named
    ap_account: Named
    due_date: str
    currency: str
    accounting_effect: Literal['none'] = 'none'


class Revision(_Input):
    version: int
    state: State
    payload: Payload
    preview: Preview
    actor: str
    principal: str | None
    interface: str
    reason: str
    created_at: str


class DraftOutput(_Input):
    snapshot: dict[str, Any] | None = None
    request_id: str | None = None
    request_version: int | None = None
    id: str
    version: int
    kind: Literal['bill']
    operation: Literal['create', 'correct', 'restore']
    original_id: str | None = None
    original_version: int | None = None
    state: State
    payload: Payload
    preview: Preview
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str
    history: list[Revision]


class ListOutput(_Input):
    items: list[DraftOutput]
    next_cursor: str | None
