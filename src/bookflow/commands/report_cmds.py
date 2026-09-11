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
from bookflow.company.receivable_reports import (
    ArAgingInput, ArAgingOutput, OpenInvoicesInput, OpenInvoicesOutput,
    StatementInput, StatementOutput, ar_aging, open_invoices, statement,
)
from bookflow.company.payable_reports import (
    ApAgingInput, ApAgingOutput, UnpaidBillsInput, UnpaidBillsOutput,
    ap_aging, unpaid_bills,
)
from bookflow.company.cash_flow_reports import CashFlowsInput, CashFlowsOutput, cash_flows
from bookflow.company.income_tax_reports import (
    IncomeTaxSummaryInput, IncomeTaxSummaryOutput, income_tax_summary,
)


@command("report ap-aging", scope="company", required_role="member", capability="reports",
    description="Accrual payables aged by bill due date as of as_of, one row per vendor, in Current, 1-30, 31-60, 61-90 and Over 90 columns. A bill due exactly 30 days before as_of is 1-30. Bills carry what is still owed on them after what bill payments have settled against them; an unapplied bill payment, a vendor credit and a payable journal entry age by accounting date, so the aging total equals Accounts Payable on the balance sheet for the same date. Paid bills, voided bills and all-zero vendors are omitted; totals cover every vendor and rows are paged.",
    input_model=ApAgingInput, output_model=ApAgingOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_ap_aging(inp, ctx, s):
    return Plan(preview=ap_aging(inp, s, principal_id=ctx.on_behalf_of))


@command("report unpaid-bills", scope="company", required_role="member", capability="reports",
    description="Unpaid and partly paid vendor bills as of as_of, oldest due date first, each with its vendor, bill date, due date, days past due, aging column, the vendor's own reference number, bill amount, applied amount and open balance. Paid and voided bills are omitted. Applied is what active bill payments have settled against the bill on or before as_of, and open balance is what is left. This lists bills only, so its total is payables before any vendor credit or unapplied bill payment; use report ap-aging for the balance that ties to Accounts Payable. Totals cover the whole filter and rows are paged.",
    input_model=UnpaidBillsInput, output_model=UnpaidBillsOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE", "E_RECORD_NOT_FOUND"])
def plan_unpaid_bills(inp, ctx, s):
    return Plan(preview=unpaid_bills(inp, s, principal_id=ctx.on_behalf_of))


@command("report ar-aging", scope="company", required_role="member", capability="reports",
    description="Accrual receivables aged by invoice due date as of as_of, one row per customer or job, in Current, 1-30, 31-60, 61-90 and Over 90 columns. An invoice due exactly 30 days before as_of is 1-30. Invoices carry their remaining balance; unapplied customer credit and receivable journal entries age by accounting date, so the aging total equals Accounts Receivable on the balance sheet for the same date. All-zero customers are omitted; totals cover every customer and rows are paged.",
    input_model=ArAgingInput, output_model=ArAgingOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_ar_aging(inp, ctx, s):
    return Plan(preview=ar_aging(inp, s, principal_id=ctx.on_behalf_of))


@command("report open-invoices", scope="company", required_role="member", capability="reports",
    description="Unpaid and partly paid invoices as of as_of, oldest due date first, each with its due date, days past due, aging column, original amount, applied amount and remaining balance. Paid and voided invoices are omitted. This lists invoices only, so its total is receivables before unapplied customer credit; use report ar-aging for the balance that ties to Accounts Receivable. Totals cover the whole filter and rows are paged.",
    input_model=OpenInvoicesInput, output_model=OpenInvoicesOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE", "E_RECORD_NOT_FOUND"])
def plan_open_invoices(inp, ctx, s):
    return Plan(preview=open_invoices(inp, s, principal_id=ctx.on_behalf_of))


@command("report statement", scope="company", required_role="member", capability="reports",
    description="What a customer's account did between date_from and date_to: an opening balance, then every invoice, payment, credit and receivable adjustment in date order with a running balance, then the closing balance, with the A/R aging columns for the same customers as of date_to at the foot. One customer with customer, or every customer with a balance or with activity in the period. A voided document has no row because it is worth nothing on its own date, not because a status was filtered; applying a receipt to that same customer's invoice changes nothing the customer owes and has no row. New cash is owned by the party whose invoice it settles, so a parent's receipt that pays a job's invoice appears on the job's statement for the settled part and on the parent's for the rest. A closing balance is the same figure report ar-aging shows for that customer, and the closing total is Accounts Receivable on the balance sheet for date_to. Totals cover the whole filter and rows are paged; a page never breaks the running balance because it is computed over the whole customer first.",
    input_model=StatementInput, output_model=StatementOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE", "E_RECORD_NOT_FOUND"])
def plan_statement(inp, ctx, s):
    return Plan(preview=statement(inp, s, principal_id=ctx.on_behalf_of))


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


@command("report cash-flows", scope="company", required_role="member", capability="reports",
    description="Indirect accrual statement of cash flows for inclusive accounting dates: net income for the period, then the period change in every other balance-sheet account, classified into operating, investing and financing. An increase in an asset is a use of cash and shows negative; an increase in a liability or in equity is a source and shows positive. Operating carries receivables, payables, inventory and the other current assets and current liabilities, including credit cards; investing carries fixed and other assets; financing carries long-term liabilities and equity, so owner draws and contributions appear there. Net income for the same two dates is the figure report profit-and-loss reports, and net income plus the three subtotals plus opening cash is closing cash, which is the sum of the bank accounts report balance-sheet shows for the same date_to; totals.difference publishes that reconciliation and is zero for books that balance. Classification comes from the account's type, so a depreciation add-back reaches the statement through the change in the fixed-asset account it was credited to and is reported under investing rather than under operating. Own-account rows are paged; totals cover every account.",
    input_model=CashFlowsInput, output_model=CashFlowsOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_cash_flows(inp, ctx, s):
    return Plan(preview=cash_flows(inp, s, principal_id=ctx.on_behalf_of))


@command("report income-tax-summary", scope="company", required_role="member", capability="reports",
    description="Income and expense account activity for inclusive accounting dates, grouped by the tax line each account is assigned, with a group for the accounts that have none. Each group shows its own total and then the accounts that make it up, so every figure can be traced to the accounts behind it. Amounts are on each account's normal side, so revenue is positive on an income line and a cost is positive on a deduction line, exactly as report profit-and-loss prints them. Totals cover every income and expense account whatever the filter shows, so net income here is the figure report profit-and-loss reports for the same two dates. Rows are paged and a group total covers the whole group even when its accounts fall on the next page.",
    input_model=IncomeTaxSummaryInput, output_model=IncomeTaxSummaryOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_income_tax_summary(inp, ctx, s):
    return Plan(preview=income_tax_summary(inp, s, principal_id=ctx.on_behalf_of))
