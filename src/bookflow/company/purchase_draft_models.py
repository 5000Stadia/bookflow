"""Expense-only saved bill drafts; no posting or approval transition."""
from typing import Literal
from pydantic import Field, model_validator
from bookflow.company.journal_models import _Input, _Selector, _Date, _Version, _Number
from bookflow.company.bill_models import BillExpenseInput, SupplierReference, Text
from bookflow.company.journal_outputs import JournalMoneyOutput


class Payload(_Input):
    date: _Date
    vendor: _Selector
    expenses: list[BillExpenseInput] = Field(min_length=1, max_length=200,
        json_schema_extra={'sample': [{'account': 'Professional Fees', 'amount': '80.00'}]})
    number: _Number | None = None
    ap_account: _Selector | None = None
    terms: _Selector | None = None
    due_date: _Date | None = None
    supplier_reference: SupplierReference | None = None
    memo: Text | None = None
    class_id: _Selector | None = None

    @model_validator(mode='after')
    def new_lines(self):
        if any(line.line_id for line in self.expenses):
            raise ValueError('draft expense rows do not reference posted line identities')
        return self


class CreateInput(Payload):
    kind: Literal['bill'] = 'bill'
    operation: Literal['create'] = 'create'
    operation_key: str = Field(min_length=1, max_length=128)


class UpdateInput(CreateInput):
    draft: _Selector
    expected_version: _Version


class DraftInput(_Input):
    draft: _Selector


class CancelInput(DraftInput):
    expected_version: _Version
    operation_key: str = Field(min_length=1, max_length=128)


class ListInput(_Input):
    state: Literal['draft', 'cancelled'] | None = None
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
    state: Literal['draft', 'cancelled']
    payload: Payload
    preview: Preview
    actor: str
    principal: str | None
    interface: str
    reason: str
    created_at: str


class DraftOutput(_Input):
    id: str
    version: int
    kind: Literal['bill']
    operation: Literal['create']
    state: Literal['draft', 'cancelled']
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
