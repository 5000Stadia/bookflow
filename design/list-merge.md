# Merging duplicate list entries (R117)

Status: built on branch future/merge (customers and vendors). Items and accounts are later.

## The anchor

The anchor desktop product merges two list entries when one is renamed to exactly the
other's name. Every transaction moves to the survivor, the other entry is removed, and the
merge cannot be undone. Some merges are refused (for example, accounts of different types).

## Where a customer id is held

Measured on the demo company (company.db at migration 0066):

| Kind | Tables | Mutable? |
|---|---|---|
| Ledger | `posting_lines.name_id`, `transaction_revisions.name_id` | No: update/delete triggers |
| AR settlement keys | `payment_component_keys.party_id`, `credit_source_keys.party_id`, `credit_profiles`, `customer_refund_profiles`, `payment_profiles.payer_id` | No: immutable payment history |
| Document profiles | `sales_profiles.customer_id`, `work_revisions.customer_id`, purchase lines' `customer_id` (billable cost) | No: revision rows |
| DB-level guards | `applications_exact_party`, `application_allocations_owned_sources` require an application's component key party to equal the invoice's `sales_profiles.customer_id` and the AR posting line's `name_id` | Trigger |
| List records | `customers.parent_id` (jobs), `customer_vendor_links`, addresses, contacts, billing groups | Versioned list rows |

## Options

**(b) Re-point by new revisions.** Every one of B's documents gets a new revision naming A,
which reverses and re-posts its ledger lines. This rewrites posted effects in closed and
reconciled periods, cannot revise voided or deleted documents, and cannot move immutable
settlement keys at all: B's payments and credits keep `party_id = B` while their invoices
would say A, which the `applications_exact_party` trigger then forbids for every later
apply or unapply. It is costly, partial, and not reversible.

**(a) Alias.** A merge records "B is merged into A". Nothing posted changes. Reads that group
or filter by customer resolve B to A; B is hidden from pickers and refused for new
references; the merge is an audited event that can be undone.

## Recommendation: (a), the alias

The deciding fact is that settlement already works across parties. A customer payment
carries one component key per (party, AR account, currency), and receive-payment already
offers the customer's whole family (the customer and its jobs), keying each component to the
party of the invoice it pays. A merged-away customer is treated the same way: a payment
received from A that pays B's old invoice carries a component keyed to B, posts its A/R
credit to B, and satisfies every existing trigger unchanged. So the alias needs no change to
any posted row, settlement key or guard, only to reads and to the family set.

What resolves through the alias:

- Customer list and pickers: B is hidden (shown under A's merge history); new documents
  naming B are refused with a pointer to A.
- Customer balance, family balance, credit-limit exposure: B's postings count to A.
- A/R aging (summary and detail), customer balance summary/detail, open invoices, statements,
  customer register and QuickReport, sales by customer: B's rows appear under A.
- Receive payment, apply credit, refund: A's family set includes merged-away customers.
- Unposted documents still naming B (estimates, sales orders, memorized transactions,
  billable costs) resolve to A when they next get a revision or are converted.

Refusals (v1):

- Merging a customer into itself, or into its own ancestor or descendant.
- A job into a non-job, or a non-job into a job.
- Different currencies: A and B have documents in different currencies.
- B already merged, or A itself merged away (merge into the survivor instead).
- B has jobs of its own, or an active customer-vendor link (move or unlink first). Moving
  jobs along with B is a cheap follow-up as recorded list edits.

The action is person-only, takes a reason, and is idempotent on its key. A preview shows
what moves (document counts by type, open balance of each) and A's balance after.
Undo ends the alias with its own audited event and reason; nothing else changes back,
because nothing else changed.

## Storage

The alias needs a table: `party_merges` (migration co0067), one row per merge with the
party kind, merged id, survivor id, reason, audit event, and an undo stamp (undo time, by,
reason, audit event), a unique index on the merged id among live merges, and append-only
triggers except for the single undo stamp. A merge never updates posted rows.

Vendors use the same table and code path (`ap_obligation_keys`, bill-payment validation and
the vendor balance read are the vendor-side readers). Items and accounts are different in
kind (accounts carry type and hierarchy rules, items carry inventory cost layers) and are
not in this design.

## As built

- `party_merges` (co0067). Triggers: no delete; the only update is the one undo stamp; both
  entries must exist; merges are one level deep (a survivor is never merged away, and nothing
  is merged into a merged-away entry). To fold a survivor into a third entry, undo the merges
  into it first.
- `src/bookflow/company/party_merges.py` owns the two SQL fragments every reader uses:
  `survivor_sql(kind, expr)` (what an id reads as) and `family_cte()` (a customer, its jobs,
  and everything merged into any of them; a merged-away job is not counted under its old parent).
- Commands (`src/bookflow/commands/merge_cmds.py`): `customer merge`, `customer unmerge`,
  `vendor merge`, `vendor unmerge`. People only (an agent gets `E_PERMISSION`), reason
  required, idempotent on the pair, `--dry-run` is the preview. Refusals are `E_MERGE_REFUSED`
  with the problem named. Permission delta `party-merge-v1` on `cutover-v1`.
- The merged entry is made inactive through the ordinary list path (audit action `merge`, not
  one the generic list undo has a handler for), and `customer activate` refuses it while merged.
- Readers resolved: the receivable effects behind A/R aging, open invoices, statements and the
  customer balance summary and detail; customer and vendor balance expressions, family balance
  and credit-limit exposure; every payment family (receive, selection, preparation, recovery,
  payer balance, authority); sales by customer; the payable effects behind A/P aging, unpaid
  bills and vendor balances; expenses and purchases by vendor; 1099; open purchase orders.
  Selecting the merged entry by name in a report filter reads as the survivor.
- A receipt from the survivor pays a merged customer's old invoice: the component is keyed to the
  merged customer, which `payments.py` admits although it is inactive.
- Vendors: a bill payment never crosses vendors, so a vendor with an open payable balance is
  refused ("pay or settle its bills and credits first"). The reads share the alias unchanged.

Not resolved yet (each a reader that still shows the merged entry under its own name):
transaction lists and QuickReports filtered by name, the customer and vendor registers, job
profitability, time and billable-cost reports, and the workbench customer and vendor centres'
own lists of merges. Unposted documents still naming the merged entry (estimates, sales and
purchase orders, memorized transactions) are refused on their next save until re-pointed.
