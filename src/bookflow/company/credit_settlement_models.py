"""Typed input and results for applying a credit memo to an invoice, and taking it back."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from bookflow.company.credit_models import CreditSourceOutput, MoneyOutput
from bookflow.company.journal_models import _Date, _Version
from bookflow.company.sales_models import Fingerprint, SalesMoneyInput, Selector, StrictModel
from bookflow.core.models import WriteOutput


class CreditApplicationItem(StrictModel):
    """One invoice this credit is applied to, and how much of it goes there."""

    invoice: Selector
    expected_version: _Version
    amount: str | SalesMoneyInput | None = Field(
        default=None,
        description='How much of the credit goes to this invoice; omit to use as much as this '
                    'invoice still owes, taking the invoices in the order you list them.')


class CreditApplyInput(StrictModel):
    credit_memo: Selector
    expected_version: _Version
    applications: Annotated[list[CreditApplicationItem], Field(min_length=1, max_length=200)]
    date: _Date | None = Field(
        default=None, description="Settlement date; omit to use the credit memo's own date.")
    expected_facts_fingerprint: Fingerprint | None = None


class CreditUnapplyReference(StrictModel):
    application_id: Selector
    invoice_expected_version: _Version


class CreditUnapplyInput(StrictModel):
    credit_memo: Selector
    expected_version: _Version
    applications: Annotated[list[CreditUnapplyReference], Field(min_length=1, max_length=200)]
    expected_facts_fingerprint: Fingerprint | None = None


class CreditApplicationOutput(StrictModel):
    application_id: str
    kind: Literal['apply', 'unapply']
    reverses_application_id: str | None
    invoice_id: str
    invoice_number: str
    invoice_version: int
    credit_source_key_id: str
    credit_source_component_id: str
    party_id: str
    amount: MoneyOutput
    effective_date: str


class CreditAllocationOutput(StrictModel):
    allocation_id: str
    kind: Literal['allocation', 'reversal']
    reverses_allocation_id: str | None
    application_id: str
    invoice_id: str
    target_ordinal: int
    logical_kind: Literal['net', 'tax']
    tax_item_id: str | None
    amount: MoneyOutput


class InvoiceSettlementChange(StrictModel):
    invoice_id: str
    invoice_number: str
    version: int
    revision_id: str
    gross_minor_units: int
    applied_minor_units: int
    due_minor_units: int
    currency: str
    status: Literal['unpaid', 'partial', 'paid', 'voided']
    gross: MoneyOutput
    applied: MoneyOutput
    due: MoneyOutput


class CreditSettlementEffect(StrictModel):
    kind: Literal['apply', 'unapply']
    credit_memo_id: str
    audit_event_id: str
    applications: list[CreditApplicationOutput]
    allocations: list[CreditAllocationOutput]
    document_changes: list[InvoiceSettlementChange]


class CreditSettlementOutput(WriteOutput):
    id: str
    version: int
    number: str
    changed: bool = True
    facts_fingerprint: str
    effect: CreditSettlementEffect
    current: CreditSourceOutput
