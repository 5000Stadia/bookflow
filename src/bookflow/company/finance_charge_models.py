"""Inputs and outputs of `finance-charge assess`."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookflow.company.journal_models import _Date
from bookflow.company.ledger_reports import MoneyOutput
from bookflow.company.sales_models import Fingerprint, Selector, Text


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FinanceChargeChoice(_Strict):
    customer: Selector = Field(description='Customer or job to assess, by id or full name.')
    amount: str | None = Field(None, description='Charge to assess instead of the computed one, e.g. 4.50; above 0.00.')


class FinanceChargeAssessInput(_Strict):
    date: _Date | None = Field(None, description="Assessment date: the charges are computed through it and dated on it. Omitted, today in the company's timezone.")
    customers: list[FinanceChargeChoice] | None = Field(
        None, max_length=1000,
        description='Which customers and jobs to assess, each optionally with a changed amount. Omitted: every '
                    'customer and job with a proposed charge, as the anchor marks them all. Name a customer once.')
    mark_to_print: bool | None = Field(None, description='Mark the charges to be printed; omitted, the company preference.')
    memo: Text | None = Field(None, description="What the customer reads on the statement; omitted, 'Finance charge'.")
    expected_facts_fingerprint: Fingerprint | None = Field(
        None, description='The facts_fingerprint of the preview this assessment was decided from; a change since is refused with E_PREVIEW_STALE.')

    @model_validator(mode='after')
    def _once(self):
        if self.customers is not None:
            names = [choice.customer.casefold() for choice in self.customers]
            if len(names) != len(set(names)):
                raise ValueError('name each customer once')
        return self


class FinanceChargeDocument(_Strict):
    transaction_id: str
    document_type: str
    number: str | None
    date: str
    due_date: str
    open_balance: MoneyOutput
    counted_from: str = Field(description='Day the interest on this document starts: its due date or date, or the last finance charge date when later.')
    days: int
    finance_charge: bool = Field(description='This document is itself an earlier finance charge.')


class FinanceChargeRow(_Strict):
    customer_id: str
    customer_label: str
    parent_id: str | None = None
    status: Literal['will_assess', 'assessed', 'not_selected', 'already_assessed']
    overdue_balance: MoneyOutput
    last_finance_charge_date: str | None
    computed: MoneyOutput = Field(description='The charge the policy gives: exact interest rounded once to the cent, half to even, then the minimum.')
    charge: MoneyOutput = Field(description='The charge assessed (or to be): the computed one unless a different amount was given.')
    unapplied_credits: MoneyOutput = Field(description='Payments and credits this customer has not applied. The anchor does not subtract them; it flags the customer so they can be applied first.')
    has_unapplied_credits: bool
    transaction_id: str | None = None
    number: str | None = None
    documents: list[FinanceChargeDocument]


class FinanceChargeAssessOutput(_Strict):
    batch: str | None = Field(default=None, description="The reversible batch written by this call; absent on previews or no-op calls.")
    next: str | None = Field(default=None, description="How to preview reversing this batch.")
    date: str
    annual_rate: str
    minimum: MoneyOutput
    grace_days: int
    calculated_from: Literal['due_date', 'invoice_date']
    on_overdue_charges: bool
    mark_to_print: bool
    account: str
    item: str
    currency: str
    rows: list[FinanceChargeRow]
    assessed_count: int = Field(description='Charges written (or, on a preview, that would be).')
    total: MoneyOutput = Field(description='Total of the charges written (or that would be).')
    facts_fingerprint: str
    summary: str
