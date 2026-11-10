# Customer relationship management (R115)

Design contract, 2026-11-08. The principal approved R115 on 2026-10-31: **“leads, a sales pipeline,
follow-ups and customer communication history beyond contact records.”** This document proposes storage,
commands and build increments; it does not claim that CRM is implemented or authorize product implementation.

**Outcome.** Record an enquiry without creating a debtor, remember the next call, see what was said, and turn the
lead into a customer without losing that history. A business that needs several potential sales can enable a
pipeline; a business that only wants names and follow-ups never has to configure stages. People and agents continue
the same work by stable IDs. CRM itself posts no money, sends no messages and invents no customer balances.

## 1. The anchor and the existing core

Reference read: the anchor enterprise 24 guide, PDF/printed pp. 14–15, 25 and 74, on 2026-11-08. The Customer Center
combines a selectable customer list, contact details and terms, brief notes, and sortable/filterable transactions.
The Lead Center creates, imports and tracks prospects, then moves lead information into the Customer Center in one
action. Dated to-dos feed the calendar; past-due work is flagged. Customer Snapshot brings purchase history,
average days to pay and outstanding balance together. These pages establish that simple route; they do not establish
configurable pipeline stages, campaign automation, mailbox synchronization or a native CRM sending service.

The implementation starts with these existing owners:

- `company/parties.py` owns customer/job validation, addresses, contacts, defaults and inheritance. `ContactInput`
  includes primary/alternate/additional people, names, titles, phones, email, website and typed contact points.
  `customer_contacts` and `customer_contact_points` belong to customers; do not manufacture a customer just to reuse
  those foreign keys. The customer `notes` field remains its short summary, distinct from attributed comments.
- `commands/note_cmds.py` provides `note add/edit/show/list`, record-target resolution, bounded text, versions and
  audit through the common pipeline. Extend its target registry for CRM records; use the existing attachment and
  activity infrastructure. A note is internal commentary, not evidence that a message was sent.
- `company/todos.py`, `scheduling_models.py` and `scheduling_schema.py` own dated, prioritized, assignable to-dos,
  `open|done|inactive` states and completion facts. Links currently name customer, vendor, transaction or memorized
  records. Add lead/opportunity links; do not introduce a competing follow-up scheduler or completion table.
  `company/reminders.py` already derives due work and checks each section's capabilities. Its current bounded
  to-do query is not proof that a complete CRM due count exists; CRM needs an exact filtered count and paging.
- `company/delivery_models.py` supports invoice, sales-receipt, estimate, statement and notice messages, not arbitrary
  prospect email. `delivery_schema.py`, `delivery.py` and `delivery_dispatch.py` retain frozen recipients/content,
  files, attempts and delivery events. `delivery_batches.py` and `delivery_batch_reversal.py` own draft batches and
  their strict reversal. Reference these facts rather than duplicating an outbox or interpreting a draft as sent.
- `customer_balances.py` owns merge-aware individual/family AR balances. Native sales, aging and payment history
  own financial snapshot figures. `query_customer.py` explicitly describes an unregistered freshness spike; it is
  not a production customer-snapshot API to call. CRM adds a registered projection over the owning services.
- `company/batches.py` supplies effect recording, native inverse adapters, version/dependency preflight and resumable
  reversal. [reversible-batches.md](reversible-batches.md) and [bulk-import.md](bulk-import.md) own those doors.

[principal-directives.md](principal-directives.md) and [permission-resolution.md](permission-resolution.md) govern
execution authority. [document-delivery.md](document-delivery.md) owns sending. R115 neither adds a transport/provider
nor connects an inbox, social network, phone service, marketing service or outside address book.

## 2. Layers and the simple route

| Layer | Default | Gives | Requires |
|---|---|---|---|
| Leads and follow-ups | Available without setup | Lead list, notes, manual communication log, to-dos, conversion | Ordinary company access and explicit command rights |
| Sales pipeline | Off | Opportunities, ordered stages, expected close dates and pipeline reports | Nothing beyond the simple route |
| Campaign grouping | Off | Local named groups and explicit lead/customer membership, filtered work lists | Nothing beyond the simple route; pipeline optional |

Company fields `crm_pipeline_enabled` and `crm_campaigns_enabled` are false on migration. A versioned
`company update` enables either, with the normal company-settings permission. Enabling pipeline installs one
editable default pipeline with New, Qualified, Proposal, Won and Lost stages, atomically and only once. Enabling
campaigns creates no audience, tasks, documents or messages. No subscription, account mapping or wizard is required.
Settings control available features, never authority. No setting grants a capability.

With both switches off: Add lead → enter name and contact → add a dated to-do → log the call → Convert to customer.
A name is the only required lead field. To-dos, notes and communication history also work directly on an existing
customer or job. Nobody must create an opportunity to record a call or choose a campaign to arrange a follow-up.

Disabling a layer hides its new-work controls and stops creation, new membership and ordinary progression, returning
`E_FEATURE_DISABLED` with the setting and enabling command. Existing records remain readable from history and an
“Existing pipeline/campaign work” link. Correction, reopening a mistaken closure, closing an existing opportunity,
removing membership, archiving and batch reversal remain permitted under their original capabilities. No switch
silently closes work, drops stages or removes a reminder. Re-enabling restores the same definitions and records.

## 3. Leads, contacts and conversion

A lead has a stable ID, display name, optional company/person names, postal address, contacts using the existing
contact shapes, owner, source text, rating `unrated|cold|warm|hot`, summary and lifecycle `open|closed|converted`.
Open is the default, owner defaults to the creating actor, and rating defaults to unrated. Owner is a current
company member (person or agent) or null; it is not the customer's accounting sales rep and grants no authority.
Closed requires a reason; reopen is an explicit versioned edit. Archive is a separate flag, preserving lifecycle.
Archiving or closing shows open follow-ups and leaves them open unless explicitly selected for inactivation.

Names need not be unique: two people may share one. Use IDs for unambiguous writes; selectors with several matches
return permitted candidates. Normalize email/phone only for duplicate suggestions, retaining entered values. Never
merge automatically on a shared household phone, company domain or name. Preview import and conversion show matching
leads/customers, including archived ones when authorized. A suspect duplicate may be explicitly kept separate.
No new lead-merge engine is required: close a duplicate with an optional same-company `duplicate_of` link and reason;
reject self-links/cycles, and retain both histories.

`lead convert` chooses **new customer** or **existing customer**, with the lead's expected version and a customer
version for an existing target. One transaction records the customer write if any, conversion link, source/contact
mapping, lead lifecycle change, audit and batch effects. A second simultaneous conversion cannot create a second
customer. Retrying the same request returns the original customer and batch IDs. A different request against an
already converted lead returns the existing target and `E_CRM_ALREADY_CONVERTED`; it never guesses a replacement.

For a new customer, copy the explicit name, address, contacts and summary into native customer inputs and run native
customer validation/defaults. Do not invent credit terms, tax choices, opening balances, jobs or accounting activity.
Preview names any required native fields before commit. New contact IDs are mapped back to lead contact IDs; retain
old lead facts. For an existing customer, default to linking only. Optional contact transfer is an explicit per-field
mapping shown in preview, with customer write permission and version guards; no overwrite or deduplication by guess.
The initial conversion target is a top-level customer, not a job; create jobs later through ordinary customer work.

Notes, attachments, communications and to-dos keep their original targets and IDs. The customer timeline traverses
the active conversion link to display them once, labelled “From lead …”; the lead offers “Open customer.” No copying
of comments, no reauthored sent messages, no mass relinking of completed tasks. Open opportunities retain the lead
reference and resolve its converted customer for display and document selection; conversion does not mark them won.
After conversion, new customer-wide work normally targets the customer. Lead identity/contact edits refuse until
conversion is reversed; new annotations remain allowed and their target is visible.

Conversion is a small R186 batch, even for one lead. Reversal unlinks it and restores the lead's prior lifecycle;
for a newly created unused customer it invokes native retirement, preserving audit. Existing-customer conversion
reversal restores only explicitly copied fields and never retires that customer. Later edits, customer transactions,
new communications or tasks relying on the conversion, and newly linked sales documents block it. Pre-existing lead
notes and to-dos do not. Inspect all dependent records before any inverse. Do not present “unconvert” as deletion of
a customer's commercial history. Normal customer merges resolve CRM references through the existing survivor rules,
retain captured labels, and register CRM dependencies in merge/unmerge and batch preflight.

## 4. Follow-ups and communication history

A follow-up is an ordinary `todo`, with due date, title, notes, priority and assignee. Extend typed links and lookup
validation with `lead` and `opportunity`. “Next follow-up” is the earliest open accessible linked to-do ordered by
company-local due date, priority, creation time and ID. Customers include converted-lead tasks; optional job/family
scope is explicit. Opportunity tasks stay distinguishable from customer-wide tasks. The same task appears only
once in Reminders. A task is overdue when its due date is before the company's date, not the browser's date.

Complete uses `todo complete`; reschedule uses `todo update`; cancel uses `status: inactive`; reopen uses `open`.
Completing a call does not assert it happened: the optional “Log call and complete” action invokes one typed
`crm follow-up-complete` command with todo version and communication fields, atomically recording both. A lost reply
cannot log two calls. Reassignment requires an active company member; a departed owner/assignee remains named in
history, and open unassigned/departed-member work is filterable. No outgoing notification is generated.

`communication log` records a call, meeting, email, message, letter or other interaction: target, direction
`inbound|outbound`, occurred-at with explicit timezone offset, channel, subject, body/summary, participants and
optional existing attachment references. Date-only input is allowed with `occurred_on` and a date precision marker;
never invent midnight as an observed time. Future occurrences refuse; planned activity belongs in to-dos. For an
outbound entry the display says “Logged by …”, not “Delivered”. No command here performs the interaction it records.
A manual log may describe an already-sent message in the owner's ordinary mail program; it cannot attest that a
Bookflow delivery succeeded. Use native `delivery mark-sent` with its authority for that attestation.

A contact link is optional, must belong to the selected subject or its explicit conversion mapping, and captures
the name/address used at the time. Renaming/removing a contact changes neither historical recipients nor participants.
`communication correct` appends a revision and correction reason; `communication retract` appends a retraction.
Original content remains in history. Notes stay independently editable through their own version/audit contract.
Do not implement privacy erasure by calling retraction; any destructive retention policy is outside this design.

The timeline is a paginated union of authorized notes, to-do activity, manual communications, lifecycle events and
native delivery events. Native entries reference message/event IDs and frozen content, not a copied mutable body.
A sent attempt, delivery acknowledgement and bounce are separate facts on the same message, not three contacts.
Draft/held/failed/outcome-unknown states are shown as such and excluded from “last contacted”. Native sent or
marked-sent facts and unretracted manual interactions qualify, labelled with provenance. Repeated native event
ingestion/projection uses source identity and cannot duplicate an entry. No heuristic linking by recipient email:
customer/job and source document IDs establish ownership; a permitted explicit opportunity-document link supplies
opportunity context. One message covering several documents appears once per timeline.

History initially reads existing delivery records on demand, including messages predating CRM. No provider fetch,
read receipt, inferred inbound reply, tracking pixel or background mailbox import is added. Manual entries have
recorded-at as well as occurred-at, so a backdated log cannot masquerade as a newly observed native delivery.

## 5. Optional pipeline and campaign grouping

An opportunity names exactly one lead or customer/job, a title, owner, pipeline/stage, optional nonnegative expected
value in integer minor units plus company currency, and optional expected-close date. Several opportunities may
belong to one customer; their values are prospective, not AR or booked revenue. Null means unknown, distinct from
zero. The lead can be converted while an opportunity remains open. A lead becoming a customer and a sale being won
are separate events.

Stages have stable IDs, name, position, outcome `open|won|lost`, active flag and version. Default open stages are
New, Qualified and Proposal. There is one active Won and Lost stage per pipeline, at least one active open stage,
and one default open stage. An opportunity's current status derives from its stage. Additional pipelines and stage
renames/reordering are optional list edits; used stages are retired, never deleted or reclassified between outcomes.
Retiring a currently used stage requires a previewed move of affected open opportunities to an active stage in the
same pipeline, as one batch. Closed history keeps its original stage/outcome labels.

`opportunity move` requires expected version, destination stage and reason for loss or reopening. Closing captures
company-local close date (default today, not future); reopening appends history, clears current closure and retains
past close events. Moves may skip stages or move backwards; no artificial sales methodology gates the plumber.
Moving to another pipeline requires an explicit destination and a preview. Stage edits never silently move records.
There are no default probabilities, weighted forecasts, quotas or automatic stage advances. Changing an estimate or
posting an invoice does not win an opportunity. A won opportunity need not have an invoice yet.

`opportunity link-document` accepts an existing estimate, sales order, invoice or sales receipt and its current
version. It checks native document read rights, company and customer/job ownership, including a converted lead's
customer. It does not create/post a sale; an unconverted lead must first convert to use customer documents. A document
belongs to at most one active opportunity link, while an opportunity can have several documents. Unlink preserves
history. Corrections/voids display their native status without changing the expected value or close outcome.

Pipeline reports give count and known expected value by pipeline/stage/owner, plus unknown-value count, overdue
expected-close work and time in current stage. Date ranges explicitly select created date or closed date. Open
pipeline is a current-state report; stage history supplies dated movement reports. Won/lost count each opportunity
once by its current terminal outcome and latest close date; reopened work is open, not an additional win. Reports
label expected value “Potential sales”, never “Revenue”. No currency conversion or aggregation across companies.

A campaign is a local name, description, optional start/end dates, active flag, version and explicit membership in
leads/customers (including jobs). Dates are descriptive, not triggers. Membership is unique per campaign and target;
conversion preserves its lead source but resolves the customer once in audience views. If that customer is already
a member, show one customer with both provenance links. Removal can name one source or all resolved sources in
preview. No automatic targeting, enrollment, send, consent inference or promotion discount is attached. Optional
opportunity links let a report count explicitly attributed opportunities; they do not claim causal revenue.
[The promotions design](promotions.md) owns prices and offers and does not require CRM campaigns.

## 6. Customer snapshot

`crm snapshot` accepts customer/job, as-of date (today by default), purchase-history interval and scope `self|family`.
Default self shows exactly the selected party; family includes descendants once, including inactive jobs, under the
native hierarchy/merge rules. The header states scope and dates. Show contact details, terms, summary, last contact,
next follow-up, open opportunities when enabled, recent communications and links to native transactions/reports.

The as-of date governs financial panels; contacts, follow-ups and CRM summaries are labelled “Current” and
link to dated history rather than pretending to reconstruct earlier operational state.

Financial panels require their source permissions in addition to CRM/customer access. Outstanding balance and overdue
open invoices come from native AR and aging as of the requested date; unapplied credits remain distinct, with drill-down.
Purchase history uses posted invoice/sales-receipt net sales less linked credits, excluding tax, void/deleted effects,
estimates and orders; date basis is document date. It does not add payments as another sale. All amounts carry currency
and exact minor units. Explain any difference between net sales, open invoices and AR rather than force them to match.

Average days to pay is a new projection over native payment-allocation history, labelled with its population: fully
cash-settled positive invoices whose final settlement date falls in the interval. Exclude credit/write-off-only or
mixed noncash settlements, voids and currently reopened invoices. For each qualifying invoice use calendar days from
invoice date to the final effective payment-allocation date, floor at zero for prepayment; take the unweighted mean,
rounded half-up to one decimal using integer arithmetic. Also show invoice count and excluded count. With none, show
“Not enough paid invoices”, never zero days. Historical as-of excludes future allocations and uses native reversal
history; unsupported source history must show unavailable, not an estimate presented as fact.

Fixture: $100 invoice paid in full after 10 days and $300 invoice paid $100 after 5 days, $200 after 20 days give
15.0 average days across two invoices, not a money-weighted 17.5. A third $80 invoice closed by credit is excluded.
A $50 unpaid invoice contributes $50 open AR and no payment-time observation. A partial $20 payment reduces its
open amount to $30 but does not add a third observation. Expected opportunity value changes none of these amounts.

## 7. Storage and invariants

All CRM business data lives in the company file and travels with its backup. Reuse common ULIDs, version history,
audit actor/interface/on-behalf-of/reason, local principal mirrors and exact-money types. New tables have company-local
RESTRICT foreign keys; polymorphic targets use the shared record registry plus explicit domain validation. No hub-wide
lead directory. Schema migration starts empty and does not turn customers into leads or parse their notes as history.

| Record | Fields and constraints |
|---|---|
| `crm_leads` | identity, names, address, summary, owner, source, rating, lifecycle, close reason, archive flag, duplicate link, version |
| `crm_lead_contacts`, `crm_lead_contact_points` | lead-owned equivalents of native contact shapes; stable IDs, role/position and parent ownership validation |
| `crm_conversions`, contact mappings | lead, customer, original lead/customer versions, copied fields, mapped contact IDs, batch, active/reversed events; at most one active conversion per lead |
| Existing `todos`, notes and attachments | new registered lead/opportunity targets; no second task or comment store |
| `crm_communications`, revisions | target, direction/channel, occurred date/time and precision, recorded-at, participants/captured contact values, subject/body, attachment links, current revision and retracted flag; immutable prior revisions |
| `crm_pipelines`, `crm_stages` | versioned definitions, order, default/active flags, stage outcome and required terminal-stage constraints |
| `crm_opportunities`, stage events | target, title, owner, stage, value/currency, expected close, current close facts, version; immutable from/to stage IDs and labels, effective date, recorded time and reason |
| `crm_opportunity_documents` | opportunity/document link, captured revision, active/unlinked history; unique active document ownership |
| `crm_campaigns`, memberships, opportunity links | dated local definitions, explicit source target, version and removal history; unique active source memberships/links |
| Existing batch/effect/audit tables | exact before-images, committed versions, source operation and inverse dependencies for every bulk effect and conversion |

Writes validate all referenced records and permissions under the execution lock. Require expected versions for CRM
updates, conversion, moves, corrections, membership removal and composite to-do completion. Multi-record writes check
every version before any write. A conflict returns changed fields, actor and time using the shared conflict shape.
All creates/actions accept stable idempotency keys; identical replay returns the committed receipt, changed payload
with the same key refuses. Authorization is revalidated even on replay. A dry-run makes no IDs durable, reserves no
names and emits no delivery work.

Index lifecycle/owner/name, due-link/date/status, subject/time/ID, stage/owner/expected-close and membership keys.
Timeline cursors bind company, target/scope, filters and effective permissions; sort by occurrence, recorded-at and
stable source ID, with date-only entries labelled accordingly. Pages are bounded (default 50, maximum 200) with
continuation. Counts cover the full authorized selection, never the first page. Source corrections invalidate the
view's freshness contract. Do not load all history just to render a customer's first screen.

## 8. Commands, authority and agent use

All rows below are proposed typed shared commands. CLI, HTTP, MCP and browser invoke the same registry; browser
buttons are not permissions. Read results contain IDs, versions, provenance, pagination and permitted next actions.
Writes return changed IDs/versions, audit ID and batch ID where relevant, with identical JSON/errors on every surface.

| Command family | Principal inputs → result |
|---|---|
| `lead create/update/show/list/archive/reopen` | identity/contact fields or ID/version, filters → lead and conversion/follow-up summary |
| `lead close` / `lead convert` | ID/version, reason or target/customer field mapping → closure or customer/conversion/batch IDs |
| Existing `note`, `attachment`, `todo`, `reminders` | registered target, existing fields → attributed records/due work |
| `crm follow-up-complete` | todo/version plus manual log fields → completed todo and communication, atomically |
| `communication log/correct/retract/show/list` | target, facts; ID/version/reason → versioned manual entry; no sending |
| `crm timeline` / `crm snapshot` | subject, scope, dates and cursor → permission-filtered history or snapshot panels |
| `pipeline create/update/show/list`, `pipeline-stage create/update/retire` | definitions/versions; retirement destination → stage definitions or affected-record batch |
| `opportunity create/update/show/list/move/archive` | subject, value/date; ID/version/destination → opportunity and stage events |
| `opportunity link-document/unlink-document` | opportunity/version, document/version or link ID → versioned association |
| `campaign create/update/show/list/archive` / `campaign add/remove` | definition or explicit targets/versions → campaign/membership IDs and batch for a selection |
| `report pipeline`, `report crm-follow-ups`, `report crm-campaigns` | filters/date basis → full-selection counts, values and drill-down IDs |
| Existing import/bulk-edit and `batch reverse` | previewed CRM mappings/selection or batch ID → exact effects, blockers or inverse result |

Proposed independent capabilities are `crm.read`, `crm.write`, `crm.convert`, `crm.manage`, `crm.communication` and
`crm.bulk`. Read permits CRM records/reports, write ordinary leads/opportunities, convert conversion, manage stage/
campaign definitions, communication manual log/correction/retraction, and bulk multi-record mutation in addition to
each underlying write. Campaign add/remove requires `crm.write`; changing definitions requires `crm.manage`;
stage retirement that moves records additionally requires `crm.write` and `crm.bulk`. Human defaults: read at member; write/convert/communication/bulk at standard; manage at admin.
Agents receive no new grants automatically. Effective access is the actor/principal intersection with denies binding;
ownership or assignment is never an access grant. Add a catalog layer at the then-current tip, not by changing an
accepted layer. Existing presets remain unchanged until explicitly extended.

Native customer, note, to-do, attachment, sales, report and delivery reads/writes keep their own requirements.
A timeline requires source read access before selecting rows, snippets, counts or search results. Direct note/todo
reads on a lead also require CRM target access; extending the registry must not bypass target admission. The snapshot
omits inaccessible panels and totals, rather than fetching them into HTML and hiding them. A CRM salesperson need
not gain ledger or delivery-body access. Customer contact records require customer access even when CRM can name
its own captured subject. Unauthorized/cross-company selectors reveal no record existence or duplicate candidates.

Sending remains exclusively in existing delivery commands and requires the principal's own authority: a delegated
actor needs an explicit applicable `delivery.send` grant, its principal must hold it, and the principal's instruction
must cover the actual release under R187. A CRM grant, task, campaign membership, owner assignment or logged call is
never that authority. Recheck at release and dispatch using native delivery controls. There is no actor-kind or
browser-only exception, CRM Send command, new sender/provider or automatic follow-up mail. Unsupported free-form lead
email remains a manual log of work done through existing channels; no permission choice makes it an implemented send.

Errors reuse permission, validation, version-conflict and feature-disabled contracts. Add `E_CRM_ALREADY_CONVERTED`,
`E_CRM_TARGET_MISMATCH`, `E_CRM_STAGE_IN_USE` and `E_CRM_DEPENDENCY` with permitted record IDs, affected fields and
next commands. Broad changes are `confirm_first` under R187: preview exact membership, versions and consequences
once; a reason string is not permission. Help indexes “prospect”, “lead”, “next call”, “follow-up”, “sales pipeline”
and “customer history”. A fresh agent can discover these, preview conversion and resume a human's opportunity by ID
without source reading, UI automation or a second agent-only workflow.

## 9. Reversible imports and batches

Register leads, opportunities, manual logs and campaign membership with the existing import/bulk-edit framework.
Preview shows column mapping, explicit create versus update, duplicate candidates, per-row validation and exact
selection. Updates require an ID or configured source key plus expected version; names never silently upsert.
Import source identity is unique within company/source, with each row's stable key and payload hash; retry resumes
landed rows, changed payload refuses. Imported communications are manual/imported provenance, never native sent facts.
Dates/timezones, owner references and contact ownership validate before apply. Existing import size limits and
`import.approve` requirements still apply; `crm.bulk` does not lift them. No imported row invokes delivery.

Every multi-record import, owner reassignment, stage move, membership edit, follow-up creation and manual-log import
gets one R186 batch ID. Record each landed effect in the same transaction as its mutation via `batches.record_effect`.
A partial failed import exposes its committed effects and row errors under that same ID; retries never create an
unrecorded tail. Bulk preview binds explicit IDs, source versions and filter fingerprint, not a changing saved query.
Do not let a new matching lead enter an already approved audience halfway through the run.

`batch reverse <id> --dry-run` lists the complete inverse and all blockers. Created leads/opportunities/campaigns
are retired; newly created to-dos become inactive; imported logs receive retraction revisions; memberships are
removed; field changes restore exact before-images; stage moves append compensating events restoring prior current
state. Already existing notes are never removed. New notes created within a supported import need a history-preserving
native inverse before that import kind is exposed; otherwise reject that column rather than promise reversal.

A later field edit, completed follow-up, new note/communication/document depending on an imported identity, downstream
conversion, customer merge, released delivery, sent mail or exported artifact blocks the affected inverse and hence
whole-unit reversal. Check dependency identities even where an annotation did not bump the parent's version.
The reverse adapter must inspect new CRM tables in addition to existing FK/dependency registries. Manual-log
retraction retracts Bookflow's assertion, not the real call or letter; an external action actually caused by a batch
cannot be undone by retracting its record.

Preflight the whole unit, execute inverse order through native commands with the reverser's current grants, and
preserve audit and original facts. Follow R186's sliced/resumable execution: a mid-run crash or newly discovered
conflict leaves visibly unfinished reversal, never a claim of all-or-nothing rollback after some slices committed.
Block new bulk work on those affected records until reversal completes or its blocker is resolved. Recheck versions,
dependencies and authority per slice. No alternate CRM undo door bypasses `batch reverse`.

## 10. Screens and acceptance

At **1440 px**, Customers gains a Leads view beside the existing customer list. Search and filters precede the list;
selecting a row opens contact summary, next follow-up and tabs for History and To-dos. Customer detail also includes
the snapshot. New lead needs one name field; other details expand. Convert previews the chosen customer and copied
fields, with duplicates alongside. The optional Pipeline view offers both stage columns and a sortable table, with
stage moves available by menu/keyboard as well as drag. Campaigns show a local member list and “Create follow-ups”,
never an implied send button. Financial numbers remain separate from potential sales.

At **390 px**, leads are cards/rows showing name, owner and next due date. Detail is a full page with sticky Add
follow-up/Log interaction actions, 44 px targets and focus returning after a sheet closes. Pipeline defaults to a
stage filter and vertical opportunity list; no mandatory sideways dragging. Conversion is a short review sheet with
explicit new/existing target and a visible cancel action. History labels Draft, Sent, Bounced, Logged and Retracted
in words, not color alone. A denied field never appears in page source, JSON or exports. Both widths preserve filters
and the selected record when a person resumes an agent-created task.

Acceptance criteria:

1. With both optional layers off, a name-only lead, call log, due follow-up and conversion work end to end. Existing
   customer/job contacts, notes, delivery and accounting are unchanged. Turning either layer on takes one settings act.
2. Conversion retains every contact mapping and historical entry, has no GL effect, survives retry and rejects a
   competing conversion. Existing-customer linking cannot overwrite a contact without the previewed choice.
3. Follow-ups appear once in the ordinary reminders list, including converted leads, with correct company-local dates,
   assignee and complete/reopen history; exact counts and subsequent pages work beyond the old 500-row bound.
4. History distinguishes internal notes, manual assertions and native delivery evidence. Retry/combined documents
   do not duplicate communications. Old sent content survives contact edits, conversion and customer merge.
5. Optional stage movement, lost/reopen, null/zero values and campaign provenance obey §§5–7. No stage move, membership
   or task completion posts revenue, creates a discount or sends anything.
6. Snapshot figures reconcile independently to native AR, sales and allocation history, including §6's fixture,
   partial payment, credit, void and as-of boundaries. Inaccessible panels and aggregates reveal nothing.
7. Human and agent authority is identical across CLI, HTTP, actual MCP and browser. Missing send grants or revoked
   principal authority cannot be bypassed by a campaign or direct endpoint; source permissions filter timeline reads.
8. Mistaken import and broad edits reverse through the shared door, preserve history, name blockers without starting
   an invalid reversal, and resume interruptions without double effects. A sent/exported dependency is never recalled.
9. Keyboard/touch journeys pass at both widths; a seeded large list remains paginated with full counts and stable
   continuation. Backup/restore retains CRM histories and links without requiring an outside service.

## 11. Decisions settled and K-questions

**Settled.** Separate prospects from accounting customers; one-name lead entry; reuse contact shapes but preserve
lead ownership; optional pipelines and local campaign groups off by default; native to-dos and notes; explicit
conversion preserving source identities; link-only default for existing customers; explicit won/lost events independent
of invoices; no probability forecast; manual communication provenance distinct from native delivery; one shared command
contract and permission model; whole-unit batch reversal with dependencies; no new sender/provider or inbox connector.
Owner is responsibility, not the accounting rep or an access rule. These reversible choices need no approval gate.

Only future scope or irreversible acts remain questions; the defaults permit every increment below:

- **K1 — Campaign scope.** Should R115 extend beyond local grouping and follow-up work into marketing automation or
  unsolicited outreach? **Default:** no; campaigns only organize explicitly selected existing records. Any proposed
  outside service, spend or messaging expansion needs a separate scope decision; no sender/provider is added here.
- **K2 — Destructive retention.** Is permanent erasure of lead/contact/communication content required, and under what
  retention policy? **Default:** archive/retract with immutable history and ordinary backup retention; no hard-delete
  or purge command. A future erasure policy needs the principal's decision before irreversible removal is built/run.

These are recorded future decisions, not requests to interrupt this design or grant sending authority.

## 12. Build increments

S is a narrow useful slice, M one bounded workflow, L a cross-cutting workflow. Every increment includes demo data,
typed help and generated command/schema references, migration checks and source permission checks. The Tests clauses
are future build acceptance, not instructions to add tests in this design-only change.

1. **Lead list and native follow-ups (S).** Lead/contact storage, simple list/detail, note/attachment targets and native
   to-do links with source-aware admission. **Done when:** a name-only lead can carry an attributed note and a dated
   follow-up, visible once in Reminders with both optional layers off. **Tests:** contact validation, ambiguous names,
   disabled layers, local-date boundary, assignee membership, target permission denial, old customer migration,
   501-plus due tasks with exact counts and continuation, CLI/MCP and one 390 px journey.

   #### As built — increment 1

   `lead create/update/show/list/close/reopen/archive` stores prospects independently of accounting customers.
   Contacts and typed contact points use the native shapes and reconciliation rules with lead-owned stable IDs.
   Exact-name selectors refuse ambiguity; IDs select a specific lead. Updates require versions, and closes require
   the command reason. Duplicate links reject cycles. Archive and closure leave native follow-ups open.

   Migration `co0132` starts the three lead tables empty, preserves existing customer/contact rows, adds disabled
   `crm_pipeline_enabled` and `crm_campaigns_enabled` fields, and extends the native to-do link constraint and index.
   These settings are reserved and cannot be enabled in this increment. No pipeline or campaign tables exist.

   Notes and attachments accept lead and lead-contact targets. Native tasks, annotation reads, cached retries and
   audit visibility enforce CRM target admission alongside each source's ordinary capability. `crm-leads-v1` adds
   `crm.read` at member and `crm.write` at standard for human roles; agent grants remain explicit. Ownership grants
   no authority. Lead summaries omit task projections without note access. To-do and Reminders queries count the full
   admitted selection and return snapshot-bound continuation; unassigned and departed-assignee tasks are filterable.

   Customers links to a searchable lead list and full-page detail with notes/files, native follow-up creation and
   completion, and phone-sized controls. The demo includes two prospects, a contact, an attributed note, a task and
   a file. Generated command/schema references include the new contracts. No money is posted and nothing is sent.

   Conversion/import reversal, manual communications, pipeline, financial snapshots and campaign grouping remain
   in increments 2–6. CRM integration fixtures create companies through the ordinary migration chain;
   the CRM upgrade itself is tested from `co0131`.

2. **Conversion and reversible lead imports (M).** Native customer validation, new/existing conversion, contact maps,
   unified lead/customer history, source-key imports and R186 inverse adapter. **Done when:** a person continues an
   agent's converted lead and a mistaken unused import/conversion reverses as one unit without lost history.
   **Tests:** competing conversion, lost response, existing-contact mapping, source-key collision, denied customer
   write, later note/task/document blockers, failed import resume, reversal interruption and customer merge/unmerge.
3. **Communication history (M).** Manual log/revision/retraction, atomic follow-up completion, native delivery union
   and full permission-filtered timeline. **Done when:** old sent mail and new manual calls appear once with correct
   provenance, and a CRM action cannot send. **Tests:** drafts/unknown outcomes excluded from last contact, bounce,
   resend, combined documents, timezone/date-only inputs, backdated log, contact rename, withheld delivery body,
   revoked authority, repeated source event, manual-log import reversal and two-width history reading.
4. **Optional sales pipeline (M).** Default/custom stages, opportunities, native document links, explicit close/reopen,
   table/board views and stage history reports. **Done when:** several potential sales progress independently for one
   customer with exact stage counts and no accounting effect. **Tests:** layer enable/disable/re-enable, stale move,
   wrong-company/customer document, stage retirement batch, reopened win, unknown/zero values, retired labels,
   concurrent links, read/write separation and keyboard/touch stage changes at both widths.
5. **Customer snapshot (M).** Shared registered customer projection over native financial/history owners, scoped job
   rollup, last contact and next follow-up. **Done when:** §6's fixture and historical source drill-downs explain every
   displayed amount and payment-time observation. **Tests:** independently computed balances and mean, partial and
   credit settlement exclusions, reversal/void, as-of dates, customer merge, family deduplication, permission-filtered
   panels and totals, large-history pagination and backup/restore.
6. **Local campaigns and bulk finishing (M).** Optional definitions/membership, explicit opportunity attribution,
   bulk owner/stage/follow-up actions and remaining inverse adapters. **Done when:** an explicit campaign list creates
   internal work only, all batch paths reverse or name dependencies, and every acceptance criterion passes.
   **Tests:** conversion membership dedupe, remove-one/all provenance, fixed preview selection, task inverse versus
   completed-task blocker, no send/provider effects, per-slice revocation, crash/resume and the closing trial below.

**Closing trial.** A fresh agent with actual MCP help imports a small prospect list, explains duplicates, records a
call and assigns tomorrow's follow-up. A person at 390 px completes it, converts the lead and resumes its history.
Enable pipeline, open two opportunities, link an estimate, lose and reopen one, then win the other without posting
money. Log a received email by hand and inspect an already-sent native document plus a bounced one. Compare the
customer snapshot to independently worked invoice/payment facts at 1440 px. Reverse an unused mistaken import and
campaign reassignment; repeat after a downstream task completion or sent-message dependency and inspect the refusal.
Try a CRM-only actor and an agent whose principal's send authority was revoked. Record first-attempt outcomes and
help/clarification friction, fix material findings and rerun failed scenes before the build is complete.
