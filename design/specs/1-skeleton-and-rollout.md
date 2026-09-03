# Row 1 — plan

## Package layout

```
pyproject.toml                 uv-managed; package `bookflow`; console script `bookflow`; Python >= 3.12
README.md                      gains install and the run command `bookflow init` when this row lands
src/bookflow/
  __init__.py                  exports connect, Client, BookflowError, Money; nothing else
  client.py                    Client: run(name, input, *, company=None, dry_run=False); attribute form client.company.new(...); builds Context; interface "python"
  core/
    registry.py                Command record, @command decorator, registry, dispatch
    context.py                 Context model (blueprint 5.2)
    errors.py                  BookflowError; every E_ code with its message template; infrastructure set and per-command sets
    ids.py                     ULID generation; case-insensitive ULID detection
    money.py                   Money value; currency table (package data); parse, format, JSON form; float and bool rejected
    models.py                  RecordBase (blueprint 6.1); ListOutput; WriteOutput (dry_run); path-field marker for redaction
    fs.py                      filesystem type per platform; locality check; mountinfo unescaping
    locks.py                   shared and exclusive locks in <data_root>/locks/; fixed order hub then company
    config.py                  config.toml read; atomic write under the hub lock
  storage/
    paths.py                   data root resolution; folder naming; marker read, atomic write, repair
    engine.py                  engine factory; read-only opens (query_only, shared lock); writable opens (WAL, foreign keys, busy_timeout, exclusive lock, checkpoint on close)
    migrate.py                 Alembic runner for both chains; backup via sqlite3 backup API; E_SCHEMA_UNKNOWN, E_SCHEMA_BEHIND
    hub_migrations/versions/
    company_migrations/versions/
  hub/
    schema.py                  users, api_tokens, organizations, companies, memberships, audit_events, audit_entries (all with 6.1 fields except memberships and audit tables)
    users.py                   bootstrap; OS-login mapping
    organizations.py           create, list for actor, resolve, rename
    companies.py               register, projection update, list for actor, resolve, detach (soft), repair on writable open
    audit.py                   hub audit event writer (events with summary; entries from touched records)
  company/
    schema.py                  company_info, principals
    info.py                    read; rename; principals upsert
    rollout.py                 staged rollout
  commands/
    hub_cmds.py                init, organization new/list/show/rename, company new/list/use/attach/detach, demo reset
    company_cmds.py            company show, company rename
  demo/
    seed.toml                  Demo Holdings LLC; Demo Plumbing Co
    reset.py
  adapters/cli/
    app.py                     Typer app generated from the registry; options declared per command scope and writes; --interactive prompting from field metadata
    render.py                  tables, JSON, error and usage-error rendering, exit codes
tests/                         every test that runs a command runs it against the demo created by a session fixture, on a fresh data root
```

## Commands

| Command | Scope | Actor | Positional | Writes to |
|---|---|---|---|---|
| `init` | hub | none (bootstrap) | | hub, config |
| `upgrade` | hub | any | | hub, company |
| `organization new` | hub | hub admin, human | | hub |
| `organization list` | hub | any | | none |
| `organization show` | hub | member or hub admin | `organization` | none |
| `organization rename` | hub | hub admin, human | `organization` | hub |
| `company new` | hub | admin or owner on the organization, or hub admin; human | | hub, company |
| `company list` | hub | any | | none |
| `company use` | hub | member or hub admin | `company` | config |
| `company attach` | hub | hub admin, human | `path` | hub, company |
| `company detach` | hub | hub admin, human | `company` | hub, config |
| `company show` | company | member or hub admin | | none |
| `company rename` | company | admin or owner, or hub admin | | hub, company |
| `demo reset` | hub | hub admin, human | | hub, company, config |
| `audit list` | hub | any | | none |
| `audit show` | hub | any | `event` | none |

`company update`, `company delete`, `trash *`, `user add`, `membership grant`, `chart apply` are later rows. Nothing in this row deletes company data: `detach` deletes hub rows inside an audited transaction whose entries carry them; `demo reset` moves folders to `trash/`. Every hub write except a `config.toml`-only write records a hub audit event in the same transaction.

## Registry and dispatch

`Command` holds: name, scope, `bootstrap` (true only for `init`), `allowed_actor_kinds`, `required_role`, `positional`, `writes` (subset of `hub`, `company`, `config`), input model, output model, description, command-specific error codes with the reason each exists, `plan(input, ctx, db) -> Plan`, `apply(plan, ctx, db) -> (output, touched)`. `touched` is a list of (record type, id, version before, version after, after snapshot, before snapshot for deletes); hub audit events and entries are written from it in the same transaction as the change. Hub `audit_events` and `audit_entries` carry every column of blueprint 7 from this migration, with the context fields that later rows supply left null.

Dispatch, entirely under the data-root lock: reject context-named input keys (`E_CONTEXT_IN_INPUT`); validate with `extra="forbid"` (`E_VALIDATION`, including a missing positional on both surfaces); resolve the data root (`E_NOT_INITIALIZED` unless `bootstrap`), actor (`E_NO_ACTOR`), organization or company (`E_ORGANIZATION_NOT_FOUND`, `E_COMPANY_NOT_FOUND`, `E_COMPANY_AMBIGUOUS`); check kind and role (`E_PERMISSION`); open the hub and, for company scope, the company, writable when in `writes` and read-only otherwise; behind-head databases migrate on writable open after a backup and return `E_SCHEMA_BEHIND` on read-only open; `plan`; on `dry_run` return the preview with `dry_run: true`; otherwise `apply`, one transaction per database with the audit rows inside the hub transaction, then the config write, then release. Dry runs open read-only, generate real ULIDs for the preview, derive the folder name the real run would choose, and fail only where the real run would, `E_SCHEMA_BEHIND` included; the only file a dry run touches is `root.lock`. The redaction rule runs on every output and every error, message and details, nulling or removing filesystem paths and lock-holder identity for non-hub-admins.

Options per command: `--json` and `--data-root` on all; `--dry-run`, `--reason`, `--source-ref` where `writes` is non-empty; `--company` on company scope; `--interactive` on writes, CLI only, pre-filled by any options and positionals given. Nested fields per blueprint 5.1 (`--address-line1`). Before or after the verb; conflicts and unknown options are `E_USAGE`; unknown commands are `E_USAGE` on both surfaces. Values are typed by the input model so every value error is `E_VALIDATION`. `--help` lists options and positionals; output fields and codes arrive with row 4 and the gap is recorded in `architecture.md`.

Output models. Writes extend `WriteOutput{dry_run}`. `InitOutput{data_root, created, hub_admin_user_id, username, display_name, system_user_id}`. `UpgradeOutput{hub_migrated, companies_migrated: [company_id]}`. `OrganizationOutput{organization_id, display_name, access, role, path, common fields}`. `CompanySummary{company_id, organization_id, organization_name, display_name, legal_name, home_currency, schema_revision, is_demo, access, role, path, registered_by_name, common fields of the hub row}`. `CompanyShowOutput{CompanySummary fields, info: CompanyInfo with its own common fields, info_created_by_name, info_updated_by_name}` where the info names resolve through `principals`. `CompanyNewOutput{company_id, organization_id, display_name, path}`. `CompanyUseOutput{company_id, display_name}`. `CompanyAttachOutput{company_id, organization_id, display_name, path}`. `CompanyDetachOutput{company_id, display_name, path}`. `CompanyRenameOutput{company_id, display_name, previous_display_name, path, moved}`. `DemoResetOutput{organization_id, company_id, display_name, path, trashed_path}`. `AuditEvent` per blueprint 7. Paths are absolute and null for non-hub-admins.

## Context and actor

The CLI builds `Context` with interface `cli`; `Client` with `python`. Both take the OS login from the process and map it through `config.toml`; nothing lets a caller name another login. On an initialized root, an unmapped login gets `E_NO_ACTOR` with no other information, except from `init` (below). `reason` and `source_ref` come from the options; other context fields are null until their rows.

Bootstrap: `init --username <default: OS login> --display-name <default: username>`. Steps, each skipped when its artifact already exists so an interrupted `init` completes on rerun: create the data root with mode `0700` after the locality check; take the lock; create `hub.db` and migrate; insert the system user (`created_by` its own id); insert the first human as hub admin; write the mapping; write the single `init` audit event with the system user as actor. A mapped login rerunning `init` gets `created: false` and the existing state; a different `--username` from a mapped login is `E_VALIDATION`. When `config.toml` is missing or invalid and the hub holds exactly one human user, `init` rewrites the mapping for the current login to that user; any other unmapped login is `E_NO_ACTOR`.

Tests that need a second actor insert users and memberships through `hub.users` and `hub.companies` inside a fixture, named as what row 7's `user add` and `membership grant` replace, and dispatch with a hand-built `Context`. Isolation assertions, each run for a member of organization A against organization B: `organization list` omits B; `organization show B`, `company new --organization B`, `company show`, `company use`, and `--company` by B's names all return the not-found code identical to a nonexistent name; `company list` omits B's companies; a company-scope member of one company in A sees only that company and sees A with `access: company`; `E_DB_BUSY` and every error detail carry no path or holder identity.

Every company write upserts the actor into `principals` in the same transaction.

## Data root and configuration

Resolution: `--data-root`, `BOOKFLOW_DATA_ROOT`, `~/.bookflow`. `init` creates `hub.db`, `config.toml`, `root.lock`, `organizations/`, `backups/`, `trash/`. `config.toml` per blueprint 4.3a, written after the hub transaction; `company use` writes only config and is not audited. A `default_company` or `BOOKFLOW_COMPANY` that no longer resolves returns `E_COMPANY_NOT_FOUND` with `details.source` `default` or `env`.

## Organizations

`organization new --name`: trim and collapse whitespace, reject `/`, check `name_key` under the lock, choose the folder name by listing `organizations/` per blueprint 3.1, mkdir, write `bookflow-organization.toml`, insert the hub row and audit event in one transaction; no membership. Failure before the commit removes the folder. `organization rename --name [--move]`: transaction one commits the name and its audit event; with `--move`, the target is chosen by listing, checked absent immediately before, the folder renamed (case-only through `<target>.moving-<id>`), and transaction two commits the organization path and every company path under it with a `move` audit event. A failure between leaves the old path valid. `organization show` returns `OrganizationOutput` without counts.

## Rollout

Input: `organization` (defaulted when the actor sees exactly one, else `E_ORGANIZATION_REQUIRED`, which exists because a silent default among several would be inferred), `chart` (`none` only), and the blueprint 9.1 fields except `closing_date`, `default_chart`, and `display_name`'s copy semantics. Required, defaults, and validations per 9.2, `home_currency` in the currency table, `timezone` from `tzlocal`, names without `/`.

Under the lock:

1. Validate; resolve the organization and role; check `name_key` uniqueness.
2. Choose the folder name by listing the organization folder; mkdir; write the marker with `state = creating`; create `attachments/`, `backups/`, `exports/`.
3. Create `company.db`, migrate, insert `company_info` (with the display name copy) and the creator's `principals` row, checkpoint, close.
4. Rewrite the marker with `state = ready` and the revision.
5. One hub transaction: insert `companies` with the projection fields, a company-scope owner membership for the creator, and the audit event.

Failure in 2 or 3 removes the folder this attempt created. Failure in 4 leaves an incomplete folder; failure in 5 leaves an unregistered `ready` folder; both return `E_ROLLOUT_INCOMPLETE` (exists because the caller must know a folder remains) naming the folder to hub admins; `attach` adopts the second case.

## Rename

`company rename --name [--move]` under the lock. Transaction one: check uniqueness, update the hub row, write the audit event. Then the display-name copy in `company_info` and the marker are rewritten; a failure here is ignored and repaired on the next writable open. With `--move`: choose the target by listing, check absent, rename the directory (case-only through the temporary name), transaction two commits the path with a `move` audit event. A failure after transaction one and before transaction two leaves the hub pointing at the old, still-existing folder and returns `E_RENAME_INCOMPLETE` (exists because the name changed and the move did not); rerunning `rename --move` with the same name performs the move. Renaming to the current name returns `moved: false` and writes nothing.

## Attach and detach

`attach <path> [--name]`: `path` is absolute or relative to the working directory. Under the lock: resolve symlinks; require the parent to be a registered organization folder (`E_NOT_IN_ORGANIZATION_DIR`); check locality; require marker and `company.db` and a `ready` marker (`E_INCOMPLETE_COMPANY`); open the database with raw `sqlite3` read-only to read `alembic_version` and `company_info.id` and the display-name copy, which is exempt from the behind-head rule; refuse unknown revisions (`E_SCHEMA_UNKNOWN`) and registered ids (`E_ALREADY_ATTACHED`); check the name; one hub transaction inserts the row with the projection from the database, an owner membership for the hub admin, and the audit event; then a writable open migrates if behind and rewrites the copies. `E_ATTACH_INVALID` covers a missing or unreadable marker, a missing database, and an id mismatch, with `details.check`; filesystem failures are `E_IO`. `detach <company>`: one hub transaction deletes the company row and its memberships with entries carrying the deleted rows, then config defaults are cleared; the folder becomes unregistered.

## Company selection

Blueprint 5.3: ULID (case-insensitive) first, on a miss `Organization/Company`, then a bare company name among what the actor can see. Names cannot contain `/`, so the forms cannot overlap. Hub admins resolve everything and may `use` any company. `Client.run(name, input, *, company=None, dry_run=False)`; `Client.use_company(value)` sets the selection for later calls; the attribute form `client.company.show()` takes only input fields, so `client.company.use(company="Acme")` is the positional input and selection comes from `use_company` or `run`. Passing a selection to a hub command is `E_USAGE`.

## Filesystem locality and locks

`fs.py` per blueprint 3.2 with mountinfo unescaping and `fuse.*`, `autofs`, and unknown types refused; applied at `init`, on every open, and at `attach`.

`locks.py`: `root.lock` taken exclusively for the whole command with `flock` or `LockFileEx`; retry until `BOOKFLOW_LOCK_TIMEOUT` (default 5 s; tests set 0.2); the holder writes hostname, pid, and command after acquiring. `engine.py`: writable opens set WAL, foreign keys, and `busy_timeout=5000` and checkpoint-truncate on close; read-only opens set `query_only`.

## Migrations and backups

Alembic chains for hub and company. `E_SCHEMA_UNKNOWN` for unknown revisions. Behind head on writable open, or on `upgrade`: backup with the SQLite backup API to `backups/`, migrate, rewrite copies. `upgrade` migrates the hub and every company the actor may write and reports which. A test adds a synthetic revision, migrates, restores the backup, and compares dumps.

## Demo reset

Hub admin only, under the lock. One hub transaction deletes the demo organization, its companies, and their memberships with entries carrying the rows, clears config defaults, and writes the audit event; then the organization folder moves to `trash/<folder>-<timestamp>/` (a second reset in the same second appends a counter); a failed move returns `E_DEMO_RESET_INCOMPLETE` (exists because unregistered folders remain) and the folders are unregistered `ready` folders a hub admin may attach or remove; then the seed organization and company are created through the ordinary paths with `is_demo` set on both hub rows. Repeated resets are tested on one root.

## Money

`Money(minor_units: int, currency: str)`; constructor and parser reject `float` and `bool`; parsing per blueprint 8.1 including the JSON object form and `E_AMOUNT_PRECISION`. Currency table is package data.

## Library surface

`bookflow.connect(data_root=None, client_name="python") -> Client`; `Client.run`, `Client.use_company`, and the attribute form as above. Raises `BookflowError` with `code`, `message`, `details`. Root exports: `connect`, `Client`, `BookflowError`, `Money`.

## CLI rendering

Per blueprint 5.4. `--interactive` prompts on stderr for fields not given as options, showing description, default, and choices; non-terminal stdin is `E_USAGE`; stdout stays one JSON document under `--json`. Startup imports only Typer and the registry's command metadata; SQLAlchemy, Alembic, and Pydantic models load on dispatch, and a subprocess test asserts `bookflow --help` completes under 300 ms.

## Error matrix

Infrastructure codes per blueprint 5.4 apply to every command. Command-specific, with the reason each exists: `organization new`, `organization rename`, `company new`, `company rename`, `company attach`: `E_NAME_TAKEN` (uniqueness); `company new`: `E_ORGANIZATION_REQUIRED`, `E_ROLLOUT_INCOMPLETE`; `organization rename`, `company rename`: `E_RENAME_INCOMPLETE`; `company show`, `company rename`: `E_COMPANY_MISSING` (registered path absent); `company attach`: `E_NOT_IN_ORGANIZATION_DIR`, `E_INCOMPLETE_COMPANY`, `E_ALREADY_ATTACHED`, `E_ATTACH_INVALID`; `demo reset`: `E_DEMO_RESET_INCOMPLETE`. The matrix in `tests/error_matrix.py` lists, per command, every code with the input or fault that produces it; a test walks the matrix through the library and the CLI and asserts code, JSON shape, exit code, and on-disk state, and a second test asserts that every code the implementation can raise appears in the matrix.

## Edges

- Not built and not faked: company audit tables, conflict contract, presence, idempotency, `--directive`, `--idempotency-key` (row 2); HTTP, workbench (row 3); documentation generator and full `--help` (row 4); charts and lists (row 5); notes and attachments (row 6); passwords, tokens, `user add`, the full role matrix (row 7); `sequences` (row 8). Hub writes are audited from this row; company writes are recorded by common fields and `principals` until row 2.
- macOS and Windows filesystem and lock branches are unit-tested with mocked system calls and recorded as untested on hardware in `architecture.md`. Every command on a data root is serialized by the lock; concurrency between agents is the host's job from row 3.
- `uv` is installed on the build machine as a build step, not part of the product.

## Verification the builder will perform

The suite green: the error matrix, the parity test that runs the row's done sequence through the library and the CLI and compares normalized JSON (excluding `organization_id`, `organization_name`, `path`, `access`, `role`, `registered_by_name`, and the hub row's common fields on the attached copy), the portability comparison of full dumps, marker, and directory listing excluding `-wal`, `-shm`, and `backups/`, the repeated demo reset, the fault-injection matrix, cold start of `bookflow --help` and `bookflow company list` in a subprocess under 300 ms, and the isolation assertions. Destructive tests use their own data root; read-only tests share the session demo. Installed size recorded in `architecture.md`. By hand: the done sequence on a fresh root, a concurrent command from two shells, and a refusal on a real non-local mount if one can be created on the build machine, otherwise the row stays open on that point and says so.

