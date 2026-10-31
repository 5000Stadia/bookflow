"""Till return contracts; accounting accounts and costs are fixed by the register."""
from typing import Literal
from pydantic import Field, model_validator
from bookflow.company.pos_sale_models import _Strict, Ticket, Amount
from bookflow.company.sales_models import Selector, Quantity
from bookflow.company.pos_models import Named, PaymentMethodRef
from bookflow.company.items import MoneyOutput


class RefundLineInput(_Strict):
    source_line: Selector | None = None
    item: Selector | None = None
    quantity: Quantity = '1'
    unit_price: Amount | None = None
    serial_numbers: list[str] | None = Field(None, max_length=1000)

    @model_validator(mode='after')
    def origin(self):
        if (self.source_line is None) == (self.item is None):
            raise ValueError('name a source_line or an item, never both')
        if self.source_line and self.unit_price is not None:
            raise ValueError('a receipt return keeps the captured price')
        return self


class PosRefundInput(_Strict):
    operation_key: Ticket
    sale: Selector | None = None
    pos_register: Selector | None = None
    customer: Selector | None = None
    lines: list[RefundLineInput] = Field(min_length=1, max_length=200)
    settlement: Literal['cash', 'original', 'store_credit'] = 'cash'
    reference: str | None = Field(None, max_length=128)
    expected_total: Amount | None = Field(None, description='Total shown by the dry run; a changed total refuses posting.')

    @model_validator(mode='after')
    def origin(self):
        if any(bool(line.source_line) != bool(self.sale) for line in self.lines):
            raise ValueError('with a receipt name source_line on every line; without one name items')
        if self.sale and self.customer is not None:
            raise ValueError('a receipt return keeps the original customer')
        if not self.sale and self.settlement == 'original':
            raise ValueError('original tender requires a receipt; choose cash or store_credit')
        return self


class PosRefundLinesInput(_Strict):
    sale: Selector


class PosRefundShowInput(_Strict):
    refund: Selector


class RefundLineOutput(_Strict):
    source_line: str | None = None
    item_id: str
    item_name: str
    quantity: str
    available_quantity: str | None = None
    tracking: str = 'none'
    unit_price: MoneyOutput | None = None
    net: MoneyOutput
    tax: MoneyOutput


class PosRefundLinesOutput(_Strict):
    sale_id: str
    receipt_number: str
    pos_register: Named
    customer: Named | None
    payment_method: PaymentMethodRef
    lines: list[RefundLineOutput]


class PosRefundOutput(_Strict):
    id: str
    operation_key: str
    sale_id: str | None
    pos_register: Named
    customer: Named | None
    credit_memo: Named
    refund: Named | None = None
    date: str
    created_at: str
    clerk_id: str
    settlement: Literal['cash', 'original', 'store_credit']
    payment_method: PaymentMethodRef | None
    reference: str | None
    lines: list[RefundLineOutput]
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    replayed: bool = False
    warnings: list[str] = Field(default_factory=list)
