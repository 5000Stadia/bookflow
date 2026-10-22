"""Inputs and outputs of the budget commands, the budget and comparison reports, and memorized reports (R109)."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.journal_models import _Version
from bookflow.company.report_basis import Basis, basis_field
from bookflow.company.sales_models import Selector
from bookflow.core.models import WriteOutput

Name = Annotated[str, Field(min_length=1, max_length=200)]
Month = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$", description="A month, YYYY-MM.")]
Amount = Annotated[str, Field(min_length=1, max_length=40)]
CellAmount = Annotated[str, Field(max_length=40)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BudgetSelector(_Strict):
    budget: Selector = Field(description='Budget id, or its exact name (ignoring case).')


class _Cell(_Strict):
    customer: Selector | None = Field(None, description='Customer or job, by id or full name; required on a customer budget, refused on any other.')
    class_id: Selector | None = Field(None, description='Class, by id or full name; required on a class budget, refused on any other.')

    @model_validator(mode='after')
    def _one(self):
        if self.customer is not None and self.class_id is not None:
            raise ValueError('name a customer or a class, not both')
        return self


class BudgetCreateInput(_Strict):
    name: Name = Field(description='Name for the budget; unique within the company, ignoring case.')
    fiscal_year: int = Field(strict=True, ge=1, le=9998, description='Fiscal year to budget, named by the calendar year it starts in. The budget has one column per month of that fiscal year.')
    kind: Literal['profit_and_loss', 'balance_sheet'] = Field('profit_and_loss', description='profit_and_loss: income and expense per month. balance_sheet: the ending balance of each asset, liability and equity account per month.')
    by: Literal['none', 'customer', 'class'] = Field('none', description='none for one company budget; customer or class to keep amounts per customer:job or per class (profit and loss only).')
    from_actuals: bool = Field(False, description="Fill the budget from the previous fiscal year's actual figures, month by month (per customer:job or class when the budget is kept that way). Off: the budget starts empty.")
    basis: Basis | None = basis_field()

    @model_validator(mode='after')
    def _dimension(self):
        if self.kind == 'balance_sheet' and self.by != 'none':
            raise ValueError('a balance sheet budget is kept for the whole company; use by=none')
        return self


class BudgetShowInput(BudgetSelector, _Cell):
    pass


class BudgetListInput(_Strict):
    fiscal_year: int | None = Field(None, strict=True, ge=1, le=9998, description='Only budgets for this fiscal year.')
    limit: int = Field(50, strict=True, ge=1, le=200, description='Maximum budgets returned, from 1 through 200.')
    cursor: str | None = Field(None, max_length=26, description='Id of the last budget on the previous page; omit to start at the first.')


class BudgetSetInput(BudgetSelector, _Cell):
    account: Selector = Field(description='Account to budget, by id, full name or number.')
    start_month: Month | None = Field(None, description='Month the first amount is for; omitted, the first month of the budget.')
    amounts: list[CellAmount] = Field(min_length=1, max_length=12, description="Amounts for consecutive months from start_month, e.g. ['1000.00','1000.00','1250.00']. 0 or an empty string clears that month. Months not given are left alone.")
    expected_version: _Version | None = Field(None, description='Version returned by budget show; a stale write is refused.')


class BudgetFillInput(BudgetSelector, _Cell):
    account: Selector = Field(description='Account whose row is filled, by id, full name or number.')
    start_month: Month | None = Field(None, description='First month filled; omitted, the first month of the budget.')
    through_month: Month | None = Field(None, description='Last month filled; omitted, the last month of the budget.')
    amount: Amount | None = Field(None, description="Amount for start_month. Omitted, the amount already in start_month is copied across (the anchor's Copy Across).")
    change: Amount | None = Field(None, description='Added to each month after the first, compounding: 5000.00 with change 100.00 gives 5000.00, 5100.00, 5200.00, ...')
    change_percent: Amount | None = Field(None, description='Percent each month changes from the month before, compounding and rounded to the cent half to even: 5000.00 at 1 gives 5000.00, 5050.00, 5100.50, ...; negative to decrease.')
    expected_version: _Version | None = Field(None, description='Version returned by budget show; a stale write is refused.')

    @model_validator(mode='after')
    def _change(self):
        if self.change is not None and self.change_percent is not None:
            raise ValueError('give change or change_percent, not both')
        return self


class BudgetClearInput(BudgetSelector, _Cell):
    account: Selector | None = Field(None, description="Only this account's row; omitted, every account.")
    expected_version: _Version | None = Field(None, description='Version returned by budget show; a stale write is refused.')


class BudgetRenameInput(BudgetSelector):
    name: Name = Field(description='New name; unique within the company, ignoring case.')
    expected_version: _Version | None = Field(None, description='Version returned by budget show; a stale rename is refused.')


class BudgetDeleteInput(BudgetSelector):
    expected_version: _Version | None = Field(None, description='Version returned by budget show; a stale delete is refused.')


class BudgetOutput(BaseModel):
    id: str
    version: int
    name: str
    fiscal_year: int
    first_month: str
    months: list[str] = Field(description='The twelve months of the budget, YYYY-MM, in order.')
    kind: Literal['profit_and_loss', 'balance_sheet']
    dimension: Literal['none', 'customer', 'class']
    currency: str
    cell_count: int = Field(description='Months with an amount, across every account, customer and class.')
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class BudgetGridRow(BaseModel):
    account_id: str
    current_account_label: str
    display_account_label: str
    account_type: str
    section: str
    active: bool
    dimension_id: str | None = Field(description='Customer or class id on a customer or class budget; null on a company budget.')
    dimension_label: str | None
    amounts: list[ledger.MoneyOutput] = Field(description='One amount per budget month, in month order; 0.00 where none is set.')
    total: ledger.MoneyOutput


class DimensionValue(BaseModel):
    id: str
    label: str


class BudgetShowOutput(BudgetOutput):
    rows: list[BudgetGridRow] = Field(description='Every account, customer and class with an amount, in statement then account order.')
    dimension_values: list[DimensionValue] = Field(description='Customers or classes that have amounts on this budget.')
    month_totals: list[ledger.MoneyOutput] = Field(description='Net income budgeted per month (income less expense) on a profit and loss budget; the sum of the shown rows on a balance sheet budget.')
    total: ledger.MoneyOutput


class BudgetWriteOutput(WriteOutput):
    budget: BudgetShowOutput | None = None
    changed_cells: int = 0
    deleted: bool = False
    changed: bool = True


class BudgetPageOutput(BaseModel):
    items: list[BudgetOutput]
    count: int
    next_cursor: str | None


# ---------------------------------------------------------------------------- reports

ColumnsBy = Literal['month', 'quarter', 'total']


class ComparisonCell(ledger.StrictModel):
    measure: Literal['actual', 'budget', 'over_budget', 'percent_of_budget', 'current', 'previous', 'change', 'percent_change']
    amount: str | None = Field(description='The figure as text: money in the home currency, or a percent with one decimal place; null where a percent has nothing to divide by.')
    currency: str | None
    minor_units: int | None = Field(description='Money in minor units; null on a percent cell.')


class ReportColumn(ledger.StrictModel):
    label: str = Field(description='Heading of this column: its period and its measure.')
    period: str = Field(description='Period this column belongs to, as a heading reads it.')
    measure: str
    date_from: str | None
    date_to: str


class SummaryLine(ledger.StrictModel):
    key: str
    label: str
    cells: list[ComparisonCell]


class ComparisonRow(ledger.StrictModel):
    account_id: str
    current_account_label: str
    current_account_name: str
    current_account_number: str | None
    display_account_label: str
    account_type: str
    parent_id: str | None
    active: bool
    section: str
    cells: list[ComparisonCell]


class _ReportPage(ledger.Page):
    columns: list[ReportColumn]
    rows: list[ComparisonRow]
    summary: list[SummaryLine] = Field(description='Statement lines -- totals of each section, gross profit, net income -- in the same columns as the rows, over every account.')
    totals: dict[str, dict[str, ComparisonCell]] = Field(description='The last column group (the Total) of each summary line, by measure.')


class _BudgetReportInput(ledger.StrictModel):
    budget: Selector = Field(description='Budget id, or its exact name (ignoring case).')
    customer: Selector | None = Field(None, description='On a customer budget: only this customer or job. Omitted, every customer budgeted, added together.')
    class_id: Selector | None = Field(None, description='On a class budget: only this class. Omitted, every class budgeted, added together.')
    columns: ColumnsBy = Field('month', description='One column group per month, per fiscal quarter, or one for the whole range; month and quarter end with a Total group.')
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)


class BudgetOverviewInput(_BudgetReportInput):
    pass


class BudgetVsActualInput(_BudgetReportInput):
    date_from: str | None = Field(None, min_length=10, max_length=10, description='Inclusive first date; omitted, the first day of the budget year.')
    date_to: str | None = Field(None, min_length=10, max_length=10, description='Inclusive last date; omitted, the last day of the budget year.')
    basis: Basis | None = basis_field()
    include_zero: bool = Field(False, description='Include every active account the budget kind covers, even with nothing budgeted or posted.')

    _from = field_validator('date_from')(lambda v: v if v is None else ledger.iso_date(v))
    _to = field_validator('date_to')(lambda v: v if v is None else ledger.iso_date(v))


class BudgetOverviewOutput(_ReportPage):
    budget: BudgetOutput


class BudgetVsActualOutput(_ReportPage):
    budget: BudgetOutput


class ProfitAndLossComparisonInput(ledger.StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description='Inclusive first accounting date, YYYY-MM-DD.')
    date_to: str = Field(min_length=10, max_length=10, description='Inclusive last accounting date, YYYY-MM-DD.')
    compare: Literal['previous_year', 'previous_period'] = Field('previous_year', description='previous_year: the same dates a year earlier. previous_period: the period of the same length just before (whole months by whole months, otherwise day for day).')
    dollar_change: bool = Field(True, description='Show the $ Change column.')
    percent_change: bool = Field(True, description='Show the % Change column.')
    basis: Basis | None = basis_field()
    include_zero: bool = Field(False, description='Include every active income and expense account, even with nothing posted in either period.')
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _from = field_validator('date_from')(ledger.iso_date)
    _to = field_validator('date_to')(ledger.iso_date)

    @model_validator(mode='after')
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError('date_from must be on or before date_to')
        return self


class BalanceSheetComparisonInput(ledger.StrictModel):
    date_to: str = Field(min_length=10, max_length=10, description='As-of date, YYYY-MM-DD; compared with the same date a year earlier.')
    dollar_change: bool = Field(True, description='Show the $ Change column.')
    percent_change: bool = Field(True, description='Show the % Change column.')
    basis: Basis | None = basis_field()
    include_zero: bool = Field(False, description='Include every active balance sheet account, even with a zero balance on both dates.')
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _to = field_validator('date_to')(ledger.iso_date)


class ProfitAndLossComparisonOutput(_ReportPage):
    compare: Literal['previous_year', 'previous_period']


class BalanceSheetComparisonOutput(_ReportPage):
    compare: Literal['previous_year']


# ---------------------------------------------------------------------------- memorized reports

DATE_RANGES = ('today', 'this_week', 'this_week_to_date', 'this_month', 'this_month_to_date',
               'this_fiscal_quarter', 'this_fiscal_quarter_to_date', 'this_fiscal_year',
               'this_fiscal_year_to_date', 'yesterday', 'last_week', 'last_month', 'last_month_to_date',
               'last_fiscal_quarter', 'last_fiscal_year', 'last_fiscal_year_to_date',
               'this_calendar_year', 'last_calendar_year', 'next_month', 'next_fiscal_quarter',
               'next_fiscal_year')
DateRange = Literal[DATE_RANGES]  # type: ignore[valid-type]


class MemorizedReportSelector(_Strict):
    memorized_report: Selector = Field(description='Memorized report id, or its exact name (ignoring case).')


class MemorizedReportCreateInput(_Strict):
    name: Name = Field(description='Name to keep the report under; unique within the company, ignoring case.')
    report: str = Field(min_length=1, max_length=64, description='The report, by its name: profit-and-loss, budget-vs-actual, ar-aging, ... (`bookflow report --help` lists them).')
    filters: dict[str, Any] = Field(default_factory=dict, description="The report's own inputs, exactly as `report <name>` takes them; cursor and limit are not kept.")
    date_range: DateRange | None = Field(None, description="A named date range resolved again at every run, in the company's timezone and fiscal year: this_month, last_fiscal_year, this_fiscal_year_to_date, ... It replaces the dates in filters. Omitted, the dates in filters are kept as they are.")
    group: Name | None = Field(None, description='Memorized report group to file it under, such as Company or Banking; omitted, no group.')


class MemorizedReportUpdateInput(MemorizedReportSelector):
    name: Name | None = Field(None, description='New name.')
    group: Name | None = Field(None, description='New group.')
    report: str | None = Field(None, min_length=1, max_length=64, description='Replace the report it runs (the anchor\'s Replace when memorizing again).')
    filters: dict[str, Any] | None = Field(None, description="Replace the report's inputs.")
    date_range: DateRange | None = Field(None, description='Replace the named date range.')
    expected_version: _Version | None = Field(None, description='Version returned by memorized-report show; a stale update is refused.')


class MemorizedReportDeleteInput(MemorizedReportSelector):
    expected_version: _Version | None = Field(None, description='Version returned by memorized-report show; a stale delete is refused.')


class MemorizedReportListInput(_Strict):
    group: str | None = Field(None, max_length=200, description='Only reports in this group (ignoring case).')
    limit: int = Field(50, strict=True, ge=1, le=200, description='Maximum reports returned, from 1 through 200.')
    cursor: str | None = Field(None, max_length=26, description='Id of the last report on the previous page; omit to start at the first.')


class MemorizedReportRunInput(MemorizedReportSelector):
    limit: int = Field(default=50, ge=1, le=200, description='Rows on the page.')
    cursor: str | None = Field(default=None, max_length=4096, description="The report's own next_cursor from the previous page.")


class MemorizedReportOutput(BaseModel):
    id: str
    version: int
    name: str
    group: str | None
    report: str
    title: str = Field(description="The report's name as a person reads it.")
    filters: dict[str, Any]
    date_range: str | None
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class MemorizedReportWriteOutput(WriteOutput):
    memorized_report: MemorizedReportOutput | None = None
    deleted: bool = False
    changed: bool = True


class MemorizedReportPageOutput(BaseModel):
    items: list[MemorizedReportOutput]
    count: int
    next_cursor: str | None


class MemorizedReportRunOutput(BaseModel):
    memorized_report: MemorizedReportOutput
    command: str = Field(description='The report command run, e.g. report profit-and-loss.')
    inputs: dict[str, Any] = Field(description='The inputs it ran with, the named date range resolved to dates.')
    result: dict[str, Any] = Field(description="The report's own output, exactly as running the command returns it.")
