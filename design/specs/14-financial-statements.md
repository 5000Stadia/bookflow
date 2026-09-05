# Standard financial statements

Implements row 14 using current posting effects and chart classification. No schema,
stored closing journal, new provider, new report framework or authority policy.

## Shared command contract

`report profit-and-loss`: required ISO `date_from`, `date_to` (inclusive, ordered),
`basis` only `accrual` default, `include_zero` false, `limit` 1–200 default50,
optional signed `cursor`. `report balance-sheet` takes the same options except
only `date_to`, an as-of snapshot. Strict typed inputs reject cash, floats, invalid
dates and extra fields. Both are read-only company commands with existing member
role and reports capability; all adapters use the registry. No write/audit event.

Outputs use existing report metadata and signed Money output (integer minor units,
formatted amount, company home currency). A bounded page contains account rows,
count and next_cursor; `totals` always covers the whole statement, never this page.
Account rows carry ID, explicitly current full label, type, parent ID, active flag,
section, current account name/number, preference-controlled display label, and
own-account normal-side amount. Parents never silently include child
balances. Full paths remain available as identity context while browser labels honor
use_account_numbers and show_lowest_subaccount_only; preference or number changes
stale continuations. Each posting enters totals once.
Inactive accounts with nonzero amounts remain. Zero omission uses period net for
profit-and-loss and ending net for balance sheet. include_zero includes never-posted
accounts in the relevant statement family, but never non-posting accounts.

Order: statement section then canonical full-name key then stable account ID.
Retain signs for contra assets, refunds, expense credits and losses; do not absolute
value or clamp. Sum with existing arbitrary-precision integer aggregate and Python
integer intermediate arithmetic. Any emitted amount or whole-statement total
outside signed64 returns E_VALUE_RANGE even when the offending row is on another
page. Each SQL result/page has bounded Python memory; no materializing postings.

## Profit and loss

Sum debit-minus-credit posting lines by account for the requested accounting dates,
including original, reversal and replacement batches regardless of current header
state. Apply current account type (posted accounts cannot change type).
Sections/order: income, cost_of_goods_sold, expense, other_income, other_expense.
Income and other_income are credit-normal; expense/COGS/other_expense debit-normal.
Totals contain each section plus gross_profit = income - COGS;
net_operating_income = gross_profit - expense;
net_income = net_operating_income + other_income - other_expense.

## Balance sheet

As-of sums all effects through date_to. Sections/order: assets (bank, AR,
other_current_asset, fixed_asset, other_asset), liabilities (AP, credit_card,
other_current_liability, long_term_liability), equity (equity). Assets debit-normal;
liabilities/equity credit-normal. Report rows show direct posted account balances.

Compute fiscal_year_start from the company's fiscal_year_start_month and as-of
date; at the supported year1 boundary clamp the unavailable preceding year to
0001-01-01. Expose fiscal_year_start in output. Derived prior_earnings is the
negative sum of raw (debit_minor_units-credit_minor_units) across the five P&L
account types strictly before that start; current_year_income is the same raw
signed sum negated from that start through date_to. Thus current_year_income equals
profit-and-loss net_income for that fiscal interval, not its negative. Both include
every effect; never add a second transfer or
pretend a synthetic amount was posted to an account. Display these separately
from posted_equity. Their sum with posted_equity is total_equity.
Totals: assets, liabilities, posted_equity, prior_earnings, current_year_income,
total_equity, liabilities_and_equity, difference = assets-liabilities_and_equity.
For valid balanced books difference is zero, including a net loss, non-January
fiscal years. Ordinary user journals that transfer income to equity are included
exactly as entered: a same-period transfer can reduce the P&L to zero, and the
report must not guess that a memo means an excluded closing entry. The browser
and docs explain this ledger-based result. Automatic/formal closing transfers and
pre-closing business-performance views require an explicit future closing profile.
The prior-period manual-transfer fixture checks residual earnings plus posted
equity without double counting; it does not promise to reconstruct pre-closing
profit from ordinary journal descriptions.

## Snapshot and continuation

Reuse report cursor signing/company/principal/query binding and consistent read
snapshot. Hash all relevant effects plus account labels/numbers, types, parents, active
flags, company fiscal/presentation settings and the company audit high-water mark. Relevant backdated effects, rename/deactivation,
hierarchy or fiscal setting changes stale continuation; foreign-company/principal,
report-kind, options, period or limit changes reject it. Metadata and totals remain
constant across an unchanged continuation. Every intervening audited company
write (even an unrelated note) stales these statements: each page thus identifies
the same company information boundary, not a selectively frozen older watermark.
Permission is rechecked on every page.
Report version and schema are preserved. Copy/attach keeps existing cursor rules.

## Browser and documentation

Discover both commands under Accounting/Report. Forms retain entered dates and
options and render a readable statement table, section labels, whole-statement
summary, home currency, basis and date metadata, with explicit page count and
next-page control. The next page resubmits the same parameters with its cursor;
editing filters starts a new report without an old cursor. Keep JSON available
under details. No auto-fetching an unbounded full report.

Each account amount links to the existing general-ledger form with stable account
ID and the report dates prefilled (balance sheet starts 0001-01-01). The GL sign
convention is documented: income/liability/equity report amounts negate GL nets.
Drill-down carries the source report audit watermark as browser context (not a
new GL command input). The form explains that it opens current books; on submission
it compares returned metadata watermark with the source watermark and explicitly
flags changed books and the need to rerun the source statement. The context survives
form submission, is escaped, and never changes accounting or authorization.
Desktop1280 and phone390 fit without page overflow. Values/labels are escaped.
Generated command docs and a short statement usage page explain earnings,
own-account rows, totals across pages and accrual-only scope.

Existing demo/reference seeds already exercise both commands' source data; extend
demo verification and its usage document with actual command examples and fixed
annual reference results: income6925000, expenses510000, net_income6415000;
assets7435000, liabilities20000, posted_equity1000000, total_equity7415000.
No new records are necessary for these read-only commands.

## Verification

Reference monthly/YTD/annual and second-half source arithmetic; unchanged TB/GL
reconciliation; negatives, contra assets, parent and child own balances, inactive
and zero-net accounts; fiscal rollover/nonJanuary start and ordinary prior-period transfers to equity;
future/backdated corrections/voids; strict inputs, overflow beyond selected page,
cursor tamper/query/company/principal binding/staleness; no writes. CLI/HTTP parity
and Python outputs/errors; actual Chrome desktop/phone navigation, input,
next-page and account drill-down, including changed-source warning. Across adapters
exercise invalid periods, unsupported cash, overflow and stale continuation where
the surface accepts those inputs. Copy/attach into a separate initialized root must
reproduce both statement amounts, fiscal split and current label preferences
independently of the original hub/process; a cursor is not an archived report.
Fresh artifact critique uses isolated copies and meaningful mutations of signs,
earnings, immutable-effects selection and browser continuation as appropriate.

The complete standard-report control inventory and explicit staged boundary are
in design/inventories/financial-statements.md. Advanced report comparison columns, classes/jobs, hierarchy rollup, cash basis,
cash-flow statement, PDF/CSV export, saved reports and operational subledger
reports remain inventoried future work; this increment makes no parity claim for
those features.
