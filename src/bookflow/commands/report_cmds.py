"""Typed domestic accrual report commands; dispatch owns authorization."""
from bookflow.company.ledger_reports import (
    GeneralLedgerInput, GeneralLedgerOutput, TrialBalanceInput, TrialBalanceOutput,
    general_ledger, trial_balance,
)
from bookflow.core.registry import Plan, command


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
