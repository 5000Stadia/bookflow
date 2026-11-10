"""Invoice attribution contracts; no commission rates, earnings or payments."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from bookflow.company.journal_models import _Date


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CommissionRecipientInput(StrictModel):
    recipient: str = Field(min_length=1, max_length=1004)
    share: Literal[100] = 100


class CommissionInput(StrictModel):
    recipients: list[CommissionRecipientInput] = Field(min_length=1, max_length=1)


class CapturedRecipient(StrictModel):
    recipient_id: str
    recipient_label: str
    share: Literal[100] = 100


class CommissionSnapshot(StrictModel):
    enabled_at_entry: Literal[True] = True
    recipients: list[CapturedRecipient] = Field(min_length=1, max_length=1)
    net_minor_units: int
    currency: str


class RecipientOptionsInput(StrictModel):
    customer: str | None = Field(None, min_length=1, max_length=1004)
    item: str | None = Field(None, min_length=1, max_length=1004)
    sales_rep: str | None = Field(None, min_length=1, max_length=1004)
    search: str = Field('', max_length=200)
    document_kind: Literal['invoice'] = 'invoice'
    limit: int = Field(100, ge=1, le=200)


class RecipientOption(StrictModel):
    id: str
    name: str


class RecipientOptionsOutput(StrictModel):
    commission_enabled: bool
    items: list[RecipientOption]
    suggested_recipient_id: str | None = None


class CommissionAttributionInput(StrictModel):
    date_from: _Date | None = None
    date_to: _Date | None = None
    recipient: str | None = Field(None, min_length=1, max_length=1004)
    customer: str | None = Field(None, min_length=1, max_length=1004)
    item: str | None = Field(None, min_length=1, max_length=1004)

    @model_validator(mode='after')
    def dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class CommissionAttributionRow(StrictModel):
    invoice_id: str
    number: str
    date: str
    line_id: str
    position: int
    item_id: str
    item_name: str
    recipient_id: str
    recipient_name: str
    net_minor_units: int
    currency: str


class CommissionAttributionTotal(StrictModel):
    recipient_id: str
    recipient_name: str
    net_minor_units: int
    currency: str


class CommissionAttributionOutput(StrictModel):
    rows: list[CommissionAttributionRow]
    totals: list[CommissionAttributionTotal]
