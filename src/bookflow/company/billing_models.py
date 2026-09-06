"""Whole-line work billing intent; source economics are never caller overrides."""
from typing import Annotated
import re
from pydantic import BeforeValidator, Field, ValidationError, field_validator, model_serializer, model_validator
from bookflow.company.sales_models import StrictModel, Selector, Text, Fingerprint, SalesMoneyInput, InvoiceFields, ReceiptFields
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.custom_fields import CustomFieldValuePatch, CustomFieldKindExpectations
from bookflow.company.work_models import CanonicalId
from bookflow.core.exact import parse_percentage_millionths, parse_quantity_micro_units


def _positive_decimal(value, *, percent=False):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,6})?', value):
        raise ValueError('use a positive decimal string with at most six fractional places')
    parsed = parse_percentage_millionths(value) if percent else parse_quantity_micro_units(value)
    if parsed <= 0 or (percent and parsed > 100_000_000):
        raise ValueError('use a positive percentage no greater than100' if percent else 'quantity must be positive')
    return value


PartialQuantity = Annotated[str, BeforeValidator(_positive_decimal)]
PartialPercent = Annotated[str, BeforeValidator(lambda v: _positive_decimal(v, percent=True))]


class BillingSelection(StrictModel):
    line_id: CanonicalId
    quantity: PartialQuantity | None = None
    net_amount: str | SalesMoneyInput | None = None
    percent: PartialPercent | None = None
    rebill_allocation_id: CanonicalId | None = None

    @model_validator(mode='after')
    def one_mode(self):
        modes = self.model_fields_set - {'line_id'}
        if len(modes) != 1 or any(getattr(self, field) is None for field in modes):
            raise ValueError('select exactly one non-null quantity, net_amount, percent or rebill_allocation_id')
        return self


class ConversionInput(StrictModel):
    expected_version: _Version
    conversion_key: str = Field(min_length=1, max_length=128)
    date: _Date
    number: _Number | None = None
    line_ids: list[CanonicalId] | None = Field(default=None, min_length=1, max_length=200)
    selections: list[BillingSelection] | None = Field(default=None, min_length=1, max_length=200)
    percent: PartialPercent | None = None
    memo: Text | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))
    expected_facts_fingerprint: Fingerprint | None = None

    @model_serializer(mode='wrap')
    def legacy_request(self, handler):
        values = handler(self)
        # Both generic cache identity and permanent conversion hashes predate
        # these optional members. Omission must retain their original bytes.
        for name in ('selections', 'percent'):
            if name not in self.model_fields_set:
                values.pop(name, None)
        return values

    @property
    def refresh_defaults(self):
        return False

    @field_validator('selections', 'line_ids')
    @classmethod
    def unique_lines(cls, values, info):
        seen = set()
        for index, value in enumerate(values or []):
            key = value.line_id if info.field_name == 'selections' else value
            if key in seen:
                location = (index, 'line_id') if info.field_name == 'selections' else (index,)
                raise ValidationError.from_exception_data(cls.__name__, [dict(type='value_error',
                    loc=location, input=key, ctx={'error': ValueError('use distinct current source line identities')})])
            seen.add(key)
        return values

    @model_validator(mode='after')
    def distinct(self):
        families = self.model_fields_set & {'line_ids', 'selections', 'percent'}
        if len(families) > 1 or any(getattr(self, field) is None for field in families):
            raise ValueError('choose one non-null line_ids, selections or percent family; omit all for remaining work')
        if 'due_date' in self.model_fields_set and self.due_date is None:
            raise ValueError('due_date cannot be null; omit it for the captured term default')
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
