# Bookflow concepts

## Commands are the product contract

Bookflow exposes named commands with declared input and output models, scope, write behavior, required role, capability, feature gate, and stable error codes. The command registry is authoritative. Available adapters call the same command kernel; they do not reimplement business rules.

A command is either hub-scoped or company-scoped. Hub commands operate on the registry and do not need a selected company. Company commands resolve one visible company before they open its database. `init`, `serve`, `company use`, and documentation generation are local operations rather than HTTP routes.

The Python API returns JSON-shaped dictionaries and raises `BookflowError`. The CLI can render those dictionaries for people or return the same shape with `--json`. Ordinary HTTP commands use JSON request and response bodies. Commands with a registry transfer descriptor use a separate raw binary route and external Python streams; see [binary transfers](transfers.md). The workbench invokes the same commands through the host. Adapter-only concerns such as CLI formatting, HTTP authentication, and browser form rendering do not change command validation or authorization.

## Company selection

For the CLI and Python client, an explicit company argument wins, then `BOOKFLOW_COMPANY`, then the current operating-system user's saved default. A selector can be a company ULID, `Organization/Company`, or an unambiguous display name. Ambiguous names are errors; Bookflow never guesses.

HTTP company routes carry the company ULID in `/companies/{company_id}/...`. If `X-Bookflow-Company` is also present, it must be the same ULID after case normalization. A caller only sees companies permitted to its authenticated identity; a missing company and an inaccessible company have the same public result.

## Context belongs to the caller and adapter

Command input contains business fields. Execution context contains actor, interface, client, session, request, company, reason, directive, source reference, and idempotency metadata. Adapters construct context; context fields in an HTTP JSON body are rejected.

The host derives the actor and any principal from the bearer or browser session. A caller cannot select `actor_id` or `actor_kind`. HTTP callers provide optional context with `X-Bookflow-Reason`, `X-Bookflow-Directive`, `X-Bookflow-Source-Ref`, `Idempotency-Key`, `X-Bookflow-Client-Name`, and `X-Bookflow-Client-Version`. A write by an agent or system identity requires a reason or an active directive.

## Credentials and agent authority

Human bearer and browser credentials authenticate an active human user. Agent
credentials bind one active human principal from the agent's assigned set and the
current unsuspended authority epoch. Authentication rejects revoked or expired
tokens, inactive identities, revoked assignments, suspension and stale epochs.
`token issue` validates the same authority and stores the principal and epoch
alongside the token hash. The secret is returned once.

Upgrade suspends existing agents, revokes their credentials and records that
conversion atomically in hub audit history. Human credentials remain valid.
Agent creation, assignment and reauthorization commands are not yet exposed;
legacy agent access remains suspended until that workflow is available. Full
capability resolution and execution/publication revocation fences are subsequent
identity integration work.

## Dry runs and retry safety

Durable write commands accept dry-run execution. A dry run performs selection, authorization, validation, directive resolution, conflict checks, and planning, then returns the planned output with `dry_run: true` without committing the business change or its audit event. Presence is advisory state rather than a durable audited business write.

Only commands whose reference says they accept an idempotency key may use one. For 30 days, repeating the same key as the same actor with the same command, validated input, and company returns the stored result with `idempotent_replay: true`. Reusing the key for different work returns `E_IDEMPOTENCY_MISMATCH`.

## Bounded list queries

Use each primary company list noun's `query` command for interactive browsing. It returns typed summary rows by default; `projection=reference` returns only stable id, version, readable label and active state for selection controls. `limit` is an integer from 1 to 200, default 50. Search, filters and sorting apply before pagination. `show` returns the complete record, and legacy `list` remains complete enumeration with full records rather than a bounded page.

A query result contains `projection`, `items`, `count` and `next_cursor`. Count is the number of returned items, not a total across all pages. Pass the cursor with the same company, permissions and query arguments for the next page. A company write invalidates an earlier continuation with `E_QUERY_STALE` (HTTP 409), even when it changes another list. Restart without the cursor and discard accumulated pages; do not combine stale and fresh pages. Presence updates do not invalidate query pages. A cursor never grants access.

## Versions and concurrent updates

Mutable records carry an integer version. Read the version from the corresponding show command and send it as the update command's `expected_version`.

- If the expected version is current, a changing write advances the version.
- If it is stale and every intervening version changed other top-level fields, Bookflow merges the update and reports `merged_over_versions`.
- If an intervening version changed an overlapping field, or the history cannot prove disjointness, Bookflow returns `E_VERSION_CONFLICT` and writes nothing.
- Omitting `expected_version` is a blind write. It can succeed but reports previous-writer metadata and a recent-activity warning when applicable.
- A no-op changes no version and creates no audit event.

Nested addresses merge as top-level fields: two changes within the same address overlap. After a conflict, read the record again, reconsider the desired change, and retry with the fresh version.

## Audit, directives, and presence

Successful durable mutations append audit events in the database that owns the changed record. Events identify the command, actor, optional principal, interface, client, session, request, reason, directive, source reference, summary, and touched record versions. Audit reads do not themselves create audit events. List pages move backward with `before`; tail polling moves forward with `after`. A first tail request without `after` returns no existing events and reports the newest visible sequence as `high_water`. When `after` is supplied, `high_water` echoes that lower-bound cursor, `next_after` is the last returned sequence, and an empty result has `next_after: null`. The HTTP event feed uses the same filters and resumes from `Last-Event-ID`.

A directive is a company-scoped standing instruction with a stable code such as `SI-3`. Active directives can be cited by a company write instead of repeating a reason. Deactivation preserves history and prevents later citation. Audit events snapshot the directive code and expose its text from the directive record.

For bounded audit scanning, pass `scan_limit`. That mode examines at most the smaller of `limit` and `scan_limit` visible candidate events before applying filters; `scanned_count` reports that work, and `scan_more` says more candidates remain. `next_after` advances across nonmatches even when `items` is empty. Continue with it while `scan_more` is true. The HTTP stream uses this mode in 100-candidate batches, reauthorizes and releases its snapshot between batches, and holds no reader while idle.

Presence says that a user is editing a company record. It expires, is advisory, does not block writes, and is not part of the audit trail.

## Identity, roles, and isolation

The first local initialization maps the operating-system login to a human hub administrator. HTTP clients authenticate with a bearer token or a browser session. Bearer secrets are shown only when issued and are stored as hashes. Public agent-user and principal administration are not part of the current command set, so a bearer issued by the current bootstrap flow represents its human issuer.

Company access is role-based. `member` can read, `standard` adds ordinary record work, and `admin` adds company administration; owners and hub administrators satisfy the applicable company checks. The registry names the required role and capability for each command. Hub and organization authorization is checked before data is exposed. Non-administrators do not receive local storage paths, and inaccessible records are not distinguishable from nonexistent ones where that distinction would reveal another tenant.

## Exact money

A money value uses an ISO 4217 currency and integer `minor_units`; binary floating-point amounts are rejected. Its JSON form includes the exact integer, currency, and a decimal-string `amount`, for example `{"minor_units": 1250, "currency": "USD", "amount": "12.50"}`. Currency metadata determines the permitted decimal places. Too much precision returns `E_AMOUNT_PRECISION`.

## Local storage and schema revisions

A data root contains the hub database, configuration, organization and company directories, backups, and trash. Each company owns a separate SQLite database. Bookflow refuses network filesystems because its correctness depends on local filesystem and SQLite locking semantics. A running host serializes mutations through one writer while allowing independent reads, and compatible local CLI/Python calls hand work to that host.

Every read uses one consistent SQLite snapshot per opened database; hub and company snapshots are not a global atomic snapshot. Writers use WAL with full commit synchronization. Checkpoints reclaim/reuse WAL space; they are not the acknowledgement durability boundary. Stop the host cleanly before copying a complete company folder, and never discard its WAL files while open or after interrupted shutdown.

Folder moves temporarily refuse new readers and wait up to five seconds for admitted readers to close. An admission conflict or drain timeout returns retryable `E_DB_BUSY`; a drain timeout leaves folders untouched. Ordinary record updates remain concurrent with readers. Shutdown refuses new reader and writer work and retains the root lock until admitted work has drained and database handles have closed.

Settings-file changes commit a recoverable intent with their hub audit event, then durably replace `config.toml`. If publication fails, `E_PARTIAL_WRITE` names the already-committed effects and request identity. Reads use committed pending settings; the next writable command retries file publication. Inspect those effects before retrying a mutation: this error does not mean that the whole command rolled back.

Hub and company schema revisions are explicit. Opening a database from an unknown newer schema returns `E_SCHEMA_UNKNOWN`; a known older schema returns `E_SCHEMA_BEHIND` until `upgrade` runs. Upgrades back up each database before migration.

## Errors and process exits

Every public failure has the JSON shape `{"code": "E_...", "message": "...", "details": {}}`. Programs branch on the stable `code` and use `details` for structured recovery; message text is for people. HTTP maps authentication, permission, missing-record, conflict, validation, and internal failures to the corresponding 4xx or 5xx status while preserving that document. The CLI exits `0` on success, `2` for `E_USAGE`, `3` for `E_INTERNAL`, and `1` for other named errors.


HTTP context headers support Unicode through `X-Bookflow-Context-Encoding:
percent-utf8`. When present, encode every supplied reason, source reference,
directive, idempotency key, client name and client version header value with UTF-8
percent encoding. The host decodes each exactly once before building command
context. Authentication and company-selection headers are unchanged. Without
this opt-in header, context values retain their ordinary header interpretation.
Malformed escapes, invalid UTF-8 and unknown encodings return E_VALIDATION before
command execution. A literal percent sign becomes `%25`; plus is not decoded as
space. Browser register entry applies this transport automatically and retains
the original context text with its pending request.


## Journal custom fields

Create definitions with scope `journal_entry`. Journal and register `post` and
`update` accept `custom_fields`, an object keyed by definition ID. Values are
text, exact decimal strings, ISO dates, booleans, or configured choice text,
according to the definition's kind. JSON numbers are not accepted for decimal
fields. Omit the object or a key to preserve a value on update. An explicit
`null` clears an optional value; `false`, `"0"`, and `""` remain supplied values.
The object itself cannot be `null`.

Journal and register writers also accept `custom_field_kinds`, an optional object
mapping definition IDs to their captured kinds. Each key must accompany a
supplied non-null `custom_fields` value. If the current kind differs, the command
returns E_VALIDATION before writing. Browser forms supply these kinds and keep
the original attempt on rejection. Use the explicit current-type action to
reinterpret a draft after a definition changes; generated forms preview that
change before save. Without captured kinds, API values use current definitions.

At the CLI, pass the value object with `--custom-fields` and the optional kind
object with `--custom-field-kinds`; both take JSON objects.

Creation applies active defaults and enforces required fields. Updates do not
apply newly added defaults or retroactively require absent values; a populated
required value cannot be cleared. An unchanged inactive definition or retired
choice remains captured. Choice matching uses trimmed, normalized,
case-insensitive text and preserves the configured choice identity.

Each revision stores its field names, kinds, values, ordering and choice labels.
`revision.custom_fields_snapshot` maps those facts by definition ID;
`revision.custom_fields` returns them in display order. Historical output never
renames fields from the current definitions. `journal update` with
`refresh_defaults=true` refreshes captured metadata while preserving canonical
values and value/choice IDs. It does not apply new custom-field defaults. A
custom-field-only correction appends a revision and a balanced reversal and
replacement with zero net account change. Clearing and later setting a value
reuses its original value ID. Voiding preserves the last captured values.

With an initialized Python client and `company` selected, the definition ID
returned by creation can be passed directly to a journal:

<!-- bookflow-example: illustrative -->
```python
field = client.run("custom-field create", {
    "name": "Work ticket", "kind": "text", "scopes": ["journal_entry"],
}, company=company, reason="Track work tickets")
journal = client.run("journal post", {
    "date": "2026-06-01",
    "lines": [
        {"account": "Checking", "side": "debit", "amount": "25.00"},
        {"account": "Service Income", "side": "credit", "amount": "25.00"},
    ],
    "custom_fields": {field["id"]: "WT-104"},
}, company=company, reason="Record work ticket receipt")
client.run("journal update", {
    "journal": journal["id"], "expected_version": journal["version"],
    "custom_fields": {field["id"]: None},
}, company=company, reason="Clear optional ticket reference")
```

List-record undo restores the audited canonical choice spelling when a choice
was renamed with the same normalized key. It still requires the corresponding
active choice for a changing value; it does not recreate a retired choice.
