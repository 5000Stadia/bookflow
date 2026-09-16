# Transaction deletion

Status: accepted product requirement. Implemented for checks, credit-card charges,
invoices, sales receipts, customer payments, vendor bills and journal entries; a family's
command becomes runnable on a root once that root has activated a permission catalog
carrying its descriptor. Applies alongside the existing void action. *Family dispositions* below is the complete list of posted
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

Deletable: `invoice`, `sales_receipt`, `check`, `card_charge`, `payment`, `bill`,
`journal_entry`.

Deferred, with the reason:
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

## What a journal entry is, before it is deleted

`check`, `card_charge` and `transfer` all store as `journal_entry` with a
`money_out_documents` marker; an inventory adjustment or cost correction stores the
same way with an `inventory_documents` marker, and an item receipt with an
`item_receipts` row. Nothing in the transaction itself says which. So every write that
addresses a transaction *as a journal entry and nothing more* — delete, update, void —
must first resolve what the document really is, and refuse it by name, pointing at the
command that owns it: `check delete`, `card-charge delete`,
`inventory void`, `item-receipt void`, or, for a transfer, `transfer void`, since a
transfer has no deletion of its own. Refusing rather than accepting is the requirement:
a cheque accepted here would carry a second deletion record over the one its own family
already wrote, and a cheque edited here would leave its own form describing a posting
nobody entered. The account register is not one of these writes: it is the surface the
three money-out documents post through and it corrects one in the document's own shape,
so the register and the document remain two doors into one entry. The storage enforces
the deletion rule independently, so the second record cannot exist even if a writer
forgot to ask.

## Implementation boundary

This supersedes the former product prohibition on user-facing deletion. Existing
immutable-table guards remain required. Each family named deletable above ships a
`delete` command carrying its own `transaction.<family>.delete` capability. A
family added to that list settles deleted-state storage and preserving migration,
history/filter behavior, numbering, retry/replay behavior, restoration policy, its
own dependencies and preview/confirmation flow before code. Accounting,
authorization and migration changes require independent review.
Payment/application planning must accommodate this lifecycle alongside voiding.
