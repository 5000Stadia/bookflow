"""Typed drawer count and daily till reports."""
from typing import Literal
from pydantic import Field
from bookflow.company.pos_sale_models import _Strict, Ticket, Amount
from bookflow.company.sales_models import Selector
from bookflow.company.journal_models import _Date
from bookflow.company.pos_models import Named, PaymentMethodRef
from bookflow.company.items import MoneyOutput

class ShiftOpenInput(_Strict):
    pos_register: Selector | None = None
    float: Amount
    operation_key: Ticket

class ShiftCloseInput(_Strict):
    shift: Selector
    counted: Amount
    operation_key: Ticket

class DayInput(_Strict):
    pos_register: Selector | None = None
    date: _Date | None = None
    shift: Selector | None = None

class ShiftOutput(_Strict):
    id: str
    pos_register: Named
    status: Literal['open', 'closed']
    date: str
    opened_at: str
    opened_by: str
    float: MoneyOutput
    closed_at: str | None = None
    closed_by: str | None = None
    counted: MoneyOutput | None = None
    expected: MoneyOutput
    over_short: MoneyOutput | None = None
    replayed: bool = False

class DayTender(_Strict):
    payment_method: PaymentMethodRef
    count: int
    amount: MoneyOutput
    refund_count: int
    refund_amount: MoneyOutput

class HeldTicket(_Strict):
    id: str
    label: str
    status: str
    held_at: str
    older_than_seven_days: bool
    total: MoneyOutput

class DayOutput(_Strict):
    date: str
    pos_register: Named
    shift: str | None = None
    sales_count: int
    gross_sales: MoneyOutput
    discounts: MoneyOutput
    tax: MoneyOutput
    total_sales: MoneyOutput
    refunds_total: MoneyOutput
    refund_tax: MoneyOutput
    voids_count: int
    voids_total: MoneyOutput
    cash_sales: MoneyOutput
    cash_refunds: MoneyOutput
    cash_to_deposit: MoneyOutput
    expected_drawer: MoneyOutput
    by_tender: list[DayTender]
    shifts: list[ShiftOutput]
    held_tickets: list[HeldTicket]
