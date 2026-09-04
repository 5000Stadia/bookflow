# Bookflow concepts

## Commands are the product contract

Bookflow exposes named commands with declared input and output models, scope, write behavior, required role, capability, feature gate, and stable error codes. The command registry is authoritative. Available adapters call the same command kernel; they do not reimplement business rules.

A command is either hub-scoped or company-scoped. Hub commands operate on the registry and do not need a selected company. Company commands resolve one visible company before they open its database. `init`, `serve`, `company use`, and documentation generation are local operations rather than HTTP routes.

The Python API returns JSON-shaped dictionaries and raises `BookflowError`. The CLI can render those dictionaries for people or return the same shape with `--json`. HTTP uses JSON request and response bodies. The workbench invokes the same commands through the host. Adapter-only concerns such as CLI formatting, HTTP authentication, and browser form rendering do not change command validation or authorization.

## Company selection

For the CLI and Python client, an explicit company argument wins, then `BOOKFLOW_COMPANY`, then the current operating-system user's saved default. A selector can be a company ULID, `Organization/Company`, or an unambiguous display name. Ambiguous names are errors; Bookflow never guesses.

HTTP company routes carry the company ULID in `/companies/{company_id}/...`. If `X-Bookflow-Company` is also present, it must be the same ULID after case normalization. A caller only sees companies permitted to its authenticated identity; a missing company and an inaccessible company have the same public result.

## Context belongs to the caller and adapter

Command input contains business fields. Execution context contains actor, interface, client, session, request, company, reason, directive, source reference, and idempotency metadata. Adapters construct context; context fields in an HTTP JSON body are rejected.

The host derives the actor and any principal from the bearer or browser session. A caller cannot select `actor_id` or `actor_kind`. HTTP callers provide optional context with `X-Bookflow-Reason`, `X-Bookflow-Directive`, `X-Bookflow-Source-Ref`, `Idempotency-Key`, `X-Bookflow-Client-Name`, and `X-Bookflow-Client-Version`. A write by an agent or system identity requires a reason or an active directive.

## Dry runs and retry safety

Durable write commands accept dry-run execution. A dry run performs selection, authorization, validation, directive resolution, conflict checks, and planning, then returns the planned output with `dry_run: true` without committing the business change or its audit event. Presence is advisory state rather than a durable audited business write.

Only commands whose reference says they accept an idempotency key may use one. For 30 days, repeating the same key as the same actor with the same command, validated input, and company returns the stored result with `idempotent_replay: true`. Reusing the key for different work returns `E_IDEMPOTENCY_MISMATCH`.

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

Presence says that a user is editing a company record. It expires, is advisory, does not block writes, and is not part of the audit trail.

## Identity, roles, and isolation

The first local initialization maps the operating-system login to a human hub administrator. HTTP clients authenticate with a bearer token or a browser session. Bearer secrets are shown only when issued and are stored as hashes. Public agent-user and principal administration are not part of the current command set, so a bearer issued by the current bootstrap flow represents its human issuer.

Company access is role-based. `member` can read, `standard` adds ordinary record work, and `admin` adds company administration; owners and hub administrators satisfy the applicable company checks. The registry names the required role and capability for each command. Hub and organization authorization is checked before data is exposed. Non-administrators do not receive local storage paths, and inaccessible records are not distinguishable from nonexistent ones where that distinction would reveal another tenant.

## Exact money

A money value uses an ISO 4217 currency and integer `minor_units`; binary floating-point amounts are rejected. Its JSON form includes the exact integer, currency, and a decimal-string `amount`, for example `{"minor_units": 1250, "currency": "USD", "amount": "12.50"}`. Currency metadata determines the permitted decimal places. Too much precision returns `E_AMOUNT_PRECISION`.

## Local storage and schema revisions

A data root contains the hub database, configuration, organization and company directories, backups, and trash. Each company owns a separate SQLite database. Bookflow refuses network filesystems because its correctness depends on local filesystem and SQLite locking semantics. A running host serializes mutations through one writer while allowing independent reads, and compatible local CLI/Python calls hand work to that host.

Hub and company schema revisions are explicit. Opening a database from an unknown newer schema returns `E_SCHEMA_UNKNOWN`; a known older schema returns `E_SCHEMA_BEHIND` until `upgrade` runs. Upgrades back up each database before migration.

## Errors and process exits

Every public failure has the JSON shape `{"code": "E_...", "message": "...", "details": {}}`. Programs branch on the stable `code` and use `details` for structured recovery; message text is for people. HTTP maps authentication, permission, missing-record, conflict, validation, and internal failures to the corresponding 4xx or 5xx status while preserving that document. The CLI exits `0` on success, `2` for `E_USAGE`, `3` for `E_INTERNAL`, and `1` for other named errors.
