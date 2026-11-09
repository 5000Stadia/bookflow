# Intercompany (R123)

Design contract, 2026-11-09. The full approved roadmap scope is **“Intercompany: linked transactions that post
matching entries in two companies of one organization. Approved (Kabe 2026-10-31: \"Full approve, hold off on
113 125 155\"); design queued.”** This document proposes commands, storage and build increments. It does not
claim an implemented feature or authorize product implementation.

**Outcome.** Enter a transaction once, preview both balanced entries, and keep their identities, corrections and
cancellations together. Each company's due-to/due-from ledger explains its own balance. A person or agent can
continue the same pair, but a link never grants access to the other company's books. There is no completed state
with only one side posted. Recovery may temporarily make affected books unavailable; it must never present an
incomplete pair as settled accounting.

## 1. The anchor and the existing core

Reference read: the anchor enterprise 24 guide, PDF/printed pp. 27, 66 and 80, on 2026-11-09 (contents pp. 2–5
used to locate them). Page 66 describes relationship management through a dashboard, assigned asset/liability
accounts, bulk transactions and historical intercompany reports. Page 27 describes combining selected company-file
reports into columns and totals; page 80 describes working in two separate company files. These pages do not
specify crash recovery, permission delegation, correction accounting or currency conversion. Adopt the relationship,
account mapping, simple entry and history/report route. The protocols below are Bookflow design decisions, not
claims about the anchor's implementation. Reciprocal receivable/payable entries and explicit reconciliation are
standard bookkeeping practice; this document specifies their exact application here.

Existing owners under `src/bookflow`:

- `hub/schema.py`, `hub/companies.py` and `hub/access.py` own registration, organization association, memberships
  and company visibility. Organization membership can admit multiple companies; company membership alone does not.
  Current permission resolution, rather than a legacy hub-admin shortcut, decides execution rights.
- `core/dispatch.py` resolves a visible company before opening its file. `core/session.py` has one selected company
  connection. `storage/engine.py` uses durable WAL databases and stable read snapshots. This is not an existing
  two-company transaction API. `core/commit_hooks.py` owns commit/publication admission; extend that owner rather
  than committing behind it. The local root lock and hosted writer/file gates remain the concurrency owners.
- `company/journals.py`, `journal_contract.py` and `journal_models.py` own integer amounts, balanced posting plans,
  closed-period validation, document identities, revisions and immutable posting batches/lines. System-owned
  control accounts cannot be bypassed with arbitrary journal lines. Reuse these validations and persistence shapes.
- `company/transfers.py` and `commands/transfer_cmds.py` implement movement between two eligible accounts **inside
  one company**, crediting the source and debiting the destination. Updates reverse and replace at the original
  date; void reverses there. R123 is a new linked document owner, not a remote account selector on `transfer post`.
- `hub/permission_runtime.py`, `permission_access.py` and `agent_authority.py` resolve current capabilities and
  actor/principal intersection. `company/batches.py` records exact effects inside domain transactions and supports
  native inverse adapters, dependency preflight and sliced/resumable `batch reverse`.

[blueprint.md](blueprint.md) §3 keeps books in company folders and registry/authority in the hub. R123 adds only
coordination metadata to the hub, explicitly extending that boundary; neither company contains the other's journal,
accounts, credentials or memberships. [method.md](method.md)'s shared intention and examination rules apply:
isolation is a property to demonstrate, not an assumption that a multi-company screen establishes. The governing
product constraints are [intention.md](intention.md), [permission-resolution.md](permission-resolution.md),
[principal-directives.md](principal-directives.md) and [reversible-batches.md](reversible-batches.md).

## 2. Layers and the simple route

| Layer | Default | Gives | Requires |
|---|---|---|---|
| Linked entries | Off in each company | Relationship setup, two-sided entry, correction, void, reversal, tie report | Same organization, same host/data root, equal home currency; grants in both companies |
| Saved entry templates | Off | Named local account/dimension defaults for repeated entries | Linked entries; never required to post |
| Bulk entry/import | Off | Explicit previewed selection, source keys and one reversible batch | Linked entries; templates optional |

Company settings enable each independently with ordinary settings authority. Enabling linked entries posts nothing
and creates no accounts. Setup selects existing active accounts explicitly: a due-from other-current-asset and a
due-to other-current-liability in each company, in the common home currency. Prefer dedicated accounts per
relationship; initial activation requires zero balances and no pre-existing postings on these accounts. Existing
manual intercompany balances need an explicit migration decision under K2, not automatic opening entries.

Simple route: enable linked entries in both companies → establish the relationship and four accounts → New linked
entry → choose “Advance funds” or “Allocate expense”, date, amount and local offset accounts → preview → Post.
A relationship is between two distinct registered company IDs in one organization, unique for the unordered pair.
Setting it up requires management rights on both sides. Account names need not match. No customer, vendor, invoice,
bill, pipeline, approval queue or consolidation setup is required. Every monetary act still requires its own grants.

Templates capture defaults, not standing authority; using one resolves current references and permissions. Turning
a layer off prevents new use, preserves history and permits correction, void, reversal and recovery of existing
work. Disabling a relationship prevents new pairs but never abandons an existing obligation. Re-enabling restores
the same IDs. Setup changes create new relationship revisions and affect only new pairs; existing pairs retain
their captured mappings. Used control accounts cannot be retyped, merged, deleted or reassigned to another relation.

## 3. Entries and balances

Each pair has a stable opaque pair ID, a common accounting date and currency, two local document IDs, a relationship
revision and a shared operation/revision number. Each side owns its own lines, descriptions, class dimensions and
audit. Input names both companies explicitly; the currently selected company only controls the initial viewpoint.
One pair cannot span three companies; allocating across several companies creates several pairs in one batch.

For **Advance funds**, choose lender A, borrower B, A's bank and B's bank and a positive amount. Bookflow records an
already authorized/observed movement; it does not instruct a bank or transfer cash. For **Allocate expense**, choose
paying/allocating A, receiving B and each company's eligible expense account. It reallocates an expense already
recorded in A, without repeating the original cash payment. Preview makes that prerequisite explicit. The user
supplies the amount; no proportional allocation or implied tax calculation occurs. Both forms generate the due
lines. **Journal detail** permits explicit eligible offset lines totalling that amount on each side, using native
journal dimension rules; the reciprocal control lines are generated and cannot be overridden independently.
Reject AR/AP, tax, inventory and other subledger-owned controls as offsets; native invoices, bills, stock transfers,
asset disposals, tax documents and legal transfer pricing are separate scope under K1.

All amounts are bounded integer cents plus currency; reject floats, excess precision, overflow, zero and negative
entry amounts. Direction is an explicit company/direction choice. Both home currencies and all selected accounts
must match. Foreign-tagged journals do not supply a cross-company FX policy. Different currencies refuse before
creating an intent, with the limitation stated in help and preview.

**Hand-worked fixture (USD).** Initially all four dedicated control accounts are zero.

| Event | A | B | Outstanding A due-from / B due-to |
|---|---|---|---|
| Advance 10,000 cents | Dr Due from B 10,000; Cr Bank 10,000 | Dr Bank 10,000; Cr Due to A 10,000 | 10,000 / 10,000 |
| Allocate 2,500 cents of expense already in A | Dr Due from B 2,500; Cr Expense 2,500 | Dr Expense 2,500; Cr Due to A 2,500 | 12,500 / 12,500 |
| B repays 4,000 cents | Dr Bank 4,000; Cr Due from B 4,000 | Dr Due to A 4,000; Cr Bank 4,000 | 8,500 / 8,500 |

“Record repayment” takes explicit outstanding source pair IDs and integer allocations (default oldest date/ID,
previewed before execution), capped at remaining obligations. Its common date must be on/after all selected source dates and open in both
companies. It creates another linked pair with inverse control
signs and links to those sources. It never silently treats B as making a new advance that leaves gross receivables
and payables uncleared. A new advance in the opposite direction is a distinct obligation using B's due-from and A's
due-to accounts. No automatic bilateral netting. Allocation links sum exactly to the repayment; partial repayment
and retry cannot consume an obligation twice. Bank fees are separate local transactions, not a mismatch in the pair.

For each obligation, signed due-from activity in the creditor equals credit-minus-debit due-to activity in the
debtor, including every correction and reversal. For an as-of date, sum immutable effective-dated posting lines,
not current document totals: **A due-from minus A due-to = B due-to minus B due-from**. Also compare gross
receivables/payables by direction so offsetting errors cannot hide in a net zero. The local control-account GL total
must equal linked detail. Reserve mapped accounts to this owner across journal, register, import, accountant
adjustment and other posting paths; unsupported direct posting refuses with the owning command. This restriction
begins at activation, not retroactively on existing ordinary accounts.

`report intercompany` shows opening, increases, reductions, closing, unsettled sources and drill-downs for the
selected local company. With authorized reads in both companies, “Compare both” uses one coordination watermark,
common cutoff and both complete ledgers. It identifies unmatched operation/revision IDs, amount/date/currency
mismatches and local GL-versus-detail differences. Never auto-post a balancing plug. One-sided access yields only
the local reconciliation, labelled “Counterpart not checked”; no remote balance, difference, count or timestamp.
This is an intercompany reconciliation report, not consolidated statements or elimination journals.

## 4. Keeping two isolated files in step

Two independent WAL commits are not atomic together. Do not claim that two sequential native `journal post` calls,
or SQLite `ATTACH` while retaining WAL, provide that guarantee. Add a core coordinator with durable decisions,
company-local prepared facts and a mandatory publication fence. All interfaces call it. It owns two separately
admitted company sessions, never swaps an unchecked connection into the ordinary selected-company session.

States are `preparing → commit_decided → complete`, or `preparing → abort_decided → aborted`; failures awaiting
repair remain visibly `recovering` with the durable decision unchanged. The business document becomes posted only
when the protocol completes. A temporary physical difference between files is possible, but no admitted reader,
export, backup or subsequent writer may observe/use the partially applied books.

1. **Admit and plan.** Resolve both selectors through current actor/principal visibility before opening either.
   Validate same organization/root/currency, features, grants, mappings, versions, dates and dependencies. Dry-run
   returns both balanced plans and a fingerprint; it persists no IDs or reservations. Execution acquires the existing
   writer/publication gates for the hub and both companies in stable company-ID order, drains pre-existing readers
   and exports, and rechecks authority and all plans. Hold these gates across the decision. Ordinary local CLI uses
   the same root lock; hosted commands use the host's owners. No adapter supplies arbitrary database paths.
2. **Fence durably.** Commit a hub intent containing the operation key/hash, pair and participant IDs, expected
   versions, authorization generation, plan digests, operation kind and decision state. This intent fences both
   companies before any local money write. Persist each company's local preparation: its exact proposed revisions,
   lines, deterministic IDs, local preconditions and attribution. Preparations have no ledger effect. No full remote
   plan is placed in either company or the hub. Each side acknowledges its own digest in the hub.
3. **Decide once.** After both preparations are durable, revalidate current authority, credentials, period locks,
   versions and the exact fingerprint under the gates. Persist `commit_decided` in the hub as the linearization
   point. Permission changes and closing-date changes are serialized against this decision. If validation fails
   before it, persist `abort_decided`, mark preparations aborted without posting, and release only after both sides
   acknowledge. No decision means abort on restart; do not infer success from one preparation. A verified absent local
   preparation counts as no effects to undo, not a reason to manufacture one.
4. **Apply and certify.** For a commit decision, apply each frozen local plan in its own native transaction: revisions,
   balanced posting batches, audit, permanent idempotency receipt and batch effects together. Unique operation/side
   keys make repeat application a no-op. Local headers record their applied revision. The hub records both applied
   digests, then a completion certificate; copy that certificate into both company files, then mark hub `complete`
   and release both publication fences. The certificate contains opaque pair/operation identity, revision, common
   date/currency/amount digest and completion sequence, not remote journal lines, paths or company names.
5. **Recover before admission.** On startup and every company-open path, consult pending intents and local markers
   before allowing ordinary reads or writes. An uncertain commit acknowledgement is resolved by querying the durable
   row/receipt, never by issuing a new operation. Before decision, abort both preparations. After commit decision,
   finish the exact unapplied side and certificates idempotently, even after loss of the HTTP response or process.
   Never decide to compensate just because a timeout occurred. Disk-full, unavailable file, corrupt digest or restore
   mismatch keeps both affected companies fenced and reports `E_INTERCOMPANY_RECOVERY`; other companies continue.

Recovery after `commit_decided` completes already-authorized frozen work as an internal commit-owner operation,
not a new delegated command. It cannot change accounts, amount, date, batch membership or actor. Revocation after
that decision prevents new business actions and receipt disclosure but does not cancel the committed decision.
Before the decision it aborts. Record recovery executor/time separately from the initiating actor/interface/principal/
reason. Recovery reads only protocol metadata and each owner's frozen local plan; it has no generic cross-company
query API. A user-triggered status/recover command still requires both-side authority and never impersonates an admin.

The fence is deliberately broad: all commands using either company's books, reports, streams, exports, scheduled
jobs, accountant copies and backups participate. Drain in-flight publications before deciding; invalidate old cached
results/cursors at release. Denied users receive no intent details. A local reader admitted to A may receive a generic
“A's books need recovery” error without naming B. Refuse direct mutation through old binaries/schema versions that
cannot enforce the fence. This shared admission work is a prerequisite to enabling R123, not later UI polish.

**Copy, backup and restore.** A completed company's file remains self-contained: its own entries, history, local
batch effects and completion certificates travel with it; neither hub nor counterpart is needed to read its books
on another machine. Portable copies have links marked disconnected and cannot mutate a linked pair until reattached
and verified with both-side rights. A preparation without a completion certificate cannot be opened as ordinary
finished books on a new root. Supported backup/export drains the coordinator and includes only complete operations.
Offline raw copies during a write have no portable-consistency guarantee, as with existing WAL files.

Restore/attach must compare company identity, restore incarnation and per-pair certified revision with hub and peer
receipts before re-enabling links. Never replay a missing historical operation into an older restore automatically.
A mismatch freezes linked mutations, exposes local restored history as explicitly disconnected, and requires an
authorized consistent restore/reconciliation plan; no fabricated counterpart or erased audit. In-place restoration
cannot release a pending fence. Detach, move between organizations, delete and identity-changing restore refuse
while intents are pending; completed disconnected history stays local. Relationship paths are resolved from the
registry at execution, never retained as an authority-bearing path in a pair.

## 5. Corrections, voids and reversals

Drafts are company-local proposals with no GL effect and no remote reservations. An actor with both companies'
rights can complete them into pairs. Once posted, all edits go through the pair owner. Native journal/transfer
update, delete, void, restore, register edits and generic bulk edits detect linked ownership and refuse with the
permitted pair command. User-facing deletion is not another route to cancel just one side.

`intercompany update` requires expected pair and both local document versions, a reason and a new preview. It
reverses each current posting and writes replacement balanced entries under one new pair revision and the same
coordination protocol. For a repayment update, cancel the old allocation links and append the exact replacement source allocations
inside the same two-sided revision; their sum must equal the replacement amount. Recheck source versions,
remaining balances excluding this repayment's old allocation, source dates and direction before deciding. Amount,
date, source selection or direction cannot change independently of those links.
Original-date reversals require that date open in both companies; a date change additionally
requires the replacement date open in both. Neither side can silently choose a different date. Captured source
mappings remain unless an explicit new valid mapping is selected and previewed. All earlier revisions remain readable.

`intercompany void` appends exact cancelling batches on the current pair's accounting date in both companies and
marks both documents voided. It requires that date open in both and no blocking downstream dependency. It does not
remove the numbers or history and it does not undo an actual bank movement. A voided pair cannot be edited/restored
through a journal command; correcting an erroneous void uses a new linked entry referencing the voided pair.

`intercompany reverse` takes a common explicit date open in both companies, on/after the original date, expected
versions and reason. It creates a linked reversal pair with exact opposite lines and a unique `reversal_of` link;
original-period books remain unchanged. Original state becomes reversed, with separate original and reversal dates.
No second active reversal of the same revision is allowed. A replacement after reversal is a separately previewed
pair, explicitly linked as replacement; it must not be smuggled into a reversal receipt. An accidental reversal is
corrected with a new entry, retaining the chain, not deleting its link.

Repayment allocations, completed bank reconciliation, bank-feed links and later linked replacements are dependencies.
A source correction/void/reversal refuses while dependent repayments remain; reverse those first through their own
paired commands. A repayment inverse cancels its exact allocation links and reopens the source amounts atomically
with its two journal sides. Preview must disclose that reversing a repayment changes Bookflow's recorded obligation,
not the bank's real payment. Reconciled bank lines require the native explicit reconciliation remedy first; no silent
unreconcile. Closed-date original void refuses even if the other company is open; offer an explicitly dated reversal,
never reopen a period or choose a date without the caller. Notes and audit remain with original identities.

## 6. Storage and invariants

All proposed IDs are stable ULIDs; local foreign keys use RESTRICT. Amount sums use native checked integer arithmetic.
No SQL join spans company databases. Host-level orchestration invokes two local owners with admitted contexts.

| Location / record | Facts and constraints |
|---|---|
| Hub relationship registry | Organization, two company IDs, opaque relationship ID, state/version, local mapping revision digests; unique unordered company pair; no local account names or balances |
| Hub operation intents | Unique root/actor/principal/operation key plus canonical input hash; kind, pair/revision, participants, plan digests, authority generation, durable decision, acknowledgements, completion sequence; append-only decision events |
| Company relationship mappings/revisions | Local relationship ID, opaque peer reference, local due-from/due-to IDs, version, enabled state; local ownership constraints and captured mapping history |
| Company intercompany documents/revisions | Pair ID, local document ID, local role/direction, relationship revision, date, currency, amount, status/version, local offsets/dimensions, correction/reversal/replacement links |
| Company preparations/receipts | Operation ID, local plan and hash, expected versions, originating audit envelope, prepared/applied/aborted state, local posting IDs and completion certificate; unique operation/side |
| Existing ledger and audit | Immutable document revisions, posting batches/lines/source links and attributed events, written with local receipt and batch effect |
| Company obligation allocations | Local source pair/revision, repayment pair/revision, integer amount, inverse-of, effective date; unique producer identity; outstanding cannot fall below zero |
| Existing batch tables plus local group manifest | Root batch ID, local effects and exact before-images, opaque coordinated group ID, inverse receipts; no peer effects stored locally |

The coordinator owns a monotonically increasing pair revision. Concurrent updates, void versus repayment, relationship
changes and multiple repayments validate all affected local versions before deciding. A prepared operation reserves
its touched records via the durable fence; ordinary writers cannot pass it. Exactly one ledger application per local
operation is enforced in storage, not merely by retrying the same command string. Conflicting idempotency payloads
refuse; successful replay first rechecks current read/execute rights, then returns the original permitted receipt.
A dry-run is not a durable approval and does not lock later execution.

Index relationship/date/pair ID, pair/revision, pending intent participants, operation key, source allocation and batch
group. Lists default to 50, maximum 200, stable date/ID ordering; counts cover the authorized filtered selection.
Cursors bind company, filters, authority and coordination watermark. Never use a stale comparison to claim balances
tie. Migration starts with switches off, no relationships and unchanged existing journals; recovery support and
schema compatibility must be deployed before settings permit activation.

## 7. Commands, authority and agent use

Proposed typed commands in the shared registry; CLI, MCP, HTTP and browser use identical validation and errors.
Every write accepts an operation key, reason/context, and expected versions for existing records. Output carries
local IDs/versions, pair state, audit/batch IDs and permitted next commands. Dry-run includes exact balanced entries,
source allocations, dates, dependencies and fingerprint without mutation.

| Commands | Inputs → results |
|---|---|
| `intercompany-relationship create/update/disable/show/list` | Two admitted companies, local mappings/versions → relationship and setup blockers |
| `intercompany-draft create/update/show/list` | Local proposed amount/date/offsets, draft/version → local draft; no peer lookup |
| `intercompany post/update/void/reverse` | Two companies or accessible pair, plans or pair versions, date/reason → coordinated receipt |
| `intercompany repay` | Relationship, direction, bank accounts, exact source allocations/versions → repayment pair and remaining obligations |
| `intercompany show/query/history` | Local company/pair, optional authorized both-side view, filters/cursor → local or two-sided history |
| `intercompany status/recover` | Pair/operation → permitted state or exact protocol recovery result |
| `report intercompany` | Local company, cutoff, relation, optional compare-both → GL/detail reconciliation and authorized counterpart comparison |
| `intercompany-template create/update/show/list/retire` | Local defaults and versions → versioned template; no standing posting permission |
| Existing import/bulk-edit and `batch reverse` | Frozen rows/versions or batch ID → coordinated effects, inverse preview or blockers |

Capabilities: `intercompany.read` (member default) reads local linked records with `ledger.read`; `intercompany.post`
(standard) posts/repays with `ledger.post` on **both** sides; `intercompany.correct` (standard) updates/voids/reverses
with native ledger authority on both; `intercompany.manage` (admin) changes relationships/mappings with account read
and settings rights; `intercompany.bulk` (standard) adds bulk use to the underlying grants. Draft/template writes
require local `intercompany.post` and applicable local reference reads, but confer no remote write. Recover requires
both-side correction rights for its user-facing door; automated frozen completion is governed by §4. Add a new
catalog layer at the then-current tip; do not rewrite accepted layers or automatically grant agents new capabilities.

One effective actor/principal binding must be admitted in both company scopes for a pair-changing command. Denies
bind; read in B does not imply write in B. No split-principal impersonation, browser exception, agent exception,
relationship-owner privilege or cached setup approval. Revalidate at execution and response publication. Explicit
ordinary instructions do not require repeated prompts; broad changes and historical corrections follow R187's
`confirm_first` preview contract. A reason is audit context, never a substitute for authority.

**Rights in only A.** The actor can read A's own ledger and permitted local linked facts, create a local untargeted
draft and pass its ID to a colleague through their existing workflow. They cannot list/select B, create a relationship,
post A provisionally, request B's automatic approval, or change either half of an existing pair. A both-authorized
colleague reviews the draft, selects both companies/accounts and posts under their own attributed authority, retaining
the draft origin. There is no distributed approval queue or remote notification in the simple route. Known inaccessible
company IDs are handled identically to unknown IDs before opening any peer file.

Local projections expose the local document and its accounting, plus an opaque pair reference. They omit peer company
ID/name, journal ID, accounts, memo, members, versions, recovery details and totals unless separately authorized.
Relationship pickers filter before selecting rows/counts; generic journal history, audit, activity, batch previews,
search, exports and errors use the same projection. Do not copy peer names into generated local descriptions; user-
authored local prose remains local evidence, not a way to fetch additional peer data. A denied cross-company request
returns no diagnostic distinguishing missing company, missing membership or valid relationship. Both-side reads check
native source permissions before fetching either source; logs never dump the combined input into a local audit row.

Reuse permission, not-found, validation, version-conflict, closed-period and feature-disabled errors. Add
`E_INTERCOMPANY_LINKED` (use the pair owner), `E_INTERCOMPANY_RECOVERY` (retry/status after repair),
`E_INTERCOMPANY_DISCONNECTED` (verified reattach required), `E_INTERCOMPANY_DEPENDENCY` and
`E_INTERCOMPANY_CURRENCY`. Details contain only authorized IDs and actionable next commands. Help indexes
“intercompany”, “due to”, “due from”, “paid for another company”, “advance” and “repayment”, with an exact example
of the cents fixture. A fresh agent must be able to discover limitations, preview and recover a lost response using
actual MCP help, without reading source or automating browser clicks.

## 8. Reversible batches

Every import and multi-record change has one existing R186 batch entry point and one coordinated group identity.
Each participant stores only its own batch effects; the hub stores group membership and progress, not financial
payloads. Starting `batch reverse` from either permitted local batch resolves the same group. The adapter preflights
the entire group, requires current authority for every company/inverse, freezes exact effects/versions and executes
newest dependent effects first. Pair inverses always use §4; ordinary nested single-company commands cannot reverse
one side. Disable skip-changed for this adapter: no “whole reversal” with omitted pairs or skipped sides.

Import preview fixes row source keys, create/update choice, relationship/mapping revisions, both company selectors,
amounts/dates and versions. One row is one pair. Stable company/source/row keys plus payload hashes prevent duplicate
retries. No name-based silent upsert. Record `batches.record_effect` within each local application, including landed
rows of a partial import. A failed row leaves prior complete pairs in the same visible unfinished batch; never an
unrecorded tail or half a pair. Existing import size and approval limits still apply.

Inverse of creation is paired cancellation; inverse of an edit restores its exact prior captured facts through a
new coordinated revision; repayment inverse restores the precise allocations. Original-date inverses require both
periods open. An explicitly previewed current-date financial reversal may be selected where appropriate, with its
changed period effect stated; never substitute it silently for historical restoration. Batch correction of settings
restores before-images only when no later entry depends on that revision. Later edits, repayments, reconciliations,
linked replacements, disconnected files or missing authority block the complete preflight with zero new inverses.

R186 slices/resumes large reversals: each pair is indivisible, the whole batch may be visibly `reversing` or `blocked`
after some pairs complete. Do not promise physical all-or-nothing rollback across a large batch. Persist progress,
block overlapping work on affected records, recheck authority/dependencies per slice and resume using the same ID.
Revocation before a pair's decision stops that pair; revocation after decision still finishes that frozen pair,
then stops the next. Original facts and audit survive. This is the only bulk undo door.

## 9. Screens and acceptance

At **1440 px**, Intercompany opens a relationship list with local balances, status and New linked entry. Two-sided
amounts appear only with both-side reads. The entry form labels companies prominently, uses a simple amount/date
route, and expands local accounting detail. Preview shows two separately balanced entries side by side. History
connects original, correction, repayment, void and reversal without blending their dates. A recovery banner says
what is unavailable and offers permitted status/retry actions; it never offers “post the missing half”.

At **390 px**, companies are labelled stacked sections, with sticky Preview/Post, 44 px targets and focus restoration
from account pickers. Both entries and totals must be reviewed without sideways scrolling. Drafts and history use
full pages; Back preserves filters and selections. Optional templates/bulk tools stay absent until enabled. An A-only
reader sees A's detail and “Counterpart not checked”; no disabled button tooltip or HTML payload reveals B.

Acceptance criteria:

1. Simple advance and expense allocation work with both deeper layers off; enabled templates merely save entry work.
2. The cents fixture independently ties both GLs, gross directions and outstanding sources; partial repayment/retry
   neither duplicates cash nor overallocates. A fee cannot disappear into a balancing line.
3. Crash at every numbered phase in §4, including lost commit acknowledgement and certificate copy, yields either
   both posted or neither, or fenced recovery until completion; every report/export/backup path respects the fence.
4. Correct, void, current-period reverse and repayment inverse retain immutable history. One closed company blocks
   original-date cancellation; a reconciled bank dependency is never silently removed.
5. A-only, B-read-only and revoked-principal actors cannot post or reveal B through selectors, errors, caches, audit,
   batches, direct endpoints or receipts. Both-authorized people and agents continue each other's work by stable IDs.
6. Batch imports/changes reverse through the shared door, or name permitted blockers; interrupted slices resume with
   exactly one inverse per pair and no claim of whole-batch rollback before completion.
7. Completed standalone company backups preserve local books/history; restored/disconnected pairs cannot silently
   resume against a different peer revision. Old files cannot bypass activation/recovery requirements.
8. Keyboard/touch journeys pass at both widths; large histories paginate with complete authorized counts and
   permission-bound cursors. The agent can discover and perform the workflow on its first attempt through MCP help.

## 10. Settled and K-questions

**Settled.** Same-organization, same-root coordination; two companies per pair; explicit relationship and dedicated
control accounts; equal home currency; no automatic netting; simple entry without templates/imports; local untargeted
drafts for one-company actors; no remote approval authority; durable decision plus admission fences; exact mechanical
recovery after commit decision; immutable corrections; explicit dates; disconnected portable history; strict paired
batch inverses. These are implementation choices with prospective evolution, not approval requests.

Only future scope/money decisions remain. Defaults permit the increments below; they do not authorize outside acts:

- **K1 — Transaction scope.** Include intercompany invoicing, tax, inventory/asset transfers, consolidation eliminations,
  different home currencies or separate hosts? **Default:** no; same-currency advances, expense allocations, eligible
  journal offsets and repayment recording in two companies on one host. Any extension needs its accounting and
  authority design before enabling it; R123 does not initiate external bank payments.
- **K2 — Existing balances.** Must existing manual due-to/due-from balances be adopted into linked obligations?
  **Default:** new zero-balance dedicated accounts only; leave old manual balances in their original accounts and
  label them outside linked reconciliation. Adoption requires a principal-approved opening reconciliation and exact
  migration totals, with no automatic plugs or duplication of existing postings.

## 11. Build increments

S is a narrow slice, M a bounded workflow, L a cross-cutting accounting change. Each includes demo fixtures, typed
help, generated references and migration/permission checks. “Tests” below describes future build acceptance; this
design-only change adds no product code or tests.

1. **Relationship setup and local drafts (M).** Feature switches, local mappings, hub registry metadata, projections
   and untargeted drafts. **Done when:** a both-authorized actor configures a valid pair and an A-only actor can save
   local work without discovering B; no money is posted. **Tests:** zero-balance/account ownership rules, organization/
   currency mismatch, duplicate relationships, disabled layers, actor/principal denies, local-only audit and migration.
2. **Coordinated posting and recovery (L).** Two admitted sessions, durable protocol, all-path publication fences,
   advance/allocation posting and portable certificates. **Done when:** a pair is both applied or neither and every
   interrupted phase recovers or fences safely before books are served. **Tests:** independent balancing, process
   death at each boundary, uncertain commit acknowledgement, disk-full/missing file, concurrent period close/revocation,
   local/hosted readers, streams/exports/backups, receipt retries, standalone copies and incompatible old openers.
3. **Corrections, voids and dated reversals (M).** Pair revisions, native mutation guards, inverse ownership and
   dependency checks. **Done when:** original facts remain and every lifecycle operation changes both sides together.
   **Tests:** stale versions, concurrent correction/void, one closed period, changed date, direct journal/delete bypass,
   reconciliation blockers, repeated reversal and recovery during each inverse.
4. **Repayments and balance reconciliation (M).** Exact allocations, gross/net tie reports, common watermarks and
   local-only projections. **Done when:** the 10,000/2,500/4,000-cent fixture leaves 8,500 cents on both sides with
   correct as-of history. **Tests:** partial/excess/competing allocations, reverse repayment then source, opposite-direction
   advances, mismatch detection without plugs, cutoff boundaries, large paging, revoked read rights and cached results.
5. **Templates, imports and whole-unit batch reversal (L).** Optional local defaults, frozen bulk selections, group
   adapter and sliced recovery. **Done when:** mistaken imports and bulk corrections reverse through `batch reverse`
   with every paired effect recorded. **Tests:** source-key/hash conflicts, failed row resume, changed mapping,
   whole-group preflight, skip-changed refusal, closed-date inverse choices, per-slice revocation and crash/resume.
6. **Browser completion and portability trial (M).** Two-width forms/history, recovery/disconnected states and all
   shared-interface journeys. **Done when:** every acceptance criterion passes and people/agents continue the same
   records without hidden permissions. **Tests:** 390/1440 px keyboard/touch, A-only redaction in HTML/JSON/export,
   move/restore/incarnation mismatches, local backup round trip and the closing trial below.

**Closing trial.** A fresh agent using only actual MCP help prepares an advance; a person previews and posts in the
browser. Alternate actors to allocate expense and record partial repayment, and reconcile the fixture independently
from posting lines. Crash between side applications, retry with the original key, and confirm both books stay fenced
until recovery finishes. Attempt correction from an A-only login, then with a revoked principal. Reverse repayment,
close one company's original period, and inspect the void refusal and explicit current-date reversal preview. Import
several pairs, reverse the batch, repeat with a downstream dependency and recover an interrupted slice. Open a portable
completed copy and demonstrate disconnected mutation refusal. Record first-attempt results and help friction, fix
material findings and rerun failed scenes before closing the build.
