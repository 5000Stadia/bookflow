"""Inputs and outputs of vehicles, mileage rates, trips, trip billing, reimbursement and mileage reports (R118)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from bookflow.company.billing_models import BillingReadInput, CanonicalId, ConversionInput, InvoiceFields, ReceiptFields
from bookflow.company.check_models import CheckParty
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.ledger_reports import MoneyOutput
from bookflow.company.sales_models import Fingerprint, SalesMoneyInput, Selector, Text


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


Miles = str
_MILES = ('Miles as a decimal number of up to six places, such as 42 or 12.5.')

# ---------------------------------------------------------------------------- vehicles


class VehicleCreateInput(_Strict):
    name: str = Field(min_length=1, max_length=200, description='Vehicle name, such as "Ford Transit 2019 White"; unique.')
    description: str | None = Field(None, max_length=256, description='Make, model, plate or VIN.')


class VehicleUpdateInput(_Strict):
    vehicle: Selector = Field(description='Vehicle ID or name.')
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=256)
    active: bool | None = Field(None, description='false hides the vehicle from new trips; its trips stay.')

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        if 'description' not in self.model_fields_set:
            values.pop('description', None)
        return values


class VehicleShowInput(_Strict):
    vehicle: Selector = Field(description='Vehicle ID or name.')


class VehicleListInput(_Strict):
    include_inactive: bool = False


class VehicleOutput(_Strict):
    id: str
    version: int
    name: str
    description: str | None
    active: bool
    trip_count: int = 0
    total_miles: str = '0'


class VehicleWriteOutput(VehicleOutput):
    changed: bool = True


class VehiclePageOutput(_Strict):
    vehicles: list[VehicleOutput]


# ---------------------------------------------------------------------------- rates


class MileageRateSetInput(_Strict):
    effective_date: _Date = Field(description='First date the rate applies to; it holds until the next rate.')
    rate: str = Field(min_length=1, max_length=16, description='Deduction rate per mile in the home currency, such as 0.655; at most four decimal places.')


class MileageRateDeleteInput(_Strict):
    effective_date: _Date


class MileageRateListInput(_Strict):
    pass


class MileageRateOutput(_Strict):
    id: str
    version: int
    effective_date: str
    rate: str
    currency: str


class MileageRateWriteOutput(MileageRateOutput):
    changed: bool = True
    deleted: bool = False


class MileageRatePageOutput(_Strict):
    rates: list[MileageRateOutput]


# ---------------------------------------------------------------------------- trips


class _TripFields(_Strict):
    trip_start_date: _Date | None = Field(None, description='Date the trip started; omitted, the end date.')
    odometer_start: Miles | None = Field(None, description='Odometer at the start. ' + _MILES)
    odometer_end: Miles | None = Field(None, description='Odometer at the end; with odometer_start, the miles are the difference.')
    total_miles: Miles | None = Field(None, description='Miles driven, when the odometer was not read. ' + _MILES)
    customer: Selector | None = Field(None, description='Customer or job the trip was for; required when billable.')
    item: Selector | None = Field(None, description='Service or other-charge item the miles are billed as; required when billable. Its price is the price per mile.')
    billing_rate: str | SalesMoneyInput | None = Field(None, description='Price per mile charged to the customer, overriding the item price.')
    class_id: Selector | None = None
    notes: Text | None = Field(None, description='Why the trip was made. On a billable trip it is the invoice line description.')


class TripCreateInput(_TripFields):
    vehicle: Selector = Field(description='Vehicle ID or name.')
    trip_end_date: _Date = Field(description='Date the trip ended; the mileage rate in effect on it applies.')
    billable: bool = Field(False, description='Bill these miles to the customer:job through `trip invoice`.')


class TripUpdateInput(_TripFields):
    trip: Selector = Field(description='Trip ID.')
    expected_version: _Version
    vehicle: Selector | None = None
    trip_end_date: _Date | None = None
    billable: bool | None = None

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        for key in list(values):
            if key not in self.model_fields_set and key not in ('trip', 'expected_version'):
                values.pop(key, None)
        return values

    @model_validator(mode='after')
    def required_values(self):
        for field in ('vehicle', 'trip_end_date', 'billable'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class TripVoidInput(_Strict):
    trip: Selector = Field(description='Trip ID.')
    expected_version: _Version


class TripShowInput(_Strict):
    trip: Selector = Field(description='Trip ID.')


class TripQueryInput(_Strict):
    vehicle: Selector | None = None
    customer: Selector | None = Field(None, description='Customer or job; a job is its own customer.')
    date_from: _Date | None = Field(None, description='Trips ending on or after this date.')
    date_to: _Date | None = Field(None, description='Trips ending on or before this date.')
    billable: bool | None = None
    unreimbursed: bool = Field(False, description='Only recorded trips not yet paid back by a posted check.')
    include_voided: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)


class TripOutput(_Strict):
    id: str
    version: int
    status: Literal['recorded', 'voided']
    vehicle_id: str
    vehicle_name: str
    trip_start_date: str
    trip_end_date: str
    odometer_start: str | None
    odometer_end: str | None
    miles: str
    billable: bool
    customer_id: str | None
    customer_name: str | None
    item_id: str | None
    item_name: str | None
    billing_rate: str | None
    class_id: str | None
    notes: str | None
    void_reason: str | None
    deduction_rate: str | None = Field(description='Mileage rate in effect on the trip end date; null when no rate is set for it.')
    deduction: MoneyOutput | None = Field(description='Miles x deduction rate, rounded half to even; null without a rate.')
    billing_document_id: str | None = Field(description='The one-line customer-work document of kind mileage that bills this trip.')
    billing_status: Literal['not_billable', 'unbilled', 'no_charge', 'partially_billed', 'billed']
    billable_amount: MoneyOutput | None
    invoices: list[str] = Field(default_factory=list, description='Sales (invoices or sales receipts) currently carrying these miles.')
    reimbursed_by: str | None = Field(description='Posted check that paid the trip back, if any.')


class TripWriteOutput(TripOutput):
    changed: bool = True
    warnings: list[str] = Field(default_factory=list)


class TripPageOutput(_Strict):
    trips: list[TripOutput]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------- billing


class TripInvoiceInput(ConversionInput, InvoiceFields):
    trip: Selector = Field(description='Billable trip ID; expected_version is the trip version.')


class TripSalesReceiptInput(ConversionInput, ReceiptFields):
    trip: Selector = Field(description='Billable trip ID; expected_version is the trip version.')
    deposit_to: Selector
    amount_received: str | SalesMoneyInput


class TripBillingInput(BillingReadInput):
    trip: Selector


class MileageInvoiceInput(ConversionInput, InvoiceFields):
    """The work billing input `trip invoice` is translated to: the trip's billing document."""
    mileage: CanonicalId


class MileageSalesReceiptInput(ConversionInput, ReceiptFields):
    mileage: CanonicalId
    deposit_to: Selector
    amount_received: str | SalesMoneyInput


class MileageBillingInput(BillingReadInput):
    mileage: CanonicalId


# ---------------------------------------------------------------------------- reimbursement


class TripReimburseInput(_Strict):
    trips: list[Selector] = Field(min_length=1, max_length=199, description='Recorded trips to pay back, by ID; each at most once ever while its check stands.')
    pay_to: CheckParty = Field(description='Who drove: an employee (name_type employee) or a vendor.')
    account: Selector = Field(description='Bank account the check is drawn on.')
    expense_account: Selector = Field(description='Expense account the mileage is charged to, such as Travel:Mileage.')
    date: _Date
    number: _Number | None = Field(None, description='Check number; omitted, the account next check number.')
    rate: str | None = Field(None, max_length=16, description='Rate per mile instead of the mileage rate in effect on each trip end date.')
    memo: Text | None = None
    expected_facts_fingerprint: Fingerprint | None = None

    @model_validator(mode='after')
    def _distinct(self):
        if len({t.upper() for t in self.trips}) != len(self.trips):
            raise ValueError('name each trip once')
        return self


class ReimbursedTrip(_Strict):
    trip_id: str
    trip_end_date: str
    vehicle_name: str
    miles: str
    rate: str
    amount: MoneyOutput


class TripReimburseOutput(_Strict):
    check_id: str | None
    check_number: str | None
    date: str
    pay_to: str
    total: MoneyOutput
    trips: list[ReimbursedTrip]
    facts_fingerprint: str


# ---------------------------------------------------------------------------- reports


class MileageReportInput(_Strict):
    date_from: _Date | None = Field(None, description='First trip end date; omitted, the start of the calendar year of date_to.')
    date_to: _Date | None = Field(None, description="Last trip end date; omitted, today in the company's timezone.")
    detail: bool = Field(False, description='One row per trip instead of one per group.')


class MileageByVehicleInput(MileageReportInput):
    vehicle: Selector | None = None


class MileageByJobInput(MileageReportInput):
    customer: Selector | None = Field(None, description='One customer or job; a job is its own customer.')


class MileageTripRow(_Strict):
    trip_id: str
    trip_start_date: str
    trip_end_date: str
    vehicle_name: str
    customer_name: str | None
    miles: str
    billable: bool
    rate: str | None
    amount: MoneyOutput | None
    billing_status: str | None = None
    notes: str | None


class MileageGroupRow(_Strict):
    key: str | None
    name: str
    trips: int
    miles: str
    billable_miles: str | None = None
    amount: MoneyOutput
    unrated_miles: str = Field('0', description='Miles on trips whose end date has no mileage rate (vehicle report).')
    detail: list[MileageTripRow] = Field(default_factory=list)


class MileageReportOutput(_Strict):
    report: str
    date_from: str
    date_to: str
    currency: str
    rows: list[MileageGroupRow]
    total_miles: str
    total_amount: MoneyOutput
