"""Shared held ticket command contract."""
from typing import Literal
from pydantic import Field
from bookflow.company.pos_sale_models import _Strict, _Cart, Ticket, PosLineInput
from bookflow.company.pos_models import Named
from bookflow.company.items import MoneyOutput
from bookflow.company.journal_models import _Version


class HoldInput(_Cart):
    ticket: Ticket
    device: str = Field("unknown", min_length=1, max_length=128, description="Device identity for ticket hand-off audit.")
    label: str | None = Field(None, max_length=400, description='Optional label; default hold time and first item.')
    expected_version: _Version | None = Field(None, ge=1, description='Required when holding a previously resumed ticket.')


class TicketInput(_Strict):
    ticket: Ticket
    device: str = Field("unknown", min_length=1, max_length=128, description="Device identity for ticket hand-off audit.")
    expected_version: _Version = Field(ge=1, description='Ticket version returned by hold, list or resume.')


class ListInput(_Strict):
    pass


class TicketOutput(_Strict):
    id: str
    version: int
    status: Literal['held', 'open', 'completed', 'discarded']
    label: str
    pos_register: Named
    customer: Named | None
    lines: list[PosLineInput]
    total: MoneyOutput = Field(description="Total when last held; preview resumed lines for current prices before charging.")
    device: str
    clerk_id: str
    clerk_name: str
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class TicketList(_Strict):
    items: list[TicketOutput]
    count: int
