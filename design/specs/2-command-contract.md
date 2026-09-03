# Row 2 — plan

## What this row adds

The company-side half of the command contract: company audit tables with baseline entries, the versioned-write rules of blueprint 6, presence as advisory state, idempotency for creates, directives and the reason gate, the cursor-based event feed, and `company update`. It reuses the hub audit machinery of row 1, adds one hub migration, and changes dispatch in the ways listed under "Dispatch".

## Registry additions

`Command` gains: `kind` (`read`, `write`, `advisory`), `accepts_idempotency_key`, `clearable` (update commands whose fields can be set to null), `streams` (commands with a `--follow` form). Adapters render `--dry-run`, `--reason`, `--source-ref`, `--directive` only for `write`; `--idempotency-key` only where `accepts_idempotency_key`; `--clear` only where `clearable`; `--follow` only where `streams`. Verb strings are names, never behavior. `advisory` commands open the company writable, upsert `principals`, write no event, take no context options, and are not gated; `presence set` and `presence clear` are the only ones.

"Write" means a `write` command whose `apply` changed something: only then is an event recorded, an idempotency result stored, or a version bumped. A `write` command that changes nothing (rule 5 below) returns `changed_fields: []` and records nothing. Every `write` and `advisory` command upserts the actor and, when present, `on_behalf_of` into `principals`.

## Command names and scopes

`audit list`, `audit show`, `audit tail` are company-scope; `hub audit list`, `hub audit show`, `hub audit tail` are hub-scope. Both take the same filters: `--since`, `--until` (dates interpreted in the viewer's zone: the user's, else the company's, else UTC for hub scope), `--actor` (id or username), `--kind`, `--principal`, `--via`, `--command`, `--record-type`, `--record-id`, `--limit`; the input field names are `kind`, `via`, `principal` because context field names are reserved on every command. `list` pages older with `--before <seq>` and returns `next_before` (null at the end); `tail` pages newer with `--after <seq>` and returns `next_after` (null when nothing is newer). List items carry `seq`, `directive_code`, `directive_text`, `actor_name`, and `on_behalf_of_name`. `tail --follow` repeats every two seconds, prints one JSON object per event (the one documented exception to one document per command), takes the data-root lock on every poll until row 3's host exists, and exits 0 on interrupt.

Record types are singular nouns: `company_info`, `directive`, `organization`, `company`, `membership`, `user`. The generated docs enumerate them.

## Event ordering

`audit_events` in both databases gains `seq`, an integer the writer assigns as `max(seq) + 1` under the data-root lock; `hub0002` adds it to the hub table and backfills existing rows in id order. Cursors are `seq`; the event `id` stays a ULID for citation.

## Company audit

`co0002` creates `audit_events` and `audit_entries` with the hub's columns plus `seq`, and writes one `baseline` entry for the existing `company_info` row at its current version under the `migrate` event, so the baseline is distinguishable from a real create and every event field on it is truthful. Rollout writes a `create` entry for `company_info`. Snapshots of `company_info` exclude `display_name` (a copy of hub state) and `tax_id` (a secret; excluded from diffs and snapshots, shown by `company show` to members).

Dispatch writes the company event inside the company transaction before its commit for `write` commands whose `touched` has company entries; a `write` command with no company entries records no company event. For dual-database commands the order follows the source of truth for the change: registration changes (`company rename`) commit the hub first and then the copy; book changes (`company update`) commit the company first and then the hub projection. The dual-writer list is read from the registry's `writes`, not maintained by hand. A failure after the first commit is `E_PARTIAL_WRITE` with `details` naming what is durable and `request_id`, which joins the two events. Self-committing commands (`audited=True`) write their own company events.

Projection repair: every writable company open compares the hub's `legal_name` and `home_currency` with `company_info` and rewrites the hub projection from the database, so a `legal_name` update whose hub step failed converges on the next writable open regardless of rule 5.

Schema migrations: dispatch opens the hub writable without migrating, loads the actor from the columns `hub0001` guarantees, then migrates and records a `migrate` entry by the system user with `on_behalf_of` the actor; company migrations record theirs into the migrated company and upsert the system user into that company's `principals`.

## Versioned writes

`core/versioning.py` exposes `check_update(...)` for `plan` and `apply_update(...)` for `apply`, so a dry run reports exactly the conflict or merge a real run would.

Vocabulary: `changes` are the top-level input-model field names the caller set (`model_fields_set`); `changed_fields` from audit entries are folded to the same names, address columns to `address`, `legal_address`, `ship_address`; reported fields always use this vocabulary, and the docs say merges are per top-level field.

1. `expected_version` equal to current: apply.
2. Lower: every version in `(expected, current]` must have an audit entry with a derivable before-state; a missing entry is a conflict. `changed_fields` is the union of those entries' diff keys; disjoint from `changes` applies with `merged_over_versions`; otherwise `E_VERSION_CONFLICT` with `{current_version, updated_by, updated_by_name, updated_on_behalf_of, updated_on_behalf_of_name, updated_via, seconds_since_update, changed_fields}`, the principal read from the entry at the current version.
3. Higher: `E_VERSION_CONFLICT` with the same details.
4. Absent: apply; report `previous_version`, `previous_updated_by`, `previous_updated_by_name`, `previous_on_behalf_of`, `previous_on_behalf_of_name`, `previous_updated_via`, `seconds_since_previous_update`, and `recent_concurrent_activity` when the previous writer is a different actor within the company's window; the CLI warns on stderr naming both.
5. No field differs: nothing recorded, `changed_fields: []`.

`UpdateOutput{dry_run, warnings, version, changed_fields, merged_over_versions (empty on a blind write), previous_* (null on a versioned write), recent_concurrent_activity}`.

Null: the JSON shape is one on every surface. A key absent leaves the field; a key with `null` clears it. The library's attribute form passes keyword arguments as given, so `phone=None` clears and omitting it leaves. The CLI's `--clear <field>` (repeatable) translates to `null`; `--clear address` clears every child; nested leaves are spelled as their flags (`address-line1`); an unknown or non-nullable name is `E_VALIDATION`. `--interactive` cannot clear; its empty answer means unchanged, and the help says so. A partial nested address patches the children given.

`company update` takes every `company_info` column except `id`, the common fields, `display_name`, `home_currency`, and `default_chart`, the three addresses nested; admin or owner role; output adds `company_id` and `info_version`, and `company show` carries `info_version` at top level beside the registration row's `version`, with `--expected-version` documented as `info_version`. The event summary names the fields changed.

## Presence

Advisory state, never audited (blueprint 6, 7). Table `presence`: `record_type`, `record_id`, `user_id`, `interface`, `started_at`, `heartbeat_at`, primary key of the first four; `set` refreshes `heartbeat_at` and keeps `started_at`. `presence set <record_type> <record_id>` and `presence clear` need the standard role; a record type outside the enumerated list is `E_VALIDATION`; a missing record id is `E_RECORD_NOT_FOUND` with suggestions. Rows older than 90 seconds are ignored and pruned by the next `set`. `company show` and every later `show` include `editing_by: [{user_id, name, interface, since}]`, names through `principals`.

## Idempotency

Tables `idempotency_keys` in hub.db and company.db: `actor_id`, `key`, `command`, `input_hash`, `state` (`in_progress` or `done`), `request_id`, `output` (JSON), `created_at`. Keys are accepted only where the registry says: `organization new`, `company new`, `directive add`, and every later `create` and `post`. Lookup happens before `plan` in the database the command registers into: the hub for `organization new` and `company new`, the company otherwise. A `done` hit with the same command and `input_hash` returns the stored output with `idempotent_replay: true`; a different command or input is `E_IDEMPOTENCY_MISMATCH`; a row older than 30 days is ignored and replaced. For `company new`, the key is stored `in_progress` in its own hub transaction before the folder is created and set `done` with the output in the registration transaction, so a retry after `E_ROLLOUT_INCOMPLETE` finds the in-progress row and returns `E_ROLLOUT_INCOMPLETE` again with the folder named, never a second company. A dry run with a `done` key returns the stored output with `dry_run` and `idempotent_replay` true. `idempotent_replay` lives on the outputs of commands that accept keys.

## Directives and the reason gate

Table `directives`: `id`, `code` (`SI-<n>`, never reused), `text` (at most 1000 characters), `given_by`, `recorded_by`, `active`, `deactivated_at`, `deactivated_by`, common fields. `given_by` is derived: the actor when human, the principal when an agent; an agent with no principal cannot record one (`E_PERMISSION`). Commands and roles: `directive add` (standard), `directive list [--include-inactive]` (member), `directive show <directive>` (member; fields: id, code, text, given_by, given_by_name, recorded_by, recorded_by_name, active, deactivated_at, deactivated_by, deactivated_by_name, common fields), `directive deactivate <directive>` (standard). Company-scope list items carry no `access` or `role`. Selection by id or code, case-insensitively; not found carries `suggestions`; an inactive directive cited on a write is `E_DIRECTIVE_INACTIVE` naming who deactivated it and when. Hub events of dual-writers store `directive_code` (hub0002 column) beside `directive_id`.

The reason gate is infrastructure: dispatch raises `E_REASON_REQUIRED` for any `write` by an `agent` or `system` actor that carries neither `reason` nor `directive_id`; it is listed once with the infrastructure codes. Agents have no surface until row 7: the done is shown on `company update` through a dispatch-level agent context in tests, the CLI maps an OS login only to a `human` user (`E_NO_ACTOR` otherwise), and both facts go in `architecture.md`.

`Context.directive_id` carries the selector as given; dispatch resolves it and copies the id in. Context values are validated on every surface (already in row 1).

## Dispatch order

Before the lock: data root, locality, bootstrap. Under the lock: config; hub open (writable when `writes` or `kind` needs it, no migration yet); actor; hub migration and its event; company resolution and role; company open with pending-move completion, projection repair, and migration; directive resolution; reason gate; idempotency lookup; `plan`; dry-run return; `apply` with events in each database's transaction; idempotency store; `principals` upsert (inside the company transaction); config write. `dispatch.run_in_session(cmd, input, ctx, s)` runs a command inside an already-open session with the same steps except locking and opening, which is how `demo reset` applies its seed and how row 3's host will run commands.

## Demo seed

After rollout, `demo reset` runs through `run_in_session`: two `directive add` calls (`SI-1`: "When I finish a job and tell you the amount, post it and invoice the customer on file"; `SI-2`: "Attach every receipt photo I send to the matching expense"), then three `company update` calls that leave a visible history: a blind update of `phone`, a versioned update of `contact_name`, and a merged update of `website` against the earlier version. `given_by` for the demo directives is the resetting admin. No closing date; row 8 sets one with its transactions. Presence and idempotency tables are ephemeral and exempt from seeding, which section 9.3 states.

## Error matrix additions

`E_VERSION_CONFLICT` (update commands), `E_DIRECTIVE_NOT_FOUND` and `E_DIRECTIVE_INACTIVE` (company-scope writes given `--directive`), `E_IDEMPOTENCY_MISMATCH` (commands that accept keys), `E_RECORD_NOT_FOUND` (presence), `E_PARTIAL_WRITE` (dual-database writes); `E_REASON_REQUIRED` joins the infrastructure list. The CLI half of two-actor tests remaps the OS login in `config.toml` to a second human user between invocations.

## Budget

Blueprint 18's audit budget changes from 1.5 to 3 times live data, with the reason recorded there: full after-snapshots on creates and updates are kept for readability (decision D-016), and one snapshot per write of a row that stays live puts the ratio near 2 before events and indexes. The fixture is fixed: 5,000 `directive add`, then 2,500 `company update` and 2,500 `directive deactivate`, half of each versioned; the expected ratio is written before building; a miss is a design change with a stated reason, never a fixture change. Measurement uses `dbstat` per table.

## Edges

- Does not touch: lists, ledger, attachments, tokens, HTTP. `undo` is row 5.
- `core/clock.py` is the only time source; tests replace it for presence expiry, the recent-activity window, and key expiry.
- Append-only is asserted across the codebase: no module outside `core/audit.py` references an update or delete on the audit tables (a test greps for it), and the repository layer exposes no such function.
- Diffs and snapshots are redacted before diffing, so a non-admin never sees that a path changed.
- Agent-facing docs (row 4) and the MCP tool (row 9) make `expected_version` the default path for agents, since a blind write outside the window still overwrites silently by design.

## Verification the builder will perform

The suite green with: two actors updating company info (conflict on overlapping fields, merge on disjoint fields, blind-write warning within the window and none outside it, dry runs reporting the same outcomes) through the library and the CLI; a missing audit entry in the version range forcing a conflict; principal names in conflict details and previous-writer fields; replay with the same key returning the same output and writing nothing; a different input with the same key rejected; an expired key reused; the in-progress key path after a failed rollout; an agent context posting `company update` with neither reason nor directive rejected, with a directive accepted and its code and text shown by `audit list`; presence visible in `company show`, absent after expiry, and never in the audit log; `audit tail` resuming from a `seq` cursor with nothing missed; every name in company-scope outputs resolved through `principals` on a copied folder; the migration baseline entry; explicit null, `--clear`, and partial nested address updates; an unauthorized update containing `closing_date`; snapshots at the raw and compressed boundary with `tax_id` and secrets excluded; delete before-states; append-only across the codebase; a failure injected after the company commit of a `legal_name` update returning `E_PARTIAL_WRITE` and converging on the next writable open; audit filters; the budget fixture.
