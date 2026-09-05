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
history. No depreciation calculation, invoice, bill, payment application, tax or
inventory processing is implied.

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

| Checkpoint | Checking | Trial balance, each side | Income | Net assets |
|---|---:|---:|---:|---:|
| June 30 | 3065000 | 3575000 | 2305000 | 3305000 |
| December 31 | 7255000 | 8005000 | 6415000 | 7415000 |

At December 31, debit balances are Checking 7255000, Equipment 240000,
Professional Fees 330000, Insurance Expense 120000 and Depreciation Expense
60000. Credit balances are Accumulated Depreciation 60000, Service Income
6925000, Opening Balance Equity 1000000 and Business Credit Card 20000.
Income is `6925000 - 330000 - 120000 - 60000 = 6415000`.
Net assets are `7255000 + 240000 - 60000 - 20000 = 7415000`, equal to
capital plus income. AR and AP are zero. Income, net-assets and AR/AP values
are arithmetic regression oracles; profit-and-loss, balance-sheet and aging
report commands are not exposed yet.

The ordinary demo contains ten journals, trial balance 664595 on each side and
Checking 612095. Account show/list express balances on each account's normal
side; reports use debit-minus-credit values. Both companies remain ordinary
independent companies selectable through company show/list and the browser
picker. The reference Checking register shows `72550.00 USD`.

## Documentation generation

Generation is rootless: no initialized data root, company or credentials are needed.

<!-- bookflow-example: illustrative -->
```sh
bookflow docs generate --output docs
bookflow docs generate --output docs --check
```

The generated [demo command reference](cli/demo.md) contains the typed contract.
