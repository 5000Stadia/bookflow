"""Inputs and outputs of ringing up a POS sale (R114 increment 5, design/point-of-sale.md §3, §4)."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company.items import MoneyOutput
from bookflow.company.pos_models import Named, PaymentMethodRef
from bookflow.company.pos_sale_schema import TICKET_PATTERN
from bookflow.company.journal_models import _Version
from bookflow.company.sales_models import MAX_SALES_LINES, Quantity, Selector
from bookflow.company import serial_lot_models as _SL


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


Ticket = Annotated[str, Field(pattern=TICKET_PATTERN, description=(
    "The ticket's key, chosen by the device when the sale starts (a ULID or any 1-64 letters, digits, '.', '_', ':' "
    "or '-'). It is the sale's operation key: completing again with the same key returns the sale already posted and "
    'never posts a second.'))]
Amount = Annotated[str, Field(min_length=1, max_length=32, pattern=r'^\d+(\.\d{1,2})?$',
                              description='An amount in the home currency, such as "20" or "24.50".')]


class PosLineInput(_Strict):
    item: Selector = Field(description='Item ID or name.')
    quantity: Quantity = Field('1', description='Quantity, in the item base unit; default 1.')
    unit_price: Amount | None = Field(None, description=(
        "A typed unit price, overriding the register's price (needs pos.price-override). Omitted, the price is the "
        'sales receipt\'s own: the item price, the customer\'s price level and the price rules.'))
    serial_numbers: _SL.LineSerials = Field(default=None, max_length=1000, description=(
        'Serial numbers of the units sold, for a serial-tracked item; omitted, the units stay unassigned, as the '
        'sales receipt allows.'))


class _Cart(_Strict):
    pos_register: Selector | None = Field(None, description='Register ID or name; omitted when the company has one.')
    customer: Selector | None = Field(None, description=(
        "Customer ID or name, from `pos customer search` or `add`. Omitted, the sale is the register's walk-in "
        "customer's, which the POS screen and the receipt never show."))
    lines: list[PosLineInput] = Field(max_length=MAX_SALES_LINES, description='The cart, in the order rung up.')


class PosSalePreviewInput(_Cart):
    payment_method: Selector | None = Field(None, description=(
        "Payment method ID or name the sale will be paid by; omitted, the register's first. The total does not "
        'depend on it.'))


class Tender(_Strict):
    payment_method: Selector = Field(description='One of the payment methods the register offers (ID or name).')
    amount: Amount | None = Field(None, description=(
        'Amount tendered. For cash, at least the total (the change is the difference); for any other method it is '
        'the total, the default.'))
    reference: str | None = Field(None, max_length=128, description=(
        'Card or check reference (last four digits, approval code), stored as the receipt\'s payment reference.'))


class PosSaleCompleteInput(_Cart):
    ticket_version: _Version | None = Field(None, ge=1, description="Required version for a resumed held ticket.")
    ticket: Ticket
    tender: Tender
    expected_total: Amount = Field(description=(
        '`pos sale preview`\'s total, which the clerk saw. If the sale now totals anything else (a price changed '
        'meanwhile), nothing posts and E_PREVIEW_STALE carries the new total.'))


class PosLineOutput(_Strict):
    position: int
    item_id: str | None
    item_name: str | None
    description: str | None
    quantity: str | None
    unit_price: MoneyOutput | None
    net: MoneyOutput
    tax: MoneyOutput


class PosSalePreviewOutput(_Strict):
    pos_register: Named
    customer: Named | None = Field(description='The chosen customer; null for the walk-in, which is never shown.')
    payment_method: PaymentMethodRef
    date: str
    lines: list[PosLineOutput] = Field(description=(
        'The lines as the sales receipt will post them (a group item is expanded to its members).'))
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    warnings: list[str] = Field(default_factory=list)


class TenderOutput(_Strict):
    payment_method: PaymentMethodRef
    amount: MoneyOutput
    reference: str | None


class PosSaleOutput(_Strict):
    id: str
    ticket: str
    pos_register: Named
    sales_receipt: Named = Field(description='The sales receipt posted: its id, and its number as `name`.')
    customer: Named | None = Field(description='The chosen customer; null for the walk-in.')
    clerk_id: str
    created_at: str
    date: str
    total: MoneyOutput
    tendered: MoneyOutput
    change: MoneyOutput
    tenders: list[TenderOutput]
    replayed: bool = Field(False, description=(
        'True when this ticket had already completed: this is that sale, and nothing new was posted.'))
    warnings: list[str] = Field(default_factory=list)


class CustomerSearchInput(_Strict):
    q: str = Field(min_length=3, max_length=200, description='At least 3 characters of a name, phone number or email address.')
    limit: int = Field(20, ge=1, le=50)


class PosCustomer(_Strict):
    id: str
    name: str
    phone: str | None
    email: str | None


class CustomerSearchOutput(_Strict):
    items: list[PosCustomer]


class CustomerAddInput(_Strict):
    name: str = Field(min_length=1, max_length=200, description='Customer name; unique among customers.')
    phone: str | None = Field(None, max_length=64)
    email: str | None = Field(None, max_length=254)


class CustomerAddOutput(PosCustomer):
    pass
