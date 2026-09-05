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
application repeats translation/reference validation in the writer transaction.
There is one audit event under the actual register command name and one company
commit. No recursive dispatch or internal commit. The journal service remains
responsible for validation, snapshots, numbering, source attribution and exact
reversal/replacement effects. No new accounting tables are required.

Post takes account, date, direction(increase/decrease of normal-side balance),
positive exact amount, optional number/memo/payee(name_type,name_id), class_id,
and either category or 1–199 allocations. Each allocation takes account, amount,
optional direction(inherit main/increase/decrease), memo, typed party and class.
Optional null party means no party; absent split party is no party rather than
implicitly copying the row payee. Absent split class inherits the row class;
explicit null clears that default. A simple category copies the row payee and
class; differing AR/AP parties use allocations. The selected-account line takes
row payee, header memo as description, and no class. Extra fields are rejected.

Register accounts are bank, AR, other current asset, fixed asset, other asset,
AP, credit card, other current liability, long-term liability or equity. Active
home-currency posting references and the journal AR/customer/job/AP/vendor rules
apply. Category equal to the selected account is rejected. Amounts remain
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
It uses the same parser and reference/sign checks. Recalculate explicitly sets
the form's main amount to this returned total; it never runs silently at save.

Update takes the complete editable register shape, journal, required
expected_version and selected_line_id; retained allocations carry line_id.
The current journal must have exactly one selected-account line and all its
lines must be represented. New allocations omit line_id; removed identities
never return. Preserve snapshots through the journal rules. Journals with
repeated selected lines, foreign facts or dimensions the register cannot express
open the journal editor. No-op keeps version/history; stale aggregate versions
always conflict. Void uses ordinary journal void with shown version and reason.

## Query and response

Post/update return the complete JournalWriteOutput plus a typed register receipt
(account ID, normal side, direction and movement Money). A stored replay receipt
is not the current account balance. Current balance is refreshed by query.

Query takes account, inclusive date_from/date_to, limit1–200(default50), cursor.
It uses the general-ledger read snapshot, exact integer aggregate and window,
deterministic account/date/batch/line ordering and restart-on-change continuation.
Do not release that snapshot between report computation and row enrichment.
Return metadata, selected account identity/type/normal side, whole-filter opening
and closing Money normal-side balances, period totals, and paged opening/posting/
closing rows. Posting rows contain stable journal/revision/batch/line IDs,
effective date, recorded time, number, effect, payee, category or Splits, memo,
class, exact increase/decrease and normal-side running balance. Enrich a page
with bounded set-based joins to revision/document-line snapshots; no per-row show
calls and no multiplying financial sums with source joins. Preserve historical
labels. Existing report currency/time/watermark metadata remains visible.

The continuation's relevant dependencies include all enriched displayed facts.
Use immutable revision snapshots for memo/payee/category/class, so later master
renames do not rerender history. Current selected-account label/type changes are
relevant. Continuation rejects changed query/company/identity/permissions before
emitting rows and preserves initial metadata on successful continuation.

## Browser

GET /c/{company}/account/{account}/register is linked from account detail and the
chart. Controls submit ordinary command routes and decimal strings. Keep prior
movements, draft and authoritative receipt together; no success redirect away
from the register. Offer simple entry and split editor with their inventory.
Use accessible labels, keyboard-selectable existing reference controls and
field errors. Enter in a picker accepts selection; posting requires Record.
Restore returns to initial draft or loaded revision. Preserve uncertain-save
payload and key; Retry same save sends exactly that request. Do not permit a
changed request to reuse an unresolved key or a second save to silently duplicate
an uncertain first one. Definite rejection can begin a revised intent.

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
