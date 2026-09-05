# Row 8

Target: row 8 in `design/intention.md`; product contract: `design/blueprint.md`
sections 8, 10 and 14, and the applicable journal cases in
`design/accounting-contract-fixtures.md`.

## Domestic journal increment

The first increment exposes `journal post`, `show`, `update`, `void`, `query` and
`history`, plus `report trial-balance` and `report general-ledger`. Company-scoped
writes require standard role and `ledger.post`; journal reads use `ledger.read`
and reports use `reports`, with member role. All commands use shared dispatch,
typed JSON, ordinary context, previews, audit and request idempotency.

A journal input contains an accounting date, optional number and memo, and at
least two entered lines. Each line names an account, debit or credit side, a
positive exact home-currency amount, optional party/type pair, class and
explanation. Accounts must be active posting accounts; AR requires a customer or
job and AP requires a vendor. Foreign amounts are rejected in this increment.
Floats, booleans and contradictory amount representations are invalid. Entered
amounts and each balanced side of a journal must fit signed 64-bit storage.

Numbers are unique per type, including voided journals. The journal sequence
starts at 1 with an empty prefix. Automatic allocation skips occupied numbers in
the transaction. A supplied number is nonblank, trimmed, case-sensitive text and
does not advance the counter. Rollback and idempotent replay do not consume a
number. Duplicate supplied numbers return `E_DUPLICATE_NUMBER`.

Journal total means the home debit total; output also names debit and credit
totals explicitly. Account balance means that account's own posting balance,
positive on its normal side, without descendants. Report fields identify signed
debit-minus-credit values separately. Hierarchical rollups are not implicit.

## Immutable storage and posting

The next company migration adds stable versioned transaction headers, immutable
revisions, stable document-line identities, revision-owned entered lines,
immutable posting batches and lines, and exact posting-source links. New rows use
frozen revision-local DDL, explicit ownership foreign keys, unique line positions,
unique reversal targets and creation provenance. History has no cascading delete;
triggers reject changes or deletion of immutable records.

A header retains its identity, number and current-revision pointer through changes.
Revisions snapshot the issuer and displayed account, party, class and custom-field
facts needed to reproduce the journal. Historical output uses those snapshots.
Stable line identities belong to one document; removed identities never return.
Sources connect posting effects to the exact immutable entered lines. Every batch
balances independently, and source allocations sum exactly to their posting line.
No command accepts caller-authored posting batches or direct posting-line edits.

Posting creates the header, first revision, original batch and source mappings.
A changing update appends a revision, reverses the current unreversed business
batch in full at its old accounting date, and adds a full replacement at the new
date. Reversals preserve exact amounts, dimensions, foreign facts and source
attribution, including inactive masters. Unchanged snapshots remain unchanged;
refreshing displayed defaults is an explicit previewed input. A no-op creates no
revision or batch.

Voiding requires a reason. It appends an exact reversal at the original batch's
date and marks the stable header voided, preserving amounts, number and all
revisions. Earlier correction pairs remain reportable. A repeated void is a no-op
following the version check. A voided journal cannot be updated.

A supplied stale expected version always conflicts for the complete financial
aggregate. Disjoint-field merging never bypasses financial validation. Optional
blind writes retain the existing warnings; browser forms submit the shown version.
Every affected old and new date must be later than the closing date. Moving a
journal to an open period cannot bypass a closed original date. Decisive period
and reference checks run in the writer transaction. List undo rejects ledger events.

## Transaction and audit boundary

Dispatch retains the single company commit and idempotency boundary. The shared
audit writer accepts an optional preallocated event ID. Ledger persistence writes
its one company event and safe snapshots within the existing transaction, then
inserts the aggregate referring to that event, and returns `Applied(audited=True)`.
It does not commit internally or use the finalized-command path. Header pointers,
number allocation, revisions, batches, sources, principals, audit and retry output
roll back together on failure.

## Reports and continuations

Trial balance and general ledger include every effective posting batch, including
originals and reversals. Trial balance nets by account as of the requested date.
General ledger supplies opening balance, period debits and credits, closing balance
and dated details over an inclusive period. Inactive historical accounts remain
available. Running balances are computed before page slicing.

General ledger groups by stable account ID, then orders by effective date, batch
ID, line number and posting-line ID. Trial balance orders by stable account ID.
Each page computes rows, whole-filter totals and metadata in one bounded read
snapshot. Metadata includes company, period, accrual basis, report version, schema
revision, generation time, home currency and audit watermark. Cash basis is rejected.

Continuation binds company, identity, permissions, filters and the exact relevant
watermark. The complete state, including initial report metadata and resolved
account, is authenticated with HMAC-SHA256 before any decoded value is accepted.
A private 32-byte company-local key is created during migration and travels with
a copied company; it is excluded from command output, audit and annotation targets.
Malformed, unsigned or tampered continuations return E_VALIDATION. A relevant posting, correction, void or displayed-label change returns
`E_QUERY_STALE` before another page is emitted; clients discard accumulated pages
and restart. Successful continuations preserve generation time and report metadata.
This is restart-on-change pagination, without an as-recorded historical framework.
Authority is revalidated on every page. Fine-grained restrictions require the
nonrevealing authorized-dependency cursors from identity integration.

Exact integer aggregation never uses SQLite REAL or TOTAL. An integer aggregate
may return lossless decimal text to avoid SQLite's intermediate SUM overflow;
public amounts outside their documented integer range return `E_VALUE_RANGE`.
Source joins never multiply financial sums. Account projections and dependency
checks use posting lines through set-based queries.

## Surfaces, seed and witnesses

Journal header details expose notes, files and activity through their stable ID.
The record-target registry includes persistent revisions, entered-line identities,
posting batches, lines and sources. The demo posts concrete journal examples
through commands and retains the existing notes and PDF links.

Tests cover independent balanced-ledger oracles, unbalanced rejection, exact
correction and void effects, old/new accounting dates, retired line IDs, historical
snapshots after master changes, closed-period rejection with no effects, full
aggregate conflicts, no-op/retry numbering, rollback injection, copied-company
history, exact overflow, readonly denial, and CLI/Python/HTTP/generated-form parity.
A backdated posting between report pages must reject continuation and reconcile
on restart; it must not silently change a later running balance.

## Following increments

Journal custom-field entry and immutable definition/value snapshots follow the domestic core.
Manual dated exchange rates and foreign-tagged posting follow a separately fixed
rounding and rate-scope contract. Functional register entry uses the same journal
service, supports keyboard entry and displays the authoritative posted result in
place. Its field inventory, split behavior and human usability checkpoint remain
explicit work before row closure. Full agent isolation remains dependent on the
unfinished identity integration. Invoice, payment, allocation, banking, inventory,
commercial-tax and cash-basis workflows are outside this journal increment.

Register field inventory and implementation contract: [8-register.md](8-register.md).
