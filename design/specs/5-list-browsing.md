# Row5 — existing list browsing and master details

Status: independently reviewed plan for completing existing Row5 contracts.
Artifact review is required before integration. Base0a1426f, companyco14/hub11. This extends5-lists.md and
blueprint11.1/11.15 without closing all Row5 or introducing a new accounting row.
The source assessment /tmp/bookflow-row5-remaining-assessment.md identifies the
four present gaps and distinguishes already implemented/staged capabilities.

## Complete outcome

All existing master lists expose their declared selectable columns through bounded
shared queries. Users choose actual named filters/columns and sort by a heading,
without entering identifiers or field=value syntax. Custom values of every supported
kind can be selected and filtered by stable definition identity, including false,
zero and absence. Complex master details show readable fields/child tables rather
than JSON as their primary display. Python/CLI/HTTP/MCP share the same typed read
contract; the browser is a thin metadata-driven consumer.

No posting or storage migration. Keep existing authorization, forms, activation,
contacts/owned children, record versions, audit/annotations and reference pickers.
Do not implement hierarchy cascade/tree centers, purchasing/AP/stock behavior,
provider integrations, spreadsheet editing/import/export or new permissions here.
Those remain required in their owning passes; this boundary does not label them
complete. No shared-builder checkout edits or independent accounting demo restart.

## Bounded query and metadata contract

Extend the existing QueryInput used by master-list providers with optional ordered
`columns: list[str] | None` and `custom_filters` described below. Omitted columns
retain existing summary/reference behavior and output fields. Requested columns are
validated against a concrete per-noun public projection catalog; unknown/duplicate
columns reject E_LIST_FILTER with allowed metadata discovery instructions. Maximum
64 selected columns; nonempty if supplied. A reference projection retains its exact
id/version/label/active shape; combining reference projection with custom columns
rejects explicitly. Existing ordinary filters and query parameters retain behavior.
Optional `ids` restricts the shared query to 1–64 stable record IDs, intersecting
all other criteria under ordinary current authority. Omission retains the original
response and fingerprint. Retained reference filters use these bounded reference
reads for current labels/activity instead of full shows; predicates keep their IDs.

Concrete built-in column identifiers follow declared show-output names and existing
aliases. Resolve declaration tokens such as $stored, $custom and grouped accounts
into explicit public descriptors, never expose tokens literally or publish arbitrary
SQL columns. The catalog is an explicit allowlist of public output fields, excluding
internal provenance/credential/storage fields absent from the declared list contract.
Money renders with its exact decimal amount/currency; quantities/custom numbers
remain exact strings and booleans remain typed. Missing custom values are null,
not false, zero, empty text or a dynamically substituted current default. Stable
record id/version/active remain in each row for navigation/concurrency even if not
visible selected columns. Selected values must be fetched in bounded batch reads;
no one-show-per-row loop or fallback to an unbounded list response.

Expose `<noun> query options` for each master noun as an ordinary authorized company
read, with typed column/filter/sort descriptors and default column order. Descriptors
include stable key, user label, value kind, nullable/read-only facts, reference noun
where applicable, allowed operators and sort eligibility. Dynamic custom descriptors
use column key `custom:<definition-id>`, definition ID, label, kind, active state and
owner target. Discovery is paged default50/max200 with stable key ordering and the
existing snapshot/cursor discipline; complete defaults are identified independently
of a metadata page. No metadata read writes a key, audit entry or cache. Reuse shared
registry/help/schema generation; don't add an MCP transport tool or a second policy.

Custom criteria are a bounded list (max32), combined with each other and ordinary
filters by AND. Each criterion identifies `definition` by stable ID and declares its
kind; reject wrong owner target, unknown definition, kind mismatch, malformed value
or unsupported operator before query execution. Preserve input omission distinctions.
Typed variants: text eq/ne/contains with string value; number eq/ne/lt/lte/gt/gte
with exact canonical decimal-string value; date same comparisons with ISO date;
bool eq/ne with strict boolean; choice eq/ne with stable choice ID. A separate
presence variant uses is_missing/is_present with no value. Number comparison must
be numeric, not lexical. ne excludes missing values; is_missing selects absence.
Empty text is a present stored value. false and0 are distinct from absence. Choice
labels are display only; renames cannot change matches. Unknown operator/kind/value
returns a structured validation error with the offending definition/field.

Definition deactivation preserves values in search, selected columns and explicit
ID-based filtering as blueprint11.15 requires. Metadata labels inactive definitions
and choices honestly; active-only chooser is a user display option, not erasure of
an explicit selected criterion. Definition/choice rename retains IDs; current labels
may change while captured record values remain unchanged. Existing text search also
finds retained values of inactive definitions. Read filters do not reactivate them.
Definitions/choices and owner custom values participate in query/metadata stale
fingerprints alongside the existing scope/data snapshot; changed criteria/column
order invalidate a previous cursor. Reordering display columns starts a fresh query
preserving other controls. Don't silently reuse a cursor for a different projection.

Provider outputs for requested projections carry the requested values with typed
column descriptors or an equally explicit typed column-to-kind mapping; preserve
legacy default output shape for callers that omit new fields. Validate every master
noun's declarations against the provider, not just customer/vendor examples.

## Human browsing and record details

Replace the primary raw filter/columns boxes with labeled chooser controls generated
from those descriptors. Add/Remove and Move Up/Down columns, reset defaults, typed
filter value editors (including false/true and reference/choice lookup), separate
missing/present controls, clear filters and visible active criteria. Retain existing
URL forms and repeated legacy filter inputs as backward-compatible links; decode
and display them accurately. Unknown URL criteria show an actionable error rather
than being silently dropped. User changes persist in the URL; no financial or
settings write merely to sort/filter/change columns.

Headings for eligible sort fields are links/buttons showing current direction;
non-sortable columns do not promise sorting. Sorting/filter/inactive/column changes
clear pagination cursor while retaining the other chosen state. Provide keyboard
and touch controls, clear empty/no-match states and full count versus page count.
Keep query paging and explicit stale restart. Large metadata choices load pages;
large data tables do not download all records just to populate a chooser.

Retain customer workspace behavior. Extend readable grouped details to all complex
master types, including vendor contacts/expense defaults, employees, item member and
vendor profiles, units/conversions, price-level entries and custom definitions/values.
Render child arrays as labeled rows/tables and nested fields as named groups with
stable links where authorized. Money/quantities/booleans/dates/reference labels use
the same exact formatters and inactive labels; raw JSON is not the main read path.
Keep stable child IDs available for audit/agent continuation without requiring the
human to interpret them. Treat all stored text as text; preserve existing escaping.
Large child collections remain navigable and contained; no truncated silent subset.

## Implementation ownership and verification

Sole builder in /tmp/bookflow-list-browsing. Parent alone integrates after independent
artifact review. Source areas: company list/query providers and metadata, query
registration, workbench list/detail templates/helpers and generated docs/tests.
Coordinate shared pages.py/registry seams with the frozen payment candidate and
MCP builder through parent; do not edit their moving trees or adapters. Preserve
all existing read authorization; pending hub-admin policy is outside this change.

Required witnesses: customer email and vendor account_number beyond defaults;
concrete projections across all master definitions; all five custom kinds and
presence/ne semantics with false/0/empty/absent; inactive/renamed definitions and
choices; wrong-owner/kind/unknown columns; omission-compatible reference/summary
responses; complete metadata/data paging and stale behavior; exact >2^53 money in
browser, no Number rounding; no per-row detail query growth. Retain the existing
10,000-record bounded-query performance witness and thresholds, measure SQL counts
and relevant data size. No threshold relaxation or smaller replacement fixture.

Actual1280/390 browser journeys choose/add/reorder/reset columns, filter false/zero/
missing/choice, sort headings while preserving state, navigate pages/restart stale,
and inspect representative complex details without JSON. Exercise keyboard-only
and touch-sized controls, existing reference return/edit/annotation navigation and
read-only authority fixtures. Independent fixtures use disposable roots only.
Generated schemas/docs must match; extend ordinary HTTP/library read parity and
MCP discoverability manifests for new commands, with actual MCP acceptance when
its reviewed adapter is available. No registry-only claim of agent usability.

Keep old tests/assertions owning existing behavior, adapting only raw-control
expectations superseded by this concrete human contract. Add independent behavioral
checks for the new outcomes; declaration inventories alone do not prove reachability.
Builder records phase elapsed time, first usable result, measured uncertainty and
remaining estimate; offer frozen checkpoints without claiming whole Row5 completion.
