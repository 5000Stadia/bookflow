"""Line attribution contracts for invoices, sales receipts and POS sales; no commission rates, earnings or payments."""
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


class MemberCommissionInput(StrictModel):
    member: int = Field(ge=1, le=200, description="1-based position of the group's active member, in member order.")
    commission: CommissionInput


class CapturedRecipient(StrictModel):
    recipient_id: str
    recipient_label: str
    share: Literal[100] = 100


class RecipientLabel(StrictModel):
    recipient_id: str
    recipient_label: str


class CommissionLabels(StrictModel):
    """Who was chosen, as any seller may see it: no rates, amounts or private recipient fields."""
    recipients: list[RecipientLabel]


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
    document_kind: Literal['invoice', 'sales_receipt', 'pos_sale'] = 'invoice'
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
    invoice_id: str = Field(description='The posted document: an invoice or a sales receipt (a POS sale posts either).')
    document_type: Literal['invoice', 'sales_receipt'] = 'invoice'
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


class PosCommissionOptionsInput(StrictModel):
    item: str | None = Field(None, min_length=1, max_length=1004, description='Item ID or name the clerk just rang up.')
    customer: str | None = Field(None, min_length=1, max_length=1004, description="The ticket's chosen customer, whose header rep is only suggested.")
    search: str = Field('', max_length=200)
    limit: int = Field(100, ge=1, le=200)


class CommissionReview(StrictModel):
    """A held line whose selection no longer stands; shown before Charge and re-asked by the till."""
    line: int = Field(description='1-based position of the ticket line.')
    member: int | None = Field(None, description='1-based group member, when the line is a group.')
    item_id: str
    problem: Literal['recipient_inactive', 'selection_required', 'item_not_enabled']
    recipient_id: str | None = None
    recipient_label: str | None = None
