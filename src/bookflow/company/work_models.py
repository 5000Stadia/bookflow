"""Strict inputs for non-posting customer work; lifecycle belongs to the service."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator, model_serializer

from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.sales_models import (
    Address, Fingerprint, Quantity, SalesLineInput, SalesMoneyInput, SalesPageInput,
    Selector, StrictModel, Text, _default_conflicts,
)
from bookflow.core.exact import (
    format_percentage_millionths, format_quantity_micro_units,
    parse_percentage_millionths, parse_quantity_micro_units,
)

# The customer-work document kinds, declared once. The service, the default resolver, the
# sales-profile reader, the outputs and the custom-field scopes all derive their copy from
# this tuple, so a new kind arrives in one place rather than in seven that drift apart.
WORK_KINDS = ('proposal', 'estimate', 'work_order', 'time_activity')

WorkKind = Literal[WORK_KINDS]
WorkStatus = Literal['draft', 'open', 'accepted', 'declined', 'superseded', 'cancelled',
                     'scheduled', 'in_progress', 'on_hold', 'complete', 'recorded']
DecisionStatus = Literal['draft', 'open', 'accepted', 'declined', 'superseded', 'cancelled']
OperationalStatus = Literal['draft', 'scheduled', 'in_progress', 'on_hold', 'complete', 'cancelled']
Priority = Literal['low', 'normal', 'high', 'urgent']
CanonicalId = Annotated[str, Field(pattern=r'^[0-7][0-9A-HJKMNP-TV-Z]{25}$')]
Title = Annotated[str, Field(min_length=1, max_length=200)]
ScopeText = Annotated[str, Field(max_length=10000)]
from bookflow.company.tax_policy import Policy

WorkHeaderDefault = Literal['sales_tax_calculation', 'billing_address', 'shipping_address', 'terms', 'ship_method',
    'sales_rep', 'class_id', 'customer_tax_code', 'sales_tax_item', 'price_level']
WorkLineDefault = Literal['description', 'unit', 'unit_price', 'class_id', 'tax_code',
                          'price_level', 'estimated_unit_cost']


def _completed(value):
    units = parse_quantity_micro_units(value, field='completed_quantity')
    if units < 0:
        raise ValueError('completed_quantity must be nonnegative')
    return format_quantity_micro_units(units)


def _markup(value):
    units = parse_percentage_millionths(value, field='markup_percent')
    if not -100_000_000 <= units <= 1_000_000_000_000:
        raise ValueError('markup_percent must be between -100 and 1000000')
    return format_percentage_millionths(units)


def _timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value
    ):
        raise ValueError('use an ISO8601 timestamp with timezone')
    try:
        stamp = datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError('timestamp must fit supported ISO years') from None
    return stamp.isoformat().replace('+00:00', 'Z')


Timestamp = Annotated[str, BeforeValidator(_timestamp)]
CompletedQuantity = Annotated[str, BeforeValidator(_completed)]
Markup = Annotated[str, BeforeValidator(_markup)]


class WorkLineInput(SalesLineInput):
    estimated_unit_cost: str | SalesMoneyInput | None = None
    markup_percent: Markup | None = None
    net_amount: str | SalesMoneyInput | None = None
    completed_quantity: CompletedQuantity = '0'
    billable: bool = True
    use_defaults: list[WorkLineDefault] = Field(default_factory=list, max_length=7)

    @model_validator(mode='after')
    def work_prices(self):
        supplied = self.model_fields_set
        modes = supplied & {'unit_price', 'markup_percent', 'net_amount'}
        if len(modes) > 1:
            raise ValueError('select only one of unit_price, markup_percent and net_amount')
        for field in modes:
            if getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null; use_defaults unit_price returns to catalog')
        if modes & {'markup_percent', 'net_amount'} and 'price_level' in supplied:
            raise ValueError('price_level conflicts with markup_percent or net_amount')
        if modes and 'unit_price' in self.use_defaults:
            raise ValueError('price mode conflicts with use_defaults unit_price')
        if ('completed_quantity' in supplied and 'quantity' in supplied
                and parse_quantity_micro_units(self.completed_quantity) > parse_quantity_micro_units(self.quantity)):
            raise ValueError('completed_quantity cannot exceed quantity')
        return self


WorkLines = Annotated[list[WorkLineInput], Field(max_length=200)]
EstimateLines = Annotated[list[WorkLineInput], Field(min_length=1, max_length=200)]


class WorkFields(StrictModel):
    sales_tax_calculation: Policy = Field(None, description='Captured document tax policy; omission retains or selects the creation default; null rejects')

    @model_serializer(mode='wrap')
    def legacy_policy_request(self, handler):
        result=handler(self)
        if 'sales_tax_calculation' not in self.model_fields_set:result.pop('sales_tax_calculation',None)
        return result

    number: _Number | None = None
    memo: Text | None = None
    scope: ScopeText | None = None
    inclusions: ScopeText | None = None
    exclusions: ScopeText | None = None
    timing: ScopeText | None = None
    commercial_terms: ScopeText | None = None
    terms: Selector | None = None
    customer_message: Text | None = None
    customer_message_item: Selector | None = None
    customer_purchase_order: str | None = Field(default=None, max_length=128)
    billing_address: Address | None = None
    shipping_address: Address | None = None
    shipping_address_id: Selector | None = None
    ship_date: _Date | None = None
    ship_method: Selector | None = None
    sales_rep: Selector | None = None
    class_id: Selector | None = None
    customer_tax_code: Selector | None = None
    sales_tax_item: Selector | None = None
    price_level: Selector | None = None
    refresh_defaults: bool = False
    use_defaults: list[WorkHeaderDefault] = Field(default_factory=list, max_length=10)
    expected_facts_fingerprint: Fingerprint | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))

    @model_validator(mode='after')
    def defaults(self):
        _default_conflicts(self)
        for left, right in (('customer_message', 'customer_message_item'), ('shipping_address', 'shipping_address_id')):
            if left in self.model_fields_set and right in self.model_fields_set:
                raise ValueError(f'choose {left} or {right}, not both')
        if 'shipping_address' in self.use_defaults and 'shipping_address_id' in self.model_fields_set:
            raise ValueError('selected shipping address conflicts with shipping defaults')
        if 'number' in self.model_fields_set and self.number is None:
            raise ValueError('number cannot be null')
        return self


class OperationalFields(StrictModel):
    priority: Priority = 'normal'
    site_address: Address | None = None
    assignees: list[Selector] = Field(default_factory=list, max_length=50)
    scheduled_start: Timestamp | None = None
    scheduled_end: Timestamp | None = None
    actual_start: Timestamp | None = None
    actual_end: Timestamp | None = None

    @model_validator(mode='after')
    def operational_values(self):
        if len(set(self.assignees)) != len(self.assignees):
            raise ValueError('assignees must be distinct')
        for prefix in ('scheduled', 'actual'):
            start, end = getattr(self, prefix + '_start'), getattr(self, prefix + '_end')
            if start and end and datetime.fromisoformat(end) < datetime.fromisoformat(start):
                raise ValueError(f'{prefix}_end cannot precede {prefix}_start')
        return self


class WorkCreateInput(WorkFields):
    date: _Date
    customer: Selector
    title: Title
    lines: WorkLines = Field(default_factory=list)

    @model_validator(mode='after')
    def new_lines(self):
        if any('line_id' in line.model_fields_set for line in self.lines):
            raise ValueError('new work lines cannot supply persistent line identities')
        return self


class DecisionLines(StrictModel):
    @model_validator(mode='after')
    def no_completion(self):
        if any('completed_quantity' in line.model_fields_set for line in (self.lines or [])):
            raise ValueError('completed_quantity is only available on work orders')
        return self


class ProposalCreateInput(WorkCreateInput, DecisionLines):
    pass


class EstimateCreateInput(WorkCreateInput, DecisionLines):
    lines: EstimateLines
    expires_on: _Date | None = None

    @model_validator(mode='after')
    def expiry(self):
        if self.expires_on and self.expires_on < self.date:
            raise ValueError('expires_on cannot precede date')
        return self


class WorkOrderCreateInput(WorkCreateInput, OperationalFields):
    @model_validator(mode='after')
    def start_required(self):
        for prefix in ('scheduled', 'actual'):
            if getattr(self, prefix + '_end') and not getattr(self, prefix + '_start'):
                raise ValueError(f'{prefix}_end requires {prefix}_start')
        return self


class WorkUpdateInput(WorkFields):
    expected_version: _Version
    date: _Date | None = None
    customer: Selector | None = None
    title: Title | None = None
    active: bool = True
    lines: WorkLines | None = None

    @model_validator(mode='after')
    def required_values(self):
        for field in ('date', 'customer', 'title', 'lines'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class DecisionFields(StrictModel):
    status: DecisionStatus = 'draft'
    decision_note: str | None = Field(default=None, min_length=1, max_length=2000)


class ProposalUpdateInput(WorkUpdateInput, DecisionLines, DecisionFields):
    proposal: Selector


class EstimateUpdateInput(WorkUpdateInput, DecisionLines, DecisionFields):
    estimate: Selector
    lines: EstimateLines | None = None
    expires_on: _Date | None = None
    acknowledge_expired: bool = False

    @model_validator(mode='after')
    def expiry(self):
        if self.date and self.expires_on and self.expires_on < self.date:
            raise ValueError('expires_on cannot precede date')
        return self


class WorkOrderUpdateInput(WorkUpdateInput, OperationalFields):
    work_order: Selector
    status: OperationalStatus = 'draft'


class WorkShowInput(StrictModel):
    links_cursor: str | None = Field(default=None, max_length=2048)
    revision_number: _Version | None = None


class ProposalShowInput(WorkShowInput):
    proposal: Selector


class EstimateShowInput(WorkShowInput):
    estimate: Selector


class WorkOrderShowInput(WorkShowInput):
    work_order: Selector


class ProposalHistoryInput(SalesPageInput):
    proposal: Selector


class EstimateHistoryInput(SalesPageInput):
    estimate: Selector


class WorkOrderHistoryInput(SalesPageInput):
    work_order: Selector


class WorkQueryInput(SalesPageInput):
    customer: Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    date_from: _Date | None = None
    date_to: _Date | None = None
    status: str | None = Field(default=None, min_length=1, max_length=32)
    active: bool | None = True
    minimum_net: str | SalesMoneyInput | None = None
    maximum_net: str | SalesMoneyInput | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the document-date then stable-id page: asc pages the oldest document '
                    'first, desc the most recent first. A cursor belongs to the direction that minted '
                    'it; changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def interval(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class DestinationInput(StrictModel):
    expected_version: _Version
    expected_facts_fingerprint: Fingerprint | None = None
    date: _Date
    number: _Number | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))

    @model_validator(mode='after')
    def number_value(self):
        if 'number' in self.model_fields_set and self.number is None:
            raise ValueError('number cannot be null')
        return self


class WorkCopyInput(DestinationInput):
    title: Title | None = None

    @model_validator(mode='after')
    def title_value(self):
        if 'title' in self.model_fields_set and self.title is None:
            raise ValueError('title cannot be null')
        return self


class ProposalCopyInput(WorkCopyInput):
    proposal: CanonicalId


class EstimateCopyInput(WorkCopyInput):
    estimate: CanonicalId
    expires_on: _Date | None = None
    copy_mode: Literal['alternative', 'independent'] = 'alternative'

    @model_validator(mode='after')
    def expiry(self):
        if self.expires_on and self.expires_on < self.date:
            raise ValueError('expires_on cannot precede date')
        return self


class WorkOrderCopyInput(WorkCopyInput):
    work_order: CanonicalId


class ConversionInput(DestinationInput):
    conversion_key: str = Field(min_length=1, max_length=128)


class ProposalEstimateInput(ConversionInput):
    proposal: CanonicalId
    expires_on: _Date | None = None

    @model_validator(mode='after')
    def expiry(self):
        if self.expires_on and self.expires_on < self.date:
            raise ValueError('expires_on cannot precede date')
        return self


class EstimateWorkOrderInput(ConversionInput):
    estimate: CanonicalId
    scheduled_start: Timestamp | None = None
    scheduled_end: Timestamp | None = None
    assignees: list[Selector] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def schedule(self):
        if len(set(self.assignees)) != len(self.assignees):
            raise ValueError('assignees must be distinct')
        if self.scheduled_end and (not self.scheduled_start or
                datetime.fromisoformat(self.scheduled_end) < datetime.fromisoformat(self.scheduled_start)):
            raise ValueError('scheduled_end requires a start no later than end')
        return self


class EstimateVoidInput(StrictModel):
    """Void an estimate. Non-posting, so nothing is reversed; the reason is the record."""

    estimate: Selector
    expected_version: _Version
    expected_facts_fingerprint: Fingerprint | None = None


class WorkOrderCompleteInput(StrictModel):
    work_order: Selector
    expected_version: _Version
    expected_facts_fingerprint: Fingerprint | None = None
    actual_start: Timestamp | None = None
    actual_end: Timestamp | None = None


def _duration(value):
    """Worked time as a quantity of hours, in the millionths every quantity here uses.

    Hours, not minutes: the stored value is a work line's ``quantity_microunits`` -- the
    same field a quoted quantity uses -- multiplied by a rate per hour to reach the charge.
    Minutes as integers would be a second quantity convention in a system that already has
    one, and would turn an hourly rate into a division. Six decimal places is what that
    field carries, so a duration is exact at the precision everything downstream is exact
    at, and a decimal string is the only spelling: an ``H:MM`` entry would have to round
    twenty minutes to 0.333333 hours and hand a rounded quantity to an exact extension.
    """
    units = parse_quantity_micro_units(value, field='duration')
    if units <= 0:
        raise ValueError('duration must be greater than zero')
    if units > 24_000_000:
        raise ValueError('duration cannot exceed 24 hours; record another day separately')
    return format_quantity_micro_units(units)


Duration = Annotated[str, BeforeValidator(_duration)]


class TimeActivityFields(StrictModel):
    """What a person records about time worked, and nothing a work order needs."""

    employee: Selector = Field(description='Who did the work: an employee ID or name.')
    customer: Selector = Field(description='The customer or job the time was worked for; a job is its own customer.')
    date: _Date = Field(description='The day the work was done.')
    duration: Duration = Field(description='How long, as decimal hours: 1.5 is an hour and a half, 0.25 is fifteen minutes, 0.333333 is twenty. Up to six decimal places, because the charge is this quantity multiplied by a rate per hour and the multiplication is exact.')
    item: Selector = Field(description='Required. The service item this time is charged as: it carries the rate, the income account the labour lands in and the tax code the invoice needs. Time with no item has nowhere to post, so there is no default and no way to leave it out.')
    note: Text | None = Field(default=None, description='What was done. It becomes the line description a customer reads on the invoice.')
    billable: bool = Field(default=True, description='Whether this time can be carried into an invoice. Non-billable time is recorded against the job and never billed.')
    rate: str | SalesMoneyInput | None = Field(default=None, description='Charge per hour, overriding the service item price.')
    class_id: Selector | None = None
    number: _Number | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))
    expected_facts_fingerprint: Fingerprint | None = None


class TimeActivityCreateInput(TimeActivityFields):
    pass


class TimeActivityUpdateInput(TimeActivityFields):
    time_activity: Selector
    expected_version: _Version
    employee: Selector | None = None
    customer: Selector | None = None
    date: _Date | None = None
    duration: Duration | None = None
    item: Selector | None = None
    billable: bool | None = None

    @model_validator(mode='after')
    def required_values(self):
        for field in ('employee', 'customer', 'date', 'duration', 'item', 'billable'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        # A field left out and a field set to null are different corrections, and the
        # idempotency hash is taken from this dump: without this a retry under one key
        # could replay the other.
        values = handler(self)
        for key in ('note', 'rate', 'class_id'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class TimeActivityVoidInput(StrictModel):
    """Withdraw a time entry. It posts nothing, so nothing is reversed; the reason is the record."""

    time_activity: Selector
    expected_version: _Version
    expected_facts_fingerprint: Fingerprint | None = None


class TimeActivityShowInput(WorkShowInput):
    time_activity: Selector


class TimeActivityHistoryInput(SalesPageInput):
    time_activity: Selector


class _TimeActivityWorkCreateInput(WorkCreateInput, OperationalFields):
    """The customer-work input a recorded time entry is, built by the service from the small one."""

    status: Literal['recorded'] = 'recorded'


class _TimeActivityWorkUpdateInput(WorkUpdateInput, OperationalFields):
    time_activity: Selector
    status: Literal['recorded'] = 'recorded'
