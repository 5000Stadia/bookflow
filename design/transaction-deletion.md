# Transaction deletion

Status: accepted product requirement. Implemented for checks, credit-card charges,
invoices, sales receipts, customer payments and vendor bills. Applies alongside the
existing void action. *Family dispositions* below is the complete list of posted
transaction types and, for each type without a delete command, the reason.

## User setup and authority

User setup must allow deletion permission to be assigned separately for each user.
Permission to create, edit or void a transaction does not itself grant deletion.
Resolve deletion authority within the selected organization/company using the
identity and capability contract. Only an authorized user administrator may change
these permissions; changes are audited. `design/permission-resolution.md` defines exact supported-family capabilities,
default-off assignment, role/read prerequisites and current graph authorization.
Each additional transaction lifecycle requires its own reviewed admission graph.

Enforce the permission in the core for browser, CLI, Python, HTTP and MCP access,
including current authority on retries. Agents remain constrained by their own
and their bound human principal's permissions. Browser action visibility reflects
the same permission; hiding a button alone is not enforcement.

## Delete and void

Void retains a visibly voided business document in normal transaction history.
Delete removes the document from ordinary transaction lists and registers, with
explicit deleted-record history available to authorized readers. Preserve original
document identity, revisions, postings, attachments and attributed audit history.
Deletion is a business action, not physical erasure of ledger or audit rows.

A posted transaction's deletion must atomically cancel its remaining accounting
effect through exact balanced reversal, preserving original amounts and dates.
Deleting an already voided transaction must not reverse it twice. Reports continue
to sum immutable accounting effects; hiding a document cannot change report math.
Record who deleted it, when, through which interface, on whose behalf and why.

Deletion respects current version checks, closed accounting periods and dependent
records. Applied payments, deposits, reconciliation, inventory movements and linked
work require explicit type-specific handling or a clear rejection before mutation;
no dependent records may silently disappear. Released work-billing portions retain
their original proof and history. Requests and retries must not duplicate reversal
or release effects. Deletion does not confer permission to erase audit history or
bypass company isolation.

## Family dispositions

Every posted transaction type in `company/ledger_schema.py::TRANSACTION_TYPES` has a
disposition here. A type is either deletable, or it is listed with the reason its
own lifecycle serves the person without deletion. A type absent from this section
does not exist.

Deletable: `invoice`, `sales_receipt`, `check`, `card_charge`, `payment`, `bill`.

Deferred, with the reason:

- `journal_entry` — `check`, `card_charge` and `transfer` all store as this type with
  a `money_out_documents` marker, and two of them already delete through the purchase
  owner. A delete for this type must first settle which of those rows it may touch,
  and must re-apply the stocked-purchase, item-receipt and inventory fences the void
  planner owns.
- `deposit` — `deposit void` already reverses the deposit at its original date and
  returns every banked receipt to Undeposited Funds. The deposit readers declare
  deletion unavailable (`feature: transaction_deleted`).
- `bill_payment` — a payment pointed at the wrong bill is re-pointed with
  `bill payment unapply` and `bill payment apply`, producing no document to remove.
  A deleted cheque's number occupancy is unsettled.
- `credit_memo` — `credit-memo update` corrects one in place, including one already
  applied and refunded.
- `sales_tax_payment` — nothing records a remittance as filed and nothing settles
  against it; the void's own reversal already returns the agency to what it was owed.
- `customer_refund` — the document carries one revision for its whole life by design,
  which is what lets its receivable attribution name one posting row for ever.
- `vendor_credit` — the gap here is the absent correction verb, not deletion.
- `statement_charge` — the void it shares with the invoice now refuses a settled
  charge with `E_HAS_APPLICATIONS`, on `ledger_schema.SETTLEABLE_RECEIVABLE_TYPES`
  rather than on a type name, so the lifecycle is whole; a wrong charge is voided
  and re-entered, which is what a sixty-dollar document is worth.

## Implementation boundary

This supersedes the former product prohibition on user-facing deletion. Existing
immutable-table guards remain required. Each family named deletable above ships a
`delete` command carrying its own `transaction.<family>.delete` capability. A
family added to that list settles deleted-state storage and preserving migration,
history/filter behavior, numbering, retry/replay behavior, restoration policy, its
own dependencies and preview/confirmation flow before code. Accounting,
authorization and migration changes require independent review.
Payment/application planning must accommodate this lifecycle alongside voiding.
