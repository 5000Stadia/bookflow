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
    if not percent and isinstance(value, str) and value.startswith('.'):
        value = '0' + value
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,6})?', value):
        raise ValueError('use a positive decimal string with at most six fractional places')
    parsed = parse_percentage_millionths(value) if percent else parse_quantity_micro_units(value)
    if parsed <= 0 or (percent and parsed > 100_000_000):
        raise ValueError('use a positive percentage no greater than 100' if percent else 'quantity must be positive')
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
        for name in ('selections', 'percent', 'apply_prepayment', 'shipments'):
            if name not in self.model_fields_set:
                values.pop(name, None)
        # true is the default, so naming it is the same request as leaving it out.
        if values.get('apply_prepayment') is True:
            values.pop('apply_prepayment')
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


class PrepaymentChoice(StrictModel):
    apply_prepayment: bool = Field(default=True, description=(
        'Use the customer prepayment held on this work (and, for a work order, on its estimate) toward the new invoice: '
        'the lesser of what is held and the invoice total, oldest receipt first, drawn by a credit memo applied in the '
        'same step. false leaves it held. The preview reports prepayment_applied and balance_due.'))


class EstimateInvoiceInput(ConversionInput, InvoiceFields, PrepaymentChoice):
    estimate: CanonicalId


class WorkOrderInvoiceInput(ConversionInput, InvoiceFields, PrepaymentChoice):
    work_order: CanonicalId


class OrderSelection(StrictModel):
    """One order line and the quantity to invoice from it: name the line, or the item one open line carries."""
    line_id: CanonicalId | None = None
    item: Selector | None = None
    quantity: PartialQuantity

    @model_validator(mode='after')
    def one_line(self):
        if (self.line_id is None) == (self.item is None):
            raise ValueError('name exactly one of line_id or item')
        return self

    # The billing selection reads these; an order selection is a quantity only.
    @property
    def net_amount(self):
        return None

    @property
    def percent(self):
        return None

    @property
    def rebill_allocation_id(self):
        return None


class SalesOrderInvoiceInput(ConversionInput, InvoiceFields, PrepaymentChoice):
    sales_order: Selector
    expected_version: _Version | None = Field(default=None, description='Optional guard: the order version read.')
    conversion_key: str | None = Field(default=None, min_length=1, max_length=128, description=(
        'Optional retry key; default: one per order version.'))
    date: _Date | None = Field(default=None, description='Invoice date; default today.')
    selections: list[OrderSelection] | None = Field(default=None, min_length=1, max_length=200)
    all_open: bool = Field(default=False, description=(
        'Bill every open quantity whatever the stock. Without it (and without line_ids or selections) the invoice '
        'bills the units shipments shipped and no invoice billed yet, and when there are none, what can ship.'))
    shipments: list[Selector] | None = Field(default=None, min_length=1, max_length=50, description=(
        'Bill exactly these shipments\' units not yet invoiced (numbers or ids); the invoice moves no stock.'))
    # `shipment invoice` (R181 increment 4): the shipment's freight to bill, (shipment id, item id, minor units),
    # added as one more line. Set in code only; never part of the request or its hashes.

    @field_validator('selections', 'line_ids')
    @classmethod
    def unique_lines(cls, values, info):
        seen = set()
        for value in values or []:
            key = (value.line_id or ('item', value.item)) if info.field_name == 'selections' else value
            if key in seen:
                raise ValueError('name each order line once')
            seen.add(key)
        return values

    @model_validator(mode='after')
    def order_choices(self):
        if 'percent' in self.model_fields_set:
            raise ValueError('an order is invoiced by quantity; percent is not offered')
        if self.all_open and self.model_fields_set & {'line_ids', 'selections'}:
            raise ValueError('choose all_open, line_ids or selections, not more than one')
        if self.shipments is not None and (self.all_open or self.model_fields_set & {'line_ids', 'selections'}):
            raise ValueError('shipments bills those shipments; leave out all_open, line_ids and selections')
        return self


class ReceiptConversionInput(ConversionInput, ReceiptFields):
    deposit_to: Selector
    amount_received: str | SalesMoneyInput


class EstimateSalesReceiptInput(ReceiptConversionInput):
    estimate: CanonicalId


class WorkOrderSalesReceiptInput(ReceiptConversionInput):
    work_order: CanonicalId


class TimeActivityInvoiceInput(ConversionInput, InvoiceFields):
    time_activity: CanonicalId


class TimeActivitySalesReceiptInput(ReceiptConversionInput):
    time_activity: CanonicalId


class BillingReadInput(StrictModel):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class SalesOrderBillingInput(BillingReadInput):
    sales_order: Selector


class EstimateBillingInput(BillingReadInput):
    estimate: CanonicalId


class WorkOrderBillingInput(BillingReadInput):
    work_order: CanonicalId


class TimeActivityBillingInput(BillingReadInput):
    time_activity: CanonicalId
