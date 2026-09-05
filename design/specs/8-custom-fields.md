# Journal custom fields

Target: the journal document facts in row 8, blueprint sections 10.3, 10.4 and
11.15. This increment adds typed header custom-field values to domestic journals
and register entry. Foreign-currency posting, line custom fields and additional
transaction forms remain separate increments.

## Values and ownership

Journal and register post/update accept an object `custom_fields`, keyed only by
stable definition ULIDs. Values use the shared exact custom-field parser:
text/string, decimal text with signed-i64 nano-unit coefficient, canonical ISO
date, JSON boolean or a declared active choice label. Top-level null is rejected;
individual null values mean clear. Unknown, wrong-company or wrong-scope keys
return E_RECORD_NOT_FOUND. Duplicate normalized keys are invalid. No float,
caller-authored snapshot or caller-selected value-slot ID is accepted.

The mapping is a patch: omitted keys preserve existing values on update. Creation
uses active applicable definitions; an omitted value receives its non-null
default, a missing required value without a default is rejected, and an optional
value without a default is omitted. Explicit null suppresses an optional default.
Explicit null for an active required definition on creation is rejected, including
when it has a default. Apply that required-value rule in the shared owner planner
for supported lists and journals. Unrelated updates do not acquire new defaults
or retroactively enforce newly required definitions. A populated required value
cannot clear. Equal canonical values remain no-ops under inactive definitions;
new or changed inactive values are rejected. Defaults and choices must remain
consistent: a definition edit cannot retire a retained default's selected choice
unless it clears/replaces that default in the same command. Same-key choice-label
spelling changes remain valid and canonicalize the default to that choice.

`custom_field_values` owns one stable slot per definition and `journal_entry`
header ID. Clearing deactivates the slot and retains its canonical text; setting
again reuses its ID. Journal values enable only this transaction scope, leaving
other unimplemented forms unavailable. Kind and scope sets remain immutable
after any value slot has existed. Choice-use guards include journal values,
including populated values of voided journals. Definition undo cannot bypass
first-use, active-value or active-owner dependency protections. Ledger events
remain non-undoable through the list undo command.

Choice equality uses the shared normalized label key (NFC, trim and casefold)
within a stable definition. If a supplied choice has the same key as an active
slot, preserve the slot's existing canonical_text; it is not a value mutation,
even after a spelling-only rename or definition deactivation. New or changed
choices resolve to an active choice and store its current canonical label. Choice
retirement and undo guards compare normalized keys across all active owner slots,
not literal display strings; removing/replacing an in-use choice ID is rejected
even when a new choice would have the same label. Cleared slots do not block
retirement, and later reactivation captures the then-selected choice identity.

For choice snapshots, `value` always decodes canonical_text and therefore retains
its original spelling on preservation or refresh. `choice_id` identifies the
selected choice. `choice_label` is its captured display label: unchanged on
preservation, refreshed from that same choice ID only on explicit refresh.
Refresh does not resolve a different choice by label. Historical human display
uses choice_label; exact typed projection still returns value/canonical_text and
both choice metadata fields. Current form selection matches the normalized value
while untouched fields remain omitted patches. Thus Web→WEB can refresh display
without changing the original canonical value or losing retirement protection.

## Immutable revision facts

Each revision's existing `custom_fields_snapshot` object is keyed by definition
ID. Each populated entry contains definition_id, value_id, captured name, kind,
exact typed value, canonical_text, definition_version and position, plus stable
choice_id and captured choice_label for a choice. No fields are reconstructed from
current master data when reading a historical revision. Cleared values are absent
from the new snapshot and remain in older immutable revisions and audit entries.

A preserved value copies its existing snapshot verbatim, even after definition
rename, reorder, deactivation or same-key choice-label spelling change. New or
changed values capture the current definition/choice facts. `refresh_defaults`
refreshes displayed facts of populated values explicitly; it does not change their
canonical values, fill missing defaults, clear values or enforce newly required
fields. It retains the journal flag's existing issuer/line refresh behavior.
An equal patch with unchanged resulting snapshot creates no revision, audit event,
version or sequence change. A real custom-only change follows ordinary correcting
journal behavior: one new revision and exact reversal/replacement effects with
unchanged net balances, normal closing-date checks and whole-header concurrency.
Void retains the current value slots and selected revision facts.

Journal show, previews, write receipts and selected historical revision return
both the snapshot and a typed `custom_fields` projection derived only from it.
Journal query/history summaries remain bounded and omit full values and lines.
Existing empty snapshots need no migration. No schema change is required.

## Writer and validation

Both initial planning and writer-time replanning use the shared owner-value
planner. The journal plan retains its value mutations alongside pending immutable
rows. Before audit, validate the resulting owner slot IDs, definition IDs, active
set, canonical/typed consistency, choice identity and preserved snapshots against
the proposed revision; mismatched owners or generated facts fail before writes.

Header, revision, posting graph, value-slot mutations, individual value audit
entries, principal snapshots, number and idempotency result share the existing
company transaction. Each value mutation produces a safe before/after slot audit
entry under the same journal/register event; slots have no independent version.
Apply slot changes through the shared value applier without recursive dispatch or
an internal commit. A failure after slot insert/clear/reactivation, after audit,
after posting sources or during retry persistence leaves zero effects. Replanning
uses current definition state under the writer lock. Copies of a company retain
all historical facts, slots and referenced definitions without external state.

## Form inventory and register preservation

Journal generated post/update and dedicated register entry display all applicable
custom fields in definition order, with no truncation below 45 definitions. Text,
exact decimal, date, boolean and choice controls expose their correct input types;
required/default indicators and clear controls reflect create versus update rules.
Untouched, explicitly empty text, false, zero and cleared are distinct states.
Updates do not prefill a missing value with today's default. Preserved inactive
values remain readable with captured labels. Labels and values are escaped.

Runtime metadata identifies journal_entry scope independently of list definitions
and physical transaction annotation targets. Use shared authorized reads for
current definitions and selected-revision values for editing originals. The
historical detail page displays captured labels and typed values without requiring
raw JSON. Generated CLI/HTTP/Python examples accept the same typed mapping.

Generated journal/register forms preserve attempted values and explicit field
states through preview/errors even if definitions deactivate, disappear from the
active inventory, or retire a selected choice between requests. Re-render from
the union of current controls and attempted field IDs. Unavailable attempts remain
visible with escaped captured/fallback labels and an explicit invalid/unavailable
state; a removed select option remains visibly selected as an invalid attempt.
Subsequent submission retains that exact attempt and is rejected until the user
explicitly changes or clears it. Do not substitute a new default, omit an invalid
field, or silently reinterpret its type. User-supplied presentation metadata never
grants validity or overrides server-side scope/type/authority validation.

Register translation forwards the custom-field patch to the journal service.
Compatible register edits preserve omitted values and snapshots, including
inactive values, through amount/category/split edits and no-op saves. Populated
header custom fields alone do not make a journal incompatible; all other
losslessness and unsupported-line checks remain. Calculate stays a split-total
calculator. Register query retains all financial effects without joining mutable
value slots into totals or adding custom-field filters in this increment.

Register field state survives preview/error, Restore and uncertain save recovery.
Pending requests retain their exact original wire payload, key and typed values
across reloads and definition changes; they are never rebuilt from new defaults.
Successful consecutive entries start fresh custom-field state with applicable
creation defaults. Keyboard, Tab/Enter boundaries and phone containment apply to
these controls as to the existing composer. Readonly has no write controls and
shared dispatch rejects forged writes.

## Demo and witnesses

Extend Demo Plumbing Co with journal_entry text Work order and choice Source
fields, populated journal/register examples, a custom-only correction, a clear
and reuse example, and a historical view after definition rename. Independently
assert trial balance and account nets are unchanged by custom-only corrections;
verify the new immutable source/reversal graphs and one audit event per write.

Cover every kind and exact boundary, default/required/null distinctions, inactive
preservation, wrong IDs, first use and choice retirement, revision snapshots after
master changes, refresh/no-op, stale disjoint patches, retry mismatch, injected
rollback, company copy and unchanged legacy list contracts except the explicit
required-null consistency rule. Actual Chrome verifies generated and dedicated
forms at desktop/390px, empty/false/zero/clear, more than 45 definitions, keyboard
entry with splits, and dropped-response recovery after definition changes.
