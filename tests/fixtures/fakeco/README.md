# Harbor Electric LLC: a fake company for fit checks

Harbor Electric LLC is a made-up three-person electrical contractor in Joliet, Illinois (owner Mike Harbor, a
journeyman, an apprentice; payroll through an outside service). It kept its books in the anchor desktop product
until 2026-06-30 and moves to Bookflow then. Every file here is written by `tests/fakeco.py` from
`tests/fakeco_data.py`; `tests/test_fakeco_fit.py` checks that the files are exactly what the generator writes,
and keeps July in Bookflow through `tests/fakeco_replay.py` to compare the books with the key.

Regenerate after changing either module:

```sh
PYTHONPATH=src:. python -m tests.fakeco
```

## `handed-over/`: what the owner gives the bookkeeper

`old-books/`, the desktop product's exports as of 2026-06-30 (report CSVs in Windows-1252, CRLF):

| File | Export |
|---|---|
| `lists.iif` | Lists to IIF Files, every list in one file: chart of accounts (63 accounts, two of them non-posting), customer types, vendor types, terms, payment methods, customers and jobs (36), vendors (19), employees, other names, items (20) |
| `trial_balance.csv` | Trial Balance, accrual basis: 367,875.65 each side |
| `open_invoices.csv` | Open Invoices: 16 invoices and a credit memo, 32,485.43 |
| `unpaid_bills.csv` | Unpaid Bills Detail: 8 bills and a vendor credit, 7,868.81 |
| `ar_aging.csv`, `ap_aging.csv` | A/R and A/P Aging Summary |
| `inventory_valuation.csv` | Inventory Valuation Summary: 8 stocked items, 5,340.25 |
| `reconciliation_summary_checking_2026-06.csv`, `reconciliation_summary_savings_2026-06.csv`, `reconciliation_summary_visa_2026-06.csv` | The June reconciliations: statement balances and what was still uncleared |
| `uncleared_2026-06-30.csv` | The uncleared checks, deposit and card charges, one by one |
| `undeposited_funds_2026-06-30.csv` | Undeposited Funds' QuickReport filtered to Cleared No: the two customer checks waiting for deposit |
| `vendor_1099_summary_2026-06.csv` | 1099 Summary, January through June 2026: what the 1099 subcontractor was paid so far this year |

The exports carry what a real file carries: an inactive customer still owing, a tax-exempt customer and a
contractor customer without sales tax, jobs under two property managers and a builder, terms the new books
do not have yet (`Net 21`), account numbers that collide with Bookflow's general chart (6100, 9100), a group
item, a duplicate vendor (`Midland Elec. Supply` beside `Midland Electric Supply`), 1,662.00 in Undeposited
Funds, three outstanding checks and a deposit in transit, two card charges not yet posted.

`2026-07/`: the owner's paperwork for each week (`paperwork-2026-07-week-1.txt` to `-5`), the checking
statement as OFX and CSV (the CSV newest first, with running balances), the savings statement and the Visa
card's transactions as CSV, and the card statement's summary box (`visa-2026-07-summary.txt`), since a card's
CSV download carries no balance. `2026-08/` and `2026-09/` carry one shorter paperwork file each and the same
statements. Statements show exactly what the paperwork describes, as the bank and card company posted it,
plus what only a statement knows: the monthly service fee and interest.

July holds, among the routine work: a short payment, a bounced customer check with the bank's fee and a
returned-check fee charged to the customer, a returned EV charger with a credit memo and a refund check, early-
payment discounts taken by a customer and by Harbor Electric, an old vendor credit used in a bill payment, stock
returned to the supplier, a shrinkage adjustment, a bad-debt write-off, one check paying three invoices on two
jobs, a cash sale, two payroll runs, a loan payment split into principal and interest, two owner draws, a
transfer to savings, the June sales tax payment, the card payment, a 1099 subcontractor's bills, and a card
receipt under the duplicate vendor's name.

## `answer/`: what the books should say

- `events.json`: every event from 2026-07-01 to 2026-09-30 as data, with the amounts the generator worked out.
- `key-2026-06-30.json`, `key-2026-07-31.json`, `key-2026-08-31.json`, `key-2026-09-30.json`: the trial
  balance, receivable and payable agings with their open documents, sales tax owed by agency, stock on hand and
  its value, and for checking, savings and the Visa card the statement's beginning and ending balances, the
  items it cleared and the items still outstanding.

The key is computed in `tests/fakeco.py` from the same events the files are written from, never from Bookflow.
Its conventions are listed in each key file under `conventions`. Amounts are decimal strings; trial balance
amounts are debit positive; account names are the old books' full names.
