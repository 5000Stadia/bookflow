"""Typed domestic accrual report commands; dispatch owns authorization."""
from bookflow.company.ledger_reports import (
    GeneralLedgerInput, GeneralLedgerOutput, TrialBalanceInput, TrialBalanceOutput,
    general_ledger, trial_balance,
)
from bookflow.core.registry import Plan, command
from bookflow.company.financial_statements import (
    ProfitAndLossInput, ProfitAndLossOutput, BalanceSheetInput, BalanceSheetOutput,
    profit_and_loss, balance_sheet,
)


@command("report trial-balance", scope="company", required_role="member", capability="reports",
    description="Accrual ending account nets as of date_to; zero balances omitted unless include_zero is true. Totals cover all accounts; rows are paged.",
    input_model=TrialBalanceInput, output_model=TrialBalanceOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_trial_balance(inp, ctx, s):
    return Plan(preview=trial_balance(inp, s, principal_id=ctx.on_behalf_of))


@command("report general-ledger", scope="company", required_role="member", capability="reports",
    description="Inclusive accrual ledger with paged opening, posting and closing rows. Closing-row debit/credit are whole-period account activity; signed balances are debit minus credit. Page totals cover the whole filter.",
    input_model=GeneralLedgerInput, output_model=GeneralLedgerOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE", "E_RECORD_NOT_FOUND"])
def plan_general_ledger(inp, ctx, s):
    return Plan(preview=general_ledger(inp, s, principal_id=ctx.on_behalf_of))


@command("report profit-and-loss", scope="company", required_role="member", capability="reports",
    description="Accrual income, costs and net profit for inclusive accounting dates. Own-account rows are paged; statement totals cover all accounts. Ordinary journal transfers affect income exactly as entered.",
    input_model=ProfitAndLossInput, output_model=ProfitAndLossOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_profit_and_loss(inp, ctx, s):
    return Plan(preview=profit_and_loss(inp, s, principal_id=ctx.on_behalf_of))


@command("report balance-sheet", scope="company", required_role="member", capability="reports",
    description="Accrual assets, liabilities and equity as of date_to, with posted equity, derived prior earnings and current fiscal-year income shown separately. Own-account rows are paged; totals cover the whole statement.",
    input_model=BalanceSheetInput, output_model=BalanceSheetOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_balance_sheet(inp, ctx, s):
    return Plan(preview=balance_sheet(inp, s, principal_id=ctx.on_behalf_of))
