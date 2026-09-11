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
from bookflow.company.summary_reports import (
    ExpensesByVendorInput, ExpensesByVendorOutput, SalesByCustomerInput, SalesByCustomerOutput,
    SalesByItemInput, SalesByItemOutput, SalesByRepInput, SalesByRepOutput,
    expenses_by_vendor, sales_by_customer, sales_by_item, sales_by_rep,
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


@command("report sales-by-customer", scope="company", required_role="member", capability="reports",
    description="Income between date_from and date_to grouped by the customer or job each sale was made to, in hierarchy-name order so a job reads directly under the customer it belongs to, with what that customer's share of the period came to as a percentage. A job's income is its own and is never rolled into its parent's figure; every row names its parent so the two can be added deliberately. Every income effect is counted whatever document posted it, including an income journal entry, and income posted against no customer -- or against a name from another list -- is the one row called No name rather than something dropped. The total is the income total report profit-and-loss shows for the same dates. Rows worth nothing on the period are omitted because they are worth nothing, not because a status was filtered; totals cover every customer and rows are paged.",
    input_model=SalesByCustomerInput, output_model=SalesByCustomerOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_sales_by_customer(inp, ctx, s):
    return Plan(preview=sales_by_customer(inp, s, principal_id=ctx.on_behalf_of))


@command("report sales-by-item", scope="company", required_role="member", capability="reports",
    description="The same period's income grouped by the item sold, each row with the quantity in the item's own base unit, the income, the average price that quantity fetched and the item's share of the period as a percentage. Quantity and income both come from the posting the sale made, so a correction, a void and a credit memo take the units and the money back off the row they were added to. Every sales line names an item, so income with no item is income no sale line posted -- an income journal entry, or a deposit taken straight to an income account: that is the row called No item, and no_item_income on the totals says what it came to, so item income plus no-item income is the income total report profit-and-loss shows for the same dates. Average price is income divided by quantity rounded to the cent for reading, and is omitted for a row whose quantity is unknown because a line was priced by allocation. Rows worth nothing are omitted; totals cover every item and rows are paged.",
    input_model=SalesByItemInput, output_model=SalesByItemOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_sales_by_item(inp, ctx, s):
    return Plan(preview=sales_by_item(inp, s, principal_id=ctx.on_behalf_of))


@command("report expenses-by-vendor", scope="company", required_role="member", capability="reports",
    description="Expense between date_from and date_to grouped by vendor, with each vendor's share of the period as a percentage. Cost of goods sold, ordinary expense and other expense are all counted, which is what makes the total the same figure the profit and loss reports for those three sections over the same dates. Every document that reaches one of those accounts is included -- bills, cheques, credit card charges, vendor credits and expense journal entries -- because the report selects on the account rather than on a list of document types. A line that names its own vendor is that vendor's; a line that names none takes the one vendor named elsewhere on the same posting, which is how a cheque's payee reaches its expense lines. Expense that names no vendor at all, including money paid to a name from another list, is the one row called No name. A vendor credit is negative and reduces the vendor. Rows worth nothing are omitted; totals cover every vendor and rows are paged.",
    input_model=ExpensesByVendorInput, output_model=ExpensesByVendorOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_expenses_by_vendor(inp, ctx, s):
    return Plan(preview=expenses_by_vendor(inp, s, principal_id=ctx.on_behalf_of))


@command("report sales-by-rep", scope="company", required_role="member", capability="reports",
    description="The same period's income grouped by the sales representative the sale itself captured, with each representative's share of the period as a percentage. The rep is read from the exact document revision that posted the effect, never from the customer's current sales representative, so reassigning a customer today does not move last year's sales and a correction that changes the rep moves only what it reposted. Income from a document that captured no representative, and income no sales document posted at all, is the one row called Unassigned. The total is the income total report profit-and-loss shows for the same dates. Rows worth nothing are omitted; totals cover every representative and rows are paged.",
    input_model=SalesByRepInput, output_model=SalesByRepOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_sales_by_rep(inp, ctx, s):
    return Plan(preview=sales_by_rep(inp, s, principal_id=ctx.on_behalf_of))
