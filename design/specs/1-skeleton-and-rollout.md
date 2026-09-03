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
| `init` | hub | none | | hub, config |
| `organization new` | hub | hub admin, human | | hub |
| `organization list` | hub | any | | none |
| `organization show` | hub | member or hub admin | `organization` | none |
| `organization rename` | hub | admin or owner on it, or hub admin | `organization` | hub |
| `company new` | hub | admin or owner on the organization, or hub admin; human | | hub, company |
| `company list` | hub | any | | none |
| `company use` | hub | member or hub admin | `company` | config |
| `company attach` | hub | admin or owner on the containing organization, or hub admin; human | `path` | hub |
| `company detach` | hub | admin or owner on the organization, or hub admin; human | `company` | hub, config |
| `company show` | company | member or hub admin | | none |
| `company rename` | company | admin or owner, or hub admin | | company, hub |
| `demo reset` | hub | hub admin, human | | hub, company, config |

`company update`, `company delete`, `trash *`, `user add`, `chart apply` are later rows. Nothing in this row deletes data: `detach` sets `companies.active = false` and `memberships.revoked_at`; `demo reset` moves folders to `trash/`. Every hub write in this row writes a hub audit event.

## Registry and dispatch

`Command` holds: name, scope, `requires_actor`, `allowed_actor_kinds`, `required_role`, `positional`, `writes` (subset of `hub`, `company`, `config`), input model, output model, description, command-specific error codes, `plan(input, ctx, db) -> Plan`, `apply(plan, ctx, db) -> (output, touched)`. `touched` is a list of (record type, id, version before, version after, after snapshot); this row writes hub audit events with a summary and one entry per touched hub record; row 2 adds the company audit tables to the same path.

Dispatch order: reject input keys named like context fields (`E_CONTEXT_IN_INPUT`); validate input with `extra="forbid"` (`E_VALIDATION`); resolve data root (`E_NOT_INITIALIZED`), actor (`E_NO_ACTOR`), and for company scope the company (`E_COMPANY_NOT_FOUND`, `E_COMPANY_AMBIGUOUS`); check actor kind and role (`E_PERMISSION`); take locks in fixed order, hub then company, exclusive for each database in `writes` and shared otherwise; open databases; writable opens migrate behind-head databases after a backup, read-only opens of a behind-head database return `E_SCHEMA_BEHIND`; call `plan`; on `dry_run` return the preview with `dry_run: true` and release; otherwise `apply` in one transaction per database, hub audit event, config write, release. Dry runs take only shared locks, open read-only, never migrate, never repair, and their previews carry null for ids and paths a real run would generate. Path-typed output fields are nulled unless the actor is a hub admin, in dispatch. `dry_run=True` on a command with no writes is `E_USAGE` from the CLI and the library alike.

Options per command: `--json` and `--data-root` on all; `--dry-run` where `writes` is non-empty; `--company` on company scope; `--interactive` on every command with a non-empty input model, CLI only. Before and after the verb both work; conflicting values are `E_USAGE`. All option values are strings at the parser and typed by the input model, so every value error is `E_VALIDATION`. `--help` in this row lists options and positionals; output fields and error codes arrive with row 4's documentation and are named as a gap in `architecture.md`.

Output models. Writes extend `WriteOutput{dry_run}`. `InitOutput{data_root, created, hub_admin_user_id, username, display_name, system_user_id}`. `OrganizationOutput{organization_id, display_name, access, role, path, company_count, common fields}`. `CompanySummary{company_id, organization_id, organization_name, display_name, legal_name, home_currency, schema_revision, is_demo, access, role, path, common fields}`. `CompanyShowOutput{CompanySummary fields, info: CompanyInfo, created_by_name, updated_by_name}`. `CompanyNewOutput{company_id, organization_id, display_name, path}`. `CompanyUseOutput{company_id, display_name}`. `CompanyAttachOutput{company_id, organization_id, display_name, path}`. `CompanyDetachOutput{company_id, display_name, path}`. `CompanyRenameOutput{company_id, display_name, previous_display_name, path, moved}`. `DemoResetOutput{organization_id, company_id, display_name, path, trashed_path}`. `access` is `hub_admin`, `organization`, or `company`; `role` is null for hub admins without membership. Paths in output are absolute and null for non-hub-admins.

## Context and actor

The CLI and `Client` build `Context` with interface `cli` or `python`, `client_name`, `client_version`, `client_host`, fresh `session_id` and `request_id`. The actor is the OS login from the process, mapped through `config.toml`; there is no parameter to name another login. `init` requires no actor. On an initialized root, an unmapped login gets `E_NO_ACTOR` from every command including `init`, with no other information.

Bootstrap: `init --username <default: OS login> --display-name <default: username>` inserts the system user with `created_by` its own id, the first human as hub admin with `created_by` the system user, writes the mapping, and returns `created: true`. `init` again by the mapped login returns the existing state with `created: false`.

Tests that need a second actor insert users and memberships through `hub.users` and `hub.companies` inside a fixture and dispatch with a hand-built `Context`; the fixture is named as the thing row 7's `user add` replaces. Isolation assertions: a member of organization A gets an empty `company list` for B's companies, `E_COMPANY_NOT_FOUND` from `company show`, `company use`, and `--company` by B's display name; a company-scope member of one company in A sees only that company in `company list`; `organization list` for that member shows A with `access: company`.

Every company write upserts the actor into `principals` in the same transaction.

## Data root and configuration

Resolution: `--data-root`, `BOOKFLOW_DATA_ROOT`, `~/.bookflow`. `init` checks locality, then creates `hub.db`, `config.toml`, `organizations/`, `locks/`, `backups/`, `trash/`. `config.toml` per blueprint 4.3a; written atomically under the hub exclusive lock; `detach` and `demo reset` clear defaults that name removed companies. A stale `BOOKFLOW_COMPANY` returns `E_COMPANY_NOT_FOUND` with `details.source: env`.

## Organizations

`organization new --name` (trimmed, whitespace-collapsed; `E_NAME_TAKEN` under the hub lock, backed by the `name_key` unique constraint): create the folder under `organizations/` by atomic mkdir with suffixing, write `bookflow-organization.toml`, insert the hub row and the creator's organization-scope owner membership in one transaction. Failure before the hub commit removes the folder. `organization rename --name [--move]` under the hub lock: hub row, then organization marker, then folder move, then hub path; company paths are stored relative to the organization folder so a move changes one row. `organization show` returns `OrganizationOutput`.

## Rollout

Input: `organization` (defaulted when the actor sees exactly one, else `E_ORGANIZATION_REQUIRED`), `chart` (only `none` in this row), and the blueprint 9.1 fields except `closing_date`, `default_chart`, `is_demo`. Required, defaults, and validations per blueprint 9.2; `home_currency` must be in the currency table; `timezone` defaults to the zone named by `TZ` or the `/etc/localtime` link, and when neither names a zone it is required. Display names are trimmed and whitespace-collapsed.

Under the hub exclusive lock throughout:

1. Validate; resolve the organization and the actor's role; check `name_key` uniqueness within the organization.
2. Reserve the folder inside the organization folder by atomic mkdir, suffixing on `FileExistsError`.
3. Write the marker atomically with `state = creating`; create `attachments/`, `backups/`, `exports/`.
4. Create `company.db`, migrate, insert `company_info` and the creator's `principals` row, checkpoint, close.
5. Rewrite the marker with `state = ready`, the schema revision, and the projection fields.
6. One hub transaction: insert `companies` (organization id, path relative to the organization folder, projection fields, `active = true`), a company-scope owner membership when the actor holds no organization-scope membership, and the audit event.

Failure in 2 to 4 removes the folder this attempt created. Failure in 5 leaves an incomplete folder (`creating`), reported as `E_ROLLOUT_INCOMPLETE` with the folder named for hub admins; the operator removes it. Failure in 6 leaves an unregistered `ready` folder, reported the same way; `attach` adopts it, with `--name` if the name was taken meanwhile.

## Rename

Under hub then company exclusive locks, which also guarantees no reader or writer has the database open: update `company_info.display_name` and commit; update the hub row projection and commit; rewrite the marker; with `--move`, compute the new folder name excluding the company's own folder from the collision check, rename the directory (through a temporary name `<target>.moving-<company_id>` for case-only changes), then update the hub path and commit; audit event. `company_info` is authoritative for the display name. A failure after the first commit returns `E_RENAME_INCOMPLETE` naming which step failed; the next writable open converges: hub projection rewritten from `company_info`, marker rewritten, and if the registered path is missing the organization folder is scanned for a folder whose marker and `company_info.id` both carry the company id, exactly one of which is accepted (`E_COMPANY_MISSING` for none, `E_COMPANY_DUPLICATE_FOLDERS` for more), a `.moving-` folder is renamed to its target first. Renaming to the current name is a no-op returning `moved: false`.

## Attach and detach

`attach <path> [--name]`: `path` is a filesystem path, absolute or relative to the working directory. Under the hub lock: resolve symlinks; require the parent to be a registered organization folder; check locality; require marker and `company.db`; open read-only; require `company_info.id` equal to the marker id and the revision known; refuse `creating` (`E_INCOMPLETE_COMPANY`); refuse a registered id (`E_ALREADY_ATTACHED`); check name uniqueness using `--name` or the database name; insert the hub row with projection from the database, a company-scope owner membership when needed, and the audit event. `detach <company>` under hub and company exclusive locks: `active = false`, `revoked_at` on its memberships, config defaults cleared, audit event; the folder is untouched and becomes unregistered.

## Company selection

Blueprint 5.3. The value is tried as a ULID (case-insensitive); on a miss it is tried as `Organization/Company`, then as a bare company name among what the actor can see. Hub admins resolve every company and may `use` any. `default_company` stores the id.

## Filesystem locality and locks

`fs.py`: Linux parses `/proc/self/mountinfo`, unescaping `\\040`, `\\011`, `\\012`, `\\134`, and takes the longest mount-point prefix of the resolved path; macOS reads `f_fstypename` through `ctypes`; Windows refuses UNC and remote drive types. Only the blueprint 3.2 allowlist passes; `fuse.*`, `autofs`, and anything else is `E_NETWORK_SHARE`; an undeterminable type is `E_FS_UNKNOWN`. Checked at `init`, on every open, and at `attach`.

`locks.py`: files in `<data_root>/locks/`; `flock` with `LOCK_SH` or `LOCK_EX` on POSIX, `LockFileEx` through `ctypes` on Windows; 5-second retry then `E_DB_BUSY` with the holder's hostname and pid read from the file. Held from before open until after close.

`engine.py`: writable opens set `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, checkpoint and truncate on close; read-only opens set `query_only=ON` and change nothing.

## Migrations and backups

Alembic chains for hub and company. Unknown revision: `E_SCHEMA_UNKNOWN`. Behind head on a writable open: backup with `sqlite3.Connection.backup` to the company's `backups/` or `<data_root>/backups/` for the hub, migrate, rewrite the marker and hub projection. A test adds a synthetic revision, migrates, restores the backup, and compares dumps.

## Demo reset

Hub admin only. Under the hub exclusive lock, and exclusive locks on every company in the demo organization: one hub transaction sets the organization and its companies inactive, revokes memberships, clears config defaults, and writes the audit event; then the organization folder moves to `trash/<folder>-<timestamp>-<ulid>/`; a failed move leaves unregistered `ready` folders and returns `E_DEMO_RESET_INCOMPLETE`; then the seed organization and company are created through the ordinary organization and rollout paths with `is_demo` set. There is one demo organization; no `--name`.

## Money

`Money(minor_units: int, currency: str)`; constructor and parser reject `float` and `bool`; parsing accepts a decimal string with optional trailing code; JSON form per blueprint 8.1. Currency table is package data.

## Library surface

`bookflow.connect(data_root=None, client_name="python") -> Client`; `Client.run(name, input, *, company=None, dry_run=False)`; `Client.<noun>.<verb>(**input, company=None, dry_run=False)`. Raises `BookflowError` with `code`, `message`, `details`. Root exports: `connect`, `Client`, `BookflowError`, `Money`.

## CLI rendering

Per blueprint 5.4. `--interactive` prompts on stderr for fields not given as options, showing description, default, and choices; non-terminal stdin is `E_USAGE`; stdout stays one JSON document under `--json`. Startup imports only Typer and the registry's command metadata; SQLAlchemy, Alembic, and Pydantic models load on dispatch, and a subprocess test asserts `bookflow --help` completes under 300 ms.

## Error matrix

Infrastructure codes per blueprint 5.4 apply to every command. Command-specific: `init` none; `organization new` `E_NAME_TAKEN`; `organization rename` `E_NAME_TAKEN`, `E_RENAME_INCOMPLETE`; `company new` `E_ORGANIZATION_REQUIRED`, `E_NAME_TAKEN`, `E_ROLLOUT_INCOMPLETE`; `company rename` `E_NAME_TAKEN`, `E_RENAME_INCOMPLETE`, `E_COMPANY_MISSING`, `E_COMPANY_DUPLICATE_FOLDERS`; `company attach` `E_NOT_IN_ORGANIZATION_DIR`, `E_INCOMPLETE_COMPANY`, `E_ALREADY_ATTACHED`, `E_NAME_TAKEN`, `E_ATTACH_INVALID`; `demo reset` `E_DEMO_RESET_INCOMPLETE`. Tests inject `OSError` at each filesystem step of rollout, rename, and reset, and a locked database at each open, and assert the code, the JSON shape, the exit code, and the resulting on-disk state through both the library and the CLI.

## Edges

- Not built and not faked: company audit tables, conflict contract, presence, idempotency (row 2); HTTP, workbench (row 3); documentation generator and full `--help` (row 4); charts and lists (row 5); notes and attachments (row 6); passwords, tokens, `user add`, the full role matrix (row 7); `sequences` (row 8). Hub writes are audited from this row; company writes are recorded by common fields and `principals` until row 2.
- macOS and Windows filesystem and lock branches are unit-tested with mocked system calls and recorded as untested on hardware in `architecture.md`.
- `uv` is installed on the build machine as a build step, not part of the product.

## Verification the builder will perform

The suite green, including the subprocess run, the cold-start timing, the fault-injection matrix, and the portability comparison of full dumps, marker, directory listing, and resolved creator name. Installed size of the environment recorded in `architecture.md`. By hand: the row's done sequence with `--json` on every step on a fresh data root, the copy-and-attach on a second root, a concurrent writer from two shells, and a refusal on an `sshfs` mount if one can be created on the build machine, otherwise recorded as untested.
