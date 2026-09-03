# Row 2 — plan

## What this row adds

The company-side half of the command contract: company audit tables with baseline entries, the versioned-write rules of blueprint 6, presence, idempotency, directives and the reason gate, the cursor-based event feed, and `company update`. It reuses the hub audit machinery of row 1 and adds one hub migration for idempotency keys.

## Modules

```
src/bookflow/
  company/schema.py            + audit_events, audit_entries (the hub's columns), presence, idempotency_keys, directives, sequences
  storage/company_migrations/  co0002: the tables above; writes a baseline `create` entry for the existing company_info row at its current version
  storage/hub_migrations/      hub0002: idempotency_keys
  core/audit.py                write_event(db, ...), readers, filters; hub/audit.py re-exports; migrate entries per blueprint 7
  core/versioning.py           apply_update(): versioned, blind, and disjoint-field merge; conflict detection callable from plan
  core/idempotency.py          lookup and store per (database, actor_id, key)
  core/clock.py                now(): the one time source; tests replace it
  core/dispatch.py             + context value validation; directive resolution; reason gate; idempotency lookup before plan and store after apply; principals upsert on every company write; company audit event inside the company transaction
  company/directives.py        add, list, show, deactivate; SI-<n> codes from sequences, never reused
  company/presence.py          set, clear, live_for(record)
  commands/company_cmds.py     + company update, directive add/list/show/deactivate, presence set/clear, audit list/show/tail (company scope)
  commands/hub_cmds.py         hub audit list/show/tail (renamed from row 1's audit list/show; tail added)
  adapters/cli/app.py          + --directive on company-scope writes; --idempotency-key on create and post commands; --clear <field> on update commands; --follow on tail
  client.py                    + directive=, idempotency_key=, clear=[...] keywords
```

## Command names and scopes

`audit list`, `audit show`, and `audit tail` are company-scope commands: the books' log. The hub log is `hub audit list`, `hub audit show`, `hub audit tail`. Row 1's hub commands are renamed in row 1's closing fixes so no name ever carries two scopes. Both families take the same filters: `--since`, `--until`, `--actor`, `--actor-kind`, `--on-behalf-of`, `--interface`, `--command`, `--record-type`, `--record-id`, `--limit`. `list` pages older with `--before <event_id>` and returns `next_before`; `tail` pages newer with `--after <event_id>` and returns `next_after`. Event ids are ULIDs generated under the data-root lock, so id order is commit order. `tail --follow` on the CLI repeats every two seconds and prints one JSON object per event, the one documented exception to one-document-per-command.

## Company audit

`co0002` creates `audit_events` and `audit_entries` in company.db with the hub's columns and writes a `create` baseline entry for `company_info` at its current version so every later version has a derivable before-state. Rollout writes the same `create` entry for new companies. Company-audit record types are table names: `company_info`, `directive`, `presence` never. `company_info` snapshots exclude `display_name`, which is a copy of hub state, so it never appears as a phantom diff.

Dispatch writes the company event inside the company transaction, before its commit, for commands whose `writes` include `company`; the hub event inside the hub transaction for hub writes. A command writing both records two events joined by `request_id`, company first; a hub failure after the company commit is reported as `E_INTERNAL` with the company event already durable, which is the honest state. Schema migrations write a `migrate` entry into the migrated database after the migration, actor the system user, `on_behalf_of` the actor whose command triggered it, and the hub projection is updated in the same command; row 1's `upgrade` events change to this shape.

Every company write upserts the actor and, when present, `on_behalf_of` into `principals` in the same transaction; every company-scope name in an output resolves through `principals`, never through hub `users`.

## Versioned writes

`core/versioning.py` exposes `check_update(db, table, current_row, changes, expected_version, window_seconds) -> UpdateMeta` for `plan` and `apply_update(...)` for `apply`, so a dry run reports exactly the conflict or merge a real run would.

Vocabulary: `changes` are the input-model field names the caller set, read from `model_fields_set`; `changed_fields` from audit entries are folded to the same vocabulary, address columns to `address`, `legal_address`, `ship_address`. Rules:

1. `expected_version` equal to the current version: apply.
2. `expected_version` lower: every version in `(expected, current]` must have an audit entry with a derivable before-state; if any is missing, conflict. Otherwise `changed_fields` is the union of those entries' diff keys; if disjoint from `changes`, apply on the current row with `merged_over_versions`; else `E_VERSION_CONFLICT` with `{current_version, updated_by, updated_by_name, updated_via, seconds_since_update, changed_fields}`.
3. `expected_version` higher than current: `E_VERSION_CONFLICT` with the same details, since a restore from backup makes this legitimate.
4. Absent: apply; report `previous_version`, `previous_updated_by`, `previous_updated_by_name`, `previous_updated_via`, `seconds_since_previous_update`, and `recent_concurrent_activity` when the previous writer is a different actor within the company's window. The CLI prints a warning on stderr naming the previous writer and, when set, `on_behalf_of`, so an owner sees that it was their own agent.
5. No field differs: no version bump, no event, output `changed_fields: []`.

`UpdateOutput{dry_run, warnings, idempotent_replay, version, changed_fields, merged_over_versions (empty list on a blind write), previous_* (null on a versioned write), recent_concurrent_activity}`.

Clearing a field: the library and HTTP send `null`; the CLI has `--clear <field>` (repeatable) on every update command, and `--interactive`'s empty answer means unchanged. Dependent defaults apply only at rollout; an update to `address` or `fiscal_year_start_month` changes nothing else.

`company update` takes every `company_info` column except `id`, the common fields, `display_name`, `home_currency`, and `default_chart`, as top-level fields with the three addresses nested; it requires the admin or owner role per blueprint 4.3, `closing_date` included. Output adds `company_id`; the event summary names the fields changed.

## Presence

Presence is advisory state, not a record and not a write for audit purposes; blueprint 6 and 7 say so. Table `presence`: `record_type`, `record_id`, `user_id`, `interface`, `started_at`, `heartbeat_at`, primary key of all but the timestamps. `presence set <record_type> <record_id>` inserts or refreshes; `presence clear` removes; neither writes an event; both need the standard role. Rows older than 90 seconds are ignored and pruned by the next `set`. A record type that does not exist is `E_VALIDATION`; a record id that does not exist is `E_RECORD_NOT_FOUND` with `details.suggestions`. `company show` and every later `show` include `editing_by: [{user_id, name, interface, since}]`.

## Idempotency

Tables `idempotency_keys` in hub.db (hub0002) and company.db: `actor_id`, `key`, `command`, `request_id`, `output` (JSON), `created_at`. The output is stored, not a result id, because a replay must return what the caller would have seen without re-running anything. Keys are accepted on `create` and `post` commands and on `company new` and `organization new`, per blueprint 6.5. In dispatch, when the context carries a key: before `plan`, look it up in the database the command writes; a hit with the same command returns the stored output with `idempotent_replay: true` and writes nothing; a hit with a different command is `E_IDEMPOTENCY_MISMATCH`. After `apply`, the output is stored in the same transaction, or in a final transaction dispatch opens for commands that commit their own. A dry run with a used key returns the stored output with `dry_run` and `idempotent_replay` true. Keys older than 30 days are deleted by the next store. `idempotent_replay` lives on `WriteOutput`.

## Directives and the reason gate

Table `directives`: `id`, `code` (`SI-<n>`), `text` (at most 1000 characters), `given_by`, `recorded_by`, `active`, `deactivated_at`, `deactivated_by`, common fields. `given_by` is derived: the actor when human, the token's principal when an agent; an agent with no principal cannot record a directive (`E_PERMISSION`). Commands: `directive add --text`, `directive list [--include-inactive]`, `directive show <directive>`, `directive deactivate <directive>` (the giver, an admin or owner, or a hub admin). Selection is by id or code, case-insensitively; not found carries `details.suggestions`; an inactive directive cited on a write is `E_DIRECTIVE_INACTIVE` with who deactivated it and when. `audit show` renders `directive_code`, `directive_text`, `given_by_name`, and `recorded_by_name`.

`Context.directive_id` carries the selector as given by the adapter; dispatch resolves it and copies the id in. `Command` gains `ledger: bool`. After actor resolution, when the actor kind is `agent` or `system` and the command is `ledger`, `reason` or `directive_id` is required, else `E_REASON_REQUIRED`. No row 2 command is `ledger`; the gate is exercised by a test-registered command and documented so an agent knows which commands demand a reason. Agents have no surface until row 7; the fixture user of kind `agent` stands in, `on_behalf_of` is null until tokens exist, and both facts are recorded in `architecture.md`.

Context values are validated at dispatch on every surface: `reason` at most 140 characters, `source_ref` 512, `idempotency_key` 128, each failure `E_VALIDATION` naming the context field.

## Demo seed

`demo reset` applies the seed through commands after rollout: `company update --closing-date 2025-12-31` and two `directive add` calls (`SI-1`: "When I finish a job and tell you the amount, post it and invoice the customer on file"; `SI-2`: "Attach every receipt photo I send to the matching expense"), all audited with the resetting hub admin as actor.

## Error matrix additions

`E_VERSION_CONFLICT` (update commands), `E_REASON_REQUIRED` (ledger commands, agent or system actor), `E_DIRECTIVE_NOT_FOUND` and `E_DIRECTIVE_INACTIVE` (company-scope writes given `--directive`), `E_IDEMPOTENCY_MISMATCH` (commands that accept a key), `E_RECORD_NOT_FOUND` (presence). Each is produced through the library and the CLI in the matrix test; the CLI half of two-actor tests remaps the OS login in `config.toml` to a second user between invocations, which is the row 7 `user add` fixture in another form.

## Edges

- Does not touch: lists, ledger, attachments, tokens, HTTP.
- Row 1's hub `audit list` and `audit show` are renamed `hub audit list` and `hub audit show` in row 1's closing fixes; this row adds `hub audit tail`.
- `core/clock.py` is the only time source from this row; tests replace it to cover presence expiry, the recent-activity window, and key expiry inside the suite budget.
- Budget: a fixture creates 5,000 directives and applies 5,000 `company update` and `directive deactivate` writes, half versioned; the test measures audit tables against live tables with `dbstat` and fails when the ratio exceeds 1.5.

## Verification the builder will perform

The suite green with: two actors updating company info (conflict on overlapping fields, merge on disjoint fields, blind-write warning within the window and none outside it, dry runs reporting the same outcomes) through the library and the CLI; a missing audit entry in the version range forcing a conflict; replay with the same key returning the same output and writing nothing; a different command with the same key rejected; an agent-kind fixture user posting the test ledger command with neither reason nor directive rejected, with a directive accepted and shown in `audit show` with its text; presence visible in `company show`, absent after expiry, and never in the audit log; `audit tail` resuming from a cursor with nothing missed; every name in company-scope outputs resolved through `principals` on a copied folder; the footprint budget.
