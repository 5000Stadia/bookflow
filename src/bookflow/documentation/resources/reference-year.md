# Reference year

`demo reset --include-reference` creates `Demo Plumbing Co` and `Reference Plumbing Co`
in the same disposable `Demo Holdings LLC` organization. Without the flag, reset
creates only the ordinary demo. **Every reset moves the entire previous demo
organization and all of its companies to trash**, including any reference company
or manually added siblings. Other organizations are untouched. A non-demo
organization with the demo name causes `E_NAME_TAKEN`.

Use a new local data root for this example:

<!-- bookflow-example: illustrative -->
```sh
ROOT=$(mktemp -d /tmp/bookflow-reference.XXXXXX)
bookflow init --data-root "$ROOT" --json
bookflow demo reset --include-reference --dry-run --data-root "$ROOT" --json
bookflow demo reset --include-reference --data-root "$ROOT" --json
bookflow company list --data-root "$ROOT" --json
bookflow report trial-balance --company "Reference Plumbing Co" --date-to 2026-12-31 --data-root "$ROOT" --json
bookflow report general-ledger --company "Reference Plumbing Co" --account Checking --date-from 2026-07-01 --date-to 2026-12-31 --limit 2 --data-root "$ROOT" --json
```

Continue reports with `--cursor` set to `next_cursor`, keeping the filters unchanged,
until `next_cursor` is null. Report totals cover the complete filter on every page.
General ledger pages include opening and closing rows as well as postings.

Python uses `client.demo.reset(include_reference=True)`. HTTP uses
`POST /commands/demo.reset` with `{"include_reference": true}` and the normal
hub-admin authentication and write context. The generated workbench exposes the
same boolean input and dry-run preview. Output adds `reference_company_id` and
`reference_display_name`; both are null when the flag is false. Preview generates
prospective IDs without changing saved data or creating companies. The existing
local read path uses ephemeral root-lock and SQLite WAL/SHM coordination files.
Applied IDs identify the actual companies.

Reset uses multiple commits and filesystem moves. If a seed command fails after
creation, `E_PARTIAL_WRITE` identifies the created organization, companies and
incomplete company. Earlier seed writes remain saved. A rerun resets that entire
disposable organization; it does not resume the seed or roll back prior commits.

## Packaged source and arithmetic

`bookflow.demo/reference.toml` contains explicit company settings and public
account/journal commands with fixed dates, journal numbers and audit reasons.
`bookflow.demo/reference-expected.json` contains twelve monthly checkpoints,
annual totals and July–December gross movements with June 30 opening balances.
Amounts in the expected file are USD integer minor units, signed debit minus
credit. `gross_debits_credits` arrays are `[debits, credits]` before netting.
Read either installed resource through `importlib.resources.files("bookflow.demo")`.

The preference example appends an exempt10.00 invoice on October2 and its exact
same-date void. Each of Accounts Receivable and Service Income gains1000 in both
gross debits and gross credits from October onward; all net balances are unchanged.
The retained accepted estimate is explicitly reactivated and company preferences
return to their defaults. The example belongs to Preference Example Customer and
uses the REF-PREF- prefix (DEMO-PREF- in the main demo).

All source dates are in **2026**. These are explicit domestic journals:

| Date | Debit | Credit | USD minor units |
|---|---|---|---:|
| January 1 | Checking | Opening Balance Equity | 1000000 |
| January 2 | Insurance Expense | Checking | 120000 |
| Each month's 15th | Checking | Service Income | Jan 300000; Feb 350000; Mar 400000; Apr 450000; May 500000; Jun 550000; Jul 600000; Aug 650000; Sep 700000; Oct 750000; Nov 800000; Dec 850000 |
| Each month's 20th | Professional Fees | Checking | 25000 |
| June 15 | Equipment | Checking | 240000 |
| July–December 28th | Depreciation Expense | Accumulated Depreciation | 10000 |
| November 16 | Checking | Service Income | 90000, voided duplicate |
| December 21 | Professional Fees | Business Credit Card | 30000 |
| December 27 | Business Credit Card | Checking | 10000 |

The May receipt is corrected to 525000 on May 15: original 500000, exact reversal
500000, replacement 525000. The November duplicate and exact reversal are both
90000 on November 16. All original, reversal and replacement postings remain in
history. Depreciation is entered explicitly; bill, payment application and
inventory processing are not performed by those journals.

September 5 adds an invoice and paid sales receipt: each has a10000 service and
800 tax, then a correcting2000 exempt service, for12800 total. The receipt
debits Checking and the invoice debits Accounts Receivable. September 6 repeats
both documents as corrected-then-voided duplicates. All14 commercial posting
batches remain. Net additions are Checking12800, AR12800, Service Income credit
24000 and Sales Tax Payable credit1600. The new documents also exercise captured
boolean custom fields and historical definition labels.

For month number `m` from 1 through 12, cumulative receipts are
`250000*m + 50000*m*(m+1)/2 + (25000 if m >= 5 else 0)`.
Checking is `1000000 + receipts - 120000 - 25000*m`, less 240000 from June
and less 10000 in December. Professional Fees are `25000*m`, plus 30000 in
December. Equipment is 240000 from June. Depreciation Expense and Accumulated
Depreciation are each `10000*max(0,m-6)` on opposite sides. Card liability is
20000 in December; capital remains 1000000.

Gross Checking debits are `1000000 + receipts + cancelled`, where `cancelled`
is 500000 from May plus 90000 from November. Gross Checking credits are
`120000 + 25000*m + equipment + card_payment + cancelled`. Service Income
debits equal `cancelled`; credits equal `receipts + cancelled`. Other account
gross movements follow the source table directly. Second-half gross movements
are December cumulative movements minus June cumulative movements. These
arithmetic formulas do not use the production report implementation.

From September, add the commercial effects above. Gross Checking and AR each
add47200 debits and34400 credits; Service Income adds64000 debits and88000
credits; Sales Tax Payable adds4800 debits and6400 credits. These include the
originals, both correction reversals/replacements and the final duplicate voids.

From October, the active customer-payment examples add Payment Example Bank17000,
Accounts Receivable1000 and Payment Example Income18000. Their independent gross
increments are bank debits30500/credits13500, AR debits32500/credits31500 and
income debits1000/credits19000. Original receipts, correction reversals and
replacements remain recorded; applications do not post cash again. See
[customer payment workflows](customer-payment-workflows.md) for ownership,
unapplied balances, control coverage and the preserving append oracles.

| Checkpoint | Checking | Trial balance, each side | Income | Net assets |
|---|---:|---:|---:|---:|
| June 30 | 3065000 | 3575000 | 2305000 | 3305000 |
| December 31 | 7267800 | 8048600 | 6457000 | 7457000 |

At December 31, debit balances are Checking7267800, Payment Example Bank17000,
Accounts Receivable13800, Equipment240000,
Professional Fees 330000, Insurance Expense 120000 and Depreciation Expense
60000. Credit balances are Accumulated Depreciation 60000, Service Income
6949000, Payment Example Income18000, Opening Balance Equity1000000,
Business Credit Card20000 and Sales Tax Payable1600.
Income is `6949000 + 18000 - 330000 - 120000 - 60000 = 6457000`.
Net assets are `7267800 + 17000 + 13800 + 240000 - 60000 - 20000 - 1600 = 7457000`, equal to
capital plus income. AP is zero. Income and net-assets values are independent arithmetic regression oracles for
the profit-and-loss and balance-sheet commands. Aging remains unavailable.

The ordinary demo retains its original ten journals and commercial history,
and adds active invoices/payments with the same17000/1000/18000 net increments.
Its trial balance is708195 on each side and Checking remains624895.
Account show/list express balances on each account's normal
side; reports use debit-minus-credit values. Both companies remain ordinary
independent companies selectable through company show/list and the browser
picker. The reference Checking register shows `72678.00 USD`.

## Documentation generation

Generation is rootless: no initialized data root, company or credentials are needed.

<!-- bookflow-example: illustrative -->
```sh
bookflow docs generate --output docs
bookflow docs generate --output docs --check
```

The generated [demo command reference](cli/demo.md) contains the typed contract.

## Financial statements

<!-- bookflow-example: illustrative -->
```sh
bookflow report profit-and-loss --company "Reference Plumbing Co" --date-from 2026-01-01 --date-to 2026-12-31 --json
bookflow report balance-sheet --company "Reference Plumbing Co" --date-to 2026-12-31 --json
bookflow report cash-flows --company "Reference Plumbing Co" --date-from 2026-01-01 --date-to 2026-12-31 --json
bookflow report income-tax-summary --company "Reference Plumbing Co" --date-from 2026-01-01 --date-to 2026-12-31 --json
```

In the browser choose the reference company, Accounting → Report, then
profit-and-loss or balance-sheet. Set the dates and choose **Run report**.
The statement table shows home-currency totals, bounded account detail and
links to the current general ledger with the same dates. A drill-down warns
when the books have changed since the source statement.

Annual P&L income is6967000, expense510000 and net income6457000 minor units.
Balance-sheet assets are7478600, liabilities21600, posted equity1000000,
prior earnings0 and current-year income6457000. Total equity7457000 plus
liabilities21600 equals assets7478600; difference0. In2027 with no new entries,
those6457000 become prior earnings and current-year income is0.

Both reports support **accrual only**; cash returns E_VALIDATION. P&L dates are
inclusive; balance sheet includes all effects through its as-of date. Each account
amount excludes descendants; subaccounts appear separately and totals count each
effect once. Inactive nonzero accounts remain, zero balances are omitted unless
include_zero is true, and contra balances retain their signs. Display labels obey
account number and lowest-subaccount preferences; full names remain in JSON.

Prior earnings and current fiscal-year income are calculated from raw income/expense
effects and are separate from posted equity. Reports include ordinary journal
transfers exactly as entered, so manually moving balances out of income/expense
changes reported profit. Bookflow does not automatically post closing transfers or
infer a special closing operation from a journal memo.

The row limit applies only to account detail: totals always cover the whole
statement. Next account page keeps the original inputs; changing filters starts
fresh. Any audited company write invalidates an old statement cursor with
E_QUERY_STALE. Preserve the complete output and its metadata if you need an
issued report; a continuation cursor is not an archived report. Amounts always
carry integer minor units and currency; overflows return E_VALUE_RANGE, not floats.

## Cash flows and the tax summary

`report cash-flows` is the indirect statement over the same effects, and the
reference year is its arithmetic oracle because the company opens with no cash.
Net income is 6457000. Every other balance-sheet account contributes its own
negated change: Accounts Receivable 13800 is -13800, Sales Tax Payable 1600 is
+1600 and Business Credit Card 20000 is +20000, so operating adjustments are
7800 and operating is `6457000 + 7800 = 6464800`. Equipment 240000 is -240000
and Accumulated Depreciation 60000 is +60000, so investing is -180000. Opening
Balance Equity 1000000 is financing. Net change in cash is
`6464800 - 180000 + 1000000 = 7284800`, opening cash is 0 and closing cash is
`7267800 + 17000 = 7284800`, which is the total of the two bank accounts on the
balance sheet for the same date. `totals.difference` is 0.

The 60000 of depreciation is added back, because writing the asset down credits
Accumulated Depreciation and a falling asset is a source of cash. It is added
back under **investing**, not under operating: Accumulated Depreciation is a
fixed-asset account and nothing recorded on an account says which fixed asset it
is. Closing cash is the same figure either way. Row balances read on each
account's own normal side, so the credit card row opens at 0 and closes at 20000
while its cash effect is +20000.

`report income-tax-summary` groups the same income and expense activity by
`accounts.tax_line`. No reference account has one set, so every account is under
**Unassigned**: Service Income 6949000, Payment Example Income 18000,
Professional Fees 330000, Insurance Expense 120000 and Depreciation Expense
60000. Each is positive on its own normal side, so that one line totals 7477000
while the report totals are income 6967000, expense 510000 and net income
6457000 -- the same net income the profit and loss reports for the same dates.
