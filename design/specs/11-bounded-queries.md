# 11 — Bounded company queries

## Public contract

Each of the 20 primary company list nouns adds a registry-generated `query` read command. Existing `list` remains complete, full-record compatibility enumeration, explicitly documented as potentially expensive. `show` is unchanged. Browser lists and pickers use `query` in the browser package that follows; no existing caller silently receives fewer fields or records.

Query input: existing query/include_inactive/filter/sort/direction semantics, plus strict integer `limit` (default 50, 1–200), optional `cursor`, and `projection` (`summary` default or `reference`). Output: typed projection, typed items, `count` (items on this page), nullable `next_cursor`. No mandatory total-count query. Reference items contain id, version, human-readable label and active. Summary models declare bounded scalar/list-column fields and labels from authoritative noun metadata; they exclude full contacts/addresses, child histories, item members, vendor profiles and custom-value collections. Summary fields must agree with the corresponding show projection, including inherited and role-redacted values.

## Selection and continuation

The shared kernel builds SQL selection, filters and deterministic ordering before materialization. Every order ends in ascending stable id. Fetch limit+1 for continuation. Query projection is performed directly or with fixed-count batched queries, never by calling list/show once per row.

A bounded, versioned opaque cursor contains company id, noun, normalized query-contract fingerprint, company audit high-water sequence, and next offset. Reject oversized/malformed or scope/contract-mismatched cursors with a validation error. Reauthorize every request; a cursor is never authority. Compare the sequence and execute the page inside the same read snapshot. An audited company change invalidates continuation with an explicit stale-query error and restart advice; unaudited presence does not. Offset is internal, not caller supplied; no cross-request immutable snapshot is implied. Count/response memory is bounded even on later pages.

Employee `profile_complete` filters and ordering use one SQL predicate generated from the company's AND-of-OR required paths. Read settings once. Builtins map to columns; custom requirements use applicable active values with exact population semantics (false and zero are populated). Preserve public role masking, including tax suffix visibility, before calculating completeness. Correct the prior descending ID tie-break. Custom-definition filtering/search over scopes/choices likewise moves into SQL before pagination; define canonical scope ordering.

Shared reference metadata supplies readable labels and eligibility. Customer/job summaries preserve effective inheritance through shared domain expressions. Accounts respect account-label settings. Item/price/unit summaries avoid aggregate collection caches. Provider-specific query code remains in the company/command layer, not adapters.

## Verification and integration

All nouns: empty/page/full-page/continuation, equality of returned fields with show, stable duplicate/null/descending sorting, unchanged compatibility list beyond 200 records, invalid/stale/cross-company cursors, and redaction. Employee matches beyond page one and custom false/zero/missing/blank/suffix roles; custom definition scope/choice search beyond page one. SQL-count witnesses compare 10 and 200 returned records; no per-row queries. A warmed local-SSD host with 10,000 customers measures first summary/reference pages and broad search separately against 100 ms; full legacy enumeration is not that interactive budget.

CLI/HTTP/docs derive from the registry; add query to lazy loading intentionally without loading unrelated noun modules on cold startup. Refresh generated references and examples after providers settle; retain schema migration and full-record tests. Query correctness is reviewed at the combined artifact boundary. No production schema migration, new frontend framework, external service, or changed accounting fields.
