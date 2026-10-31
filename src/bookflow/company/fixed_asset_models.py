"""Inputs and outputs of the `fixed-asset` and `loan` commands (R119)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company.journal_models import _Date, _Version
from bookflow.company.ledger_reports import MoneyOutput
from bookflow.company.sales_models import Selector
from bookflow.core.models import WriteOutput

Amount = Annotated[str, Field(min_length=1, max_length=40)]
Memo = Annotated[str, Field(max_length=2000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


# ---------------------------------------------------------------------------------- fixed assets

class FixedAssetSelector(_Strict):
    item: Selector = Field(description='Fixed-asset item, by id or full name.')


class FixedAssetSetupInput(FixedAssetSelector):
    method: Literal['straight_line', 'declining_balance'] | None = Field(
        None, description='Book depreciation method. Required the first time; kept when omitted later.')
    declining_percent: Literal[150, 200] | None = Field(
        None, description='Declining-balance rate as a percent of the straight-line rate: 200 (double declining) or 150. Required with declining_balance; it switches to straight line when that is larger.')
    salvage: Amount | None = Field(None, description='Salvage value, e.g. 1000.00; the asset is never depreciated below it. Omitted: 0.00 the first time, kept later.')
    in_service_date: _Date | None = Field(None, description="Date placed in service; depreciation starts with its month. Omitted the first time: the item's purchase date.")
    life_months: int | None = Field(None, strict=True, ge=1, le=1200, description="Useful life in months, e.g. 60. Omitted the first time: the item's useful life.")
    period: Literal['month', 'year'] | None = Field(None, description='Depreciate by the month or by the year (twelve months from the in-service month). Omitted the first time: month.')
    accumulated_depreciation_account: Selector | None = Field(None, description="Accumulated depreciation account, set on the item; omitted, the item's.")
    depreciation_expense_account: Selector | None = Field(None, description="Depreciation expense account, set on the item; omitted, the item's.")
    gain_loss_account: Selector | None = Field(None, description="Gain or loss on disposal account, set on the item; omitted, the item's.")
    expected_version: _Version | None = Field(None, description='Version of the setup this change was decided from (fixed-asset schedule).')


class FixedAssetListInput(_Strict):
    include_disposed: bool = Field(True, description='Include assets sold or disposed of.')
    limit: int = Field(200, strict=True, ge=1, le=500, description='Maximum assets returned.')


class FixedAssetDepreciateInput(_Strict):
    through: _Date = Field(description='Post every period ending on or before this date that is not posted yet.')
    assets: list[Selector] | None = Field(None, max_length=500, description='Only these fixed-asset items; omitted, every asset set up for depreciation.')
    entry: Literal['per_period', 'combined'] = Field(
        'per_period', description='per_period: one journal entry per period end date, dated that day, covering every asset due then. combined: one journal entry dated `through` covering every pending period.')
    memo: Memo | None = Field(None, description="Journal memo; omitted, 'Depreciation' with the period.")


class FixedAssetDisposeInput(FixedAssetSelector):
    date: _Date = Field(description='Date the asset was sold or disposed of.')
    proceeds: Amount = Field('0.00', description='What it sold for; 0.00 when scrapped.')
    costs: Amount = Field('0.00', description='Costs of the sale, taken from the proceeds.')
    deposit_to: Selector | None = Field(None, description='Account the net proceeds go to (a bank account or Undeposited Funds); required when proceeds less costs is not zero.')
    memo: Memo | None = Field(None, description="Journal memo; omitted, 'Disposal of' and the asset.")


class FixedAssetPeriod(_Strict):
    number: int
    start: str
    end: str
    months: int
    amount: MoneyOutput
    basis: Literal['straight_line', 'declining_balance'] = Field(description='The method that gave this amount; declining balance switches to straight line when that is larger.')
    status: Literal['posted', 'pending', 'stopped'] = Field(description='posted: a posted journal entry carries it. pending: not posted yet. stopped: after the asset was disposed of.')
    transaction_id: str | None = None
    journal_number: str | None = None
    accumulated: MoneyOutput = Field(description='Accumulated depreciation at the end of the period.')
    book_value: MoneyOutput = Field(description='Book value at the end of the period.')


class FixedAssetDisposal(_Strict):
    date: str
    proceeds: MoneyOutput
    costs: MoneyOutput
    cost: MoneyOutput
    accumulated: MoneyOutput
    gain: MoneyOutput = Field(description='Gain (positive) or loss (negative).')
    deposit_account: str | None
    transaction_id: str
    journal_number: str


class FixedAssetScheduleOutput(_Strict):
    item_id: str
    item: str
    asset_number: str | None
    set_up: bool = Field(description='Whether the asset has a depreciation setup; without one, rows is empty.')
    version: int | None = Field(None, description='Version of the depreciation setup.')
    method: str | None = None
    declining_percent: int | None = None
    salvage: MoneyOutput | None = None
    in_service_date: str | None = None
    life_months: int | None = None
    period: str | None = None
    cost: MoneyOutput | None = Field(None, description="Cost basis: the item's book basis, else its original cost.")
    asset_account: str | None = None
    accumulated_depreciation_account: str | None = None
    depreciation_expense_account: str | None = None
    gain_loss_account: str | None = None
    status: Literal['in_service', 'disposed']
    accumulated_posted: MoneyOutput | None = Field(None, description='Depreciation posted so far.')
    book_value: MoneyOutput | None = Field(None, description='Cost less depreciation posted so far.')
    pending_periods: int = 0
    rows: list[FixedAssetPeriod] = Field(default_factory=list)
    disposal: FixedAssetDisposal | None = None


class FixedAssetSetupOutput(WriteOutput):
    schedule: FixedAssetScheduleOutput
    changed: bool = True


class FixedAssetSummary(_Strict):
    item_id: str
    item: str
    asset_number: str | None
    set_up: bool
    status: Literal['in_service', 'disposed']
    cost: MoneyOutput | None
    accumulated_posted: MoneyOutput | None
    book_value: MoneyOutput | None
    pending_periods: int
    next_period_end: str | None


class FixedAssetListOutput(_Strict):
    items: list[FixedAssetSummary]


class DepreciationLine(_Strict):
    item_id: str
    item: str
    periods: list[int]
    amount: MoneyOutput


class DepreciationEntry(_Strict):
    date: str
    transaction_id: str | None = None
    journal_number: str | None = None
    total: MoneyOutput
    lines: list[DepreciationLine]


class FixedAssetDepreciateOutput(WriteOutput):
    batch: str | None = Field(default=None, description="The reversible batch written by this call; absent on previews or no-op calls.")
    next: str | None = Field(default=None, description="How to preview reversing this batch.")
    through: str
    entry: Literal['per_period', 'combined']
    entries: list[DepreciationEntry]
    periods_posted: int = Field(description='Asset periods posted (or, on a preview, that would be).')
    total: MoneyOutput
    summary: str


class DisposalLine(_Strict):
    account: str
    debit: MoneyOutput | None = None
    credit: MoneyOutput | None = None


class FixedAssetDisposeOutput(WriteOutput):
    item_id: str
    item: str
    date: str
    proceeds: MoneyOutput
    costs: MoneyOutput
    cost: MoneyOutput
    accumulated: MoneyOutput
    gain: MoneyOutput = Field(description='Gain (positive) or loss (negative).')
    lines: list[DisposalLine]
    transaction_id: str | None = None
    journal_number: str | None = None
    summary: str


# ------------------------------------------------------------------------------------------- loans

class LoanSelector(_Strict):
    loan: Selector = Field(description='Loan, by id or exact name (ignoring case).')


class LoanCreateInput(_Strict):
    name: Annotated[str, Field(min_length=1, max_length=200)] = Field(description='Name for the loan, e.g. Truck loan; unique, ignoring case.')
    liability_account: Selector = Field(description='Liability account carrying the balance (other current or long-term liability). Record the money borrowed into it with a deposit or journal entry, as the anchor asks.')
    interest_account: Selector = Field(description='Expense or other expense account interest is charged to.')
    principal: Amount = Field(description='Original amount borrowed, e.g. 25000.00.')
    annual_rate: Amount = Field(description='Annual interest rate in percent, e.g. 6.5.')
    term_payments: int = Field(strict=True, ge=1, le=3000, description='Number of payments over the full term, e.g. 60 for five years paid monthly.')
    first_payment_date: _Date = Field(description='Due date of payment 1.')
    frequency: Literal['weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'annual'] = Field('monthly', description='How often payments fall due.')
    origination_date: _Date | None = Field(None, description='Date the loan was taken out.')
    lender: Selector | None = Field(None, description='Vendor lending the money: the payee on payment checks.')
    payment_account: Selector | None = Field(None, description='Bank or credit card account payments come from unless `loan pay` names another.')
    balloon: Amount = Field('0.00', description='Balloon due with the last payment; 0.00 when the loan pays down fully.')
    escrow: Amount = Field('0.00', description='Escrow added to each scheduled payment.')
    escrow_account: Selector | None = Field(None, description='Account escrow is paid into (usually an other current asset); required with escrow.')
    payment: Amount | None = Field(None, description='Regular principal and interest payment from the loan documents; omitted, the level payment computed from the terms.')


class LoanUpdateInput(LoanSelector):
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    interest_account: Selector | None = None
    lender: Selector | None = None
    payment_account: Selector | None = None
    annual_rate: Amount | None = Field(None, description='New annual rate in percent; the remaining schedule is recomputed from the balance now owed.')
    escrow: Amount | None = Field(None, description='New escrow per scheduled payment.')
    escrow_account: Selector | None = None
    payment: Amount | None = Field(None, description='New regular principal and interest payment. Omitted with a new rate: the level payment over the payments left.')
    expected_version: _Version | None = Field(None, description='Version of the loan this change was decided from (loan show).')


class LoanListInput(_Strict):
    limit: int = Field(200, strict=True, ge=1, le=500)


class LoanPayInput(LoanSelector):
    date: _Date | None = Field(None, description='Date of the check or charge; omitted, the due date of the next scheduled payment.')
    from_account: Selector | None = Field(None, description="Bank account (writes a check) or credit card (writes a card charge); omitted, the loan's payment account.")
    number: Annotated[str, Field(min_length=1, max_length=64)] | None = Field(None, description='Check number; omitted, the next one for the bank account.')
    extra_principal: Amount | None = Field(None, description='Principal paid beyond the schedule. The remaining schedule is recomputed: same payment, fewer payments.')
    extra_only: bool = Field(False, description="An extra payment outside the schedule (the anchor's 'An extra payment'): only extra_principal is paid, no interest or escrow, and the next scheduled payment stays due.")
    memo: Memo | None = None


class LoanScheduleRow(_Strict):
    number: int | None = Field(description='Scheduled payment number; null for an extra principal payment.')
    status: Literal['paid', 'remaining']
    due_date: str | None
    payment_date: str | None = None
    principal: MoneyOutput
    interest: MoneyOutput
    escrow: MoneyOutput
    total: MoneyOutput
    balance: MoneyOutput = Field(description='Principal owed after this payment.')
    transaction_id: str | None = None
    document_number: str | None = None


class LoanOutput(_Strict):
    id: str
    version: int
    name: str
    lender_id: str | None
    lender: str | None
    liability_account_id: str
    liability_account: str
    interest_account: str
    escrow_account: str | None
    payment_account: str | None
    principal: MoneyOutput
    annual_rate: str
    term_payments: int
    frequency: str
    origination_date: str | None
    first_payment_date: str
    payment: MoneyOutput = Field(description='Regular principal and interest payment.')
    escrow: MoneyOutput
    balloon: MoneyOutput
    balance: MoneyOutput = Field(description='Principal still owed by the schedule after the payments recorded.')
    liability_balance: MoneyOutput = Field(description='Balance of the liability account in the books, all dates.')
    ties: bool = Field(description='Whether the liability account balance equals the schedule balance.')
    paid_count: int
    remaining_count: int
    maturity_date: str | None
    interest_paid: MoneyOutput
    interest_remaining: MoneyOutput
    next_payment: LoanScheduleRow | None


class LoanShowOutput(_Strict):
    loan: LoanOutput
    rows: list[LoanScheduleRow]


class LoanWriteOutput(WriteOutput):
    loan: LoanShowOutput
    changed: bool = True


class LoanPageOutput(_Strict):
    items: list[LoanOutput]


class LoanPayOutput(WriteOutput):
    loan_id: str
    loan: str
    payment_number: int | None
    date: str
    principal: MoneyOutput
    interest: MoneyOutput
    escrow: MoneyOutput
    total: MoneyOutput
    balance_after: MoneyOutput
    document_type: Literal['check', 'card_charge']
    transaction_id: str | None = None
    document_number: str | None = None
    summary: str
