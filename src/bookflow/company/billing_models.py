"""Whole-line work billing intent; source economics are never caller overrides."""
from typing import Annotated
from pydantic import Field, model_validator
from bookflow.company.sales_models import StrictModel, Selector, Text, Fingerprint, SalesMoneyInput, InvoiceFields, ReceiptFields
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.custom_fields import CustomFieldValuePatch, CustomFieldKindExpectations
from bookflow.company.work_models import CanonicalId


class ConversionInput(StrictModel):
    expected_version: _Version
    conversion_key: str = Field(min_length=1, max_length=128)
    date: _Date
    number: _Number | None = None
    line_ids: list[CanonicalId] | None = Field(default=None, min_length=1, max_length=200)
    memo: Text | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))
    expected_facts_fingerprint: Fingerprint | None = None

    @property
    def refresh_defaults(self):
        return False

    @model_validator(mode='after')
    def distinct(self):
        if self.line_ids is not None and len(self.line_ids) != len(set(self.line_ids)):
            raise ValueError('line_ids must be distinct current source line identities')
        if 'line_ids' in self.model_fields_set and self.line_ids is None:
            raise ValueError('omit line_ids to select remaining work; null is not a selection')
        return self


class EstimateInvoiceInput(ConversionInput, InvoiceFields):
    estimate: CanonicalId


class WorkOrderInvoiceInput(ConversionInput, InvoiceFields):
    work_order: CanonicalId


class ReceiptConversionInput(ConversionInput, ReceiptFields):
    deposit_to: Selector
    amount_received: str | SalesMoneyInput


class EstimateSalesReceiptInput(ReceiptConversionInput):
    estimate: CanonicalId


class WorkOrderSalesReceiptInput(ReceiptConversionInput):
    work_order: CanonicalId


class BillingReadInput(StrictModel):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class EstimateBillingInput(BillingReadInput):
    estimate: CanonicalId


class WorkOrderBillingInput(BillingReadInput):
    work_order: CanonicalId
