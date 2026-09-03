# Row 2 — plan

## What this row adds

The company-side half of the command contract: company audit tables, the versioned-write rules of blueprint 6, presence, idempotency, directives and the reason requirement, the cursor-based event feed, and `company update`. Everything reuses the hub audit machinery of row 1; nothing in row 1's hub path changes shape.

## Modules

```
src/bookflow/
  company/schema.py            + audit_events, audit_entries (same columns as hub), presence, idempotency_keys, directives, sequences (directive codes)
  storage/company_migrations/  co0002: the tables above
  core/audit.py                write_event(db, ...) and readers generalized over hub or company (moved from hub/audit.py, which re-exports)
  core/versioning.py           apply_update(): versioned, blind, and disjoint-field merge per blueprint 6.2-6.3; UpdateOutput fields
  core/idempotency.py          lookup and store per (database, actor_id, key); replay detection in dispatch
  core/dispatch.py             + idempotency check before plan and store after apply; + reason/directive gate for ledger commands; + directive resolution; + company audit event in the company transaction
  hub/schema.py                + idempotency_keys (hub commands)
  company/directives.py        add, list, show, deactivate; code allocation SI-<n> from sequences
  company/presence.py          set, clear, live_for(record); 90 s expiry
  commands/company_cmds.py     + company update, directive add/list/show/deactivate, presence set/clear, audit list/show/tail (company scope)
  commands/hub_cmds.py         + audit tail (hub scope)
  adapters/cli/app.py          + --directive, --idempotency-key on writes; --follow on audit tail; expected_version as an ordinary option on update commands
  client.py                    + directive=, idempotency_key= keywords
```

## Company audit

`co0002` creates `audit_events` and `audit_entries` in company.db with exactly the hub's columns. Dispatch writes the event inside the company transaction for commands whose `writes` include `company`, and inside the hub transaction for hub writes; a command writing both records one event in each database, each describing the records that live there. `before` for updates is derived from the previous entry's `after` for the same record; deletes store `before`. Snapshots are model-filtered (secrets excluded, derived fields excluded) and blob-prefixed as in row 1.

`audit list`, `audit show`, and `audit tail` become available in company scope with the same inputs and outputs as the hub versions; company scope needs no visibility filter beyond membership. `audit tail --after <event_id> --limit <n>` returns events with ids greater than the cursor in id order and `next_cursor`; the CLI's `--follow` repeats the call every two seconds until interrupted, printing one JSON object per event under `--json`.

## Versioned writes

`core/versioning.py` exposes `apply_update(db, table, current_row, changes, *, expected_version, ctx, window_seconds) -> (new_row, UpdateMeta)`:

1. `expected_version` given and equal to `current_row["version"]`: apply.
2. `expected_version` given and lower: collect `changed_fields` from the record's audit entries with `version_after` in `(expected_version, current]`, as the union of their diff keys. If `changed_fields` and `changes` are disjoint, apply on the current row and set `merged_over_versions` to the intervening versions; otherwise raise `E_VERSION_CONFLICT` with `details` `{current_version, updated_by, updated_by_name, updated_via, seconds_since_update, changed_fields}`.
3. `expected_version` absent: apply; set `previous_version`, `previous_updated_by`, `previous_updated_by_name`, `previous_updated_via`, `seconds_since_previous_update`, and `recent_concurrent_activity` when the previous writer is a different actor and the interval is within the company's `recent_activity_window_seconds`.
4. `expected_version` higher than current: `E_VALIDATION` on `expected_version`.

Nested fields such as `address` count as one field each for the disjoint test. Every update output extends `UpdateOutput{dry_run, warnings, version, merged_over_versions, previous_version, previous_updated_by, previous_updated_by_name, previous_updated_via, seconds_since_previous_update, recent_concurrent_activity}`, and the CLI prints a warning line on stderr when `recent_concurrent_activity` is true.

`company update` is the first user: every `company_info` field except `home_currency`, `display_name` (that is `rename`), `default_chart`, and `is_demo`; `closing_date` requires the admin or owner role, other fields the standard role. Changing `timezone` or `report_basis` is audited like any field.

## Presence

Table `presence` in company.db: `record_type`, `record_id`, `user_id`, `interface`, `started_at`, `heartbeat_at`, primary key `(record_type, record_id, user_id)`. `presence set <record_type> <record_id>` inserts or refreshes; `presence clear` removes; both are writes with audit events of action `presence`. Rows whose `heartbeat_at` is older than 90 seconds are ignored on read and removed by the next `presence set` on any record. `company show` and every later `show` include `editing_by: [{user_id, name, interface, since}]`. Presence never blocks a write.

## Idempotency

Tables `idempotency_keys` in hub.db and company.db: `actor_id`, `key`, `command`, `request_id`, `output` (JSON), `created_at`, primary key `(actor_id, key)`. In dispatch, when the context carries a key: before `plan`, look the pair up in the database the command writes (company for company scope, hub otherwise); on a hit with the same command name return the stored output with `idempotent_replay: true` and write nothing; on a hit with a different command raise `E_IDEMPOTENCY_MISMATCH`. After a successful `apply`, store the output in the same transaction. Keys older than 30 days are deleted opportunistically by the next store. Dry runs neither look up nor store.

## Directives and the reason gate

Table `directives` in company.db: `id`, `code` (`SI-<n>` from a per-company sequence), `text` (at most 1000 characters), `given_by` (a human user id), `recorded_by`, `active`, common fields. Commands: `directive add --text [--given-by <user>]` (given_by defaults to the actor when human and to `on_behalf_of` when an agent), `directive list [--include-inactive]`, `directive show <directive>`, `directive deactivate <directive>`. A directive is selected by id or code, case-insensitively.

`Command` gains `ledger: bool`. Dispatch, after actor resolution: when the actor kind is `agent` or `system` and the command is `ledger`, require `reason` or `directive_id`, else `E_REASON_REQUIRED`. A `directive_id` that is not active in the selected company is `E_DIRECTIVE_NOT_FOUND` for every actor. The audit event stores the directive id; `audit list` and `audit show` render `directive_code` and `directive_text` beside `reason`. No row 2 command is `ledger`; the gate is exercised by a test-registered command, and row 8's ledger commands set the flag.

## Dispatch changes

Order under the lock after actor resolution: directive resolution; reason gate; idempotency lookup; plan; on dry run return; apply with the audit events in each database's transaction; idempotency store; config write. The `Applied.touched` of company writes feeds the company event. Commands that return `audited=True` write their own events in both databases as needed.

## CLI and client

`--directive` and `--idempotency-key` are defined on every writing command; `--expected-version` is an ordinary input field on update commands. `audit tail` gains `--follow`. The client accepts `directive=` and `idempotency_key=` keywords on `run` and the attribute form.

## Demo seed

The seed gains two directives (`SI-1`: "When I finish a job and tell you the amount, post it and invoice the customer on file"; `SI-2`: "Attach every receipt photo I send to the matching expense") and a `closing_date` of the prior year end.

## Error matrix additions

`E_VERSION_CONFLICT` (update commands), `E_REASON_REQUIRED` (ledger commands, agent or system actor), `E_DIRECTIVE_NOT_FOUND` (any command given `--directive`), `E_IDEMPOTENCY_MISMATCH` (any write given a key), `E_PRESENCE_INVALID` (presence on a record type that does not exist). Each is produced through the library and the CLI in the matrix test.

## Edges

- Does not touch: lists, ledger, attachments, tokens, HTTP. Row 1's hub audit code moves to `core/audit.py` with `hub/audit.py` re-exporting, so no caller changes.
- The company `audit_events` table gets no visibility filter; membership on the company is the gate.
- Budget: a test writes 10,000 `company update` calls, half blind and half versioned, and asserts the audit tables' size is at most 1.5 times the live tables' size, measured with `dbstat` when available and page counts otherwise.

## Verification the builder will perform

The suite green with: two sessions updating company info (conflict on overlapping fields, merge on disjoint fields, blind-write warning within the window, no warning outside it, all through the library and the CLI); replay with the same key returning the same output and writing nothing; a different command with the same key rejected; an agent-kind fixture user posting the test ledger command with neither reason nor directive rejected, with a directive accepted and shown in `audit show`; presence visible in `company show` and gone after expiry; `audit tail` resuming from a cursor with nothing missed; the footprint budget.
