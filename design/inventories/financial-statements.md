# Financial statement controls and staged coverage

This inventory covers standard profit and loss and balance sheet, their report
controls, and adjacent variants. The current bounded implementation is standard
accrual own-account statements. Broader standard-screen customization remains
explicitly staged; this is not full equivalent-screen parity. All variants use
the report source/date/basis/metadata contract in blueprint section14.

| Element / behavior | Current increment | Remaining coverage |
|---|---|---|
| Profit and loss period | Required inclusive from/to, date validation | Common date presets, current month default, fiscal YTD shortcut |
| Balance sheet period | Required inclusive as-of, fiscal earnings split | Current date default, quarter/year presets |
| Accounting basis | Explicit accrual only, cash rejected | Cash recognition by settlement/document type |
| Account rows | Own-account signed normal-side value, ID, current full/name/number/type/parent/active, display preferences | Collapsible hierarchy, parent subtree subtotals; alphabetical order fixed within section |
| P&L groups | Income, COGS, expense, other income/expense | Expand/collapse group controls |
| P&L totals | Section sums, gross profit, net operating income, net income | Percent-of-income, prior period/year/YTD comparisons with change and percentage columns |
| Balance sheet groups | Assets, liabilities, equity | Current/fixed/other asset and current/long-term liability display subtotals |
| Balance sheet totals | Assets, liabilities, posted equity, prior earnings, current-year income, total equity, liabilities+equity, difference | Previous-year comparison and percentage change |
| Earnings | Derived fiscal split from immutable effects, separate from posted equity | Formal closing profiles and pre-closing presentation; no automatic closing postings |
| Empty values | Zero-net rows omitted by default, include_zero includes relevant inactive/never-used accounts | Active/nonzero/activity-only radio options and blank-versus-zero formatting |
| Drill-down | Own account GL with source dates/ID, current-books change warning, GL document links through existing output | Bespoke statement-detail variant and richer source-form drill-down |
| Identity/source | Company/basis/currency/dates/version/generated/audit watermark, labels explicitly current | Saved issued report snapshot / as-recorded reconstruction |
| Paging | Bounded signed continuation, complete totals each page, new filters restart, audited change stales | Whole-report print/exports with consistent snapshot limits |
| Columns and groups | Fixed typed columns, immutable source facts | Display/filter/sort customization, class/job/customer/vendor columns, unclassified views |
| Comparative reports | Inventoried only | Prior month/year, YTD, budget/forecast comparison, variance and percentage semantics |
| Display output | Browser table/summary and structured JSON on all existing adapters | Print preview/PDF/CSV/spreadsheet, font/style/header/footer/logo controls, packing/delivery templates |
| Reuse/discovery | Accounting→Report, generated command help | Report center previews/descriptions, favorite/recent/memorized reports and groups, saved filters |
| Collaboration/delivery | Read-only company permission, no sends | Comment-on-report, email recipient/output/queue and delivery status |
| Data protection | No report writes, all original/reversal/replacement effects, exact money | No destructive history condensation or raw secrets in reports |

Independent source-year checks cover the first implementation. The future
report set also includes financial detail/comparison/class/job, cash flows,
AR/AP/aging/open items, sales/purchases/items, inventory, jobs/time/mileage,
bank/reconciliation, budgets/forecasts, payroll/tax and list/audit families.
Operational reports wait for their source records and financial reconciliation
rules; none can be replaced with a relabeled generic ledger sum.
