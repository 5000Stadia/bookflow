# Row 1 — plan

Round 2. Revised against two plan critiques; the blueprint was amended in the same change (sections 3, 4.1, 4.1a, 5.1, 5.3, 5.4, 9.2, 9.3).

## Package layout

```
pyproject.toml                 uv-managed; package `bookflow`; console script `bookflow`; Python >= 3.12
src/bookflow/
  __init__.py                  connect() -> Client; nothing else exported
  client.py                    Client: run(name, input, *, dry_run) and attribute access client.company.new(...); builds Context; no engine access
  core/
    registry.py                Command record, @command decorator, registry, dispatch (validation, dry-run, actor and role gates)
    context.py                 Context model (blueprint 5.2); constructed only by adapters and Client
    errors.py                  BookflowError(code, message, details); every E_ code as a constant with its message template
    ids.py                     ULID generation and detection
    money.py                   Money value; currency table; parse and format; float rejected
    models.py                  RecordBase (blueprint 6.1); ListOutput envelope
    fs.py                      filesystem type detection per platform; locality check
    locks.py                   lifetime write locks on .lock files; holder hostname and pid
  storage/
    paths.py                   data root resolution; folder naming; marker read, write, repair
    engine.py                  engine factory (WAL, foreign keys, busy_timeout), read-only and writable opens, checkpoint on close
    migrate.py                 Alembic runner for both chains; backup via sqlite3 backup API; E_SCHEMA_UNKNOWN
    hub_migrations/versions/
    company_migrations/versions/
  hub/
    schema.py                  users, api_tokens, companies, memberships
    users.py                   bootstrap (system then first human, hub_admin), OS-login mapping in config.toml
    companies.py               registry: register, list for actor, resolve id-or-name, detach
  company/
    schema.py                  company_info, principals, sequences
    info.py                    read; rename; principals upsert
    rollout.py                 company new: staged, recoverable
  commands/
    hub_cmds.py                init, company new/list/use/attach/detach, demo reset
    company_cmds.py            company show, company rename
  demo/
    seed.toml                  Demo Plumbing Co company info
    reset.py
  adapters/cli/
    app.py                     Typer app generated from the registry; global options on every command
    render.py                  tables, JSON, error and usage-error rendering, exit codes
tests/
  conftest.py                  fresh data root fixture (tmpfs or local disk), second root fixture, fake clock, subprocess runner
  test_registry.py             every command: scope, positional list, descriptions, error list, no context-named input fields
  test_errors.py               every E_ code declared by a row 1 command is reachable and asserted through library and CLI
  test_init.py                 first run, replay, second OS user, bootstrap provenance
  test_company_new.py          required and default fields, validation, folder naming table, collision race, staged failure at each boundary
  test_company_cmds.py         list, show (admin and member views), use, rename with and without --move, attach validation matrix, detach
  test_selection.py            --company, env, default; id-before-name; ambiguous; not-found parity for nonexistent and unauthorized
  test_demo.py                 reset twice; membership and hub-admin gates; trash contents
  test_locks.py                second writer E_DB_BUSY; read-only opens unlocked; lock released on close
  test_fs.py                   allowlist; refusal codes; unknown type refused
  test_portability.py          write, close, copy folder to second root, attach, compare full dumps, marker, directories, resolved creator name
  test_dry_run.py              every write with --dry-run: output shape and data root hash unchanged
  test_cli_parity.py           every command and every error through Client and CliRunner; one real subprocess run of the console script
  test_money.py                parse, format, float rejection, currency precision
```

## Commands in this row

| Command | Scope | Actor | Positional | Writes |
|---|---|---|---|---|
| `init` | hub | none required | | yes |
| `company new` | hub | hub admin, human | | yes |
| `company list` | hub | any | | no |
| `company use` | hub | any member | `company` | yes (config.toml) |
| `company attach` | hub | hub admin, human | `path` | yes |
| `company detach` | hub | hub admin, human | `company` | yes |
| `company show` | company | member or hub admin | | no |
| `company rename` | company | admin or owner role, or hub admin | | yes |
| `demo reset` | hub | hub admin, human | | yes |

`company update` and `company delete` are not in this row; `update` arrives with row 2's conflict contract, `delete` with row 7's roles and the hub audit. Nothing in this row deletes anything; `demo reset` moves to `trash/`.

## Registry and dispatch

`Command` holds: name, scope (`hub` or `company`), `requires_actor`, `allowed_actor_kinds`, `required_role` (`member`, `admin`, `owner`, or `hub_admin`), `positional` (ordered input field names), `writes`, input model, output model, description, error codes, and two functions: `plan(input, ctx, db) -> Plan` and `apply(plan, ctx, db) -> output`. Dispatch: validate input (`E_VALIDATION` with `details.fields`), resolve actor and company, check kind and role, open databases, call `plan`; with `dry_run` return `plan.preview` as the output model with `dry_run: true` and close without writing; otherwise call `apply` inside the write lock and a transaction. Registration fails if an input model declares a field named like a context field, so `E_CONTEXT_IN_INPUT` cannot arise through the CLI; the code exists for row 3.

Global options defined on every generated command and on the root: `--json`, `--dry-run`, `--company`, `--data-root`. Options belonging to rows 2 and 7 (`--reason`, `--directive`, `--source-ref`, `--idempotency-key`, `--as-token`) are not defined in this row.

The output model of every `list` is `ListOutput{items, count}`. Output models: `InitOutput{data_root, created, hub_admin_user_id, username, system_user_id}`, `CompanySummary{company_id, display_name, legal_name, home_currency, role, is_demo}`, `CompanyNewOutput{company_id, display_name, path}`, `CompanyShowOutput{company_id, display_name, role, schema_revision, is_demo, path (null unless hub admin), info: CompanyInfo, created_by_name, updated_by_name}`, `CompanyUseOutput{company_id, display_name}`, `CompanyAttachOutput{company_id, display_name, path}`, `CompanyDetachOutput{company_id, display_name, path}`, `CompanyRenameOutput{company_id, display_name, previous_display_name, path, moved}`, `DemoResetOutput{company_id, display_name, path, trashed_path (null if none)}`.

## Context and actor

`Client` and the CLI build `Context` with `interface`, `client_name`, `client_version`, `client_host`, fresh `session_id` and `request_id`. Actor resolution: `config.toml` has `[users.<os_login>]` with `user_id` and `default_company`. An unmapped OS login on an initialized root gets `E_NO_ACTOR` with a message naming the hub admin as the person who can add them (row 7 adds `user add`); in this row the data root serves the one human that ran `init`. `init` runs with no actor; the registry marks it.

Bootstrap invariant: `init` inserts the system user with `created_by` equal to its own id, then the first human with `created_by` the system user, then writes the mapping. `init` on an initialized root returns the existing state with `created: false` and changes nothing.

Every company write upserts the actor into `principals` before its own rows, in the same transaction.

## Data root and configuration

Resolution: `--data-root`, `BOOKFLOW_DATA_ROOT`, `~/.bookflow`. `init` creates `hub.db`, `config.toml`, `companies/`, `trash/`. Any other command on a root without `hub.db` returns `E_NOT_INITIALIZED`. Locality is checked on the data root before `init` creates anything.

## Rollout

Input: blueprint 9.1 fields except `closing_date`, `default_chart`, and `home_currency` immutability handled by omission from later `update`. Required and defaults per blueprint 9.2. `--interactive` prompts for each field with its default shown; non-interactive terminals get `E_USAGE`. `--chart` is not accepted in this row; `default_chart` is stored null, meaning none applied.

Stages, in order, with the recovery each failure leaves:

1. Validate input and display-name uniqueness (`E_NAME_TAKEN`).
2. Derive the folder name and reserve it with atomic directory creation, trying suffixes on `FileExistsError`.
3. Write the marker with `state = creating`. Create `attachments/`, `backups/`, `exports/`.
4. Create `company.db`, run company migrations, insert `company_info` and the creator's `principals` row, checkpoint, close.
5. In one hub transaction: insert `companies` (path relative to the data root, `is_demo`) and the owner membership.
6. Rewrite the marker with `state = ready` and the schema revision.

A failure in stages 2 to 4 removes the folder this attempt created and nothing else. A failure in stage 5 leaves a complete folder with no registration: it is invisible, `company new` with the same name takes the next suffix, and `attach` can adopt it after its checks, which also repair the marker. A failure in stage 6 leaves a registered company with a `creating` marker; every open repairs the marker from the database when the hub row exists.

## Rename

`company rename --name <new> [--move]`. Order: check uniqueness; update `company_info.display_name`; update the hub row; rewrite the marker; with `--move`, derive the new folder name excluding the company's own folder from the collision check, rename the directory, then update the hub path. A case-only rename on a case-insensitive filesystem goes through a temporary name. A failure after the directory rename and before the hub update is repaired on the next open: the hub path no longer exists, the marker in the folder found by scanning `companies/` for the company id wins, and the hub path is rewritten. A rename with the database open by another process fails with `E_DB_BUSY` before anything changes.

## Attach and detach

`attach` performs the checks of blueprint 3.1 in order, opening the database read-only, and registers in one hub transaction with owner membership for the actor. `detach` removes the registry row and memberships in one transaction and leaves the folder untouched.

## Company selection

Blueprint 5.3: id first (ULID shape), then display name after NFC and case folding; `E_COMPANY_AMBIGUOUS` cannot occur because display names are unique, so the code is not declared. `BOOKFLOW_COMPANY` and `default_company` resolve through the same function. Non-members and nonexistent companies get `E_COMPANY_NOT_FOUND` from the same code path; hub admins resolve any company.

## Filesystem locality and locks

`fs.py`: Linux reads `/proc/self/mountinfo` and picks the longest mount point prefix of the resolved path; macOS calls `statfs` through `ctypes` and reads `f_fstypename`; Windows refuses UNC paths and drives whose `GetDriveTypeW` is remote, and accepts fixed and removable drives. Allowlist per blueprint 3.2; otherwise `E_NETWORK_SHARE` or `E_FS_UNKNOWN`. Applied to the data root at `init`, to `hub.db` and each `company.db` on every open, and to the `attach` path.

`locks.py`: `fcntl.flock` or `msvcrt.locking` on the `.lock` file, exclusive, retried for 5 seconds, then `E_DB_BUSY` with the holder read from the file. The lock file holds hostname and pid. Held from before migration until after the closing checkpoint.

`engine.py`: `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`; `PRAGMA wal_checkpoint(TRUNCATE)` on close of a writable open.

## Migrations and backups

Alembic chains for hub and company, run programmatically. `migrate.py` compares `alembic_version` with the chain: unknown revision returns `E_SCHEMA_UNKNOWN`; behind head migrates after a backup taken with `sqlite3.Connection.backup` into `backups/`; the marker is rewritten with the new revision. A test migrates a database with a synthetic second revision, restores the backup, and compares dumps.

## Demo reset

Hub admin only. Finds companies with `is_demo` in the registry, moves each folder to `trash/<folder>-<timestamp>/` and removes its hub rows in one transaction, then runs the rollout with the seed and `is_demo = true`, marker `demo = true`. Never touches a company not flagged demo.

## Money

`Money(minor_units: int, currency: str)`. Constructor and parser reject `float` and `bool`; parsing accepts a decimal string with optional trailing currency code. The currency table is a data file in the package listing ISO 4217 codes and minor units; no dependency.

## Library surface

`bookflow.connect(data_root=None, os_login=None, client_name="python") -> Client`. `Client.run("company new", {...}, dry_run=False)` and `Client.company.new(...)`. Raises `BookflowError`. No engine, session, or table is importable from the package root.

## CLI rendering

Without `--json`: `list` renders `items` as a table; `show` renders one field per line with nested `info` indented; writes print the output's identifying fields. With `--json`: the output model as one object. Errors: always JSON on stderr; a one-line message first when not `--json`; usage errors as `E_USAGE`. Exit codes per blueprint 5.4. `--help` is generated and includes the global options after the verb.

## Edges

- Not built here and not faked: audit tables, conflict contract, presence, idempotency (row 2); HTTP, workbench (row 3); documentation generator (row 4); charts and lists (row 5); notes and attachments (row 6); passwords, tokens, `user add`, per-command role matrix beyond this row's table (row 7). Writes in this row are recorded only by the common fields and `principals`; the audit log that the accountability quality requires arrives in row 2, and this row states that gap rather than claiming to close it.
- Service functions take `Context` now so row 2 adds audit writes without changing signatures.
- The build machine runs Linux; macOS and Windows filesystem branches are unit-tested with mocked system calls and marked as untested on real hardware in `design/architecture.md`.

## Verification the builder will perform

The test suite above, green, including the subprocess run. Then, by hand and recorded in `architecture.md`: the row's done sequence with `--json` on every step, on a fresh data root, plus the copy-and-attach on a second root and a concurrent-writer attempt from two shells.
