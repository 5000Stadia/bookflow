"""Budgets, the budget and comparison reports, and memorized reports (R109).

Three nouns. ``budget`` keeps named plans for a fiscal year, an amount per account per month.
``report`` gains budget-overview, budget-vs-actual, profit-and-loss-comparison and
balance-sheet-comparison. ``memorized-report`` keeps a report and its options under a name.

The work lives in ``company/budgets.py``, ``company/budget_reports.py`` and
``company/memorized_reports.py``.
"""
from bookflow.company import budget_reports as reports, budgets, memorized_reports as memorized
from bookflow.company.budget_models import (
    BalanceSheetComparisonInput, BalanceSheetComparisonOutput, BudgetClearInput, BudgetCreateInput,
    BudgetDeleteInput, BudgetFillInput, BudgetListInput, BudgetOverviewInput, BudgetOverviewOutput,
    BudgetPageOutput, BudgetRenameInput, BudgetSetInput, BudgetShowInput, BudgetShowOutput, BudgetVsActualInput,
    BudgetVsActualOutput, BudgetWriteOutput, MemorizedReportCreateInput, MemorizedReportDeleteInput,
    MemorizedReportListInput, MemorizedReportOutput, MemorizedReportPageOutput, MemorizedReportRunInput,
    MemorizedReportRunOutput, MemorizedReportSelector, MemorizedReportUpdateInput, MemorizedReportWriteOutput,
    ProfitAndLossComparisonInput, ProfitAndLossComparisonOutput,
)
from bookflow.core.registry import Plan, command

_CELL_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_AMOUNT_PRECISION',
                'E_VALUE_RANGE', 'E_VERSION_CONFLICT']
_VERSION = ('budget show', 'budget', 'version')


def _budget_write(name, description, model, planner, applier, *, error_codes, positional, version=True):
    cmd = command(
        name, scope='company', description=description, input_model=model, output_model=BudgetWriteOutput,
        writes={'company'}, required_role='standard', capability='account', accepts_idempotency_key=True,
        positional=positional, version_source=_VERSION if version else None, error_codes=error_codes)(planner)
    cmd.applier(applier)
    return cmd


def plan_budget_create(inp, ctx, s) -> Plan:
    return budgets.plan_create(s, ctx, inp)


def plan_budget_set(inp, ctx, s) -> Plan:
    return budgets.plan_set(s, ctx, inp)


def plan_budget_fill(inp, ctx, s) -> Plan:
    return budgets.plan_fill(s, ctx, inp)


def plan_budget_clear(inp, ctx, s) -> Plan:
    return budgets.plan_clear(s, ctx, inp)


def plan_budget_rename(inp, ctx, s) -> Plan:
    return budgets.plan_rename(s, ctx, inp)


def plan_budget_delete(inp, ctx, s) -> Plan:
    return budgets.plan_delete(s, ctx, inp)


def plan_budget_show(inp, ctx, s) -> Plan:
    return Plan(budgets.show(s, inp))


def plan_budget_list(inp, ctx, s) -> Plan:
    return Plan(budgets.page(s, inp))


budget_create = _budget_write(
    'budget create',
    "Create a budget: a named plan for one fiscal year with one amount per account per month, as the anchor's"
    ' Set Up Budgets builds one. kind profit_and_loss budgets income and expense (each month\'s net);'
    ' balance_sheet budgets each asset, liability and equity account\'s ending balance. A profit and loss budget'
    ' can be kept per customer:job (by=customer) or per class (by=class). from_actuals fills it from the'
    " previous fiscal year's actual figures month by month, on the company report basis unless basis is given."
    ' Amounts are in the statement sign, so income and expense are both positive. Several budgets may cover the'
    ' same year under different names. A budget never posts to the ledger.',
    BudgetCreateInput, plan_budget_create, budgets.apply_create,
    error_codes=['E_VALIDATION', 'E_NAME_TAKEN', 'E_VALUE_RANGE', 'E_CASH_BASIS_EVIDENCE'], positional=[], version=False)

budget_set = _budget_write(
    'budget set',
    'Set the amounts of one account row of a budget for consecutive months, from start_month (default the first'
    ' month). 0 or an empty amount clears that month; months not given are left alone. On a customer or class'
    ' budget, name the customer or class the row is for. The account must be active and of the budget\'s kind.'
    ' Exact decimal amounts in the home currency; negative amounts are allowed. Every changed month is audited'
    ' with its amount before and after, and the budget version goes up by one.',
    BudgetSetInput, plan_budget_set, budgets.apply_cells, error_codes=_CELL_ERRORS, positional=['budget'])

budget_fill = _budget_write(
    'budget fill',
    "Fill one account row from start_month through through_month (defaults: the whole year). The first month takes"
    ' amount -- or, when amount is omitted, the amount already in start_month, which is the anchor\'s Copy Across.'
    ' Each later month takes the month before plus change, or the month before changed by change_percent'
    ' (compounding, rounded to the cent half to even: 5000.00 at 1 gives 5050.00, 5100.50, ...); with neither,'
    ' every month takes the first amount. Audited like budget set.',
    BudgetFillInput, plan_budget_fill, budgets.apply_cells, error_codes=_CELL_ERRORS, positional=['budget'])

budget_clear = _budget_write(
    'budget clear',
    'Clear budget amounts: one account row (account), one customer or class (customer / class_id), both, or the'
    " whole budget when neither is named, as the anchor's Clear button does. Every cleared amount is audited with"
    ' its value before, so nothing is lost from the trail.',
    BudgetClearInput, plan_budget_clear, budgets.apply_cells,
    error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VERSION_CONFLICT'],
    positional=['budget'])

budget_rename = _budget_write(
    'budget rename', 'Rename a budget. Its amounts are untouched; only the name a person reads changes.',
    BudgetRenameInput, plan_budget_rename, budgets.apply_rename,
    error_codes=['E_RECORD_NOT_FOUND', 'E_NAME_TAKEN', 'E_VERSION_CONFLICT', 'E_VALIDATION'], positional=['budget'])

budget_delete = _budget_write(
    'budget delete',
    'Delete a budget and all its amounts. Nothing in the ledger changes, because a budget never posted; the'
    ' audit event keeps every amount it held.',
    BudgetDeleteInput, plan_budget_delete, budgets.apply_delete,
    error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'], positional=['budget'])

budget_show = command(
    'budget show', scope='company',
    description='Show a budget as its entry grid: one row per account (and per customer or class on a customer or'
                ' class budget) with an amount for each of the twelve months and the annual total, in statement'
                ' then account order, with net income budgeted per month. Name customer or class_id to see one.',
    input_model=BudgetShowInput, output_model=BudgetShowOutput, required_role='member', capability='reports',
    positional=['budget'], error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VALUE_RANGE'])(plan_budget_show)

budget_list = command(
    'budget list', scope='company',
    description='Page budgets, newest fiscal year first and then by name, each with its kind, how it is kept'
                ' (company, customer or class) and how many month amounts it holds.',
    input_model=BudgetListInput, output_model=BudgetPageOutput, required_role='member', capability='reports',
    error_codes=[])(plan_budget_list)


# ---------------------------------------------------------------------------- reports

_REPORT_ERRORS = ['E_CASH_BASIS_EVIDENCE', 'E_QUERY_STALE', 'E_VALUE_RANGE', 'E_VALIDATION', 'E_RECORD_NOT_FOUND']


@command('report budget-overview', scope='company', required_role='member', capability='reports',
    description="A budget on its own, as the anchor's Budget Overview shows it: one row per budgeted account with"
                ' its amount per month, per fiscal quarter, or for the year, then a Total, and the statement'
                ' lines -- section totals, gross profit, net income -- that the entry grid does not show. On a'
                ' customer or class budget the amounts of every customer or class are added together unless one'
                ' is named. Rows are paged; the statement lines cover every account.',
    input_model=BudgetOverviewInput, output_model=BudgetOverviewOutput,
    error_codes=['E_QUERY_STALE', 'E_VALUE_RANGE', 'E_VALIDATION', 'E_RECORD_NOT_FOUND'])
def plan_budget_overview(inp, ctx, s):
    return Plan(preview=reports.budget_overview(inp, s, principal_id=ctx.on_behalf_of))


@command('report budget-vs-actual', scope='company', required_role='member', capability='reports',
    description='Uses the company cash/accrual report preference unless basis is supplied. Budget against actual'
                ' for inclusive dates (default the budget year): for each month, fiscal quarter or the whole range,'
                ' four columns -- Actual, Budget, $ Over Budget (actual less budget) and % of Budget (actual over'
                ' budget, one decimal) -- and the same four for the Total. Actual is report profit-and-loss for the'
                ' same dates and basis, account for account (the ending balance, on a balance sheet budget). A'
                ' budget month only partly inside the dates is prorated by days, rounded to the cent half to even.'
                ' On a customer or class budget the actual figures are those posted to the customers or classes'
                ' budgeted, or to the one named. Rows are paged; the statement lines cover every account.',
    input_model=BudgetVsActualInput, output_model=BudgetVsActualOutput, error_codes=_REPORT_ERRORS)
def plan_budget_vs_actual(inp, ctx, s):
    return Plan(preview=reports.budget_vs_actual(inp, s, principal_id=ctx.on_behalf_of))


@command('report profit-and-loss-comparison', scope='company', required_role='member', capability='reports',
    description='Uses the company cash/accrual report preference unless basis is supplied. The profit and loss for'
                ' inclusive dates beside the same dates a year earlier (compare=previous_year) or the period of'
                ' the same length just before (compare=previous_period: whole months by whole months, otherwise day'
                ' for day), with $ Change and % Change columns (change over the size of the earlier figure, one'
                ' decimal; blank where it is zero). Each column is report profit-and-loss for its own dates.'
                ' Rows are paged; the statement lines cover every account.',
    input_model=ProfitAndLossComparisonInput, output_model=ProfitAndLossComparisonOutput,
    error_codes=['E_CASH_BASIS_EVIDENCE', 'E_QUERY_STALE', 'E_VALUE_RANGE'])
def plan_profit_and_loss_comparison(inp, ctx, s):
    return Plan(preview=reports.profit_and_loss_comparison(inp, s, principal_id=ctx.on_behalf_of))


@command('report balance-sheet-comparison', scope='company', required_role='member', capability='reports',
    description='Uses the company cash/accrual report preference unless basis is supplied. The balance sheet on'
                ' date_to beside the balance sheet a year earlier (the last day of the same month when date_to'
                ' ends a month), with $ Change and % Change, as the anchor\'s Balance Sheet Prev Year Comparison.'
                ' Each column is report balance-sheet for its own date, retained earnings and this year\'s net'
                ' income included. Rows are paged; the statement lines cover every account.',
    input_model=BalanceSheetComparisonInput, output_model=BalanceSheetComparisonOutput,
    error_codes=['E_CASH_BASIS_EVIDENCE', 'E_QUERY_STALE', 'E_VALUE_RANGE'])
def plan_balance_sheet_comparison(inp, ctx, s):
    return Plan(preview=reports.balance_sheet_comparison(inp, s, principal_id=ctx.on_behalf_of))


# ---------------------------------------------------------------------------- memorized reports

def plan_memorized_create(inp, ctx, s) -> Plan:
    return memorized.plan_create(s, ctx, inp)


def plan_memorized_update(inp, ctx, s) -> Plan:
    return memorized.plan_update(s, ctx, inp)


def plan_memorized_delete(inp, ctx, s) -> Plan:
    return memorized.plan_delete(s, ctx, inp)


def plan_memorized_show(inp, ctx, s) -> Plan:
    return Plan(memorized.show(s, inp))


def plan_memorized_list(inp, ctx, s) -> Plan:
    return Plan(memorized.page(s, inp))


def plan_memorized_run(inp, ctx, s) -> Plan:
    return Plan(memorized.run(s, ctx, inp))


def _memorized_write(name, description, model, planner, applier, *, error_codes, positional, version=True):
    cmd = command(
        name, scope='company', description=description, input_model=model, output_model=MemorizedReportWriteOutput,
        writes={'company'}, required_role='member', capability='reports', accepts_idempotency_key=True,
        positional=positional, error_codes=error_codes,
        version_source=('memorized-report show', 'memorized_report', 'version') if version else None)(planner)
    cmd.applier(applier)
    return cmd


memorized_report_create = _memorized_write(
    'memorized-report create',
    "Memorize a report: keep a report and its options under a name, optionally in a group, for everyone in the"
    ' company, as the anchor\'s Memorize button does. filters are the report\'s own inputs, checked against it'
    ' now. date_range keeps a named range -- this_month, last_fiscal_year, this_fiscal_year_to_date, ... --'
    ' that is resolved again at every run, so a report memorized as This Month shows June in June and December'
    ' in December; without it, the dates in filters are kept as given.',
    MemorizedReportCreateInput, plan_memorized_create, memorized.apply_create,
    error_codes=['E_VALIDATION', 'E_NAME_TAKEN', 'E_RECORD_NOT_FOUND'], positional=[], version=False)

memorized_report_update = _memorized_write(
    'memorized-report update',
    'Rename a memorized report, move it to another group, or replace the report and options it keeps (the'
    " anchor's Replace when memorizing again). Fields not given are left alone; --clear group or date_range"
    ' takes it out of its group or back to fixed dates.',
    MemorizedReportUpdateInput, plan_memorized_update, memorized.apply_update,
    error_codes=['E_VALIDATION', 'E_NAME_TAKEN', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'],
    positional=['memorized_report'])

memorized_report_delete = _memorized_write(
    'memorized-report delete', 'Delete a memorized report. Nothing it reported on changes; the audit event keeps what it held.',
    MemorizedReportDeleteInput, plan_memorized_delete, memorized.apply_delete,
    error_codes=['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT'], positional=['memorized_report'])

memorized_report_show = command(
    'memorized-report show', scope='company',
    description='Show one memorized report: the report it runs, the options it keeps and its named date range.',
    input_model=MemorizedReportSelector, output_model=MemorizedReportOutput, required_role='member',
    capability='reports', positional=['memorized_report'], error_codes=['E_RECORD_NOT_FOUND'])(plan_memorized_show)

memorized_report_list = command(
    'memorized-report list', scope='company',
    description='Page memorized reports by group and then by name, as the anchor\'s Memorized Report List shows'
                ' them; group narrows it to one group.',
    input_model=MemorizedReportListInput, output_model=MemorizedReportPageOutput, required_role='member',
    capability='reports', error_codes=[])(plan_memorized_list)

memorized_report_run = command(
    'memorized-report run', scope='company',
    description='Run a memorized report: its named date range resolved to dates for today in the company\'s'
                ' timezone and fiscal year, then the report command run with the kept options as the caller, so'
                ' the caller needs that report\'s own permission. Returns the inputs it ran with and the report\'s'
                ' own output; page with limit and the report\'s next_cursor.',
    input_model=MemorizedReportRunInput, output_model=MemorizedReportRunOutput, required_role='member',
    capability='reports', positional=['memorized_report'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_QUERY_STALE', 'E_VALUE_RANGE',
                 'E_CASH_BASIS_EVIDENCE'])(plan_memorized_run)
