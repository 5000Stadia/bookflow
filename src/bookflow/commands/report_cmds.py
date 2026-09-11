"""Typed domestic accrual report commands; dispatch owns authorization."""
from bookflow.company.ledger_reports import (
    GeneralLedgerInput, GeneralLedgerOutput, TransactionDetailInput, TransactionDetailOutput,
    TrialBalanceInput, TrialBalanceOutput, general_ledger, transaction_detail, trial_balance,
)
from bookflow.company.check_reports import (
    MissingChecksInput, MissingChecksOutput, missing_checks,
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
from bookflow.company.summary_reports import (
    ExpensesByVendorInput, ExpensesByVendorOutput, SalesByCustomerInput, SalesByCustomerOutput,
    SalesByItemInput, SalesByItemOutput, SalesByRepInput, SalesByRepOutput,
    expenses_by_vendor, sales_by_customer, sales_by_item, sales_by_rep,
)
from bookflow.company.inventory_reports import (
    InventoryValuationInput, InventoryValuationOutput, StockStatusInput, StockStatusOutput,
    inventory_valuation, stock_status,
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


@command("report transaction-detail", scope="company", required_role="member", capability="reports",
    description="Every posting line of every account between date_from and date_to, one section per account in chart-of-accounts order. A section opens with the account's opening balance, lists each line with its accounting date, document type and number, the party it names, the line's own description and the document's memo, the split account, and the debit or credit, and closes with the period's debits, credits and the closing balance. The running balance on each line is the account's balance after that line, computed over the whole account before the page is cut, so a page boundary never breaks it. The split account is the other side of the entry read off the posting batch itself: the one other account when the entry has exactly two lines, and -SPLIT- when it has more. Corrections and voids appear as the reversal and replacement effects they are, so the section reconciles to report trial-balance for the same date. accounts filters to a set of accounts by ID or canonical full name; omit it for every account with an opening balance or with activity in the period. Totals cover the whole filter and rows are paged.",
    input_model=TransactionDetailInput, output_model=TransactionDetailOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE", "E_RECORD_NOT_FOUND"])
def plan_transaction_detail(inp, ctx, s):
    return Plan(preview=transaction_detail(inp, s, principal_id=ctx.on_behalf_of))


@command("report missing-checks", scope="company", required_role="member", capability="reports",
    description="Holes and repeats in each bank account's check-number sequence as of as_of, so an unrecorded or twice-entered check can be found. One row per hole, naming the first and last missing number and the checks that occupy the numbers immediately below and above it, and one row per number two or more checks carry. A check counts as drawn on the bank account its first entered line credits, so a correction that moves it moves it here too, and a voided check still occupies its number because the paper it was written on is still gone. A check whose number is not a plain run of digits has no position in a sequence and is counted rather than placed; so is one no longer drawn on a bank account. account limits the report to one bank account. Totals cover every examined check and rows are paged.",
    input_model=MissingChecksInput, output_model=MissingChecksOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE", "E_RECORD_NOT_FOUND", "E_VALIDATION"])
def plan_missing_checks(inp, ctx, s):
    return Plan(preview=missing_checks(inp, s, principal_id=ctx.on_behalf_of))
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


@command("report inventory-valuation", scope="company", required_role="member", capability="reports",
    description="What every inventory item holds on as_of and what it is worth: on-hand quantity, weighted-average cost and asset value, one row per item, named in item order. Quantity and value are the running sums of the item's own stock movements up to that date, dated cost corrections included, so a backdated purchase shows in the value of the day it belongs to and not the day it was entered. Items with no stock and no value are listed while they are active and drop off once they are not. The total asset value is the inventory asset on report balance-sheet for the same date; the report refuses rather than print a figure the balance sheet would contradict. Totals cover every item and rows are paged.",
    input_model=InventoryValuationInput, output_model=InventoryValuationOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_inventory_valuation(inp, ctx, s):
    return Plan(preview=inventory_valuation(inp, s, principal_id=ctx.on_behalf_of))


@command("report stock-status", scope="company", required_role="member", capability="reports",
    description="What to reorder, as of as_of: each inventory item with what is on hand, what is available, its reorder points and whether it has fallen to or below the lower one, beside its average cost, asset value and preferred vendor. Quantity available equals quantity on hand and quantity on order is zero until sales orders, purchase orders and assembly builds exist to commit or expect stock; both columns are here so the reading does not change when they do. Asset value totals the same figure report inventory-valuation totals and the balance sheet carries for that date. Totals and the below-reorder count cover every item; rows are paged.",
    input_model=StockStatusInput, output_model=StockStatusOutput,
    error_codes=["E_QUERY_STALE", "E_VALUE_RANGE"])
def plan_stock_status(inp, ctx, s):
    return Plan(preview=stock_status(inp, s, principal_id=ctx.on_behalf_of))
