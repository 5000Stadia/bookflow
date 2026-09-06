# Accounting contract fixtures

These fixtures specify acceptance cases for blueprint sections 10.2–10.7 and 14.
The ledger, forms, applications, and reports implement the relevant cases in their
own rows. These are design fixtures, not claims of implemented accounting behavior.

Amounts below are integer USD minor units. Dates are accounting dates; recorded
times are distinct. Every named id is a symbolic stable identity. No test computes
money with floating-point arithmetic. Each branch starts from the stated state.

## 1. Taxed inventory sale

Opening batch O on 2027-05-01: Dr Inventory Asset 6000, Cr Opening Equity 6000.
There is one stocked unit of item I, base-unit cost 6000. The fixture has no other
inventory movements or costing dependencies. Customer C is `Harbor Plumbing`.

Invoice D, number 1001, revision R1, date 2027-05-10:

| Document line | Quantity | Net | Tax | Gross |
|---|---:|---:|---:|---:|
| L1, item I, `Valve`, unit `each`, rate 10000 | 1 | 10000 | 800 | 10800 |
| L2, subtotal display | — | 10000 | — | — |

L1 snapshots the 8% tax rate, tax agency, addresses, terms, unit conversion,
description, and account mappings. L2 displays a subtotal; it does not increase
the document total, sold quantity, or posting amounts. D.total is 10800.

Batch B1, kind original, source D/R1, effective 2027-05-10:

| Account | Debit | Credit |
|---|---:|---:|
| AR, customer C | 10800 | 0 |
| Product Sales | 0 | 10000 |
| Sales Tax Payable | 0 | 800 |
| COGS | 6000 | 0 |
| Inventory Asset | 0 | 6000 |
| Total | 16800 | 16800 |

Posting attribution links the revenue and cost effects to L1 and the tax effect
to its tax component. The AR effect has net/tax attribution summing to 10800.
There is one inventory movement of -1 unit with value -6000, linked to L1 and
the inventory/cost postings. Five accounting legs do not mean five units sold.
Accrual sales are 10000, COGS 6000, profit 4000, tax liability 800, and AR 10800.
The balance sheet reconciles: assets 10800 = liabilities 800 + opening equity
6000 + profit 4000. Trial balance and general ledger agree account by account.

## 2. Partial payment and exact allocation

Starting after fixture 1, payment P dated 2027-05-11 receives 5401 into
Undeposited Funds. Batch BP posts Dr Undeposited Funds 5401, Cr AR/C 5401.
Application A1 applies all 5401 from P to D at that date.

Largest-remainder allocation over L1's remaining components:

| Component | Weight | Integer quotient | Remainder numerator | Allocated |
|---|---:|---:|---:|---:|
| L1 net | 10000 | 5000 | 10000 | 5001 |
| L1 tax | 800 | 400 | 800 | 400 |
| Total | 10800 | 5400 | — | 5401 |

The common denominator is 10800: `5401 * weight = quotient * 10800 + remainder`.
One remaining cent goes to L1 net. Stored allocation integers are 5001 and 400;
repeated report runs cannot choose different rounding. D's remaining components
are net 4999 and tax 400, summing to open AR 5399. P has no available credit.
As of May 10, AR is 10800; as of May 11, AR is 5399 and Undeposited Funds 5401.
Accrual income, cost, and tax liability are unchanged by the payment.

The supported cash-receipt allocation view attributes this receipt as sales
5001 and tax-liability settlement 400. This is not a complete cash-basis P&L or
balance sheet: a cash-basis report including this inventory sale additionally
requires its declared inventory/COGS and tax recognition policies (blueprint 14).
It must reject unsupported basis coverage instead of fabricating a COGS figure.

A subsequent deposit Dr Bank 5401 / Cr Undeposited Funds 5401 moves the cash only;
it cannot create another sales allocation. In a separate full-settlement branch,
the final payment 5399 consumes exactly net 4999 and tax 400. Cumulative allocation
is net 10000 and tax 800, with zero residue and zero AR.

## 3. Edit an applied invoice

Start immediately after A1 in fixture 2, without the deposit or final-payment
branches. On May 12, edit D while May remains open. Keep its May 10 accounting
date, number 1001, L1 and L2 ids, and L1's original snapshots. Add line L3 for
non-taxable service, amount 2000. The caller's expected version must match or
the ordinary aggregate-conflict rules must validate the complete replacement.

Revision R2 supersedes R1. Its total is 12800: product 10000, service 2000, tax
800. The edit writes these records in one transaction:

1. Exact reversal BR1 of B1, effective May 10: Cr AR 10800, Dr Product Sales
   10000, Dr Sales Tax Payable 800, Cr COGS 6000, Dr Inventory Asset 6000.
2. Replacement B2, source D/R2, effective May 10: Dr AR 12800, Cr Product Sales
   10000, Cr Service Sales 2000, Cr Sales Tax Payable 800, Dr COGS 6000,
   Cr Inventory Asset 6000. Each side totals 18800.
3. Exact reversals of A1's old allocation attribution and new attribution to
   R2, all effective May 11, recorded May 12. A1 still applies 5401 from P to D.
4. D's new current revision/version and one audit event with the new records.

Reallocation of A1 uses denominator 12800:

| Component | Weight | Integer quotient | Remainder numerator | Allocated |
|---|---:|---:|---:|---:|
| L1 net | 10000 | 4219 | 6800 | 4219 |
| L1 tax | 800 | 337 | 7200 | 338 |
| L3 net | 2000 | 843 | 11600 | 844 |
| Total | 12800 | 5399 | — | 5401 |

Two residual cents go to L3 net then L1 tax. New sales allocation is 5063 and
tax allocation 338. Old attribution remains in history with its exact negation;
it is not counted alongside the replacement. A later final payment 7399 consumes
L1 net 5781, L1 tax 462, and L3 net 1156 exactly.

Without that final payment, the net ledger is Undeposited Funds 5401, AR 7399,
COGS 6000 debit; Product Sales 10000, Service Sales 2000, Sales Tax Payable 800,
Opening Equity 6000 credit. Both sides total 18800. Accrual profit is 6000.
Assets 12800 = liabilities 800 + opening equity 6000 + profit 6000. Sold stock
quantity remains one, not three; the original/reversal/replacement movement
chain nets to -1. Operational service quantity comes only from L3.

R1 remains renderable with total 10800. The current document renders R2 with
total 12800. A report issued before May 12 retains its original rendered result
and watermark; a rerun after the backdated open-period correction includes R2's
effects. The recorded-time difference must be visible, not disguised as an
unchanged historical report.

Negative cases: lowering D's total below 5401 returns `E_APPLIED_EXCEEDS_TOTAL`;
changing D's customer or moving D's date past May 11 with A1 attached returns
`E_HAS_APPLICATIONS`. Neither rejection writes a revision, batch, or audit event.

## 4. Void and reversal inclusion

Unpaid branch: start after fixture 1. Voiding D appends the exact inverse of B1
on May 10 and marks D voided. It retains number 1001 and every original amount.
The net effect of D is zero in every ledger account; the stock movement chain
returns to the opening one unit/value 6000. The opening batch O remains.
Filtering B1 out because D is voided would leave only a negative sale and is
a failing implementation. A repeated void creates no additional reversal.

Applied branch: start after fixture 3 before its final-payment branch. A direct
void is `E_HAS_APPLICATIONS`. Explicitly unapply A1: append its exact reversal
and reverse its current allocation attribution at May 11, with the actual
recorded time. Then void D: append an exact inverse of B2 at May 10. B1/BR1
still cancel each other. No new invoice number is consumed.

D is zero; P is an unapplied customer credit of 5401. The remaining payment
effects are Dr Undeposited Funds 5401 and Cr AR/C 5401. Aging shows zero invoice
obligation and a separate credit 5401, reconciling to the control-account credit
balance. The payment does not become sales revenue merely because its invoice
was voided. Voiding P after that adds Dr AR/C 5401 / Cr Undeposited Funds 5401
on May 11; only opening inventory/equity 6000 remains. The fixture has no actual
bank refund, deposit, fulfillment, or reconciliation dependency to bypass.

## 5. Master-data rename

Start after fixture 1. Rename customer C to `Harbor Mechanical`; change its
billing address, item I's name to `Valve B`, price to 12000, its tax default,
and the relevant account labels. Do not edit D.

D/R1 still renders `Harbor Plumbing`, its original address, `Valve`, rate
10000, and its original 8% tax facts. Live navigation may separately show the
current master names. Batch B1, source attribution, inventory movement, document
total, and report totals remain byte-for-byte unchanged. A newly created invoice
can adopt the new defaults. A later edit of D that does not explicitly refresh
defaults retains its stored facts; preview makes any requested refresh visible.

## 6. Closed-period rejection and allowed settlement

Start after fixture 2, then set closing date to 2027-05-31.

- Posting a journal entry dated May 31, voiding D, editing D's amount, or moving
  D's date to June 1 returns `E_PERIOD_CLOSED`. The last operation still affects
  B1's May 10 date; the new date cannot conceal that effect.
- Unapplying A1 returns `E_PERIOD_CLOSED` because its correction would restate
  May 11. Reassigning its allocation under an edit cannot bypass this check.
- Every rejection leaves transaction versions, document revisions, posting and
  allocation rows, sequences, and audit events unchanged.
- A new payment of the remaining 5399 on June 2, applied to unchanged D on
  June 2, is allowed. AR is still 5399 as of May 31 and zero as of June 2.
  Its allocations consume net 4999 and tax 400 without restating May.
- A separately entered June adjusting journal may link D as evidence. It has
  its own identity and June effects; it cannot masquerade as D's edit or void.

## Shared checks

Every successful financial operation has one atomic command boundary for its
document state, immutable history, inventory effects where applicable,
applications, exact allocations, idempotency result, and audit event. Injected
failure between those writes leaves none committed. Idempotent replay creates no
second batch, number, movement, or settlement. Amount and source attribution
totals reconcile independently; operational quantities are counted once.

For settlement largest-remainder ties, order by durable settlement-line ordinal,
then net before tax, then binary tax-item id. Physical revision/component ids and
later display reordering do not change this order. First-settlement ordinals follow
binary stable-line-id order; later new occurrences follow submitted order above
the historical maximum. Retired ordinals are never reused.

Independent tie witnesses, with all amounts in integer cents:

- A retained line has ordinal 1 and stable id Z; a later line has ordinal 2 and
  stable id A. Both have remaining net 1. A payment of 1 allocates 1 to Z and 0 to
  A, despite A sorting first as an id. The final payment of 1 allocates 0 to Z and
  1 to A. Reordering their display or replacing physical revision rows changes
  neither result.
- One line has remaining net 1 and tax 1. A payment of 1 allocates net 1, tax 0;
  the final payment allocates net 0, tax 1.
- One line has net 100 and tax components A=8, Z=8. A payment of 8 has denominator
  116: quotients 6, 0, 0 and remainders 104, 64, 64. The two residual cents go to
  net then tax A, giving net 7, tax A 1, tax Z 0. Reversing physical tax-component
  id ordering does not change the result. Final settlement consumes net 93,
  tax A 7, tax Z 8, totaling 108.

Preview, commit, corrections and every interface use these same logical keys.
Repeated partial settlements consume exactly the original component amounts.
Fixtures 2 and 3 retain their numerical oracles.
