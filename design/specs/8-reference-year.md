# Full-year domestic reference company

Target: blueprint 9.3 and the domestic report witnesses in row 8. The packaged
reference seed creates `Reference Plumbing Co` beside `Demo Plumbing Co` in the
same disposable demo organization. It records explicit source transactions and
independent expected monthly and annual account balances.

## Public invocation

`demo reset` accepts `include_reference`, boolean, default false. The CLI flag is
`--include-reference`. With the default it creates the ordinary demo company.
With true it creates and seeds both companies under the replacement demo
organization. The existing reset boundary remains the complete demo organization:
all of its companies move to trash before recreation, including a previous
reference company. The command documentation and browser preview state that
scope explicitly. No existing non-demo organization is adopted or reset.

The typed output adds nullable `reference_company_id` and
`reference_display_name`. Preview includes the reference company when requested
and remains free of files, migrations, audit, configuration or database writes.
Both companies use the same company rollout, chart, public seed command and
permission paths. IDs and company-local data remain independent. Reference
seeding uses explicit journal numbers and supplied audit reasons.

The two seeds are separate package resources. `reference.toml` holds reference
company settings and commands; `reference-expected.json` holds expected account
balances and report totals with their measurement dates. A linked documentation
page gives the exact invocation, source amounts and independent arithmetic.
The ordinary demo fixture stays small unless reference data is requested.

Reset retains its existing multi-commit filesystem lifecycle and partial-write
error behavior. An after-commit seed failure must be reported; it cannot return
successful completion or claim an all-or-nothing rollback. A rerun explicitly
resets the disposable demo organization. Existing user data outside that
organization and the running isolated human checkpoint are not used by tests.

## Source transactions

All dates use the fixed calendar year 2026. All amounts below are USD minor units. Accounts use the contractor chart where
available; missing reference accounts are created through account commands.

| Date | Debit | Credit | Amount |
|---|---|---|---:|
| January 1 | Checking | Opening Balance Equity | 1000000 |
| January 2 | Insurance Expense | Checking | 120000 |
| Each month, day 15 | Checking | Service Income | January 300000, February 350000, March 400000, April 450000, May 500000, June 550000, July 600000, August 650000, September 700000, October 750000, November 800000, December 850000 |
| Each month, day 20 | Professional Fees | Checking | 25000 |
| June 15 | Equipment | Checking | 240000 |
| July–December, day 28 | Depreciation Expense | Accumulated Depreciation | 10000 |
| November 16 | Checking | Service Income | 90000, then voided as a duplicate |
| December 21 | Professional Fees | Business Credit Card | 30000 |
| December 27 | Business Credit Card | Checking | 10000 |

A correcting edit changes the May receipt from 500000 to 525000 at its original
accounting date. That exact reversal and replacement remain in history. The
November duplicate is voided with an exact reversal. No invoice, bill, payment
application, inventory, tax or depreciation calculation is implied by these
explicit journals.

## Independent expected results

December 31 balances are positive on each named side:

| Account | Side | Minor units |
|---|---|---:|
| Checking | debit | 7255000 |
| Equipment | debit | 240000 |
| Professional Fees | debit | 330000 |
| Insurance Expense | debit | 120000 |
| Depreciation Expense | debit | 60000 |
| Accumulated Depreciation | credit | 60000 |
| Service Income | credit | 6925000 |
| Opening Balance Equity | credit | 1000000 |
| Business Credit Card | credit | 20000 |

Trial balance debit and credit totals each equal 8005000. Service income less
expenses is 6415000. Net assets after card liability are 7415000, equal to
1000000 capital plus 6415000 income. These latter arithmetic oracles describe
future profit-and-loss/balance-sheet regression values; those report commands
are not exposed by this increment. AR/AP are zero; aging reports remain future
work rather than a claimed successful report run.

June 30 Checking is 3065000, Equipment 240000, Professional Fees 150000 and
Insurance Expense 120000, against Service Income credit 2575000 and capital
credit 1000000. Trial balance each side is 3575000. All other reference balances
are zero. Monthly expectations are explicit stored integers derived from these
source transactions, independent of production report helpers.

## Witnesses

Run the actual reset command with the opt-in flag in an isolated data root,
then query both companies through public commands. The reference trial balance
matches all twelve monthly checkpoints and the annual expected table. The
ordinary demo still reconciles to its independent seed expectations.

For each reference account, paged general ledger opening/period/closing totals
and every returned source posting reconcile to its expected balance. Verify gross
debits and credits independently, including the reversal pairs, and exercise
July–December with the nonzero June 30 opening balances. Traversing
small pages yields exactly the same posting IDs, order and amounts as a large
page traversal. The May correction and November void include their original,
reversal and replacement facts without multiplying financial sums. Company
show/list balances agree with the report; a copied closed root retains them.

Verify first/default reset compatibility, opt-in preview purity, independent
company IDs, readonly denial, repeated reset, a foreign existing organization
name collision, and visible partial-seed failure. Generated schemas, examples,
CLI help and packaged documentation stay current. Real Chrome at desktop and
390-pixel width selects `Reference Plumbing Co`, opens its Checking register,
and displays 72550.00 USD without body overflow. Reference data arrives before
this increment's artifact review.
