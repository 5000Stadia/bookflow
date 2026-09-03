# Bookflow — blueprint

This document states what Bookflow is and how every part of it works. It is written for a reader with no prior knowledge of the project. Exact values carry this license: change it if it makes the product better, and say why.

## 1. What it is

Bookflow is a multi-company double-entry accounting system for small businesses, built on the conventions of desktop bookkeeping software. It is a Python library first. A CLI, an HTTP host, and an MCP server are thin adapters over the library. Graphical clients, on desktop, web, or mobile, are built later against the HTTP host or the library directly.

Pillars, in rank order:

1. **Correct books.** Every posted transaction balances. History is never destroyed.
2. **Agent-usable.** An AI agent with only the documentation can operate every command.
3. **One contract, many surfaces.** CLI, HTTP, MCP, and GUI call the same commands and get the same results.
4. **Accountable.** Every write records actor, interface, principal, and reason.
5. **Isolated.** An organization and its companies are invisible to anyone without membership.

## 2. Layering

```
clients      browser (workbench, later the GUI)   AI agent      shell / scripts    Python programs
                 |                                   |               |                  |
adapters     HTTP host (bookflow serve)          MCP server        CLI (bookflow ...)   |
                 \___________________________________|_______________|__________________/
                                                 |
core         command registry  ->  services  ->  repositories  ->  SQLite (hub.db, company.db)
```

- **Core** contains all logic. It has no knowledge of terminals, HTTP, or MCP.
- **Adapters** parse their protocol into a command input plus a context, call the core, and render the command output. They contain no business logic.
- **Clients** never touch the database.

The graphical interface is a web application served by the HTTP host. Run locally, the host binds to loopback and the browser opens `http://127.0.0.1:8123`, which is the desktop-application shape. Run on a server with `--allow-network` behind TLS, the same application with the same login serves many people, which is the hosted shape. Nothing in the interface changes between the two. A desktop wrapper that launches the host and opens a window is a packaging step, not a different client. Any machine other than the host talks to it over HTTP; the host is the only process that opens a company database on behalf of remote clients.

### 2.0 The command registry

A command is a function plus an input model and an output model. The registry maps the command name, such as `customer create`, to those three things and to the command's error codes and one-sentence description. The CLI, the HTTP routes, the MCP tools, the documentation pages, and the workbench forms are all generated from the registry. Nothing is written per command in any adapter.

### 2.1 Technology

| Concern | Choice |
|---|---|
| Language | Python 3.12 or later |
| Package manager | uv |
| CLI | Typer |
| Command and record models | Pydantic v2 |
| Database access | SQLAlchemy 2 Core |
| Migrations | Alembic, one migration chain for hub.db and one for company.db |
| Storage | SQLite in WAL mode |
| Password hashing | argon2id |
| HTTP host | FastAPI over uvicorn |
| Workbench | Jinja2 templates plus HTMX served by the host; no JavaScript build step, no Node |
| MCP | Official Python MCP SDK, tool schemas generated from command models |
| Tests | pytest |
| Identifiers | ULID strings, generated in the core |

Everything must remain portable to PostgreSQL. No SQLite-only SQL in repositories. No triggers.

## 3. Data on disk

```
<data_root>/                     default ~/.bookflow, override with BOOKFLOW_DATA_ROOT
  hub.db                         users, credentials, tokens, company registry, memberships, hub audit
  config.toml                    per-OS-user mapping to a Bookflow user and that user's default company
  root.lock                      the one lock for this data root; holds the holder's hostname, pid, and command while held
  backups/
    hub-<YYYY-MM-DD-HHMMSS>.db   copies of hub.db taken before hub migrations and by `hub backup`
  organizations/
    <Organization Name>/         one folder per organization: the business entity that holds one or more companies
      bookflow-organization.toml organization id
      <Company Name>/            one folder per company, named after the company
        bookflow-company.toml    company id and rollout state; display name and schema revision as an informational copy
        company.db               every table for one company (SQLite adds company.db-wal and company.db-shm while open)
        attachments/
          <first two hex of sha256>/<sha256>    content-addressed file bodies
        backups/
          <YYYY-MM-DD-HHMMSS>.db copies of company.db taken with the SQLite backup API, before migrations and by `company backup`
        exports/                 files written by report `--csv` and by `company export`; safe to empty
  trash/
    <Organization Name>/<Company Name>-<YYYY-MM-DD-HHMMSS>/   company folders removed by `company delete`
    <Organization Name>-<YYYY-MM-DD-HHMMSS>/                  organization folders removed by `demo reset` or `organization delete`; `trash empty` deletes everything here
```

### 3.0 Organizations

An organization is the business entity that holds one or more companies: a single business with one set of books, or a holding or accounting firm keeping books for many. Access is granted at the organization or at the company. A member of an organization sees every company in it, including ones created later; a member of one company sees only that company. Nobody sees an organization they have no membership in. A hub admin (4.1) sees every organization. Two organizations on one data root are invisible to each other, which is the hosted-service shape: the operator is hub admin, each client is an organization, and each client's people and agents are members of it.

Table `organizations` in hub.db: `id`, `display_name` and `name_key` (unique across the hub after trimming, NFC, and case folding), `path` relative to the data root, `is_demo`, common fields. Table `companies` in hub.db: `id`, `organization_id`, `display_name`, `name_key` (unique within the organization), `path` relative to the data root, `pending_path`, `legal_name`, `home_currency`, `schema_revision`, `is_demo`, common fields. Organizations carry `pending_path` too. A member of one company sees its organization's row with `access: company`, since the `Organization/Company` selection form needs the name. Moving an organization folder updates every path under it in one transaction. Every company belongs to exactly one organization. `organization new`, `organization list`, `organization show`, `organization rename [--move]`. Organizations are created and renamed only by hub admins, because their names are unique across the hub and a collision would reveal another organization to a non-admin. When the actor can see exactly one organization, `company new` defaults to it; otherwise `--organization` is required. Display names of organizations and companies may not contain `/`.

### 3.1 Company folders

- The company folder lives inside its organization's folder. Organization folder names follow the same derivation rule as company folder names. Company display names are unique within their organization.
- The folder name is derived from the display name and chosen under the data-root lock by listing the parent and comparing existing names after NFC and case folding, so that two names that differ only in case or normalization never become two folders that one filesystem would merge; the directory creation then reserves exactly the chosen name. Derivation: Unicode NFC normalization; `/ \ : * ? " < > |` and characters below U+0020 replaced by a space; runs of whitespace collapsed; leading and trailing spaces and dots removed; truncated to 90 bytes of UTF-8 at a character boundary and then stripped again; `Company` if empty; if the part before the first dot is, case-insensitively, a Windows reserved device name (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`), ` Co` is appended. If a folder with that name exists, compared after NFC and case folding, ` (2)`, ` (3)`, and so on is appended. Reservation is the atomic directory creation itself; a creator that loses the race takes the next suffix. A company's own folder is excluded from the collision check when it is renamed.
- Display names are trimmed and whitespace-collapsed on input, may not contain `/` (legal names may), and are unique within the organization compared after NFC and case folding; `E_NAME_TAKEN` otherwise.
- The hub registry stores the folder path relative to the data root, so a whole data root can be moved or copied.
- **The hub is the source of truth for registration**: which organization a company belongs to, its display name, its folder path, its memberships, and its demo flag. **The company database is the source of truth for the books**: everything in `company_info` except `display_name`, and every list and transaction. `company_info.display_name` and the marker's display name and revision are one-way copies written after the hub commit for the benefit of `attach` on another machine; if the copy is not written, nothing depends on it, and the next writable open rewrites it. Organization association is local to a data root and is not carried in the folder: an attached folder joins the organization whose folder contains it.
- A company folder is in exactly one of these states. **Incomplete**: the marker says `creating`; rollout did not finish. It is ignored by every command, `attach` refuses it with `E_INCOMPLETE_COMPANY`, and the operator removes it by hand. **Unregistered**: the marker says `ready` and no hub row references the folder; the database is complete. `attach` adopts it after validation. **Registered**: a hub row references the folder. Rollout writes `ready` only after the database is complete and before the hub row is written, so a crash at any point leaves a folder in one of these states.
- Folder moves are the only operation that cannot be inside a hub transaction, so the intended move is recorded before it happens. `rename --move` is: one transaction commits the new name and writes `pending_path`; the folder is moved with a no-replace rename (`renameat2` with `RENAME_NOREPLACE` on Linux, `renamex_np` with `RENAME_EXCL` on macOS, `MoveFileExW` without the replace flag on Windows), a case-only change going through `<target>.moving-<id>`, which resolution also checks for and completes; a second transaction sets `path` to `pending_path` and clears it. Resolution of a company whose `path` does not exist consults `pending_path` and, when that folder or its `.moving` form exists, completes the operation on the spot: a pending move commits the path, a pending trash completes the deletion. When neither exists the error is `E_COMPANY_MISSING`. Rerunning `rename --move` with the current name completes an interrupted move and returns `moved: true`; with no pending move it returns `moved: false` and writes nothing. Organizations use the same `pending_path` on their own row; a completed organization move rewrites the stored `path` (and any `pending_path`) of every company under it as a versioned, audited update recorded as entries on the move event. If a registered path does not exist on open, the error is `E_COMPANY_MISSING` naming the company; there is no scan.
- The company id is `company_info.id`, the single row's primary key. `attach` reads the marker, opens the database read-only, and refuses on any mismatch.
- Every company is exactly one folder under its organization's folder. Nothing about a company is written outside it, and nothing that is not about that company is written inside it. Temporary files go to the operating system temporary directory.
- `company attach <path> [--name <display name>]` is a hub-admin command that registers a folder already under a registered organization's folder, under the display name copied in the folder or the one given. Any other path is refused with `E_NOT_IN_ORGANIZATION_DIR`. Before registering, `attach` verifies: the path resolves, after symlinks, to a directory directly under that organization's folder; the filesystem is local; the marker and database exist and agree on the company id; the database schema revision is known to this version, behind the current revision or not, since the first writable open migrates it; the marker state is `ready`; the id is not registered (`E_ALREADY_ATTACHED`); the display name is free in the organization (`E_NAME_TAKEN`). Any failure leaves the hub unchanged. Registration is one hub transaction and creates no membership, since hub admins need none. A folder with no display-name copy needs `--name` (`E_ATTACH_INVALID`, `details.check` `display_name`). Both the given path and the organization folder are resolved through symlinks before comparison. `company detach <company>`, hub admin only, deletes the registry row and its memberships in one audited transaction whose entries carry the deleted rows, and leaves the folder in place. Restoring from a copy on the same machine is detach the live company, move the copy into the organization's folder, attach it.
- `demo reset` acts only on the organization whose hub row carries `is_demo`, which only `demo reset` sets.

### 3.2 Locality and locking

- Before opening `hub.db`, any `company.db`, or an `attach` path, the core resolves symlinks and determines the filesystem type. Local types are an allowlist: `ext2`, `ext3`, `ext4`, `xfs`, `btrfs`, `f2fs`, `zfs`, `tmpfs`, `overlay`, `apfs`, `hfs`, `ntfs`, `exfat`, `vfat`, `fat32`, `refs`. On Linux the type comes from the mount table; on macOS from `statfs`; on Windows a UNC path or a drive whose type is remote is refused. Any other type, and any failure to determine the type, is refused: `E_NETWORK_SHARE` when the type is known and not local, `E_FS_UNKNOWN` when it cannot be determined.
- **One lock per data root.** Every command, reading or writing, takes an exclusive lock on `root.lock` for its duration; the holder writes its hostname, pid, and command name into the file while it holds it. A process that cannot take the lock within 5 seconds (`BOOKFLOW_LOCK_TIMEOUT` overrides, for tests) fails with `E_DB_BUSY` whose details carry the holder's command name and how long it has held the lock, and nothing else, since the caller's role cannot be known before the lock is held. The lock file's contents are ephemeral and are not part of any data. Everything a command does, including resolving the actor and company, checking roles, opening databases, moving folders, and writing `config.toml`, happens under this lock, so nothing it resolved can change under it. Commands are short, so contention is rare; the host process (section 15.2) holds the lock for its lifetime and every other process on that machine finds the host's pid in the lock file and sends its command to the host instead. Row 3 builds that hand-off; until then a running host simply blocks other processes with `E_DB_BUSY`.
- On closing a writable database, the WAL is checkpointed and truncated, so a folder no process has open is safe to copy with ordinary file tools.
- Migrations run under the root lock. Before migrating an existing database, a backup is written with the SQLite backup API to the company's `backups/` or to `<data_root>/backups/` for the hub.
- Read-only opens set `query_only` and never change the journal mode. A read-only open of a database behind the current revision returns `E_SCHEMA_BEHIND` whose message names `bookflow upgrade`, which migrates the hub and every company the actor may write, or tells a read-only user to ask someone with write access; any writable open also migrates. `upgrade` migrates one database at a time, each with its own audit event after it commits, reports migrated, skipped, missing, and failed databases, stops at the first failure, and converges on rerun.
- Every command runs with umask `077`, so every directory Bookflow or SQLite creates is `0700` and every file `0600`; `attach` refuses a folder whose mode is wider (`E_ATTACH_INVALID` with `details.check` `mode`). On Windows the default ACL of the creating user applies.

### 3.3 Schema versions

Both databases carry Alembic's `alembic_version` table, which is authoritative. Opening a database whose revision is not in this version's migration chain returns `E_SCHEMA_UNKNOWN` with the revision in the details and a message saying to upgrade Bookflow; the database is not modified. A database behind the head is migrated on writable open.

## 4. Identity

### 4.1 Users

Table `users` in hub.db.

| Field | Type | Meaning |
|---|---|---|
| id | ULID | |
| kind | enum | `human`, `agent`, `system` |
| username | text, unique | login name for humans, handle for agents |
| display_name | text | |
| owner_user_id | ULID, nullable | for `agent` kind: the human that owns this agent. Required for agents. |
| password_hash | text, nullable | humans only |
| hub_admin | bool | operator of the data root: sees and manages every organization and company, and manages users |
| timezone | IANA name, nullable | the zone timestamps are rendered in for this user; the company's zone when null |
| active | bool | |
| created_at, updated_at, version | | see section 6 |

There is exactly one `system` user per data root, created by `bookflow init`. Scheduled jobs and migrations act as it. Its `created_by` is its own id; it is the only self-referencing row. The first human user, created by `init`, is a hub admin. Hub admins are the operators of the data root: they see every organization and company and their folder paths, and they create organizations. Every other user sees only what their memberships grant and never a path.

`organization new` creates no membership; a hub admin grants the organization's first owner with `user add` and `membership grant` (row 7).

### 4.1a Principals mirror

Table `principals` in company.db: `user_id`, `username`, `display_name`, `kind`, `first_seen_at`, `last_seen_at`. Every write to a company upserts the acting user and, when present, the `on_behalf_of` user. It is the company-local copy of who the ids in `created_by`, `updated_by`, and the audit log refer to, so a copied folder renders its own provenance without the hub it came from. `show` outputs resolve `*_by` fields to names through this table.

### 4.2 Tokens

Table `api_tokens` in hub.db. Tokens are for agents and for GUI or HTTP sessions.

| Field | Meaning |
|---|---|
| id | ULID |
| user_id | the user this token authenticates |
| on_behalf_of | for agent users: the human principal this token acts for; issued by that human or by an admin; null for human users |
| token_hash | sha256 of the secret; the secret is shown once at creation and never stored |
| label | free text |
| expires_at | nullable |
| last_used_at | |
| revoked_at | nullable |

An agent acting for several people holds one token per person. The principal is fixed by the token, never chosen per call. `token issue --for-agent <agent> --on-behalf-of <human>` is run by that human or by an admin of a company they share.

### 4.3 Companies and memberships

The company registry table is defined in section 3.0.

Table `memberships` in hub.db: `user_id`, `scope_type` (`organization` or `company`), `scope_id`, `role`, `granted_by`, `granted_at`, `revoked_at`. An organization membership applies to every company in the organization; a company membership applies to that company only. When both apply, the higher role wins. Creating a company requires the admin or owner role on its organization or hub admin, and always grants the creator an explicit company-scope owner membership.

Roles and what they may do:

| Role | Read | Write lists | Post transactions | Manage members | Company settings, closing date, delete |
|---|---|---|---|---|---|
| readonly | yes | no | no | no | no |
| standard | yes | yes | yes | no | no |
| admin | yes | yes | yes | yes | yes except delete |
| owner | yes | yes | yes | yes | yes |

An agent's memberships are granted by a human with admin or owner role on that organization or company. An agent never inherits its owner's memberships.

### 4.3a Configuration file

`config.toml` holds one `[users.<os_login>]` table per mapped OS login with `user_id` and `default_company`, and `[client]` with `display_name`. It is rewritten atomically (temporary file and rename) under the root lock, after the hub transaction that made it necessary. A `default_company` that no longer resolves is ignored and reported in the error's `details.source` as `default`; a command that removes a company clears such defaults, and a failure to do so is harmless. An unreadable or malformed file is `E_CONFIG_INVALID` with the path.

### 4.3b Capabilities and features

Roles are the coarse grant. Two finer shapes exist from the first version so later rows can enforce them without changing the registry or the schema of what already exists:

- **Capabilities.** Every command declares a `capability`, a dotted area such as `lists.customers`, `ledger.post`, `banking`, `payroll`, `audit`, or `settings`; by default it is derived from the command's noun. Each role maps to a default set of capabilities (table `role_capabilities`, seeded), and a membership may carry `grants` and `denies` lists of capabilities that override its role (columns on `memberships`, null by default). Dispatch checks the role today and the resolved capability set once row 7 lands; the check has the same shape either way: `E_PERMISSION` names the capability.
- **Features.** Some functionality is a service unlocked for a whole company or organization: payroll, bank feeds, email delivery, and later paid integrations. Every command may declare a `feature`; table `features` in the hub (`scope_type`, `scope_id`, `feature`, `enabled`, `enabled_by`, `enabled_at`, `source` such as `admin` or `license`) says which are on. A command whose feature is not enabled for the selected company returns `E_FEATURE_DISABLED` naming the feature and who can enable it. `feature enable` and `feature disable` are hub-admin or organization-owner commands. Nothing in the first release declares a feature.

### 4.4 Authentication per interface

| Interface | How the actor is established |
|---|---|
| CLI, local | The OS login, read from the process, is mapped to a Bookflow human user in `config.toml`. `bookflow init` creates this mapping for the first owner; `user add` creates others. `--as-token <secret>` acts as a token's user instead. |
| Python | `bookflow.connect()` resolves the actor the same way as the CLI and records interface `python`; a token may be passed instead. There is no way to name another user. |
| HTTP | Bearer token. |
| MCP | Token from the server's launch configuration. One MCP server process serves one token. |
| GUI | Whatever the GUI uses to obtain a token; the GUI presents a bearer token to the host, or, when in-process, a password login that yields a session token. |

## 5. The command contract

Every operation is a command. A command has a name, an input model, an output model, and a set of error codes. The CLI, HTTP host, and MCP server are generated from command definitions, not written by hand per command.

### 5.1 Names

`<noun> <verb>`, nouns singular: `company new`, `customer create`, `invoice post`, `audit list`. Nouns are conventional bookkeeping names.

Verbs used across lists: `create`, `update`, `show`, `list`, `activate`, `deactivate`. Verbs used on transactions: `post`, `show`, `list`, `void`. Reports use `report <name>`.

Every command has a kind: `read`, `write`, or `advisory` (presence: opens the company writable, records no event, takes no context options). Every command has a scope. **Hub** commands act on the data root and take no company: `init`, `organization *`, `company new`, `company list`, `company use`, `company attach`, `company detach`, `company delete`, `demo reset`, `user *`, `token *`. **Company** commands act on the selected company (section 5.3): everything else, including `company show`, `company update`, `company rename`, `company backup`. A hub command that names a company takes it as a positional argument accepting an id or a display name.

Positional arguments are declared per command in the registry; everything else is an option. `--json` and `--data-root` exist on every command; `--dry-run` only on commands that write; `--company` only on company-scope commands; the context options of 5.2 only on commands whose scope and version support them. The CLI accepts these options both before and after the noun and verb; the same option in both positions with different values is `E_USAGE`, and an option given to a command that does not define it is `E_USAGE`. `--help` on every command lists exactly the options it accepts.

`E_USAGE` covers syntax only: an unknown option, an unknown command, or a conflicting placement. Every value problem, including a missing required field or positional or an unparseable value, is `E_VALIDATION` from the input model, so the library and the CLI return the same code for the same input.

Nested input fields become flags joined with `-`: the `address` object's `line1` is `--address-line1`. Lists of scalars repeat the flag. Anything deeper takes a JSON value.

`--interactive`, on the CLI only and only on commands that write, prompts on stderr for every input field not supplied as an option or positional, using the field's description, default, and choices from the registry; a non-terminal stdin is `E_USAGE`. It never reaches HTTP or MCP.

### 5.2 Context

Every command receives a context that the adapter builds. No field of the context is accepted from command input; an adapter that receives a context field in the input rejects the call with `E_CONTEXT_IN_INPUT`.

| Field | Type | Source |
|---|---|---|
| actor_id | ULID | authenticated user |
| actor_kind | enum | from the user record |
| on_behalf_of | ULID, nullable | for agent actors, the principal bound to the token; for system, the schedule owner; null for humans |
| interface | enum | `cli`, `http`, `mcp`, `gui`, `python`, `system` |
| client_name | text | e.g. `bookflow-cli`, `bookflow-desktop` |
| client_version | text | |
| client_host | text | hostname of the machine the adapter runs on |
| session_id | ULID | one per CLI invocation, HTTP session, MCP server process, or GUI login |
| request_id | ULID | one per command call |
| idempotency_key | text, nullable | caller supplied; see 6.5 |
| reason | text, at most 140 characters, nullable | caller supplied; see 5.8 |
| directive_id | ULID, nullable | caller supplied; a standing instruction from section 5.8 |
| source_ref | text, nullable | caller supplied; an identifier for what triggered the write, such as an email id or attachment id |
| company_id | ULID, nullable | the company the command runs against; null for hub commands |

Every field but `directive_id` and `idempotency_key` is recorded from the first version. `reason` and `source_ref` exist on every writing command from the first version; `directive_id` and `idempotency_key` from the rows that store them. They are set by the caller through global flags on the CLI (`--reason`, `--directive`, `--source-ref`, `--idempotency-key`), request headers on HTTP (`X-Bookflow-Reason`, `X-Bookflow-Directive`, `X-Bookflow-Source-Ref`, `Idempotency-Key`), and named tool arguments on MCP.

### 5.3 Company selection

For company-scoped commands, the company is resolved in this order, first match wins:

1. `--company <id-or-display-name>` on the CLI, path segment on HTTP, tool argument on MCP.
2. Environment variable `BOOKFLOW_COMPANY`.
3. `default_company` in `config.toml`, set by `bookflow company use <id>`.

With none of the three, the error is `E_COMPANY_NOT_FOUND` with `details.source` `none`. In the library, `company=` on a call is step 1 and `Client.use_company` sets a session value that is also step 1, ahead of the environment.

A value is tried as an id first, then as `Organization/Company`, then as a company display name compared after NFC and case folding among the companies the actor can see; a bare name matching companies in more than one organization is `E_COMPANY_AMBIGUOUS`. `company use` stores the id. Uniqueness of display names is enforced by a stored `name_key` (NFC, case-folded) with a unique constraint, scoped by organization for companies. `company list` and `organization list` read only the hub. If the resolved company is not among the actor's memberships and the actor is not a hub admin, the error is `E_COMPANY_NOT_FOUND`. The same error is returned whether the company does not exist or the actor lacks membership.

### 5.4 Output

Every command returns a structured result. On the CLI:

- `--json` prints the output model as one JSON document on stdout.
- Without `--json`, a human-readable table or summary goes to stdout.
- Warnings and progress go to stderr.
- Exit code 0 on success, 1 on a rejected command with a named error, 2 on invalid usage, 3 on internal failure.

Every `list` output is `{"items": [...], "count": n}`, and every item carries the common fields of 6.1 plus `access` (`hub_admin`, `organization`, or `company`) and `role` (null for hub admins without membership). Every output model for a write includes the identifying fields of what it wrote, `dry_run`, and `warnings`, a list of strings for work the command finished without, such as a schema migration left for `upgrade`. Fields that hold filesystem paths are null, never absent, in every output and error detail unless the actor is a hub admin, and error messages are built from templates whose path arguments pass through the same rule; dispatch applies it, not individual commands.

Errors are always JSON documents on stderr with `code`, `message`, and `details`, whether or not `--json` was given; without `--json` a one-line message precedes the JSON. Usage errors from the CLI parser are emitted the same way with code `E_USAGE`. Input validation failures are `E_VALIDATION` with `details.fields`, a list of `{"field", "problem"}`. Unknown input keys are `E_VALIDATION`; input keys named like context fields are `E_CONTEXT_IN_INPUT` on every surface. Every command may return the infrastructure codes, listed once in the documentation: `E_USAGE`, `E_VALIDATION`, `E_CONTEXT_IN_INPUT`, `E_NOT_INITIALIZED`, `E_NO_ACTOR`, `E_PERMISSION`, `E_COMPANY_NOT_FOUND`, `E_COMPANY_AMBIGUOUS`, `E_ORGANIZATION_NOT_FOUND`, `E_REASON_REQUIRED`, `E_FEATURE_DISABLED`, `E_DB_BUSY`, `E_NETWORK_SHARE`, `E_FS_UNKNOWN`, `E_SCHEMA_UNKNOWN`, `E_SCHEMA_BEHIND`, `E_CONFIG_INVALID`, `E_IO`, `E_INTERNAL`. `E_IO` carries `details.operation` and `details.errno`; `E_INTERNAL` is the only code for exit 3. An unknown command name is `E_USAGE` on every surface. `E_ORGANIZATION_NOT_FOUND` and `E_COMPANY_NOT_FOUND` are returned identically for absent and for inaccessible targets. Every not-found or ambiguous error for a named record carries `details.suggestions`, up to three close matches drawn only from what the caller can see, so an agent that misspelled a name can correct it without a second lookup. Command-specific codes are listed per command. Error codes are stable strings prefixed `E_`. Every command's documentation lists the codes it can return. An option that a command does not support in the current version is not defined on that command; nothing is accepted and ignored.

### 5.5 Dry run

Every command that writes accepts `--dry-run`. It runs validation and returns the output model that a real run would return, with `dry_run: true`, and writes nothing at all: no record, no configuration, no folder, no audit row, no schema migration, and no pending-move completion. It takes the data-root lock and opens every database read-only; a database behind the current revision makes the dry run fail with `E_SCHEMA_BEHIND` naming `upgrade`, and `upgrade --dry-run` reports what would migrate without migrating.

### 5.6 Money on input

Amounts on input are decimal strings, never floats: `"123.45"`. An optional currency code follows: `"2345 JPY"`. The CLI accepts `--amount "2345 JPY"`. Amounts with more decimal places than the currency allows are rejected with `E_AMOUNT_PRECISION`.

### 5.7 Dates

Dates are ISO 8601 `YYYY-MM-DD`. Timestamps are stored as UTC and rendered in every output as ISO 8601 with the offset of the viewer's zone: the acting user's `timezone`, else the company's. Dates without times are interpreted in the company's zone. Zone names are IANA names resolved through the standard library's `zoneinfo`, whose data is refreshed by the `tzdata` package dependency so rule changes arrive with ordinary updates.

### 5.8 Reasons and directives

Any write by an `agent` or `system` actor must carry a `reason`, a `directive_id`, or both; otherwise `E_REASON_REQUIRED`. Human actors may supply either and are never required to.

`reason` is one short phrase, at most 140 characters. The MCP tool description says: one phrase, under ten words, what triggered this.

A directive is a standing instruction recorded once and cited many times. Table `directives` in company.db:

| Field | Meaning |
|---|---|
| id | ULID |
| code | short display code, `SI-` plus a per-company sequence, e.g. `SI-3` |
| text | the instruction, as the principal gave it, at most 1000 characters |
| given_by | the human whose instruction it is |
| recorded_by | the actor that recorded it, human or agent |
| active | |

Commands: `directive add --text`, `directive list`, `directive show`, `directive deactivate`. An agent that is told "when I finish a job and tell you the amount, post it and invoice it to the email on file" records that once and then posts with `--directive SI-3 --reason "job done, 1,250"`. `audit list` and `audit show` render the directive code and text beside the reason. A deactivated directive can no longer be cited.


## 6. Records, versions, and concurrency

### 6.1 Common fields

Every table except `audit_events`, `audit_entries`, `presence`, `principals`, `memberships`, `idempotency_keys`, `sequences`, `exchange_rates`, and `custom_field_values` carries:

| Field | Meaning |
|---|---|
| id | ULID |
| version | integer, starts at 1, increments on every write |
| created_at, created_by, created_via | timestamp, user id, interface |
| updated_at, updated_by, updated_via | timestamp, user id, interface |

Every `show` and `list` output includes these fields.

### 6.2 Versioned writes

An `update` command accepts `expected_version`. When present and equal to the current version, the write proceeds. When present and different, the command is rejected with `E_VERSION_CONFLICT` and details:

```json
{
  "current_version": 7,
  "updated_by": "01J...",
  "updated_by_name": "claude-agent",
  "updated_via": "mcp",
  "seconds_since_update": 0.6,
  "changed_fields": ["phone", "notes"]
}
```

unless the fields the caller is writing are disjoint from `changed_fields`, in which case the write is merged onto the current version, succeeds, and the output carries `merged_over_versions: [6, 7]`.

### 6.3 Blind writes

An `update` without `expected_version` proceeds against the current version. The output carries `previous_version`, `previous_updated_by`, `previous_updated_via`, and `seconds_since_previous_update`. If the previous write was by a different actor within the company's `recent_activity_window_seconds` (default 60), the output also carries `recent_concurrent_activity: true` and the CLI prints a warning to stderr.

### 6.4 Presence

Table `presence` in company.db: `record_type`, `record_id`, `user_id`, `interface`, `started_at`, `heartbeat_at`. Presence is not audited. A GUI registers presence when a user opens a record for editing and sends a heartbeat every 30 seconds. Presence older than 90 seconds is ignored. `show` output includes `editing_by` listing live presence. Presence never blocks a write.

### 6.5 Idempotency

Any `create` or `post` command may carry an idempotency key. Table `idempotency_keys` stores `key`, `actor_id`, `command`, `result_id`, `created_at`. A repeat of the same key by the same actor returns the original result with `idempotent_replay: true` and writes nothing. Keys expire after 30 days.

### 6.6 Deactivation, void, and deletion

List records are never deleted through the API. `deactivate` sets `active = false`; deactivated records are excluded from `list` unless `--include-inactive`, and cannot be referenced by new transactions. Transactions are never deleted; see section 10.5. The only deletion command is `company delete`, which requires the owner role and the literal company id repeated in `--confirm`.

## 7. Audit log

Two tables in company.db, plus the same pair in hub.db for hub commands. Hub commands that create, register, detach, rename, or trash organizations and companies write a hub audit event from the first version that has them; the hub audit is never behind the commands it records.

`audit_events`: one row per command execution that wrote anything.

| Field | Meaning |
|---|---|
| id | ULID |
| seq | integer assigned by the writer as one more than the highest, under the data-root lock; the feed cursor |
| at | timestamp |
| command | command name |
| actor_id, actor_kind, on_behalf_of | from context |
| interface, client_name, client_version, client_host | from context |
| session_id, request_id, idempotency_key | from context |
| reason, directive_id, directive_code, source_ref | from context; `directive_code` is stored so hub events can show it |
| summary | one line, returned by the command with its output, e.g. `posted invoice 1043 to Acme Plumbing for 1,250.00` |

`audit_entries`: one row per record touched by the event.

| Field | Meaning |
|---|---|
| id | ULID |
| event_id | |
| record_type, record_id | |
| action | `create`, `update`, `delete`, `deactivate`, `activate`, `post`, `void`, `link`, `unlink`, `migrate`, `baseline` |
| version_before, version_after | |
| after | JSON snapshot of the record after the write; on `deactivate` and `void`, the record as it stands after; null on `delete` |
| before | JSON snapshot before the write, stored only on `delete`, since the previous entry's `after` supplies it otherwise |

The snapshot before a write is not stored. It is the `after` of the previous entry for the same record, found by `(record_type, record_id, version_before)`, and is null on create. `audit show` returns both and the field diff. Snapshots hold only stored fields, never derived ones such as `full_name`, `quantity_on_hand`, or `open_balance`, and never secret values: `password_hash`, `token_hash`, and `tax_id` are stored as a short hash of the value, so a change is visible and the value is not. Schema migrations are audited as `migrate` entries by the system user with `on_behalf_of` the actor whose command triggered them, and update the hub's `schema_revision` projection in the same command. A transaction snapshot includes its lines, so posting a 20-line transaction writes one entry. Snapshots over 512 bytes are stored zlib-compressed; the column is a blob with a one-byte prefix marking raw or compressed, from the first migration that creates the table.

Rules:

- No command updates or deletes audit rows. The repository layer exposes only insert and read.
- `audit list` filters by actor, actor kind, interface, record type, record id, command, and time range, and returns events with entry counts. `audit show <event_id>` returns the event with entries and a computed field diff per entry.
- `activity <record_type> <record_id>` merges audit entries, notes, and attachment links for that record in time order. The hub has `hub audit list`, `hub audit show`, and `hub audit tail`, hub-scoped, with the same filters: hub admins see every event; other users see events whose entries reference an organization or company they can see, and `audit show` on any other event returns `E_EVENT_NOT_FOUND` exactly as for a nonexistent id. Snapshots inside entries are redacted like any output: fields named `path` or ending in `_path` are removed for non-hub-admins. `audit list` filters: `--since`, `--until` (dates in the viewer's zone), `--actor` (id or username), `--kind`, `--principal`, `--via`, `--command`, `--record-type`, `--record-id`, `--limit`; `list` pages older with `--before` and `tail` pages newer with `--after`.
- Reads are not audited. Every write, including one that changes only `config.toml`, records an event. Presence (6.4) is advisory state, not a record, and its heartbeats are never audited.

`undo <event_id>` reverses a list-record event by writing the entry's before-state as a new versioned write, itself audited with `reason` `undo of <event_id>`; it refuses events that touched the ledger (`E_NOT_UNDOABLE`), which are voided instead.

### 7.1 Event feed

The audit log is the event stream. `audit tail --after <cursor> --limit <n>` returns events after the cursor in commit order with a new cursor; the cursor is the event's `seq`, an integer assigned in insertion order under the data-root lock, so no two events share a position and a restart never reorders the feed. `tail` accepts every `list` filter. `--follow` on the CLI keeps polling. The host exposes `GET /companies/{company_id}/events?after=<cursor>` as server-sent events, one event per message, with the same filters as `audit list`. A subscriber that stores its last cursor resumes with nothing missed. Webhooks are a later addition on top of this feed and are not in release 1.

## 8. Money and currency

### 8.1 Representation

An amount is `(minor_units: integer, currency: ISO 4217 code)`. Stored as two columns wherever an amount lives. In JSON output an amount is `{"amount": "12.50", "currency": "USD", "minor_units": 1250}`: the decimal string for reading, the integer for arithmetic, never a JSON number with a fraction. Currency codes are the upper-case three-letter codes in the package's currency table; anything else is `E_VALIDATION`. Displayed using the currency's decimal places. Arithmetic is integer arithmetic. Allocation across lines uses largest-remainder rounding so totals always reconcile.

### 8.2 Home currency

The company has one home currency, chosen at rollout and immutable afterwards. Every ledger line is in home currency.

### 8.3 Foreign-tagged amounts

Any amount input may carry a foreign currency code. At posting the core:

1. Looks up the rate for the transaction date in `exchange_rates` (`date`, `from_currency`, `to_currency`, `rate` as a decimal string, `source`, `entered_by`). An explicit `--rate` on the command overrides the table and is recorded with source `manual`.
2. Converts to home currency, rounding to the home currency's precision.
3. Stores on the line: `amount` (home), plus `original_amount`, `original_currency`, `rate_used`.

If no rate exists and none is supplied, the command is rejected with `E_NO_EXCHANGE_RATE`.

When a foreign-tagged payment settles a home-currency receivable or payable and the converted amount differs from the open balance, the difference posts to the seeded account `Exchange Gain/Loss`.

Rates are entered with `rate set` or fetched with `rate fetch --date <date> --from <code>`, which asks the configured provider and stores the result with `source` naming it. Providers implement one interface, `rate(date, from, to)`; the first is the European Central Bank's published reference rates through a keyless public API, and others are added as providers. The company setting `rates.auto_fetch`, off by default, lets a posting with a foreign amount and no stored rate fetch one first. A fetch sends only currency codes and a date, and the documentation of `rate fetch` and of the setting says so, which is what the intention's rule about data leaving the machine requires.

## 9. Company

### 9.1 Company info

Table `company_info` in company.db, exactly one row. This table and the rollout below are the completed inventory of the anchor's company information screen; the artifact critic checks the built form against it, and the human runs the side-by-side.

| Field | Meaning |
|---|---|
| legal_name | the name on tax forms |
| display_name | copy of the hub's display name, written after every hub change; read only by `attach`, never returned by `show` |
| tax_id_kind | `ein` or `ssn` |
| tax_id | `NN-NNNNNNN` for EIN, `NNN-NN-NNNN` for SSN, validated for shape only |
| industry | free text; selects the default chart during rollout |
| contact_name | |
| entity_type | `sole_proprietor`, `partnership`, `llc`, `s_corp`, `c_corp`, `nonprofit`, `other` |
| income_tax_form | `1040_schedule_c`, `1065`, `1120`, `1120s`, `990`, `other` |
| address (line1, line2, city, state, postal_code, country) | the company address shown on forms |
| legal_address (same shape) | the address on tax forms; defaults to the company address |
| ship_address (same shape) | where the company receives goods; defaults to the company address |
| phone, fax, email, website | |
| fiscal_year_start_month | 1 to 12 |
| tax_year_start_month | 1 to 12; defaults to the fiscal year start |
| report_basis | `accrual` or `cash`; the default basis for reports |
| home_currency | ISO 4217, immutable |
| timezone | IANA name |
| closing_date | date, nullable; see 10.6 |
| recent_activity_window_seconds | default 60 |
| default_chart | which seeded chart was applied; null until one is applied with `chart apply` |

### 9.2 Rollout

`bookflow company new` takes every field above as options, or `--interactive` to prompt for each, plus `--organization` (id or name; defaulted when the actor can see exactly one) and `--chart` (a seeded chart name or `none`). Required: `legal_name`, `home_currency`. Defaults: `display_name` = `legal_name`; `fiscal_year_start_month` = 1; `tax_year_start_month` = `fiscal_year_start_month`; `timezone` = the machine's zone as reported by the `tzlocal` package; `country` = `US`; `entity_type` and `income_tax_form` = `other`; `tax_id_kind` = `ein`; `report_basis` = `accrual`; `recent_activity_window_seconds` = 60; `legal_address` and `ship_address` = `address`; `industry`, `contact_name`, `tax_id`, address lines, `phone`, `fax`, `email`, `website` = null; `closing_date` and `default_chart` = null. `closing_date` is not accepted at rollout; it is set with `company update`. The tax id, when given, must match the shape for its kind; email must contain one `@`; timezone must be an IANA name; months must be 1 to 12. Until charts exist, `--chart` accepts only `none`; `chart apply <name>` seeds a chart into a company whose `default_chart` is null. It creates the directory, the database, the `company_info` row, the seeded chart of accounts named by `--chart`, the standard terms, payment methods, and sales tax codes listed in section 11, and grants the creating user the owner role. Output is the company id and display name.

Seeded charts, chosen by `--chart`: `general`, `service`, `construction_trades`, `retail`, `nonprofit`. Each is a data file in the package. `general` is the default.

### 9.3 Demo company

`bookflow demo reset`, a hub-admin command, creates, or moves to `trash/` and recreates, an organization `Demo Holdings LLC` holding a company `Demo Plumbing Co` from a seed file in the package, and grants the local owner the owner role. The seed holds sample data for every table that exists: company info, accounts, customers and jobs, vendors, employees, items, terms, notes, attachments, directives, and, once the ledger exists, a year of transactions. The seed grows in the same change that adds a table or command, so the demo always exercises everything that exists. There is one demo organization per data root. The demo company is an ordinary company: it appears in the workbench picker and every command works on it. Tests run against the demo. Ephemeral tables (presence, idempotency keys) are exempt from seeding. A second seed, `Reference Plumbing Co`, holds a full year of hand-verified transactions whose trial balance, profit and loss, balance sheet, and aging totals are recorded beside it; every report change regresses against those totals. It arrives with the ledger and grows with each form.

### 9.4 Other company commands

`company list`, `company show`, `company update`, `company rename [--move]`, `company use <company>`, `company attach <path>`, `company detach <company>`, `company backup`, `company restore <backup>`, `company verify` (SQLite integrity check plus ledger invariants: every transaction balances, every application is within its open balance, every referenced record exists), `company compact`, `company delete <company> --confirm <id>`. Backups can be scheduled (13.3) with a retention count. `delete` moves the folder to `trash/` and removes the registry rows; `trash list` and `trash empty` manage the folder. `company show` includes `path` only for hub admins.

## 10. The general ledger

### 10.1 Accounts

Table `accounts`.

| Field | Meaning |
|---|---|
| name | unique among siblings |
| number | text, optional, unique within the company when present |
| type | one of the account types below |
| parent_id | nullable; sub-accounts must share the parent's type |
| description | |
| active | |
| is_system | true for accounts the core requires and will not let be deactivated |

Account types and their normal balance:

| Type | Normal balance | Balance sheet or P&L |
|---|---|---|
| bank | debit | balance sheet |
| accounts_receivable | debit | balance sheet |
| other_current_asset | debit | balance sheet |
| fixed_asset | debit | balance sheet |
| other_asset | debit | balance sheet |
| accounts_payable | credit | balance sheet |
| credit_card | credit | balance sheet |
| other_current_liability | credit | balance sheet |
| long_term_liability | credit | balance sheet |
| equity | credit | balance sheet |
| income | credit | P&L |
| cost_of_goods_sold | debit | P&L |
| expense | debit | P&L |
| other_income | credit | P&L |
| other_expense | debit | P&L |
| non_posting | none | neither |

System accounts every company has: `Accounts Receivable`, `Accounts Payable`, `Undeposited Funds`, `Opening Balance Equity`, `Retained Earnings`, `Sales Tax Payable`, `Inventory Asset`, `Cost of Goods Sold`, `Exchange Gain/Loss`.

### 10.2 Transactions

Table `transactions` (header).

| Field | Meaning |
|---|---|
| type | see 10.3 |
| number | per-type sequence, text, editable, unique per type |
| date | transaction date |
| status | `posted`, `voided` |
| name_type, name_id | the customer, vendor, employee, or other name the transaction is with, when the type has one |
| memo | |
| total | home currency; meaning depends on type |
| form fields | type-specific, stored in the type's own table keyed by transaction id (e.g. `invoices` holds terms, due date, ship date) |
| voided_at, voided_by, void_reason, reversing_transaction_id | null unless voided |

Table `transaction_lines`.

| Field | Meaning |
|---|---|
| transaction_id, line_no | |
| account_id | |
| debit, credit | home currency minor units; exactly one non-zero |
| original_amount, original_currency, rate_used | null unless foreign-tagged |
| name_type, name_id | customer, job, vendor, employee, or other name |
| item_id | nullable |
| quantity | decimal string, nullable |
| class_id | nullable |
| description | |
| billable | bool |
| sales_tax_code_id | nullable |

Every posted transaction satisfies: sum of debits equals sum of credits, every line's account is active and not `non_posting`, every AR line names a customer or job, every AP line names a vendor.

### 10.3 Transaction types

Each type is a form that produces lines. The posting rule is fixed per type.

| Type | Command | Name | Lines produced |
|---|---|---|---|
| journal_entry | `journal post` | optional per line | as entered |
| invoice | `invoice post` | customer or job | Dr AR; Cr income per item; Cr Sales Tax Payable; inventory items also Dr COGS, Cr Inventory Asset |
| sales_receipt | `sales-receipt post` | customer or job | Dr deposit-to account or Undeposited Funds; Cr income; tax and inventory as invoice |
| credit_memo | `credit-memo post` | customer or job | reverse of invoice |
| payment | `payment receive` | customer or job | Dr Undeposited Funds or bank; Cr AR; applies to open invoices |
| deposit | `deposit post` | none | Dr bank; Cr Undeposited Funds and any other lines |
| bill | `bill post` | vendor | Dr expense, COGS, or Inventory Asset per line; Cr AP |
| bill_payment | `bill pay` | vendor | Dr AP; Cr bank or credit card; applies to open bills |
| check | `check write` | any | Cr bank; Dr per line |
| credit_card_charge | `charge post` | vendor | Cr credit card; Dr per line |
| transfer | `transfer post` | none | Dr to account; Cr from account |
| inventory_adjustment | `inventory adjust` | none | Dr or Cr Inventory Asset; offset to adjustment account |
| vendor_credit | `vendor-credit post` | vendor | reverse of bill |
| estimate | `estimate create` | customer or job | non-posting; converts to an invoice in full or by progress percentage or selected lines |
| sales_order | `sales-order create` | customer or job | non-posting; converts to an invoice and tracks backorders |
| purchase_order | `purchase-order create` | vendor | non-posting; received against by bills and item receipts |
| item_receipt | `item-receipt post` | vendor | Dr Inventory Asset; Cr AP, converted to a bill when the bill arrives |
| statement | `statement send` | customer | non-posting; the customer's open items and activity for a period |

Bank and credit card reconciliation: `reconcile start <account> --statement-date --ending-balance`, `reconcile mark <transaction> [--clear|--unclear]`, `reconcile finish`, which records the reconciliation with its difference (zero, or posted to a reconciliation discrepancy account with `--force`) and marks cleared transactions with the reconciliation id. Batch invoicing creates one invoice per selected customer from a template. `find <text>` searches names, memos, numbers, and amounts across lists and transactions the actor can see.

Forms and their tables are built in the release after the ledger; see section 21.

### 10.4 Applications

Table `applications`: `paying_transaction_id`, `paid_transaction_id`, `amount`. Payments apply to invoices, bill payments to bills, credits to either. Open balance of a transaction is its total minus applied amounts. Aging reports read this table. Editing a transaction that has applications is allowed while its new total still covers the applied amount; otherwise the edit is rejected with `E_APPLIED_EXCEEDS_TOTAL` and the caller unapplies first. Every edit keeps its applications and re-derives the open balance.

### 10.5 Void

`<type> void <id>` requires a reason. It sets status `voided`, zeroes nothing, and posts a reversing transaction of the same type dated the same day, linked by `reversing_transaction_id`, so the ledger history of both remains. Voiding a transaction with applications is rejected with `E_HAS_APPLICATIONS` until the applications are removed by voiding or unapplying the paying transaction.

### 10.6 Closing date

When `closing_date` is set, any post, void, or update whose transaction date is on or before it is rejected with `E_PERIOD_CLOSED`. Changing the closing date requires the admin or owner role and is audited. There is no password override; the audit log is the control.

### 10.7 Numbering

Each transaction type has a sequence in `sequences` (`type`, `next_number`, `prefix`). Posting takes the next number unless the caller supplies one. A supplied duplicate is rejected with `E_DUPLICATE_NUMBER`.

## 11. Lists

All lists share the common fields of section 6.1, `active`, and the five verbs `create`, `update`, `show`, `list`, `activate`, `deactivate`. Names are unique within a list, case-insensitively. Hierarchical lists have `parent_id` and a computed `full_name` of the form `Parent:Child:Grandchild`. Hierarchy depth is limited to 5.

### 11.1 Customers and jobs

Table `customers`. A job is a customer with `parent_id` set; jobs may nest to depth 5.

Fields: `name`, `company_name`, `salutation`, `first_name`, `last_name`, `bill_address` (line1, line2, city, state, postal_code, country), `ship_address` (same shape), `phone`, `alt_phone`, `fax`, `email`, `cc_email`, `website`, `contact`, `alt_contact`, `terms_id`, `sales_tax_code_id`, `sales_tax_item_id`, `resale_number`, `credit_limit`, `price_level_id`, `customer_type_id`, `sales_rep_id`, `preferred_payment_method_id`, `preferred_delivery_method` (`none`, `email`, `mail`), `account_number`, `job_status` (`none`, `pending`, `awarded`, `in_progress`, `closed`, `not_awarded`), `job_start`, `job_projected_end`, `job_end`, `job_description`, `job_type_id`, `linked_vendor_id` (nullable, see 11.3), `notes`.

### 11.2 Vendors

Table `vendors`. Fields: `name`, `company_name`, `salutation`, `first_name`, `last_name`, `address`, `phone`, `alt_phone`, `fax`, `email`, `cc_email`, `website`, `contact`, `alt_contact`, `terms_id`, `account_number`, `tax_id`, `eligible_1099` (bool), `vendor_type_id`, `default_expense_account_ids` (up to three, in order), `billing_rate_level_id` (nullable, reserved), `linked_customer_id` (nullable), `notes`.

### 11.3 Customer and vendor link

A customer and a vendor may be linked when they are the same legal entity. `customer link-vendor <customer> <vendor>` sets both sides; `unlink` clears both. The link changes nothing about posting. `show` on either side includes the other. The GUI may offer to copy contact fields across the link; the core does not.

### 11.4 Employees

Table `employees`. Fields: `name`, `first_name`, `middle_name`, `last_name`, `address`, `phone`, `email`, `ssn_last4`, `hire_date`, `release_date`, `notes`. Employees may be named on checks and on time entries. Payroll fields are absent by design and will be added to this table by a later migration.

### 11.5 Other names

Table `other_names`. Fields: `name`, `company_name`, `address`, `phone`, `email`, `contact`, `account_number`, `notes`. Used for owners, partners, and payees that are neither customers nor vendors. Bookflow provides `other-name convert --to customer|vendor`, which creates the target, deactivates the other name, and rewrites `name_type` on its transactions in one audited event.

### 11.6 Items

Table `items`. `type` is one of:

| Type | Purpose | Accounts |
|---|---|---|
| service | labor and services | income; optional expense for purchased services |
| inventory_part | stocked goods | income, COGS, asset |
| non_inventory_part | goods not tracked in stock | income; optional expense |
| other_charge | fees, shipping, miscellaneous | income; optional expense |
| subtotal | sums preceding lines on a form | none |
| group | a bundle of other items | none |
| discount | percentage or amount off the preceding line or subtotal | income or expense |
| payment | a payment recorded on an invoice | deposit-to |
| sales_tax_item | one rate payable to one agency | Sales Tax Payable; agency is a vendor |
| sales_tax_group | several sales tax items applied together | none |
| inventory_assembly | built from other items | as inventory_part; reserved, not built in release 1 |

Fields: `name`, `type`, `parent_id`, `description`, `purchase_description`, `price` (amount or nullable), `cost`, `income_account_id`, `expense_account_id`, `asset_account_id`, `sales_tax_code_id`, `preferred_vendor_id`, `manufacturer_part_number`, `unit_of_measure`, `reorder_point`, `quantity_on_hand` (derived, never edited directly), `average_cost` (derived), `percent` (for discount and sales tax items), `tax_agency_vendor_id`, `group_members` (item id and quantity, for group and assembly).

Inventory valuation is average cost. Quantity on hand and average cost are recomputed from inventory-affecting transactions and adjustments; they are never set by an `update`.

### 11.7 Classes

Table `classes`. Fields: `name`, `parent_id`. Company setting `use_classes` controls whether forms prompt for one.

### 11.8 Terms

Table `terms`. Fields: `name`, `kind` (`standard` or `date_driven`), `due_days`, `discount_days`, `discount_percent`, `due_day_of_month`, `discount_day_of_month`, `due_next_month_if_within_days`. Seeded: `Due on receipt`, `Net 15`, `Net 30`, `Net 60`, `1% 10 Net 30`, `2% 10 Net 30`.

### 11.9 Payment methods

Table `payment_methods`. Fields: `name`, `kind` (`cash`, `check`, `credit_card`, `debit_card`, `ach`, `other`). Seeded: `Cash`, `Check`, `Visa`, `MasterCard`, `American Express`, `Discover`, `ACH`, `Other`.

### 11.10 Sales tax codes

Table `sales_tax_codes`. Fields: `code` (three characters), `description`, `taxable` (bool). Seeded: `Tax` taxable, `Non` non-taxable. Sales tax items and groups live in `items`.

### 11.11 Supporting lists

Each is a table with the common fields, `name`, and `active`, plus the fields listed.

| Table | Extra fields | Used by |
|---|---|---|
| `customer_types` | `parent_id` | customers |
| `vendor_types` | `parent_id` | vendors |
| `job_types` | `parent_id` | customers (jobs) |
| `sales_reps` | `initials` (unique, up to 5 characters), `name_type` and `name_id` pointing at an employee, vendor, or other name | customers (default rep), sales forms, sales by rep reports, commission tracking |
| `ship_methods` | | sales forms |
| `customer_messages` | `text` | sales forms |
| `price_levels` | `kind` (`fixed_percent` or `per_item`), `percent` (signed), `per_item_prices` (item id and price or percent) | customers, sales forms |
| `units_of_measure` | `base_unit`, `related_units` (name, conversion factor), `default_purchase_unit`, `default_sales_unit`, `default_shipping_unit` | items |
| `memorized_transactions` | `transaction_type`, `template` (the form input as JSON), `schedule_id` (nullable), `group_name` | forms; see 13.3 |
| `to_dos` | `text`, `due_at`, `done`, `record_type`, `record_id` | any record |

### 11.12 Custom fields

Table `custom_field_defs`: `list_name` (customers, vendors, employees, items, or any transaction type), `name`, `kind` (`text`, `number`, `date`, `bool`, `choice`), `choices`, `position`, `active`. Table `custom_field_values`: `def_id`, `record_type`, `record_id`, `value` as text, parsed by kind on read. Every list and form input model accepts `custom_fields` as a mapping of definition name to value, and every `show` output returns it. The workbench renders them as ordinary fields.

### 11.13 Completeness rule

Before a list, form, or report is built, its blueprint section is completed to name every field, option, and behavior the anchor offers on the equivalent screen or report, including defaults, validations, filters, columns, and what each field is used by. The section is the inventory; the row is built against it; the artifact critic checks the built thing against the section. Reports are designed as one set with shared parameters, filters, column conventions, and drill-down, never one at a time. Improvements beyond the anchor, such as commission rates on sales reps tied to employees, are added as separate fields marked as such in the section, after the anchor's set is complete.

## 12. Notes, attachments, and activity

### 12.1 Notes

Table `notes`. Fields: `record_type`, `record_id`, `body` (text, Markdown allowed), `author_id`, `interface`, `at`, `edited_at`, `kind` (`comment` or `system`). Any record in any table in company.db may carry notes, including `company_info`. `note add <record_type> <record_id> --body`, `note edit`, `note list`. Editing keeps the original in the audit log. Notes are never deleted.

### 12.2 Attachments

Table `attachments`: `sha256`, `size_bytes`, `media_type`, `original_filename`, `uploaded_by`, `uploaded_at`. The file body lives at `attachments/<sha256[:2]>/<sha256>`. Uploading the same bytes twice creates one body and one row.

Table `attachment_links`: `attachment_id`, `record_type`, `record_id`, `linked_by`, `linked_at`, `caption`. One attachment may link to many records.

Commands: `attachment add <record_type> <record_id> <path>`, `attachment link`, `attachment unlink`, `attachment list`, `attachment get <id> --out <path>`. Maximum size per file is a company setting, default 25 MB. Unlinking the last link does not delete the body; `company compact` removes unlinked bodies and is audited.

### 12.3 Activity feed

`activity <record_type> <record_id>` returns, in time order, every audit entry for the record, every note, and every attachment link, each tagged with its kind and actor. `--since`, `--until`, and `--kinds` filter it. This is the chronological job history the GUI shows on any record.

## 13. Work orders and scheduler

Built after release 1. Designed here so release 1 leaves room.

### 13.1 Work orders

Table `work_orders`: `number`, `customer_id` (customer or job), `title`, `description`, `status` (`draft`, `scheduled`, `in_progress`, `on_hold`, `complete`, `invoiced`, `cancelled`), `priority`, `scheduled_start`, `scheduled_end`, `actual_start`, `actual_end`, `site_address`, `assignees` (employee ids), `invoice_transaction_id` (nullable). Table `work_order_lines`: `item_id`, `description`, `quantity`, `rate`, `billable`. `work-order invoice <id>` creates an invoice from billable lines and links it. Work orders carry notes and attachments like any record, and the activity feed is their job log.

### 13.2 Time entries

Table `time_entries`: `employee_id`, `customer_id`, `item_id` (service), `date`, `duration_minutes`, `billable`, `work_order_id`, `notes`. Billable time is pulled onto invoices. This is the table payroll will read for hourly employees.

### 13.3 Scheduler

Table `schedules` in company.db: `name`, `owner_user_id`, `command`, `input` (JSON), `run_at` (one-off, nullable), `rrule` (RFC 5545 recurrence, nullable), `next_run_at`, `last_run_at`, `last_result`, `enabled`. The host process runs due schedules as the `system` user with `on_behalf_of` the owner and `interface` `system`. First uses: memorized transactions (`invoice memorize`, `bill memorize`) and reminders (`reminder add`), which surface through `reminder list` and through the HTTP host as a notification endpoint the GUI polls.

### 13.4 Deliveries and email

Sending anything out of Bookflow, such as an invoice, a statement, a report, or a reminder, is a delivery. Table `deliveries`: `record_type`, `record_id` (what was sent, or null for a report run), `document` (rendered PDF or CSV as an attachment id), `channel`, `to`, `subject`, `body`, `status` (`queued`, `handed_off`, `sent`, `failed`), `provider_message_id`, `error`, `requested_by`, `requested_at`, `sent_at`. Every form and report command accepts `--deliver <channel>:<address>`; the scheduler schedules the same command, so a scheduled report that emails itself is one schedule row.

Channels are providers behind one interface with `send(delivery) -> provider_message_id`:

| Channel | Provider | Where the code lives |
|---|---|---|
| `event` | Writes the delivery with status `handed_off` and emits it on the event feed (7.1). An agent harness subscribed to the feed sends it through whatever mail access it has and calls `delivery mark-sent <id> --provider-message-id`. This is the default channel and needs no configuration. | core |
| `smtp` | Standard SMTP with STARTTLS or TLS and password or app-password authentication. Works with any mail host. | core, standard library only |
| `gmail` | Gmail API with OAuth, for accounts where SMTP app passwords are unavailable. | separate optional package `bookflow-gmail`, pinned to the vendor client library, dependency updates automated |
| `file` | Writes the rendered document to `exports/`. | core |

Inbound email is not read by Bookflow. An agent harness reads mail and calls commands, such as attaching a receipt or posting a bill. Company settings hold the configured channel per purpose (`invoices`, `statements`, `reports`, `reminders`) and the sender address; provider credentials live in the hub, encrypted with a key held in the operating system keyring where one exists and otherwise in a key file outside the data root named at `init`, never in a company folder.

Dependency updates for the whole project, including the optional email package, run through the repository's automated dependency update service; an update that passes the test suite merges.

## 14. Reports

Every report reads `transaction_lines`, `transactions`, and `applications` only. Every report takes `--from`, `--to`, `--basis accrual|cash`, `--json`, and `--csv`. Cash basis treats income and expense as occurring when payment applies, using `applications`.

Release 2 reports: trial balance, general ledger, profit and loss (standard, detail, by class, year to date comparison), balance sheet (standard, detail), statement of cash flows, AR aging summary and detail, AP aging summary and detail, customer balance summary and detail, vendor balance summary and detail, open invoices, unpaid bills, collections, sales by customer and by item summary and detail, purchases by vendor and by item, inventory valuation summary and detail, inventory stock status, job profitability summary and detail, estimates versus actuals, unbilled costs by job, time by job, sales tax liability, transaction list by date, transaction detail by account, check detail, deposit detail, missing checks, reconciliation summary and detail, budget versus actual, 1099 summary and detail, audit trail. The full set is inventoried from the anchor's report guides before the release is planned (11.13).

Trial balance and general ledger ship with the ledger in release 1.

### 14.1 Import and export

`import <noun> <file.csv>` reads one row per record, maps columns to the noun's `create` input model by header name, and runs one `create` command per row through the registry, so every row is validated, audited, and idempotent (the idempotency key is the file hash plus row number). The output reports created, replayed, and rejected rows with their errors. `--dry-run` validates every row and writes nothing. Import of transactions uses the same mechanism with one file per transaction type. Export is `list --csv` on any noun and `report <name> --csv`. IIF import is a later addition that maps IIF sections onto the same imports. Import and export are release 2.

## 15. Adapters

### 15.1 CLI

`bookflow <noun> <verb> [args] [flags]`. Global flags: `--company`, `--json`, `--dry-run`, `--reason`, `--source-ref`, `--idempotency-key`, `--as-token`, `--data-root`. `bookflow --help` and `bookflow <noun> --help` are generated from command definitions and include every flag, every output field, and every error code.

### 15.2 HTTP host

`bookflow serve --bind 127.0.0.1:8123`. Routes are `POST /companies/{company_id}/commands/{command_name}` with the input model as the JSON body and the output model as the response. Hub commands are `POST /commands/{command_name}`. Errors return status 400 for named errors with the error JSON as body, 401 for missing or bad token, 404 for `E_COMPANY_NOT_FOUND`, 409 for `E_VERSION_CONFLICT`, 500 for internal failure. `GET /openapi.json` is generated. Binding to a non-loopback address requires `--allow-network`, and the docs state that TLS termination is the deployer's job. The host also serves the workbench at `/` and static assets under `/static/`.

While the host runs it holds the data root's lock (3.2); a CLI or library call on the same machine reads the host's pid from the lock file and forwards the command to it over loopback, so there is never a second writer. Inside the host, reads run concurrently on their own read-only connections and writes queue on one writer per database, so many users reading never wait on each other.

Browser sessions: `POST /login` with username and password sets an HTTP-only session cookie that maps to a session token in `api_tokens` with kind `session`, expiring after 12 hours of inactivity. API calls from the browser send the cookie; API calls from programs send a bearer token. Both resolve to the same context.

### 15.2a Workbench

The workbench is the browser interface used to exercise every command. It is generated from the registry and is complete by construction: when a command is registered, its page exists.

| Page | Content |
|---|---|
| `/login` | username and password |
| `/` | company picker listing the session user's memberships; selecting one sets the working company |
| `/c/<company_id>/` | the noun index: one link per noun that has commands |
| `/c/<company_id>/<noun>` | the `list` output as a table with `--include-inactive` toggle; one row link to `show` |
| `/c/<company_id>/<noun>/<id>` | the `show` output as a field table, the record's activity feed, its notes and attachments, and buttons for each verb the role permits |
| `/c/<company_id>/<noun>/<verb>` | a form generated from the input model: one input per field, typed, with the field description, and a `reason` and `directive` field where 5.8 requires them; submit runs the command and renders the output model and any error with its code |
| `/c/<company_id>/audit` | `audit list` with its filters, each event expanding to its entries and diffs |
| `/c/<company_id>/reports/<name>` | report parameters form, then the report as a table with a CSV link |

The workbench has no styling beyond a readable default stylesheet. It exists so that a person can verify every function works. The product interface built later is a separate application against the same HTTP routes, and it may keep the generated forms for rarely used commands.

### 15.3 MCP server

`bookflow mcp --token <secret>`. Three tools, so that the agent's context never carries every command's schema at once: `bookflow_list_commands` returns every command name with its one-sentence description and scope; `bookflow_help` returns a command's documentation page, including its input schema, output fields, and error codes; `bookflow_run` takes `command`, `input`, and the context arguments `company`, `reason`, `directive`, `source_ref`, and `idempotency_key`, and returns the output or the error document. The three are generated from the registry like every other adapter.

## 16. Documentation contract

The reader is an agent that has never seen Bookflow and cannot read the source. Every page states facts; none explains reasoning.

| Location | Content | Produced by |
|---|---|---|
| `README.md` | what it is, install, the one command to run, where the docs are | hand |
| `docs/cli/<noun>.md` | every command of that noun: purpose in one sentence, every flag with type and default, output fields, error codes, one example invocation and its JSON output | `bookflow docs generate`, from command definitions |
| `docs/schema/<table>.md` | every field with type, nullability, meaning, and references | `bookflow docs generate`, from models |
| `docs/concepts.md` | the command contract, context, concurrency rules, money, company selection, exit codes, stated declaratively | hand, verified by tests where possible |
| `docs/agent-guide.md` | one page for an agent: authenticate, pick a company, record and cite a directive, post, read the event feed, handle `E_VERSION_CONFLICT`; every example runnable against the demo company | hand, examples verified by a test |
| `docs/index.md` | the entry point; lists every page | generated |
| `design/architecture.md` | what is built, module by module, with what was verified on real hardware and what was not; written by whoever builds, for the next builder | hand |

A test regenerates the docs and fails when the result differs from the committed files.

## 17. Decision hierarchy

When two good things conflict, the earlier line wins.

1. Balanced books beat everything. A command that would unbalance the ledger fails, whatever else it would achieve.
2. Never lose history. A slower audit write beats a faster unaudited one.
3. Isolation beats convenience. A command that could reveal another company's existence is wrong even if it would be useful.
4. Correct output beats fast output. Fast beats pretty.
5. Explicit beats inferred. An ambiguous name, account, or date is rejected with a named error rather than guessed. The one exception is section 6.2's disjoint-field merge, which is reported, never silent.
6. Machine-readable on stdout beats friendly on stdout. Friendly on stderr beats machine-readable on stderr.
7. The conventional bookkeeping name for a thing beats a better name.
8. When a rule is ambiguous, the interpretation that gives the human more information wins.

## 18. Budgets

| Target | Condition |
|---|---|
| Any list or show command returns in under 100 ms | 10,000 records in the list, SQLite on local SSD |
| Posting a 20-line transaction completes in under 50 ms | same |
| Trial balance over 100,000 lines returns in under 2 s | same |
| A company database with 100,000 transactions stays under 500 MB excluding attachments | |
| Audit tables occupy at most 6 times the live data they describe, in aggregate | measured with `dbstat` on the fixed fixture of 5,000 creates and 5,000 updates; every write keeps a full after-snapshot for readability and an event row of context, which the arithmetic in the row 2 plan puts near 5 |
| Installed package with dependencies under 60 MB; no service other than SQLite required | |
| CLI cold start under 300 ms | Python 3.12, warm disk cache |
| Full test suite under 60 s | |

## 19. Out of scope for release 1, and beyond

Not in release 1: transaction forms other than journal entries, reports beyond trial balance and general ledger, work orders, time entries, scheduler, price levels, ship methods, inventory assemblies, the product interface beyond the workbench, a desktop wrapper, rate fetching, email, bank feeds, encryption at rest.

Four outside-facing integrations are designed as their own passes, each behind a provider interface so the churn of outside services never reaches the core. They come after the internal systems that grow out of the accounting core (CRM, work orders, inventory and receiving, assemblies, shipping), which are the product's priority:

- **Email per company** (section 13.4) ships in release 2 with the `event` and `smtp` channels; the Gmail package follows. Provider credentials and OAuth refresh live in the hub behind the provider interface, so a provider change never touches a company's books.
- **Bank feeds.** A `bank_feed_items` inbox per bank or credit card account, with items stored in the Financial Data Exchange account and transaction shape so every provider is a translation and the inbox never changes: transactions arrive from a provider (statement import first: OFX, QFX, CSV, and parsed PDF statements through a maintained open-source statement parser chosen at that design pass; then live connections behind the same interface: SimpleFIN Bridge for its small universal shape, BankSync for its open-banking flow with a CLI of the same noun-verb and JSON shape as Bookflow's, and Plaid for institution coverage; the provider is a company setting and adding one never changes the inbox), are matched to existing transactions or proposed as new ones by rules the company defines (payee pattern to account, class, and memo), and are either auto-posted where a rule says so or held for approval in the inbox. Approval is the default for anything a rule does not cover; the approving user and the rule are recorded on the posted transaction.
- **Tax forms**, after payroll: federal and state filings (payroll returns, 1099s, W-2s, sales tax returns, and the income tax forms the company's entity type calls for) prepared from the ledger and payroll data, kept current through the same versioned data package as tax tables, and submitted through a provider interface where electronic filing exists.
- **Payroll.** Employees, the time clock, and time entries already have their tables. Payroll adds pay schedules, earnings and deduction items, tax tables delivered as a versioned data package updated like `tzdata`, paycheck posting through the ordinary ledger, and filings and payments through a provider interface, so tax-table updates and e-file submission change providers without changing the module.

Never in scope without a new design pass: multi-currency ledgers, non-US tax regimes.

Later releases, each designed as its own pass against this blueprint: customer relationship management (lead generation and tracking, contacts, follow-ups, communication history, pipeline reporting), purchase orders and receiving, inventory assemblies and build orders, shipping with printable labels, customer letters and statements, marketing lists and mailings, scheduled automated billing and late-payment notices with delivery of delinquent invoices after a set age, calendar integration with work orders and multi-phase project schedules spanning days, an employee time clock (clock in and out per employee, feeding time entries), payroll. Each is a set of lists, transaction types, and reports registered through the same command registry.

## 20. Risks

| Risk | What it would look like |
|---|---|
| Adapter logic drift | HTTP and CLI accept different inputs for the same command. Guard: adapters are generated from the same definitions and a test calls every command through all three. |
| Silent merge surprises | A disjoint-field merge lands a change the caller did not expect. Guard: the output names the merged versions and the CLI warns. |
| SQLite on a shared drive | Corruption. Guard: refusal on network filesystems and the host-only rule. |
| Migration on an open file | Two processes migrate at once. Guard: the data-root lock. |
| Docs go stale | An agent follows a page that no longer matches. Guard: generation plus the freshness test. |
| Attachment store bloat | Unlinked bodies accumulate. Guard: `company compact`. |
| Precision drift on rates | Repeated conversions compound rounding. Guard: convert once at posting, store the rate, never re-convert. |

## 21. Build order and the final checklist

The build order is the spec list in `design/intention.md`, rows 1 to 9. That list is release 1.

Release 1 is done when a stranger, given a fresh machine and the README, can: initialize a data root, create a company, log in to the workbench in a browser, add accounts and customers there, attach a receipt image to a customer, post a balanced journal entry from the workbench and another from the CLI, post a third over HTTP from a second process, list accounts from an MCP client, see all of it in the audit page with correct actors and interfaces, and reproduce all of it on a second copy of the company folder.

Release 2 is forms (section 10.3), reports (section 14), import and export (section 14.1), and email delivery (section 13.4). Release 3 is work orders, time, and the scheduler (section 13). The product interface is designed after release 2 against the HTTP host.
