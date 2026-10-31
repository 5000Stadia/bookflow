# Purchase and bill approvals (R122)

Design contract, 2026-10-31; approved for design by Kabe on that date. Proposed behavior, not a claim that these
commands exist. R122: “Approvals: purchase and bill approval steps before posting.” This document owns those steps;
[principal-directives.md](principal-directives.md) owns authority and [reversible-batches.md](reversible-batches.md)
owns batch reversal. Payroll is a separate module, not an implicit third approval target.

## 1. Outcome, anchor and existing core

A buyer prepares a purchase, a reviewer sees the exact revision and approves or returns it, and a permitted
bookkeeper releases that purchase or posts that bill. Approval is business evidence, not a grant of posting rights.
Correctness is Bookflow's own totals, source quantities and ledger lines being right, checked by hand.

The anchor enterprise 24 guide, p. 66, describes configurable bill/PO approval flows, templates, a dashboard,
reminders and an audit trail. It does not establish exact locking, threshold or bypass semantics. The 2016 manual,
printed pp. 182–186, describes bill header fields, expense/item splits and purchase-order selection, but does not
establish bill approval. Bookflow retains those purchase forms and adds explicit pre-post drafts, revision-bound
steps and one reversal door. It does not copy an undocumented permission bypass.

Today `company/purchase_orders.py` owns non-posting order revisions, source consumption and receipt/bill dependency
refusals. `purchase_order_billing.py` owns partial consumption. `bills.py` prepares and persists AP, item/expense
lines, stock and receipt links; its ordinary post is already an accounting act. `checks.py` owns money-out
instruments and printing guards. No pending bill may be represented as an ordinary posted bill with a hidden flag.
The existing `employee` list is an employment identity, not an approver identity: approvers are company members.

## 2. Layers and the simple route

All switches migrate off. Purchasing and bills continue to work alone exactly as today.

| Company setting | Off by default | When enabled |
|---|---|---|
| `purchase_approvals_enabled` | Yes | New orders and proposed order corrections require release evidence |
| `bill_approvals_enabled` | Yes | New bills and proposed bill corrections require approval evidence |
| `approval_routing_enabled` | Yes | Optional amount bands and sequential named steps; requires at least one target enabled |
| `approval_reminders_enabled` | Yes | Optional local to-dos by due date; no email or automatic approval |

The first two switches are independent. Each simple enabled target has one “Review” step with an explicit nonempty
list of eligible member IDs; one eligible approval suffices. No amounts, rules, departments, reminders or second
reviewer must be configured. The principal may put themselves in that list and allow self-approval for a one-person
business; self-approval defaults false. Setup refuses an enabled policy with no eligible approver and explains how
to assign one. Configuration does not grant a capability to the selected member.

Turning a target off affects future submissions only. Pending/approved drafts retain their captured policy and
must finish or be cancelled; off is not a way to bypass them. Turning routing off similarly leaves captured routes
intact. Reads, cancellation, withdrawal and reversal remain available while a layer is off. Turning approvals on
leaves existing released orders and posted bills alone; no retrospective “approved by owner” events are invented.
Later corrections to those documents require the currently enabled target's approval. Historical reads label them
“Created before approvals were enabled.” Settings changes require company admin and `approval.manage` together.

## 3. Document lifecycle

A `purchase-draft` has kind `purchase_order|bill`, intended operation `create|correct|restore`, stable ID, version, exact
commercial payload and optional original document/version. It is useful even with approvals off: Save draft and
preview work, and Commit posts/releases directly if no approval is required. It has no GL, AP aging, open-order,
stock, receiving, purchase consumption or billable-cost effect. Drafts appear in a separate list, with attachments.

| State | Allowed next act | Effect |
|---|---|---|
| `draft` | edit, submit, cancel; commit when policy off | Submit freezes a revision and selects route |
| `pending` | approve current step, reject, withdraw | Approval advances one step; final approval sets `approved` |
| `rejected` | revise, cancel | Reason required; revision returns to `draft`, old decisions remain |
| `approved` | commit, withdraw | Commit invokes native domain post/update and becomes `committed` |
| `committed` | inspect linked native document | Native correction/void/delete rules govern it |
| `cancelled` | inspect or copy into a new draft | No resurrection of approval evidence |

Withdrawal from pending/approved appends an invalidation and returns to draft; no deletion of votes. Editing any
submitted commercial field, posting date, source revision, allocation, vendor, account, amount, item, quantity,
class, tax/discount input or supporting attachment selection requires withdrawal and resubmission. Comments may
append without invalidation; attachment bytes are immutable. Even a smaller amount needs a fresh decision. Approval
binds the complete normalized domain payload and captured calculated preview, not just total or a client hash.

Approve never posts, pays, sends or prints. Commit requires current native posting authority and performs the native
validation again inside the write transaction. If the preview would change, refuse with changed fields and require
resubmission. A referenced account becoming inactive, source being consumed, closing date moving, or required
approver losing authority can prevent the next act. Approval does not waive native validation. Once granted, a
completed decision remains evidence if its author later loses a grant; future decisions require current authority.
A principal can withdraw a suspect uncommitted approval explicitly. Replacing the policy is never retroactive.

**Orders.** Committing an approved order releases the non-posting PO into the existing open-order workflow; this
uses the native `purchase-order post` internals, not a new ledger entry. Drafts cannot be sent as orders, received
against, counted as on-order or converted to bills. A draft PDF is visibly “Draft — not released”. Released orders
can use ordinary delivery only with delivery authority. A correction draft leaves the old released revision valid
until replacement commits; receipts/bills created meanwhile block replacement through native dependency checks.

**Bills.** Commit creates AP/stock exactly once. Source order/receipt selections are captured as explicit line IDs,
revisions, quantities and amounts on submission; “all remaining” is expanded then. Drafts reserve no source quantity.
Two approved bills competing for the same remainder cannot both consume it. PO approval never approves its bill;
if bill approvals are enabled, each partial bill gets its own route, even for unchanged PO prices. A correction draft
leaves the old bill in AP and aging until native correction atomically replaces its accounting effect. Applied
payments and closed periods retain their existing refusal rules.

**Every door.** Enabled-target `bill post/update` and `purchase-order post/update` require a matching approved draft
ID/version, or return `E_APPROVAL_REQUIRED` with the draft command. They never silently make a draft instead of a
posted result. Imports, memorized runs, receipt conversions, commission affiliate transfers, register shortcuts and
Python calls use that same gate. Callers may explicitly choose draft creation; parent runs record those drafts and
remain awaiting approval until commit. Commission transfers must retain their source claim and transfer batch
link until the approved bill actually posts; cancelling the draft releases the transfer claim, not its accrual.
No generic journal command may mutate an existing purchase-owned transaction. A manual unrelated journal is outside
R122's purchasing scope and does not create approved purchase evidence. Approval is not a total spending-control
system: checks, card purchases and payments retain their own capabilities and are not secretly added to R122.

Void/delete/restore remain separate native lifecycle acts under [transaction-deletion.md](transaction-deletion.md).
Void and delete do not need a purchase approval; they need their own capability and dependency checks. Restoration
of a posted bill while approvals are enabled requires an approved `restore` proposal bound to the preserved revision
and current deleted-state version, then delegates to the native restore. It cannot use create to duplicate a bill.
No purchase draft deletion capability is invented: cancellation preserves its record. Released POs retain native
void semantics, not a new delete command.

## 4. Optional routing, assignments and audit

One active immutable policy revision per target. Routing uses contiguous half-open amount bands `[min,max)`, starting
at zero with an unbounded final band, in company currency. Basis is the native final amount payable including
captured discounts, taxes and freight, never line gross. Refuse foreign-currency routing until that core supports it;
no guessed exchange rate. Zero-value purchases still enter the zero band. Negative bills must use the native credit
workflow, not a negative threshold loophole.

Each band selects an ordered, nonempty list of steps. Each step has label, eligible member IDs, optional relative
business due date entered as an explicit date at submission, and `allow_self` (false). One eligible decision per step;
no quorum, arbitrary code, auto-approve or escalation. Policy validation rejects gaps/overlap, empty sets and inactive
members. There is no rule priority ambiguity. A later layer could add other predicates with a new schema version.

For self-review, requester means the draft creator and submitter, plus each actor's bound human principal. The same
principal cannot use a second agent to review its own submission when self-approval is false. Multiple steps may use
the same reviewer unless a step specifies `different_from_previous=true`; then compare effective principal identity,
not token/interface. This optional field defaults false. Eligibility is assignment plus current `approval.decide`
and read access to this target; a broad role alone cannot impersonate a selected approver.

If the last eligible reviewer is deactivated, retain the pending step and show “Needs reviewer”. `approval reassign`
requires manage, expected version and reason; it replaces eligible members of the current/unreached steps with a
recorded assignment event. It does not add a vote, change amounts or erase previous decisions. Withdrawing and
resubmitting is required to change step count, thresholds or self-review policy. Nobody has a hidden owner bypass.

The audit timeline contains submitter/actor/principal/interface/reason, policy and draft revisions, step, decision,
comment, reassignment, invalidations, committed document and reversal links. Notifications are local to-dos with one
stable key per request/step/member, dismissed as the step ends. Deadlines flag overdue work; they never approve it.

## 5. Storage and transaction boundaries

Company-local ULIDs and RESTRICT foreign keys; mutable headers are versioned; revisions/events are insert-only.
Hub member references are validated within organization/company without exposing other memberships.

| Table | Facts and constraints |
|---|---|
| `purchase_drafts`, `purchase_draft_revisions` | kind/operation, source document/version, version/state, normalized native payload, calculation snapshot, currency, attachment IDs, creator; immutable submitted revision |
| `approval_policies`, `approval_policy_revisions` | target, version/state, bands, step definitions, self-review flags; published revisions immutable |
| `approval_requests` | unique draft revision, captured policy revision/steps, submission actor/principal, state/version, current step |
| `approval_events` | request, sequence, step, action, actor/principal/interface/reason, timestamp, assignment before/after; unique operation identity; decisions never overwritten |
| `approval_commits` | request and draft revision, commit-attempt sequence, unique native producing operation, document/revision, source versions and batch IDs; append-only inverse links, at most one unreversed commit per request |

The writer transaction checks version, policy requirement and source versions, appends the event, advances the
header, records audit/idempotency receipt and R186 effect together. Commit additionally performs all native posting,
consumption and link writes there. A crash cannot leave a posted bill whose approval says uncommitted. Permanent
operation keys scope to company/command/actor; a retry checks current authorization, replays the original result on
identical input, and refuses different input. A pending-version check serializes approve/reject/withdraw races.
Indexes support target/state/due date, reviewer inbox, original document and draft/revision history.

Migration creates no approvals for old documents. Demo data includes layer-off ordinary purchase, rejected/revised
bill, one-step owner policy, two-step routed PO, partial conversion and an unauthorized clerk. Copying a company file
retains snapshots and history; unresolved member assignments on a new hub refuse decisions until explicitly reassigned.

## 6. Commands, capabilities and agents

All commands use the shared typed registry across Python, CLI, HTTP, MCP and browser. Writes require operation key,
reason/context, and expected versions for existing objects. `--dry-run` returns exact effects, totals, steps, required
capabilities and blockers without reserving, approving or sending. Lists use stable pagination and permission filters.

| Command | Main inputs → output |
|---|---|
| `purchase-draft create/update/show/list/cancel` | kind, operation, native payload/source, version → draft, totals, source versions |
| `purchase-draft submit/withdraw/commit` | draft/version; commit expected request version → request/steps or native document and batch |
| `approval-policy create/update/publish/show/list` | target, bands/steps, version → validated revision |
| `approval inbox/show/history` | target, assigned-to-me, state, due range, cursor → visible requests/events |
| `approval decide` | request/version, step ID, approve/reject, comment → decision/current step/state |
| `approval reassign` | request/version, step IDs/member IDs, reason → assignment event/version |
| `approval batch-decide` | explicit request/version/step tuples, one decision/comment → all results and batch ID |
| `batch show/query/reverse` | batch ID, dry-run → whole-unit plan, blockers or reversal progress |

Errors: `E_APPROVAL_REQUIRED` names the route into drafts; `E_APPROVAL_STALE` lists changed versions/fields;
`E_APPROVAL_STATE` gives the legal next actions; `E_APPROVAL_ASSIGNEE` names the required step without revealing
invisible people; `E_APPROVAL_SELF` explains principal identity; `E_APPROVAL_POLICY` gives gap/overlap/empty fields.
Reuse `E_PERMISSION`, feature-disabled, closed-period, version-conflict and domain dependency errors. No error gives
an inaccessible company or document name. Role labels below describe proposed defaults for new capabilities.

| Capability | Human default | Extent |
|---|---|---|
| `approval.read` | standard | Requests/history; also needs underlying target read |
| `approval.prepare` | standard | Draft/submit/withdraw/cancel; target read plus native write right required at commit only |
| `approval.decide` | admin | Decide when assigned, with target read; no posting right implied |
| `approval.manage` | admin | Policies/reassignment; settings additionally require company admin |

Agents get none of these until the principal grants them; effective rights intersect principal rights. Draft edits
require prepare plus target read; one actor with prepare may continue another's draft, attributed and version-checked.
Presets are editable grant bundles: Purchase preparer = prepare/read and target reads; Purchase reviewer adds decide;
Purchase bookkeeper adds native posting. Neither reviewer nor preparer automatically gains payment or payroll access.
Target-specific underlying read grants limit bill versus PO visibility. Publishing policy, disabling a layer,
reassigning, withdrawing approved work and reversal carry `confirm_first`; ordinary named approve/post needs no
second consent ritual. Broad permanent policy changes get one exact preview check under R187, not a size threshold.

Help aliases include “approve bill”, “purchase approval”, “waiting for me” and “why can't I post”. An agent previews,
creates a draft on an approval-required refusal, returns its shared ID, and never claims submission means posting.
A human can approve the same ID in the browser and the agent can commit with its own current native authority.
Reasons and directives are evidence of intent, never an alternative permission system.

## 7. One reversible batch door

`approval batch-decide` preflights the whole explicit selection and commits all decisions atomically as one R186
batch. A stale or ineligible row changes nothing. Reversal appends decision-withdrawal events and restores each prior
pending step, preserving old decisions. A later decision, edit, reassignment, commit or external artifact is a
blocker; reverse later batches first. It never simply erases the earlier approval.

Bulk submit/cancel/reassign, policy assignments and multi-draft commit runs also register effects inside each write.
Long commit runs may slice: their landed prefix is one visible batch, with resume cursor and per-child idempotency;
posting is not falsely advertised as all-or-nothing for such a run. The batch planner preflights the entire landed
unit before inverses, then uses R186 resumable slices and fences new dependent writes during reversal. Commit inverses
are operation-specific: create delegates to native void; correct appends a native compensating revision restoring
the exact captured prior commercial/ledger/source-consumption state; restore delegates to native delete to return
to the prior deleted state, requiring the reverser's separate deletion grant. A correction inverse never voids the
whole document. If native restoration of prior effects is unavailable or blocked, the entire batch refuses rather
than approximating it. Inverse restoration uses the captured prior approval evidence through a private typed inverse
adapter, not a caller-supplied approval bypass flag. It still enforces native versions, dates and dependencies.
Only after the inverse completes does the draft regain commit eligibility. A correction/restore draft's original
version reference advances to the exact inverse-produced version, recorded in an append-only rebase event; any
other changed fact requires resubmission. It does not recreate an external order or cash movement. Recommit uses a
new operation/commit-attempt and retains links to earlier commits and inverses. Later payments, receiving, source use, closed periods and sent/printed/
exported artifacts block whole-unit reversal. Local on-screen preview is not an export. A failed reversal names all
blockers before changing anything. Reversers need their own rights for every inverse, including native void rights.

## 8. Screens and acceptance criteria

At **390 px**, Purchases has Drafts and Waiting for me tabs only when useful; enabled-target forms show Save draft
and Submit for approval. Cards show vendor, amount, date, requester, status and current reviewer. Opening one gives
Summary, Lines, Evidence and History sections; a sticky Approve/Return bar leaves the total visible, with 44 px touch
targets. Return requires a reason; submitting and posting are visibly different actions. Changed-version errors
retain typed comments and offer refresh, never silently overwrite. Bulk select has a count and review screen listing
every amount; no horizontally clipped finance grid. Route setup is one step per card.

At **1440 px**, the inbox is a filterable table with a side preview of lines, source order/receipt, ledger preview and
step history. Draft differences highlight amounts and non-money changes. Totals stay visible alongside actions.
Route setup shows amount bands with steps beneath each. Both widths support keyboard-only operation, named controls,
focus return, accessible status text and no color-only decision cues. Authorized historical cleanup stays reachable
with layers off. An empty inbox says whether nothing is assigned or read permission is missing.

Acceptance fixtures (USD, exact minor units):

1. **Simple route.** All switches off: direct bill $80 posts Dr Supplies $80 / Cr AP $80. Turn only bill approvals on:
   the same proposed purchase creates no journal/AP/stock until commit; approve alone still creates none. Commit
   posts exactly $80/$80. Paying $30 by native bill payment posts Dr AP $30 / Cr Bank $30, leaving AP $50. Approval
   reversal now refuses with the committed bill; commit reversal refuses with payment. Neither refusal changes books.
2. **Threshold.** A native preview with lines $80+$40, captured purchase tax $7 and freight $3 totals $130. Bands
   `[0,130)` one step and `[130,infinity)` two steps select two at $130, one at $129.99. If represented as expense
   lines, commit is Dr Supplies $120, Dr Purchase Tax Expense $7, Dr Freight $3 / Cr AP $130. No new sales-tax engine
   is implied. For sales-derived commission transfer input, R189's discounted fixture remains $127 with $7 tax;
   approval does not recalculate it as $128 or include tax in commission. Affiliate bill $8 clears commission
   liability Dr Commissions Payable $8 / Cr AP $8 only after approval; no duplicate expense or early transfer link.
3. **Source race.** Released PO 10 units at $12 = $120, no GL. Approved bills for 6 and 5 units race. The 6-unit commit
   posts Dr Inventory $72 / Cr AP $72, leaves 4 on order; the 5-unit commit refuses, preserving $72 and 4. Revise to
   4, resubmit and approve: Dr Inventory $48 / Cr AP $48; stock 10 and AP $120. Existing receipt-billing routes must
   instead use their native receipt clearing entries, with no second stock increase.
4. **Revision evidence.** Changing vendor, date, account, class, one cent, source or evidence invalidates approval.
   Rejected draft resubmission retains old reasons. Concurrent approve/reject with one version yields one winner.
5. **Authority and layers.** Direct API/import/memorized/commission routes cannot bypass; same-principal agents
   cannot self-approve; revocation blocks new decisions/retry access. Layer-off legacy forms remain unchanged.
6. **Batches.** Approve three independent drafts, reverse once: all three pending, no GL, full audit. Add a next-step
   decision or committed child: whole reversal refuses. Resume interrupted commit/reversal without duplicate bills.
7. **Surfaces.** CLI-to-browser-to-actual-MCP continuation, both screen widths, no hidden draft AP aging, source
   drill-down permission checks and no cross-company existence leaks. Tests assert hand amounts, not output parity
   alone. Native closed-period, deletion and restoration gates remain effective.

## 9. Decisions settled and open questions

Settled: off by default per target; drafts are separate non-posting records; approval binds the complete revision;
approval and commit are separate; no silent bypass even for an owner; one decision per sequential step; principal
identity for self-review; no source reservations by drafts; threshold is final payable amount; native correction and
restoration require evidence; local reminders only; whole-batch reversal uses R186. These are reversible design
choices, not requests for Kabe's permission.

- **K1 — Broader purchasing control scope?** Should approval extend to checks/card charges or payment release?
  **Default:** no; R122 stops at PO release and bill posting. Existing payment capabilities apply. A future expansion
  must define those transactions explicitly; these increments do not represent approval as a bank-payment mandate.

## Appendix: references read and adoption

Read 2026-10-31, public documentation/source only; nothing executable fetched. Names and branded URLs are omitted to
honor the repository naming rule. Public reference A and B mean, respectively, the first and second public systems
named in the task. Version, repository-relative path or official documentation slug identifies the source.

| Source | Observed mechanism | Adopt / reject and reason |
|---|---|---|
| Anchor enterprise 24 guide PDF p. 66 | Bill/PO workflows, templates, reminders, dashboard, audit | Adopt discoverable queue and captured history; do not infer exact permission semantics from marketing text. |
| Anchor 2016 manual PDF 213–217 / printed 182–186, Entering Bills; PDF 559–562 / printed 528–531, Creating Purchase Orders | Header, expense/item splits, non-posting orders and order conversion | Preserve domain entry and consumption, add drafts before accounting. No approval claim from these pages. |
| A official docs, `workflows`, “How to Create”, “Conditions”, quotation example | States/transitions, role decisions, conditional multiple steps; workflow overrides regular submit/cancel | Adopt explicit steps and assigned decisions. Reject arbitrary expressions and replacing native lifecycle: neither should bypass ledger/deletion protections. |
| B official public source, branch `19.0`, `addons/purchase/models/purchase_order.py`, `button_confirm`, `button_approve`, `_approval_allowed` | Confirmation can enter `to approve`; approval eligibility governs release | Adopt release as a separate state. Reject implicit manager bypass and applying purchase approval to a bill automatically: neither is this contract. |

Read-only safety review: these mechanisms inform design only. No source copied, dependencies installed, external
callbacks, executable rules or network approval links are introduced. Thresholds and state arithmetic above are
Bookflow decisions; prior art is not proof that the output is correct.

## 10. Build increments

S = narrow useful slice, M = bounded workflow, L = cross-cutting domain integration. Each increment includes demo
records, typed help/schema references, migration preservation and its own appropriate checks. No product code is
part of this design commit.

1. **Saved purchase drafts (S).** Create/edit/preview/cancel/show/list for expense-only bill drafts, with browser and
   shared command contract; layers remain off. **Done when:** a bookkeeper saves a bill and another actor resumes it
   with no AP effect. **Tests:** exact $80 preview, stale edit, cancelled history, permissions, migration and 390 px.
2. **One-step bill approval and posting (M).** Simple policy, submit/decide/withdraw/commit, capabilities and all native
   bill post/update gates; source-aware bill drafts. **Done when:** fixture 1 and direct/import/automated bypass
   refusals pass, and cancelled drafts have no financial residue. **Tests:** revision races, source revalidation,
   closed periods, retry, approved correction and restoration, R186 effects for commit from its first release.
3. **Purchase release and conversions (M).** PO drafts, simple purchase policy, release/correction and receiving,
   partial-bill and commission-draft seams. **Done when:** fixture 3 and affiliate transfer wait correctly. **Tests:**
   racing consumption, receipt clearing, old-order migration, sent-order reversal blocker, distinct PO/bill approvals.
4. **Optional routes and local reminders (M).** Amount bands, sequential steps, identity rules, reassignment/inbox.
   **Done when:** $130 routes to two reviewers and neither same-principal nor inactive reviewer bypasses. **Tests:**
   every band boundary, invalid bands, deadlines, policy edits, switch-off with pending work, no outbound messages.
5. **Batch decisions and finish (M).** Batch decide/submit/commit adapters, full inverse plans/resume, both-width
   workflows and presets. **Done when:** fixtures 4–7 and closing trial pass. **Tests:** stale all-or-nothing decisions,
   interrupted sliced commits and inverses, payment/receipt/export blockers, current inverse authority and keyboard UI.

**Closing trial.** A fresh agent with only actual MCP help saves and submits a purchase; a person reviews it at
390 px, releases it at 1440 px and receives part. The agent submits a partial bill, the person returns a wrong
account, and the agent revises and resubmits. Approve/post it and independently sum AP, inventory and remaining
order quantity. Approve three bills in one batch and reverse that batch; commit one and show why the old decision
batch can no longer reverse. Repeat a lost-response retry and a principal-grant revocation. Record first-attempt
results and help friction, fix material failures and repeat the failed scene. Completion checks Bookflow's own
amounts, audit and authority, not resemblance alone.
