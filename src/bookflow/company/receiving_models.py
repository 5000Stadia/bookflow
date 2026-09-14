"""Typed public receipt lifecycle and exact source selection."""
from typing import Literal
from pydantic import Field, model_validator
from bookflow.company.bill_models import BillItemInput, BillProfile, BillItemProfile, SalesMoneyInput
from bookflow.company.journal_models import _Input, _Date, _Selector, _Version, _Number
from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.core.models import WriteOutput


class ReceiptItemInput(BillItemInput):
    order_line_id: _Selector | None = None


class ReceiptPostInput(_Input):
    date: _Date
    vendor: _Selector
    ap_account: _Selector | None = None
    number: _Number | None = None
    reference: str | None = Field(default=None, max_length=128)
    memo: str | None = Field(default=None, max_length=2000)
    purchase_order: _Selector | None = None
    purchase_order_version: _Version | None = None
    shipping: str | SalesMoneyInput | None = Field(default=None, description='Shipping charged by this receipt vendor, allocated only over received quantities. Product costs exclude shipping. Omit to retain on edits; enter 0 to clear.')
    items: list[ReceiptItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def selection(self):
        if self.purchase_order and self.purchase_order_version is None:
            raise ValueError('purchase_order_version is required for a purchase order selection')
        if any(i.line_id is not None for i in self.items):
            raise ValueError('new receipt lines cannot supply existing identities')
        return self


class ReceiptUpdateInput(_Input):
    receipt: _Selector
    expected_version: _Version | None = None
    date: _Date | None = None
    vendor: _Selector | None = None
    ap_account: _Selector | None = None
    reference: str | None = Field(default=None, max_length=128)
    memo: str | None = Field(default=None, max_length=2000)
    shipping: str | SalesMoneyInput | None = Field(default=None, description='Shipping charged by this receipt vendor, allocated only over received quantities. Product costs exclude shipping. Omit to retain on edits; enter 0 to clear.')
    items: list[ReceiptItemInput] | None = Field(default=None, min_length=1, max_length=100)
    purchase_order_version: _Version | None = None


    @model_validator(mode='after')
    def required_values(self):
        for field in ('date', 'vendor', 'ap_account', 'items', 'shipping'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(field + ' cannot be null; omit it to retain captured facts')
        return self


class ReceiptVoidInput(_Input):
    receipt: _Selector
    expected_version: _Version | None = None


class ReceiptShowInput(_Input):
    receipt: _Selector
    revision_number: _Version | None = None


class ReceiptQueryInput(_Input):
    vendor: _Selector | None = None
    date_from: _Date | None = None
    date_to: _Date | None = None
    status: Literal['posted', 'voided'] | None = None
    unbilled_only: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class ReceiptHistoryInput(_Input):
    receipt: _Selector


class ReceiptLineOutput(_Input):
    id: str
    line_id: str
    movement_id: str
    quantity: str
    quantity_microunits: int
    amount: JournalMoneyOutput
    product_amount: JournalMoneyOutput
    shipping: JournalMoneyOutput
    product_per_unit: str
    shipping_per_unit: str
    total_per_unit: str
    unbilled_quantity_microunits: int
    unbilled_value: JournalMoneyOutput
    order_line_id: str | None = None
    description: str | None
    profile: BillItemProfile


class ReceiptOutput(_Input):
    id: str
    type: Literal['item_receipt'] = 'item_receipt'
    number: str
    version: int
    status: Literal['posted', 'voided']
    revision_number: int
    current_revision_id: str
    revision_id: str
    transaction_id: str
    financial_revision_id: str
    date: str
    reference: str | None
    memo: str | None
    void_reason: str | None = None
    purchase_order_id: str | None
    profile: BillProfile
    total: JournalMoneyOutput
    product_total: JournalMoneyOutput
    shipping: JournalMoneyOutput
    receipt_liability_current: JournalMoneyOutput
    items: list[ReceiptLineOutput]


class ReceiptWriteOutput(ReceiptOutput, WriteOutput):
    changed: bool = True


class ReceiptPageOutput(_Input):
    items: list[ReceiptOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class ReceiptHistoryOutput(_Input):
    id: str
    version: int
    items: list[ReceiptOutput]
