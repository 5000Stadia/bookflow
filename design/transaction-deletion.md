# Transaction deletion

Status: accepted product requirement; not implemented. Applies alongside the
existing void action to invoices, checks and other applicable transaction types.

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

## Implementation boundary

This supersedes the former product prohibition on user-facing deletion. Existing
immutable-table guards remain required. No delete command is implemented by this
document. The owning plan must settle deleted-state storage and preserving
migration, history/filter behavior, numbering, retry/replay behavior, restoration
policy, each supported type's dependencies and preview/confirmation flow before
code. Accounting, authorization and migration changes require independent review.
Payment/application planning must accommodate this lifecycle alongside voiding.
