# Account register increment

Target: the domestic entry and account-history portion of Row 8. Contract:
blueprint10.4, journal storage in10.3, reports14 and browser18. The domestic
journal artifact remains a dependency. FX, journal custom-field values, full
identity isolation, banking reconciliation and the wider register inventory are
separate increments. Human register usability remains a later checkpoint.

## Commands and ownership

Company-scoped `register post`, `update`, `calculate` and `query` use the shared
registry. Writes require standard role and ledger.post, normal context and
idempotency. Reads require member role and ledger.read. Command planning
translates register inputs to the journal aggregate within the existing session;
application retains the typed register request and repeats register translation,
compatibility and reference validation in the writer transaction.
There is one audit event under the actual register command name and one company
commit. No recursive dispatch or internal commit. The journal service remains
responsible for validation, snapshots, numbering, source attribution and exact
reversal/replacement effects. No new accounting tables are required.

Post takes account, date, direction(increase/decrease of normal-side balance),
positive exact amount, optional number/memo/payee(name_type,name_id), class_id,
and either category or 1–199 allocations. Each allocation takes account, amount,
optional direction(inherit main/increase/decrease), memo, typed party and class.
Optional null party means no party; absent split party is no party rather than
implicitly copying the row payee. Split class_mode is inherit(default), none or
value. Only value accepts a non-null class_id; none explicitly clears the default.
The canonical model dump retains class_mode, so inherit and none cannot collide
under the same idempotency key. A simple category copies the row payee and
class; differing AR/AP parties use allocations. The selected-account line takes
row payee, header memo as description, and no class. Extra fields are rejected.

Register accounts are bank, AR, other current asset, fixed asset, other asset,
AP, credit card, other current liability, long-term liability or equity. Active
home-currency posting references and the journal AR/customer/job/AP/vendor rules
apply. Category or any allocation equal to the selected account is rejected, including
calculate and writer-time translation. Amounts remain
positive exact home-currency strings or typed integer Money; no floats.

Increase uses the selected account's normal side; decrease uses its opposite.
Each allocation posts on the opposite side of its indicated selected-account
movement. Offsets' own normal balances do not determine posting direction.
Same-direction allocations minus reverse-direction allocations must equal the
selected amount, with a positive net. Bank payment100 with expense120 and
refund20 therefore posts Cr bank100, Dr expense120, Cr expense20. The balanced
journal debit total is120; register movement is100. Each side fits signed i64.

Calculate takes account, main direction and allocations and returns exact
positive net Money, currency and direction without posting/reserving a number.
It validates supported active home-currency selected/offset accounts, offset
party references and their AR/AP requirements, explicit allocation classes, and
allocation amount/sign rules.
It does not validate a selected AR/AP payee or inherited row class: those are not
calculator inputs and remain post/preview validation. Calculate is a split-total
calculator, not an assertion that the entire proposed journal can post. Recalculate explicitly sets
the form's main amount to this returned total; it never runs silently at save.

Update takes the complete editable register shape, journal, required
expected_version and selected_line_id; retained allocations carry line_id and
a retained simple category carries category_line_id. The editor loads every
current line in order. Update is an explicit full replacement: omitted old
allocations are intentionally removed, retained identities keep order unless the
user reorders them, and new allocations omit line_id. Removed identities never
return. Changing category may retain that allocation identity.

A compatible current journal has exactly one selected-account line, first in
entered order, with no class and description equal to header memo; every other
line names another account and all foreign facts are null. Split inputs can
express arbitrary offset descriptions, party and class. A single offset can
use simple category only when its description and party match row memo/payee;
otherwise it loads as a one-allocation split. No adapter silently normalizes an
incompatible journal. The lossless read/edit mapping includes every stable line
ID and displayed field. Untouched compatible load/save must be a no-op. Preserve snapshots through the journal rules. Journals with
repeated selected lines, foreign facts or dimensions the register cannot express
open the journal editor. No-op keeps version/history; stale aggregate versions
always conflict. Void uses ordinary journal void with shown version and reason.

## Query and response

Post/update return the complete JournalWriteOutput plus a typed register receipt
(account ID, normal side, direction and movement Money). A stored replay receipt
is not the current account balance. Query returns a separate all-entries balance snapshot, including future-dated
postings. It is labeled All entries, not the selected period closing balance.

Query takes account, inclusive date_from/date_to, limit1–200(default50), cursor.
It uses the general-ledger read snapshot, exact integer aggregate and window,
deterministic account/date/batch/line ordering and restart-on-change continuation.
Do not release that snapshot between report computation and row enrichment.
Return metadata, selected account identity/type/normal side, whole-filter opening
and closing Money normal-side balances, period totals, and paged opening/posting/
closing rows. Posting rows contain stable journal/revision/batch/line IDs,
effective date, recorded time, number, effect, payee, category or Splits, memo,
class, exact increase/decrease and normal-side running balance. Header memo comes
from the batch's immutable revision; line_description remains separately visible.
Payee comes from that selected posting line. When the revision has exactly one
selected-account line and one offset, category and class come from the offset's
snapshots. Multiple offsets show Splits; a common offset class is shown when all
match, otherwise Mixed. Multiple selected-account lines show General journal and
retain the individual posting line's class. Apply these rules to reversal as well
as business batches; never omit incompatible entries. Enrich a page
with bounded set-based joins to revision/document-line snapshots; no per-row show
calls and no multiplying financial sums with source joins. Preserve historical
labels. Existing report currency/time/watermark metadata remains visible.

The continuation's relevant dependencies include all enriched displayed facts.
Shared report cursors use HMAC-SHA256 over the complete serialized state, including
metadata, offset, resolved account, query and authority fingerprints. A private
32-byte company-local key is generated by migration and travels with company
copies; it is never exposed in commands, audit, cursors or annotation targets.
Verify the signature before accepting any decoded account or metadata. Register
continuations additionally bind selected-account name/number/type/currency and
company account-label preferences. An outer signed register continuation may
carry the authenticated report cursor plus this register display fingerprint.
A tampered signature/state/account/metadata is invalid; a changed relevant display
fingerprint returns E_QUERY_STALE. No unsigned fallback.

Query's all-entries balance is computed in the same read snapshot as that page,
across every posting date. Its own generation_time/audit_watermark describe that
fresh read; initial paginated period-report metadata remains separate and stable.
Future-dated activity outside the period may change All entries without staling
period rows. Explicit current-versus-period labels persist on every page.
Use immutable revision snapshots for memo/payee/category/class, so later master
renames do not rerender history. Current selected-account label/type changes are
relevant. Continuation rejects changed query/company/identity/permissions before
emitting rows and preserves initial metadata on successful continuation.

## Browser

GET /c/{company}/account/{account}/register is linked from account detail and the
chart. Install this specific GET route before the generic four-segment command-form
route. A dedicated register module renders through the shared company shell.
JavaScript submits decimal strings using JSON fetch to /companies/{company}/
commands/register.post or register.update with X-Bookflow-Workbench:1,
X-Bookflow-Client-Name:bookflow-workbench and ordinary context/idempotency headers.
It does not use the generic form-submit endpoint that redirects on success.
Prevent form-wide implicit Enter submission unless Record owns focus, including
closed/empty pickers; picker acceptance never posts. Keep prior
movements, draft and authoritative receipt together; no success redirect away
from the register. Offer simple entry and split editor with their inventory.
Use accessible labels, keyboard-selectable existing reference controls and
field errors. Enter in a picker accepts selection; posting requires Record.
Restore returns to initial draft or loaded revision. Preserve uncertain-save
payload and key; Retry same save sends exactly that request. Do not permit a
changed request to reuse an unresolved key or a second save to silently duplicate
an uncertain first one. Only a known pre-commit validation, version, period, permission, inactive-reference,
unbalanced or duplicate-number rejection permits a revised intent. Transport
failure, invalid response, E_IO/E_INTERNAL or unknown errors remain unresolved.
E_PARTIAL_WRITE means committed-with-error: retry the exact key to retrieve its
receipt, never allocate another key. Idempotency mismatch keeps the old intent
for reconciliation rather than starting another save.

Before sending, persist one bounded pending intent per browser tab (at most1MiB)
in sessionStorage: actor ID, company, account, command, exact typed payload,
nonsecret attribution context, key and attempt time. Never store credentials.
If storage fails, do not start a save whose recovery state cannot survive reload.
Restore cannot discard unresolved intent. Navigation preserves it; reload offers
Retry same save rather than automatic submission. Bind it to the authenticated
actor before displaying it, and clear protected payload on identity change/logout.
After the 30-day retry retention window, disable automatic resubmission and link
to authoritative journal/audit review using the original idempotency key/request
ID where available. Explicit reconciliation is required before discarding that
intent or beginning a replacement. This does not claim exact-once after expiry.

A precommit rejection permits revision only when no earlier attempt of that
intent may have committed. Persist attempt state before sending. Once any
response is uncertain, a later permission/validation rejection does not resolve
the earlier attempt; retain its exact key and payload until an authoritative
saved result or the documented reconciliation flow resolves it. Recovery state
without reliable attempt metadata is treated as uncertain.

A receipt survives failed balance refresh and a backdated entry outside the
selected period. A separate Refresh action retries the read. Next-page staleness
discards accumulated history and restarts while preserving the draft. Conflict
preserves attempted version and draft; no automatic rebase/overwrite. Readonly
shows no mutation controls and forged requests fail in shared dispatch.

Keep account/date for consecutive entries and select Date after success. Record
is reachable by Tab then Enter. Splits retain draft on Close and restore focus;
Add/Remove/Recalculate work without dragging. At390px and desktop1280px, and at
zoom, body never owns horizontal history overflow and focused controls remain
visible. Currency amounts do not wrap internally or undergo JS Number arithmetic.

## Demo and verification

Extend the demo with bank payment/deposit, card charge/payment and mixed-direction
split entries through commands. Record independent expected trial-balance and
per-account totals beside the seed. Verify all source allocations and correction/
void history, not merely displayed balance. Existing domestic tests remain.

Service witnesses cover every supported account normal-side mapping; simple and
mixed splits; mismatches, positive net and integer limits; party/class inheritance;
closed periods; whole-aggregate stale versions; incompatible journal shapes;
no-op, retry and rollback. Report witnesses compare complete paged register rows
to GL, backdated changes and selected-label changes, metadata consistency and
exact totals. UI witnesses use real Chrome/CDP already in the repository, with
keyboard-only payment/deposit and splits, saved receipt/current balance, stale
two-tab edits, dropped-response retry, readonly denial and desktop/phone layout.
Docs contain executable CLI/HTTP/Python examples with structured allocations.

## Shared persistence seam and adversarial witnesses

Extract journals.persist_prepared(fresh_plan, context, session, command_name)
from the current applier. It validates the pending aggregate, writes the one
outer-named audit event with preallocated references, inserts rows and returns
Applied(audited=True), without committing. Ordinary journal.apply still replans
its journal request first. Register.apply replans its retained RegisterInput
inside BEGIN, translates to fresh JournalInput, calls journal.prepare there,
then calls the shared persistence seam with register post/update. Extend the
returned authoritative JournalWriteOutput with a receipt derived from that fresh
translation. Preserve no-op, rollback and dispatch idempotency behavior.

Witness explicit split class_mode inherit versus none with one non-null row class
and the same key (mismatch), changed type/reference between preview and writer,
simple-category line-ID no-op, deliberately different descriptions/classes and
selected-line order, same-account allocations, and metadata/account signature
tampering. UI witnesses include E_PARTIAL_WRITE, uncertain reload/Restore,
posts outside both period ends, closed/empty picker Enter, Shift+Tab/Escape,
company-local T and +/− date shortcuts. A reduced visual viewport with focused
lower controls must preserve visibility; phone viewport sizing alone is not a
claim about an actual device's soft keyboard.


Attribution text uses the HTTP percent-utf8 context-header transport and arrives
unchanged in audit, including non-Latin reason/source reference. The browser
validates local request construction before transmission; local construction
failures are known rejections, while a failure after a possibly sent attempt
retains conservative retry state. Opening/closing split presentation preserves
null versus empty descriptions, identities and untouched version/history.
