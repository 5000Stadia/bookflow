# Bookflow — blueprint

This document states what Bookflow is and how every part of it works. It is written for a reader with no prior knowledge of the project. Exact values carry this license: change it if it makes the product better, and say why.

## 1. What it is

Bookflow is a multi-company double-entry accounting system for small businesses, built on the conventions of desktop bookkeeping software. Its accounting core is a Python library. A CLI, an HTTP host, and an MCP server are thin adapters over the library. The browser workbench is the primary human interface, served by the HTTP host for local or remote use; native wrappers are optional packaging.

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

Everything must remain portable to PostgreSQL. No SQLite-only SQL in repositories.
Business calculations and audit orchestration run in the application, not in
triggers. Storage migrations may install rejection-only guards against updating
or deleting immutable ledger history; these guards do not calculate or write data.

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
- Directory moves and metadata files are outside SQLite transactions; their durable intent is recorded before publication. `rename --move` is: one transaction commits the new name and writes `pending_path`; the folder is moved with a no-replace rename (`renameat2` with `RENAME_NOREPLACE` on Linux, `renamex_np` with `RENAME_EXCL` on macOS, `MoveFileExW` without the replace flag on Windows), a case-only change going through `<target>.moving-<id>`, which resolution also checks for and completes; the destination and source parents are synchronized before a second transaction sets `path` to `pending_path` and clears it. Retry validates destination identity and repeats synchronization before clearing pending state. Resolution of a company whose `path` does not exist consults `pending_path` and, when that folder or its `.moving` form exists, completes the operation on the spot: a pending move commits the path, a pending trash completes the deletion. When neither exists the error is `E_COMPANY_MISSING`. Rerunning `rename --move` with the current name completes an interrupted move and returns `moved: true`; with no pending move it returns `moved: false` and writes nothing. Organizations use the same `pending_path` on their own row; a completed organization move rewrites the stored `path` (and any `pending_path`) of every company under it as a versioned, audited update recorded as entries on the move event. If a registered path does not exist on open, the error is `E_COMPANY_MISSING` naming the company; there is no scan.
- The company id is `company_info.id`, the single row's primary key. `attach` reads the marker, opens the database read-only, and refuses on any mismatch.
- Every company is exactly one folder under its organization's folder. Nothing about a company is written outside it, and nothing that is not about that company is written inside it. General temporary files go to the operating system temporary directory; atomic metadata replacement uses an owner-private unique temporary beside its destination.
- `company attach <path> [--name <display name>]` is a hub-admin command that registers a folder already under a registered organization's folder, under the display name copied in the folder or the one given. Any other path is refused with `E_NOT_IN_ORGANIZATION_DIR`. Before registering, `attach` verifies: the path resolves, after symlinks, to a directory directly under that organization's folder; the filesystem is local; the marker and database exist and agree on the company id; the database schema revision is known to this version, behind the current revision or not, since the first writable open migrates it; the marker state is `ready`; the id is not registered (`E_ALREADY_ATTACHED`); the display name is free in the organization (`E_NAME_TAKEN`). Any failure leaves the hub unchanged. Registration is one hub transaction and creates no membership, since hub admins need none. A folder with no display-name copy needs `--name` (`E_ATTACH_INVALID`, `details.check` `display_name`). Both the given path and the organization folder are resolved through symlinks before comparison. `company detach <company>`, hub admin only, deletes the registry row and its memberships in one audited transaction whose entries carry the deleted rows, and leaves the folder in place. Restoring from a copy on the same machine is detach the live company, move the copy into the organization's folder, attach it.
- `demo reset` acts only on the organization whose hub row carries `is_demo`, which only `demo reset` sets.

### 3.2 Locality and locking

- Before opening `hub.db`, any `company.db`, or an `attach` path, the core resolves symlinks and determines the filesystem type. Local types are an allowlist: `ext2`, `ext3`, `ext4`, `xfs`, `btrfs`, `f2fs`, `zfs`, `tmpfs`, `overlay`, `apfs`, `hfs`, `ntfs`, `ntfs3`, `exfat`, `vfat`, `fat32`, `refs`. On Linux the type comes from the mount table; on macOS from `statfs`; on Windows a UNC path or a drive whose type is remote is refused. Any other type, and any failure to determine the type, is refused: `E_NETWORK_SHARE` when the type is known and not local, `E_FS_UNKNOWN` when it cannot be determined.
- **One lock per data root.** Every command, reading or writing, takes an exclusive lock on `root.lock` for its duration; the holder writes its hostname, pid, and command name into the file while it holds it. A process that cannot take the lock within 5 seconds (`BOOKFLOW_LOCK_TIMEOUT` overrides, for tests) fails with `E_DB_BUSY` whose details carry the holder's command name and how long it has held the lock, and nothing else, since the caller's role cannot be known before the lock is held. The lock file's contents are ephemeral and are not part of any data. Offline command execution holds this lock for actor/company resolution, authorization, database access, folder moves, and configuration changes. Hosted commands share the host-held lock; its writer serializes mutations while per-request read snapshots run concurrently. Commands are short, so contention is rare; the host process (section 15.2) holds the lock for its lifetime and another POSIX process on that machine finds the host's pid in the lock file and sends its command to the host instead. Other clients use the browser or bearer-authenticated HTTP API.
- Writable connections verify WAL, foreign-key enforcement, and `synchronous=FULL`. Commit synchronization is the durability boundary; checkpoints are maintenance. A failed passive checkpoint does not suppress event notification. Closing a writable database attempts to truncate the WAL. Host shutdown stops admitting readers and writer jobs, waits for admitted readers, checkpoints, and only then releases the root lock. Copy the complete company folder only after clean shutdown; never omit WAL files from a folder copied while open or after an interrupted shutdown.
- Hosted folder changes close reader admission across the data root before releasing company handles. Existing readers have five seconds to drain; conflicts return retryable `E_DB_BUSY`, and a drain timeout leaves folders untouched. The gate remains held through writer cleanup, including moves of empty organizations. During the gate, authentication and reads for unrelated organizations can also return `E_DB_BUSY`; event streams can end and require reconnection with their last cursor. Ordinary record writes and name changes without folder movement remain concurrent with readers.
- Migrations run under the root lock. Before migrating an existing database, a backup is written with the SQLite backup API to the company's `backups/` or to `<data_root>/backups/` for the hub.
- Read-only opens set `query_only`, begin an explicit read transaction after connection setup, and never change the journal mode. Revision checks and subsequent data reads use that same snapshot. Close, error, and cancellation release the transaction. A read-only open of a database behind the current revision returns `E_SCHEMA_BEHIND` whose message names `bookflow upgrade`, which migrates the hub and every company the actor may write, or tells a read-only user to ask someone with write access; any writable open also migrates. `upgrade` migrates one database at a time, each with its own audit event after it commits, reports migrated, skipped, missing, and failed databases, stops at the first failure, and converges on rerun.
- Every command runs with umask `077`, so every directory Bookflow or SQLite creates is `0700` and every file `0600`; `attach` tightens the modes of the folder and everything inside it to `0700` and `0600` and reports what it changed in `warnings`, since restored copies commonly arrive with wider modes. On Windows the default ACL of the creating user applies.

Metadata replacement uses private unique sibling temporaries, synchronized file content, atomic replacement, then parent-directory synchronization on POSIX. Company creation synchronizes the new directory entries through the organization/data-root lineage before registration; backup publication synchronizes the backup file and directory before migration proceeds. A post-replacement failure is reported without assuming the old file survived. Move and trash recovery retain persisted destinations until source/destination parent synchronization succeeds.

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

Username matching uses Unicode NFC normalization and case folding; stored spelling is preserved. Passwords remain case-sensitive. Creation rejects a username that matches an existing human, agent, or system handle, including inactive users. Legacy ambiguous names do not resolve by username; authenticated commands can still use stable user IDs. OS-login mappings in `config.toml` retain the operating system's exact spelling.

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
| authority_epoch | row 7: the agent authority epoch at issuance; null for human tokens; must equal the agent's current unsuspended epoch on use |
| token_hash | sha256 of the secret; the secret is shown once at creation and never stored |
| label | free text |
| expires_at | nullable |
| last_used_at | |
| revoked_at | nullable |

An agent acting for several people holds one token per person. The principal is fixed by the token, never chosen per call. Only a human may run `token issue`: a human may issue for itself, while a hub admin may issue for any user; an agent target requires `--principal <human>`, and an agent credential cannot issue tokens. Row 7 adds assigned principal sets and the shared-permission checks below. Revoked and expired tokens are hidden from the default token list.

### 4.3 Companies and memberships

The company registry table is defined in section 3.0.

Table `memberships` in hub.db: `user_id`, `scope_type` (`organization` or `company`), `scope_id`, `role`, nullable JSON-text `grants` and `denies`, `granted_by`, `granted_at`, `revoked_at`. An organization membership applies to every company in the organization; a company membership applies to that company only. When both apply, the higher role wins. Creating a company requires the admin or owner role on its organization or hub admin, and always grants the creator an explicit company-scope owner membership.

Roles and what they may do:

| Role | Read | Write lists | Post transactions | Manage members | Company settings, closing date, delete |
|---|---|---|---|---|---|
| readonly | yes | no | no | no | no |
| standard | yes | yes | yes | no | no |
| admin | yes | yes | yes | yes | yes except delete |
| owner | yes | yes | yes | yes | yes |

The following shared-agent authority contract is implemented with row 7. An agent's memberships and capabilities are granted exactly as a human's are, by a human with admin or owner role on that organization or company; an agent never inherits its owner's memberships. Table `agent_principals` in hub.db assigns the humans an agent may act on behalf of (`agent_user_id`, `principal_user_id`, `assigned_by`, `assigned_at`, `revoked_at`). Every active principal assigned to an unsuspended agent must hold identical effective permissions across the data root: scope visibility, roles, and resolved capability grants and denies. Assigning a principal whose permissions differ is refused with `E_AGENT_PRINCIPAL_MISMATCH`; its details expose only differences the caller may see and suggest a separate agent identity. A token may name `on_behalf_of` only from the active assigned set; anything else is `E_PERMISSION`. Each permission check requires both the agent's own grant and the bound principal's grant, an unsuspended agent authority, an active principal, and a non-revoked assignment. One agent identity per principal is the recommended shape, and `token issue` says so.

An authorized membership change, revocation, user deactivation, capability-policy change, scope-visibility change, or loss of principal eligibility succeeds without first repairing an agent's principal set. In the same hub transaction, the core recomputes every affected agent set. Any loss of an assigned principal's or the agent's own effective authority, or any change that leaves the set unequal, suspends that agent's entire authority and revokes all its tokens, including tokens bound to unchanged principals. A permission increase that leaves a set unequal also suspends it; it never silently expands existing agent authority. Table `agent_authority` holds `agent_user_id`, monotonically increasing `epoch`, nullable `suspended_at`, and `suspension_reason`; suspension increments the epoch. The initiating permission change, suspensions, and token revocations share one hub audit event and either all commit or none do. Suspending an agent does not prevent further authorized permission reductions.

Reauthorization is explicit and audited: an authorized human adjusts or splits the assigned set, confirms its equality and permitted uses, then clears suspension and issues new tokens bound to the new epoch. Old credentials remain revoked; restoring a membership never revives them. Dispatch checks current authority at execution, not merely when work is enqueued or a connection opens. A queued write rechecks before its write transaction; responses and event-stream batches pass a current-epoch authorization fence before publication, and suspended streams close without another data batch. Already released data cannot be recalled. Tokens and epoch caches cannot authorize work across a revocation commit.

This policy controls Bookflow access; it cannot erase knowledge from an external agent's memory, logs, or conversation. A narrowed or split principal set must use a fresh isolated execution context when its previous context contains data the new set may not share. Rebinding an identity or issuing a token does not by itself establish that isolation.

### 4.3a Configuration file

`config.toml` holds one `[users.<os_login>]` table per mapped OS login with `user_id` and `default_company`, and `[client]` with `display_name`. A change commits complete desired contents in the hub's singleton `pending_config` row alongside its audit event. Pending committed contents override a stale or missing file on read. The writer durably replaces the file, then deletes the pending row in a new hub transaction. Failure returns `E_PARTIAL_WRITE` with the durable effects and request identity; the next writable command retries. Read-only commands use the committed overlay and do not repair it. A default that no longer resolves is reported with `details.source` `default`; removing a company clears affected defaults through the same recovery path. With no pending projection, an unreadable or malformed file is `E_CONFIG_INVALID`.

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

Business workflow language stays close to the command vocabulary. An agent request
such as "make and send an invoice" maps to explicit invoice creation/posting and
delivery actions with a shared result, without requiring the user to describe
ledger internals. Command documentation includes these ordinary-language examples.
The planned customer-work workflow and its vocabulary are defined in
[Customer work and billing](customer-work-and-billing.md).

Verbs used across lists: `create`, `update`, `show`, `list`, `query`, `activate`, `deactivate`. Verbs used on transactions: `post`, `update`, `show`, `list`, `void`; an update appends a document revision and the correcting posting batches defined in 10.2–10.4. Reports use `report <name>`.

Every command has a kind: `read`, `write`, or `advisory` (presence: opens the company writable, records no event, takes no context options). Every command has a scope. **Hub** commands act on the data root and take no company: `init`, `organization *`, `company new`, `company list`, `company use`, `company attach`, `company detach`, `company delete`, `demo reset`, `user *`, `token *`. **Company** commands act on the selected company (section 5.3): everything else, including `company show`, `company update`, `company rename`, `company backup`. A hub command that names a company takes it as a positional argument accepting an id or a display name.

Positional arguments are declared per command in the registry; everything else is an option. `--json` and `--data-root` exist on every command; `--dry-run` only on commands that write; `--company` only on company-scope commands; the context options of 5.2 only on commands whose scope and version support them. The CLI accepts these options both before and after the noun and verb; the same option in both positions with different values is `E_USAGE`, and an option given to a command that does not define it is `E_USAGE`. `--help` on every command lists exactly the options it accepts.

`E_USAGE` covers syntax only: an unknown option, an unknown command, or a conflicting placement. Every value problem, including a missing required field or positional or an unparseable value, is `E_VALIDATION` from the input model, so the library and the CLI return the same code for the same input.

Nested input fields become flags joined with `-`: the `address` object's `line1` is `--address-line1`. Lists of scalars repeat the flag. An owned collection or mapping uses one JSON array/object option; array order is authoritative, `[]` clears a collection, `{}` changes no custom values, and a custom-field key with null clears that value. Python and HTTP receive the identical decoded shape. `--interactive` prompts for the same JSON text for these fields, and `--clear` remains limited to nullable scalar/object fields.

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

Errors are always JSON documents on stderr with `code`, `message`, and `details`, whether or not `--json` was given; without `--json` a one-line message precedes the JSON. Usage errors from the CLI parser are emitted the same way with code `E_USAGE`. Input validation failures are `E_VALIDATION` with `details.fields`, a list of `{"field", "problem"}`. Unknown input keys are `E_VALIDATION`; input keys named like context fields are `E_CONTEXT_IN_INPUT` on every surface. Every command may return the infrastructure codes, listed once in the documentation: `E_USAGE`, `E_VALIDATION`, `E_CONTEXT_IN_INPUT`, `E_NOT_INITIALIZED`, `E_NO_ACTOR`, `E_PERMISSION`, `E_COMPANY_NOT_FOUND`, `E_COMPANY_AMBIGUOUS`, `E_ORGANIZATION_NOT_FOUND`, `E_REASON_REQUIRED`, `E_FEATURE_DISABLED`, `E_UNAUTHENTICATED`, `E_DB_BUSY`, `E_NETWORK_SHARE`, `E_FS_UNKNOWN`, `E_SCHEMA_UNKNOWN`, `E_SCHEMA_BEHIND`, `E_MIGRATION_FAILED`, `E_CONFIG_INVALID`, `E_IO`, `E_PARTIAL_WRITE`, `E_INTERNAL`. `E_IO` carries `details.operation` and `details.errno`; `E_INTERNAL` is the only code for exit 3. An unknown command name is `E_USAGE` on every surface. `E_ORGANIZATION_NOT_FOUND` and `E_COMPANY_NOT_FOUND` are returned identically for absent and for inaccessible targets. Every not-found or ambiguous error for a named record carries `details.suggestions`, up to three close matches drawn only from what the caller can see, so an agent that misspelled a name can correct it without a second lookup. Command-specific codes are listed per command. Error codes are stable strings prefixed `E_`. Every command's documentation lists the codes it can return. An option that a command does not support in the current version is not defined on that command; nothing is accepted and ignored.

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

Mutable primary records carry the following fields. Audit rows, presence, principal mirrors, memberships, idempotency keys, sequences, and exchange rates use their declared schemas. The append-only document revisions, posting batches/lines, applications, and allocation history in 10.2–10.4 have immutable creation/source metadata instead of mutable update fields; the stable document identity owns its current version.

| Field | Meaning |
|---|---|
| id | ULID |
| version | integer, starts at 1, increments on every write |
| created_at, created_by, created_via | timestamp, user id, interface |
| updated_at, updated_by, updated_via | timestamp, user id, interface |

Every primary-record `show` and `list` output includes these fields. Owned child tables—`customer_addresses`, `customer_contacts`, `customer_contact_points`, `vendor_contacts`, `vendor_contact_points`, `vendor_expense_accounts`, `item_members`, `item_vendor_profiles`, `price_level_items`, `unit_conversions`, `custom_field_scopes`, `custom_field_choices`, and `custom_field_values`—instead carry stable id, owner id, active state, their specific fields, and position where the collection is ordered. They have no independent version or provenance: the owning primary record supplies both. `custom_field_values` omit position because definition plus owner is their semantic slot.

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

An `update` without `expected_version` proceeds against the current version. The output carries `previous_version`, `previous_updated_by`, `previous_updated_via`, and `seconds_since_previous_update`. If the previous write was by a different actor within the company's `recent_activity_window_seconds` (default 60), the output also carries `recent_concurrent_activity: true` and the CLI prints a warning to stderr. Row 5 list writes deliberately strengthen this rule: every missing-version list update emits a visible blind-write warning regardless of actor or elapsed time.

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
| undo_of_event_id | company audit only: nullable original company event id, unique when present, so one event is compensated at most once |
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

The snapshot before a write is not stored. It is the `after` of the previous entry for the same record, found by `(record_type, record_id, version_before)`, and is null on create. `audit show` returns both and the field diff. Snapshots hold stored aggregate state, including owned-child identity/state and the Row 5 `counterparty_link` concurrency projection, but exclude ordinary derived presentation values such as `full_name`, `quantity_on_hand`, or `open_balance`. They never expose secret values: `password_hash`, `token_hash`, and `tax_id` are stored as a short hash of the value, so a change is visible and the value is not. Schema migrations are audited as `migrate` entries by the system user with `on_behalf_of` the actor whose command triggered them, and update the hub's `schema_revision` projection in the same command. A transaction audit snapshot includes the new document revision and commercial lines plus the posting batches, lines, application/allocation adjustments and source references generated by that command. It does not copy all preceding immutable history into each event. Audit history, source documents and accounting records remain independently inspectable. Snapshots over 200 bytes are stored zlib-compressed (the boundary at which compression pays on JSON rows and keeps the fixed fixture of section 18 under its ratio); the column is a blob with a one-byte prefix marking raw or compressed, from the first migration that creates the table.

Rules:

- No command updates or deletes audit rows. The repository layer exposes only insert and read.
- `audit list` filters by actor, actor kind, interface, record type, record id, command, and time range, and returns events with entry counts. `audit show <event_id>` returns the event with entries and a computed field diff per entry.
- `activity <record_type> <record_id>` merges audit entries, notes, and attachment links for that record in time order. The hub has `hub audit list`, `hub audit show`, and `hub audit tail`, hub-scoped, with the same filters: hub admins see every event; other users see events whose entries reference an organization or company they can see, and `audit show` on any other event returns `E_EVENT_NOT_FOUND` exactly as for a nonexistent id. Snapshots inside entries are redacted like any output: fields named `path` or ending in `_path` are removed for non-hub-admins. `audit list` filters: `--since`, `--until` (dates in the viewer's zone), `--actor` (id or username), `--kind`, `--principal`, `--via`, `--command`, `--record-type`, `--record-id`, `--limit`; `list` pages older with `--before` and `tail` pages newer with `--after`.
- Reads are not audited. Every write, including one that changes only `config.toml`, records an event. Presence (6.4) is advisory state, not a record, and its heartbeats are never audited.

`undo <event_id>` reverses eligible list-record fields with a compensating versioned write. It preserves the caller's ordinary reason or directive, including the agent-write gate, and links to the compensated event with unique `undo_of_event_id`; section 11.16 defines conflict and dependency behavior. It refuses events that touched the ledger (`E_NOT_UNDOABLE`), which are voided instead.

### 7.1 Event feed

The audit log is the event stream. `audit tail --after <cursor> --limit <n>` returns events after the cursor in commit order with a new cursor; the cursor is the event's `seq`, an integer assigned in insertion order under the data-root lock, so no two events share a position and a restart never reorders the feed. `tail` accepts every `list` filter. `--follow` on the CLI keeps polling. The host exposes `GET /companies/{company_id}/events?after=<cursor>` as server-sent events, one event per message, with the same filters as `audit list`. A subscriber persists its last processed cursor and reconnects after interruption using `Last-Event-ID` or `after` to resume without gaps. The root-wide folder gate in 3.2 can interrupt an unrelated company's stream; clients retry transient busy responses and must not interpret disconnect as end of history. Webhooks are a later addition on top of this feed and are not in release 1.

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

Manual rate set/show/query and foreign-tagged journal posting follow the bounded
[foreign journal contract](specs/8-foreign-journals.md). Current journal commands
use exact-date manual rates and explicit overrides, half-even conversion and
explicit repricing; provider fetching and foreign settlements remain later work.

## 9. Company

### 9.1 Company info

Table `company_info` in company.db, exactly one row. This table and the rollout below are the completed inventory of the anchor's company information screen; the artifact critic checks the built form against it, and the human runs the side-by-side.

| Field | Meaning |
|---|---|
| legal_name | the name on tax forms |
| display_name | copy of the hub's display name, written after every hub change; read only by `attach`, never returned by `show` |
| tax_id_kind | `ein` or `ssn` |
| tax_id | `NN-NNNNNNN` for EIN, `NNN-NN-NNNN` for SSN, validated for shape only |
| industry | free text used for reporting and later setup suggestions; it does not override the explicit `--chart` selection |
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
| default_chart | id of the packaged chart template that was applied; null for an explicit chartless rollout or a legacy chartless upgrade |
| default_chart_version | version of the applied chart-template manifest; null when `default_chart` is null |
| use_account_numbers | whether ordinary forms, tables, and pickers display stored account numbers; default true so a new company exposes the identifiers it records |
| show_lowest_subaccount_only | whether account pickers display leaf names instead of full names; default false |
| required_employee_profile_fields | ordered requirements encoded as arrays of alternative registered non-secret employee field paths; each requirement needs one populated alternative |
| use_classes, prompt_for_class | whether later forms expose class entry and warn for missing classes; both default false |
| enable_price_levels | whether later sales forms expose price-level defaults; default false |
| units_of_measure_mode | `disabled`, `single_unit_per_item`, or `multiple_related_units`; default `disabled` |
| sales_tax_enabled | whether later sales forms calculate sales tax; default false |
| default_sales_tax_item_id | nullable sales-tax item or group used by later forms |
| sales_tax_liability_basis | `invoice_date` or `payment_receipt`; default `invoice_date` |
| sales_tax_remittance_frequency | `monthly`, `quarterly`, or `annually`; default `quarterly` |
| default_ship_method_id | nullable active ship-method default |
| free_on_board | nullable default free-on-board location text for later sales forms |
| order_printable_checks | default printable-check ordering preference inherited by accounts whose override is null; default false |

`company show` returns every setting above. `company update` accepts every non-derived setting, including the Row 5 flags and employee-requirement array, under the existing company-info version contract. Reference defaults require active targets when set, may be cleared, and retain readable inactive targets until changed. Cross-field validation rejects `prompt_for_class` unless classes are enabled, a default tax item unless sales tax is enabled and the item is a tax item/group, disabling sales tax while a default tax item remains set, or unit defaults inconsistent with the selected mode. Every successful change uses the ordinary company audit event and appears on the generated company workbench form.

Feature disabling never destroys list records or existing assignments. `use_account_numbers` and `show_lowest_subaccount_only` are presentation-only. When classes or price levels are disabled, their lists remain manageable and existing assignments remain visible, but new or changed assignments are rejected until the feature is enabled; clearing remains allowed. Disabling units of measure is rejected with `E_ACTIVE_DEPENDENTS` while any active item references a unit set, naming the blocking items; changing between single and multiple modes preserves valid sets and defaults. Disabling sales tax requires the company default tax item to be cleared but preserves tax codes, tax items, and customer/item classifications for later re-enabling.

Before `co0003`, `co0002` is frozen by replacing its live-schema imports with revision-local DDL that produces the already-shipped historical shape without changing its revision id. An independently captured schema fixture from the published pre-Row-5 commit proves that frozen revision rather than deriving its expectation from the revision under test. `co0003` is entirely revision-local and adds exactly the Row 5 columns in this table that are absent from that shape, then rebuilds `audit_events` to add nullable `undo_of_event_id` plus a unique non-null index. Every later company and hub migration is likewise revision-local. Pre-Row-5 `default_chart` was a rollout request with no account records; the migration clears that value and adds null `default_chart_version`, making the upgraded company explicitly chartless. After Row 5, only the chart service writes either chart-identity field. Migration witnesses compare normalized full SQLite schemas for fresh and populated-upgrade chains for both company and hub databases, and byte-compare existing audit ids, sequences, foreign keys, and snapshot blobs. Before Row 5 ships, a side-by-side finding that changes physical storage edits `co0003` in place and reruns every historical/convergence witness; after it ships, every physical correction uses a new revision.

### 9.2 Rollout

`bookflow company new` takes every writable non-reference field above as options, or `--interactive` to prompt for each, plus `--organization` (id or name; defaulted when the actor can see exactly one) and `--chart` (a packaged chart-template id or `none`). Reference-valued list defaults are set by `company update` after rollout. Required: `legal_name`, `home_currency`. Defaults: `display_name` = `legal_name`; `fiscal_year_start_month` = 1; `tax_year_start_month` = `fiscal_year_start_month`; `timezone` = the machine's zone as reported by the `tzlocal` package; `country` = `US`; `entity_type` and `income_tax_form` = `other`; `tax_id_kind` = `ein`; `report_basis` = `accrual`; `recent_activity_window_seconds` = 60; `legal_address` and `ship_address` = `address`; `industry`, `contact_name`, `tax_id`, address lines, `phone`, `fax`, `email`, `website` = null; `closing_date` = null; `chart` = `general`; Row 5 settings use the defaults in the table. `closing_date` is not accepted at rollout; it is set with `company update`. The tax id, when given, must match the shape for its kind; email must contain one `@`; timezone must be an IANA name; months must be 1 to 12. Rollout applies the selected chart with the chart service in section 10.1 and grants the creating user the owner role. Standard terms, payment methods, sales-tax codes, and other profile seeds are installed by the versioned `standard` profile manifest independently of the selected chart, including when `--chart none` is used. The provisional company database receives chart/profile writes and their deterministic audit events before the ready marker and hub registry row become visible; failure leaves no registered company and no partial ready folder. `--chart none` explicitly leaves `default_chart` and `default_chart_version` null. Output is the company id and display name.

Packaged chart-template ids, chosen by `--chart`: `general`, `service`, `product`, `contractor`, `retail`, `nonprofit`. Each is an immutable, versioned manifest in the package. `general` is the default.

### 9.3 Demo company

`bookflow demo reset`, a hub-admin command, creates, or moves to `trash/` and recreates, an organization `Demo Holdings LLC` holding a company `Demo Plumbing Co` from a seed file in the package, and grants the local owner the owner role. The seed holds sample data for every table that exists: company info, accounts, customers and jobs, vendors, employees, items, terms, notes, attachments, directives, and, once the ledger exists, a year of transactions. The seed grows in the same change that adds a table or command, so the demo always exercises everything that exists. There is one demo organization per data root. The demo company is an ordinary company: it appears in the workbench picker and every command works on it. Tests run against the demo. Ephemeral tables (presence, idempotency keys, session tokens) are exempt from seeding, and so are users and passwords, which are hub state. A second seed, `Reference Plumbing Co`, holds a full year of hand-verified transactions whose trial balance, profit and loss, balance sheet, and aging totals are recorded beside it; every report change regresses against those totals. It arrives with the ledger and grows with each form.

### 9.4 Other company commands

`company list`, `company show`, `company update`, `company rename [--move]`, `company use <company>`, `company attach <path>`, `company detach <company>`, `company backup`, `company restore <backup>`, `company verify` (SQLite integrity check plus ledger invariants: every posting batch balances, corrections exactly reverse source effects, document totals and posting attribution reconcile, dated applications/allocations reconcile to source amounts and control-account balances, and every referenced record exists), `company compact`, `company delete <company> --confirm <id>`. Backups can be scheduled (13.3) with a retention count. `delete` moves the folder to `trash/` and removes the registry rows; `trash list` and `trash empty` manage the folder. `company show` includes `path` only for hub admins.

## 10. The general ledger

### 10.1 Accounts

Table `accounts` carries the common list contract in section 11 and these fields:

| Field | Meaning and validation |
|---|---|
| `name` | trimmed display name, unique among siblings after NFC normalization and case folding |
| `number` | optional identifier of one through seven ASCII digits, unique within the company; comparison and ordering are lexical from the left, so `4100020` precedes `4101` |
| `type` | one of the account types below |
| `parent_id` | nullable account; the parent has the same `type` and the resulting tree has at most five levels |
| `description` | nullable text, at most 200 characters |
| `currency` | ISO 4217 code; defaults to the company's home currency; immutable after a ledger line references the account |
| `tax_line` | nullable tax-form line code carried into tax preparation reports |
| `institution_name` | nullable financial-institution display name |
| `institution_account_last4` | nullable, protected display-only last four characters; a full account number is never stored here |
| `routing_number_last4` | nullable, protected display-only last four characters; a full routing number is never stored here |
| `provider_profile_ref` | nullable opaque reference to an existing protected provider profile; never returned by `list` and not writable until that provider exists |
| `next_check_number` | nullable text used as the default by a later check transaction form |
| `check_reorder_number` | nullable bank-only printable-check reorder identifier |
| `order_printable_checks` | nullable bank-only preference; null means inherit the company setting |
| `default_class_id` | nullable active class used as the account's form default |
| `track_reimbursable_expenses` | expense and cost-of-goods-sold only; default false |
| `reimbursable_income_account_id` | required active income or other-income account when reimbursable tracking is enabled; otherwise null |
| `note` | nullable internal text |
| `system_role` | nullable stable role key, unique within the company; only chart application sets it |
| `is_system` | derived as `system_role != null` |
| `balance` | derived posted balance in home-currency minor units; zero until ledger transactions exist |
| `available_balance` | nullable provider-supplied balance in the account currency; bank-feed work owns updates |
| `normal_balance` | derived from `type` using the table below |
| `statement_family` | derived as `balance_sheet`, `profit_and_loss`, or `neither` |
| `has_transactions` | derived false until a ledger line references the account |
| `child_count` | derived number of direct children |
| `active` | common list activation flag |

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

Required system roles are `accounts_receivable`, `accounts_payable`, `undeposited_funds`, `opening_balance_equity`, `retained_earnings`, `sales_tax_payable`, `inventory_asset`, `cost_of_goods_sold`, and `exchange_gain_loss`. A chart template supplies exactly one active account for every role. A system account may be described and numbered, but its `name`, `type`, `parent_id`, `currency`, `system_role`, and active state cannot be changed. `E_SYSTEM_RECORD` names the protected fields or operation.

`account create`, `account update`, `account show`, `account list`, `account activate`, and `account deactivate` use the shared list behavior in section 11. New accounts never accept `balance`, `available_balance`, `is_system`, or `system_role`. An account cannot be deactivated while it has active descendants, a required system role, transactions, or an active hard dependency: item posting, tax liability, or reimbursable-income use. Vendor expense-account entries are soft form defaults and remain readable if their target becomes inactive. `--cascade` applies only to descendants; it never changes cross-list references. A parent change rejects a cycle, a sixth level, or a parent of another account type. Account `type` changes reject receivable/payable accounts, accounts with children, system accounts, incompatible currencies, and any change that would invalidate an existing master-data or ledger use.

Company setting `use_account_numbers`, default true, controls whether stored numbers appear in ordinary forms, tables, and pickers. Company setting `show_lowest_subaccount_only`, default false, controls picker labels. Neither setting removes stored numbers or computed full names. Account create/update forms expose only fields allowed by the selected type: institution and printable-check fields are limited to bank, credit-card, asset, or liability types as applicable, and routing suffix plus printable-check fields are bank-only. A tax-line value is validated against the account type.

Account lookup and tables use the following declared inventory:

| Concern | Fields |
|---|---|
| Query search | `name`, `full_name`, `number`, `description`, `institution_name`, institution-account suffix, `note` |
| Equality filters | `active`, `type`, `parent_id`, `currency`, `tax_line`, `is_system`, `default_class_id`, `track_reimbursable_expenses` |
| Sort fields | `full_name`, `number`, `type`, `balance`, `updated_at` |
| Default columns | `number`, `full_name`, `type`, `balance`, `currency`, `active` |
| Additional selectable columns | parent, `description`, currency, `tax_line`, `institution_name`, `next_check_number`, `default_class_id`, reimbursable-expense fields, `system_role`, `is_system`, `available_balance`, `updated_at`, every common field |

The default sort is `number` ascending with unnumbered accounts after numbered accounts, using the declared lexical comparison, then `full_name` ascending and `id` ascending. A hierarchy-order sort returns parents before children; flat sorts do not alter stored parentage. `show` includes every stored and derived field, `full_name`, `depth`, and `has_children`. Balances are informational outputs and are never accepted by Row 5 commands. Opening balances and all provider balance refresh behavior are deferred to their ledger and bank-feed passes. Account edits never recategorize existing transactions; any previewed batch application to existing transaction lines belongs to the ledger/form workflow pass.

Packaged chart templates are immutable manifests with `template_id`, `version`, and a complete ordered account tree. The ids are `general`, `service`, `product`, `contractor`, `retail`, and `nonprofit`. `chart list` returns id, version, display name, description, and account count. `chart show <template>` returns the manifest account tree and system-role assignments. Both are authenticated hub-scoped reads of packaged data and are routed over HTTP; they require no company selection. `chart apply` is the company-scoped write.

Version 1 of every template begins with these exact root accounts in this order; currency is the company home currency and omitted fields are null:

| Key | Number | Name | Type | System role |
|---|---:|---|---|---|
| `checking` | 1000 | Checking | bank | |
| `accounts_receivable` | 1100 | Accounts Receivable | accounts_receivable | `accounts_receivable` |
| `undeposited_funds` | 1200 | Undeposited Funds | other_current_asset | `undeposited_funds` |
| `inventory_asset` | 1300 | Inventory Asset | other_current_asset | `inventory_asset` |
| `accounts_payable` | 2000 | Accounts Payable | accounts_payable | `accounts_payable` |
| `sales_tax_payable` | 2100 | Sales Tax Payable | other_current_liability | `sales_tax_payable` |
| `opening_balance_equity` | 3000 | Opening Balance Equity | equity | `opening_balance_equity` |
| `retained_earnings` | 3100 | Retained Earnings | equity | `retained_earnings` |
| `cost_of_goods_sold` | 5000 | Cost of Goods Sold | cost_of_goods_sold | `cost_of_goods_sold` |
| `exchange_gain_loss` | 9000 | Exchange Gain/Loss | other_income | `exchange_gain_loss` |

Each template then appends these exact root accounts in the shown order:

| Template | Number, name, type |
|---|---|
| `general` | 4000 Sales Income `income`; 4100 Service Income `income`; 6000 Advertising `expense`; 6100 Bank Fees `expense`; 6200 Insurance `expense`; 6300 Office Supplies `expense`; 6400 Professional Fees `expense`; 6500 Rent `expense`; 6600 Repairs and Maintenance `expense`; 6700 Utilities `expense`; 6800 Payroll Expenses `expense`; 8000 Other Income `other_income`; 9100 Other Expense `other_expense` |
| `service` | 4000 Service Income `income`; 4100 Reimbursed Expenses `income`; 5100 Subcontractors `cost_of_goods_sold`; 6000 Advertising `expense`; 6100 Bank Fees `expense`; 6200 Insurance `expense`; 6300 Office Supplies `expense`; 6400 Professional Fees `expense`; 6500 Rent `expense`; 6600 Repairs and Maintenance `expense`; 6700 Utilities `expense`; 6800 Payroll Expenses `expense`; 8000 Other Income `other_income`; 9100 Other Expense `other_expense` |
| `product` | 4000 Product Sales `income`; 4010 Sales Returns and Allowances `income`; 5100 Freight and Delivery `cost_of_goods_sold`; 5200 Inventory Adjustments `cost_of_goods_sold`; 6000 Advertising `expense`; 6100 Bank Fees `expense`; 6200 Insurance `expense`; 6300 Office Supplies `expense`; 6400 Professional Fees `expense`; 6500 Rent `expense`; 6700 Utilities `expense`; 6800 Payroll Expenses `expense`; 8000 Other Income `other_income`; 9100 Other Expense `other_expense` |
| `contractor` | 4000 Construction Income `income`; 4100 Service Income `income`; 5100 Job Materials `cost_of_goods_sold`; 5200 Subcontractors `cost_of_goods_sold`; 5300 Equipment Rental `cost_of_goods_sold`; 5400 Permits `cost_of_goods_sold`; 6000 Advertising `expense`; 6100 Bank Fees `expense`; 6200 Insurance `expense`; 6300 Office Supplies `expense`; 6400 Professional Fees `expense`; 6500 Vehicle Expense `expense`; 6600 Repairs and Maintenance `expense`; 6700 Utilities `expense`; 6800 Payroll Expenses `expense`; 8000 Other Income `other_income`; 9100 Other Expense `other_expense` |
| `retail` | 4000 Merchandise Sales `income`; 4010 Sales Returns and Allowances `income`; 5100 Freight-In `cost_of_goods_sold`; 5200 Inventory Shrinkage `cost_of_goods_sold`; 6000 Advertising `expense`; 6100 Bank Fees `expense`; 6200 Insurance `expense`; 6300 Merchant Fees `expense`; 6400 Office Supplies `expense`; 6500 Rent `expense`; 6700 Utilities `expense`; 6800 Payroll Expenses `expense`; 8000 Other Income `other_income`; 9100 Other Expense `other_expense` |
| `nonprofit` | 4000 Contributions `income`; 4100 Grants `income`; 4200 Program Service Revenue `income`; 6000 Program Services `expense`; 6100 Fundraising `expense`; 6200 Management and General `expense`; 6300 Bank Fees `expense`; 6400 Professional Fees `expense`; 6500 Rent `expense`; 6700 Utilities `expense`; 6800 Payroll Expenses `expense`; 8000 Other Income `other_income`; 9100 Other Expense `other_expense` |

If `--chart` is omitted, rollout selects `general`. An explicit template id or `none` always wins; `industry` never changes that selection.

`chart apply <template>` is a company-scoped admin write. It requires null chart identity, validates the complete manifest plus every existing ordinary account before the transaction begins, creates every nonconflicting manifest account in order, and writes `company_info.default_chart` and `default_chart_version` in the same transaction and audit event. Existing ordinary accounts remain untouched; a collision in name, number, parent path, or system role rejects the entire command, so a user may rename the conflicting ordinary account and retry. It accepts an idempotency key. The event has one entry for `company_info` and one for every created account in deterministic parent-first order. A missing role, duplicate role, invalid parent, collision, unsupported type, or invalid currency returns `E_CHART_INVALID`; an already applied chart returns `E_CHART_EXISTS`. Dry run performs the same validation and returns prospective ids/count without migration, rows, audit, or idempotency write. `company new --chart` invokes this service during provisional rollout; failure at any chart or later profile step leaves neither the company nor a partial ready folder registered. Legacy upgraded companies remain chartless and migrations never invent business records. Chart application and rollout events are not undoable.

Packaged profile manifests use the same immutable id/version/key rules. `profile list` and `profile show <manifest>` are authenticated hub-scoped reads; `profile apply standard` is an idempotent company admin write that inserts only absent seed keys and preserves renamed, edited, or inactive seeded rows. It installs the exact term, payment-method, sales-tax-code, ship-method, and customer-message rows in section 11. Profile application is independently available to upgraded or chartless companies and is not undoable.

### 10.2 Transactions

Sections 10.2–10.7 specify the future ledger and forms contract; they do not describe implemented tables. The ledger row implements journal entries and their posting records. Later form rows add the document profiles, applications, and operational subledgers they use. Worked acceptance fixtures are in [accounting-contract-fixtures.md](accounting-contract-fixtures.md).

A **transaction** is the stable business document a bookkeeper opens, edits, prints, pays, or voids. Its **document lines** describe what was sold, bought, or entered. A **posting batch** records one immutable balanced accounting effect. A document line may produce several posting lines or none; neither quantities nor commercial totals are inferred by summing its accounting legs.

Table `transactions` is the current document header and carries the common version/provenance fields. Its id remains unchanged through edits and voids; references, notes, attachments, applications, and external links use that id.

| Field | Meaning |
|---|---|
| type | see 10.3 |
| number | per-type sequence, text, editable, unique per type |
| current_revision_id | immutable document revision currently displayed |
| status | `posted` or `voided` for posting types; a non-posting type has its explicitly defined workflow status |
| voided_at, voided_by, void_reason, void_posting_batch_id | null unless voided; the batch is the final reversal, not a second invoice or payment |

Journal-entry numbers begin at `1` with an empty prefix. Automatic allocation skips numbers already used by the type, including voided documents, within the posting transaction. Explicit numbers are nonblank trimmed case-sensitive text and do not advance the counter. A rolled-back or replayed post consumes no new number. A journal revision's total is its home debit total; output also exposes debit and credit totals separately. Account balance is the account's own posting balance, positive on its normal side, without descendant rollup. Reports explicitly identify signed debit-minus-credit amounts and currency.

Table `transaction_revisions` stores `id`, `transaction_id`, sequential `revision_number`, `supersedes_revision_id`, `date`, `number`, `name_type`, `name_id`, `memo`, exact home-currency `total`, `audit_event_id`, and creation provenance. A revision snapshots all facts needed to reproduce the document: issuer/payee names and addresses, shipping address, applied terms and due date, and the type's relevant form fields. Type-specific tables such as `invoices` are keyed by **revision id**, not a mutable transaction id. A new revision never modifies the old revision or its child rows. The current header pointer and all new history rows commit atomically.

Table `document_lines` holds the commercial or entered lines of each revision.

| Field | Meaning |
|---|---|
| revision_id, line_id, position | `line_id` is stable within the document across revisions; a new line gets a new id and removed ids are never reused |
| kind, parent_line_id | item, expense, entered journal line, subtotal, group heading, discount, or another type-defined role; optional hierarchy within this revision |
| item_id, name_type, name_id, class_id | nullable stable master-record references; not copies of posting legs |
| description, item_name, unit_name, class_name, party_name | relevant displayed values snapshotted for this revision |
| quantity, unit_id, unit_factor, base_quantity | exact quantities and conversion used; null where not applicable; group headings/subtotals carry no stock movement |
| rate, net_amount, discount_amount, tax_amount, gross_amount | exact commercial amounts with currency; type rules define which apply and reconcile them to the document total |
| tax components | immutable child components with tax item/agency ids, applied rates, taxable bases, rounding results, and displayed labels |
| billable, source references | relevant billable state and explicit originating document/line, work-order line, or time-entry references |
| entered journal fields | account, debit/credit, and foreign input facts on a journal-entry form; posting lines carry the resulting ledger effects |

Form validation resolves item defaults, account mappings, prices, units, tax facts, customer/job inheritance, and terms once for the submitted revision. Their applied values are stored. Editing master data never rerenders a historical document with new values or recalculates its postings. Editing a document preserves unchanged snapshot values; refreshing from current defaults is an explicit input shown in preview. Live master labels may appear separately as navigation aids. Historical rendering and reports retain access to inactive referenced masters.

Table `posting_batches` holds `id`, `transaction_id`, `revision_id`, `kind` (`original`, `reversal`, `replacement`), `effective_date`, `reverses_batch_id`, `replaces_batch_id`, `audit_event_id`, and creation provenance. A non-posting document has no batch. Original and replacement batches are generated from a validated document revision. A reversal names exactly one batch, is unique for that target, and contains the exact inverse of its lines. Every batch and line is immutable. The accounting date and UTC recorded time are distinct; reports use the former for accounting periods and expose the latter for history.

Table `posting_lines` holds `id`, `batch_id`, `line_no`, `account_id`, positive home-currency `debit` or `credit` (exactly one is non-zero), applicable `name_type`/`name_id`, `class_id`, description, and foreign amount/currency/rate facts. `posting_line_sources` links a posting line to its source revision and document line, tax component, application, or type-defined header component. Aggregated postings carry exact per-source allocated amounts; source amounts sum to their posting line. Cross-document sources carry their own document/revision ids. Reversals retain the same attribution with opposite debit/credit and a link to the reversed line. Operational stock quantities live in inventory movements linked to document lines and posting effects, not repeated on revenue, tax, receivable, and cost legs.

Every posting batch balances independently. Every new business posting uses active accounts other than `non_posting`; every AR line names a customer or job and every AP line names a vendor. An exact reversal may reference an account or master deactivated since the original posting. It must not substitute a currently active account or recompute foreign rates, prices, taxes, or inventory cost. Original and reversal batches both remain reportable.

An allowed `<type> update` performs the ordinary versioned document-edit workflow. For a posted document it validates the complete replacement, appends a revision, reverses the current unreversed business batch in full, and appends a full replacement batch. The reversal uses the old accounting date and replacement uses the new accounting date; both dates must be open (10.6). Posting history, document revision, application adjustments, and audit commit in one company transaction. Every financial edit is validated as one aggregate; disjoint-field merging under 6.2 cannot bypass recalculation, application, reconciliation, or period checks. Type plans explicitly define permitted edits and dependencies, including inventory costing and cleared/reconciled entries, before exposing update. No caller can edit a posting line directly. A no-op creates neither a revision nor posting batches.

### 10.3 Transaction types

The supported home-currency invoice and paid-receipt forms, captured defaults,
exact calculations, correcting history and migration rules are defined in
[Service invoices and sales receipts](service-sales.md).

Each type has its own document profile and a fixed posting rule. The table gives the accounting effects, not the shape or count of document lines. A subtotal or group heading posts nothing; a stocked sale line can produce revenue, receivable, tax, inventory, and cost effects with linked source attribution. Journal entries use entered debit/credit lines as their document profile and produce a distinct balanced posting batch.

| Type | Command | Name | Accounting effects |
|---|---|---|---|
| journal_entry | `journal post` | optional per line | as entered |
| invoice | `invoice post` | customer or job | Dr AR; Cr income per item; Cr Sales Tax Payable; inventory items also Dr COGS, Cr Inventory Asset |
| sales_receipt | `sales-receipt post` | customer or job | Dr deposit-to account or Undeposited Funds; Cr income; tax and inventory as invoice |
| credit_memo | `credit-memo post` | customer or job | sales credit: Dr income/tax liability; Cr AR; inventory returned only for explicitly returned quantities |
| payment | `payment receive` | customer or job | Dr Undeposited Funds or bank; Cr AR; applies to open invoices |
| deposit | `deposit post` | none | Dr bank; Cr Undeposited Funds and any other lines |
| bill | `bill post` | vendor | Dr expense, COGS, or Inventory Asset per line; Cr AP |
| bill_payment | `bill pay` | vendor | Dr AP; Cr bank or credit card; applies to open bills |
| check | `check write` | any | Cr bank; Dr per line |
| credit_card_charge | `charge post` | vendor | Cr credit card; Dr per line |
| transfer | `transfer post` | none | Dr to account; Cr from account |
| inventory_adjustment | `inventory adjust` | none | Dr or Cr Inventory Asset; offset to adjustment account |
| vendor_credit | `vendor-credit post` | vendor | purchase credit: Dr AP; Cr the credited expense/asset accounts; inventory returned only for explicitly returned quantities |
| estimate | `estimate create` | customer or job | non-posting; converts to an invoice in full or by progress percentage or selected lines |
| sales_order | `sales-order create` | customer or job | non-posting; converts to an invoice and tracks backorders |
| purchase_order | `purchase-order create` | vendor | non-posting; received against by bills and item receipts |
| item_receipt | `item-receipt post` | vendor | Dr Inventory Asset; Cr AP; later billing links the receipt's lines and cannot receive the goods or recognize AP a second time |
| statement | `statement send` | customer | non-posting; the customer's open items and activity for a period |

Bank and credit card reconciliation: `reconcile start <account> --statement-date --ending-balance`, `reconcile mark <transaction> [--clear|--unclear]`, `reconcile finish`, which records the reconciliation with its difference (zero, or posted to a reconciliation discrepancy account with `--force`) and marks cleared transactions with the reconciliation id. Batch invoicing creates one invoice per selected customer from a template. `find <text>` searches names, memos, numbers, and amounts across lists and transactions the actor can see.

Credit memos and vendor credits are independent business documents. They are not the internal exact reversals used by edit or void. Conversions retain source document/line identities and exact converted quantities/amounts; the receiving type prevents duplicate fulfillment or posting. Each form's owning plan specifies its fields, snapshot profile, rounding, posting attribution, and conversion dependencies before implementation. Forms and their tables are built in the release after the ledger; see section 21.

### 10.4 Applications

Applications settle open items without changing document identity. Table `applications` holds immutable rows: `id`, `paying_transaction_id`, `paid_transaction_id`, exact positive home-currency `amount`, `effective_date`, `recorded_at`, `kind` (`apply`, `unapply`), nullable unique `reverses_application_id`, `audit_event_id`, and creation provenance. Payments apply to invoices, bill payments to bills, customer credits to customer receivables, and vendor credits to vendor payables. Both sides must belong to the same company and compatible party/AR-or-AP account; cross-party netting is an explicit future operation, never implicit. The source and target must be eligible and not voided. An application cannot predate either source document. Both documents' current versions are checked atomically.

For each date, effective applications are applies minus their effective unapplies. A document's open amount is its gross obligation or available credit/payment amount at that date minus its effective applications; neither side may be over-applied. One unapplied payment balance is held as customer credit, not recognized as invoice revenue. Application date records settlement, not the later date a deposit moves Undeposited Funds to bank. Aging uses the historical document revision and applications effective at the report date, not today's mutable header or only today's active application set. Unapply corrects an allocation: it appends the exact negation of an application and its allocation records at the original application effective date, with the actual later recorded time; it never deletes or edits them. That effective date must be open. Reapplication creates a new application. A later-period commercial credit or refund is its own document, not a backdated unapply of closed-period settlement.

Table `application_allocations` attributes each applied amount to immutable target revision/line/components with exact integer amounts, source posting attribution, and recognition role (for example sales net, tax liability, purchase expense, or asset principal). Each row has `id`, `application_id`, `kind` (`allocation`, `reversal`), nullable unique `reverses_allocation_id`, effective date, recorded time, and audit provenance. Effective allocations, including exact reversals, sum exactly to the effective application amount. When no explicit line allocation is supplied, allocate over the target's remaining settlement components proportionally by their remaining amounts using largest remainders; break ties by stable document-line id then component id. A later installment consumes remaining component amounts, and final settlement consumes the exact residue. Credits, discounts, withholding, foreign-exchange differences, and write-offs require explicit typed components and balanced posting rules; they cannot be hidden as rounding or payment revenue. Each owning form defines eligible components and signs before supporting these cases.

Editing either applied document is allowed only while the new amount covers its applications, party/account compatibility remains valid, and its new date does not move past an existing application date; otherwise return `E_APPLIED_EXCEEDS_TOTAL` or `E_HAS_APPLICATIONS` without changes. Applications keep their document ids and amounts. If an edit changes their target allocation components, the edit atomically appends exact allocation reversals and replacements tied to the new revision; prior attribution remains available. Such restatement uses the original application effective dates, so every affected application date must also be open. An edit that leaves components unchanged retains the allocation records. The preview includes changed open balances and attribution. Reversing or voiding a payment does not silently delete applications: its command explicitly unapplies them in the same transaction or rejects with `E_HAS_APPLICATIONS`.

Cash-basis reporting uses these stored allocations and declared recognition rules (14), not a floating-point payment-to-invoice ratio applied anew on each report. An allocation to sales tax remains a liability component, never income. Depositing an already recognized receipt, transferring cash, or settling credit against credit does not create a second cash receipt.

### 10.5 Void

`<type> void <id>` requires a reason and the ordinary version check. It marks the document `voided`, preserves every revision and amount, and appends an exact reversing posting batch for its current unreversed business batch at that batch's date. It records `void_posting_batch_id`; the internal reversal has no new business-document number and cannot be paid, sent, or counted as another sale. Earlier edit/reversal pairs remain untouched. A second void is a no-op with no second reversal. A non-posting document follows its own cancellation contract and creates no accounting reversal.

Voiding a document with applications is rejected with `E_HAS_APPLICATIONS` until they are explicitly unapplied, or the paying type's void command explicitly includes that unapplication atomically (10.4). Inventory, fulfillment, and reconciliation dependencies are also validated by the owning type; a void cannot erase later dependent movements. Zeroing the original amounts or excluding its original batches from reports is forbidden. Open-period checks apply to the reversal and every associated unapplication; a closed-period transaction cannot be voided by choosing a later reversal date through this command.

### 10.6 Closing date

When `closing_date` is set, any post, void, or update affecting an accounting date on or before it is rejected with `E_PERIOD_CLOSED` before writing. An edit checks both the old and new posting dates and any application/allocation dates it restates; changing the header date to an open period does not bypass the old-period check. Apply/unapply checks its own effective date. Applying a new open-period payment to an unchanged old invoice is allowed: it changes the open balance from the payment date, not the closed-period postings. No command rewrites or removes closed-period posting or settlement history.

Changing the closing date requires the admin or owner role and is audited. There is no password override. A current-period adjusting journal entry may refer to a closed-period document as evidence but remains a distinct dated document; it does not edit, void, or backdate that document. Type-specific later-period credits and returns follow their own posting rules.

### 10.7 Numbering

Each transaction type has a sequence in `sequences` (`type`, `next_number`, `prefix`). Creating/posting a new business document takes the next number unless the caller supplies one. A supplied duplicate is rejected with `E_DUPLICATE_NUMBER`. Edits retain the number unless the caller explicitly changes it; number uniqueness includes voided documents. Document revisions, exact reversals, and replacement posting batches never consume business-document numbers. Internal ids, not editable numbers, join accounting history and applications.

### 10.4 Account register inventory

An account register shows prior account movements alongside an entry row. Balance-sheet accounts open registers from the chart and account detail; income and expense accounts open the account-filtered general ledger. Non-posting accounts accept no entry. The selected account header contains its name, permitted number, type, home currency and own balance, without descendants.

| Area | Fields and actions |
|---|---|
| Entry | accounting date; journal number; typed payee; Payment and Deposit for bank accounts; Charge and Payment for credit cards; Increase and Decrease for other balance-sheet accounts; category or transfer account; memo; class; Splits; Record; Restore |
| Splits | account, positive amount, direction relative to the main entry, memo, customer/job or other typed party, class; Add, Remove, Clear, Recalculate, Close |
| History | accounting date, journal number, journal type, payee, category or Splits, memo, class, increase/decrease, normal-side running balance; Original/Reversal/Replacement effect and journal detail/history link |
| Navigation | searchable selected account; date range; bounded previous movements; next page with restart on change; account general-ledger link; current journal edit and void |
| Keyboard | selected Date on entry; Tab/Shift+Tab through row fields and actions; picker arrows/Enter/Escape; Tab to Record then Enter; T for company-local today and +/− for adjacent days; return to selected Date after posting |

The initial register records journals. Journal number uses the journal sequence, including explicit unique numbers; it does not consume a check number or create a check, deposit or payment document. Selected money direction controls the account's normal-side movement. All arithmetic, split totals and journal translation run in the company service. Browser money stays decimal text. No balancing plug is inserted.

The wider register inventory includes one-line display, date/order-entered sorting, remembered payee amounts and expense accounts, check/reference numbering and printing, Go to, report printing, bank-feed setup, cleared status and statement reconciliation, billing-related split attribution, and additional month/week/year date shortcuts and date preferences. These belong to their corresponding document, banking, billing, print and preference workflows. They are unavailable in the domestic journal register. The billing-related column's complete behavior and the sort/print/Go-to option inventories remain unspecified until those workflows are inventoried.

Register history includes every accounting effect. An old effect opens the stable journal's current version for editing and its immutable revision for historical display. A journal with multiple selected-account lines or incompatible dimensions stays readable and opens the journal editor; the register does not flatten it. Closed-period and whole-document version checks apply to all register edits. A new current-period adjustment is a distinct journal with an explicit reason/source reference.

On narrow displays the entry fields stack in keyboard order and history scrolls within its own labeled region. A successful post remains visible in a receipt even outside the selected period; balance refresh is a separate current read. An ambiguous save retains its exact request and idempotency key for retry. Stale reads restart without erasing the draft. Readonly users have history and authorized detail links, without entry/edit/void controls.

## 11. Lists

### 11.1 Shared list contract

Every primary list noun provides `query` with typed `summary` and `reference` projections. Input preserves the noun's text search, typed filters, active/inactive selection and ordering, and adds strict `limit` 1–200 (default 50) and an optional base64-encoded JSON cursor. The cursor is transparent: it is neither encrypted nor signed, and it carries the company id, noun, a hash of the query arguments, a hash of the caller's effective permissions, the company audit sequence, and the next offset; the server recomputes and compares every hash on each request, so a forged or altered cursor cannot widen scope, but any holder can read it. Output is `{projection, items, count, next_cursor}`; count is returned page size, not a total-result count. Reference rows have stable id, version, readable label and active state. Summary rows contain declared bounded display columns; full owned collections remain in `show`. Legacy `list` remains complete full-record enumeration and may be expensive. Filtering and ordering happen before pagination; projections do not call show once per result.

A continuation is bound to company, noun, query arguments and current actor/principal/effective permissions. It records the company audit watermark and next offset. Mismatched scope/arguments/permissions are validation errors. Any audited company write makes the continuation `E_QUERY_STALE` (HTTP 409); restart without the cursor, preserve search inputs, and discard accumulated pages. Presence does not invalidate it. Pages within an unchanged watermark enumerate deterministically with id as the final ascending tie-break. Each request reauthorizes; the cursor grants no authority. Customer inherited fields use fixed-depth indexed ancestor lookups, bounded by the five-level hierarchy, with no materialized inheritance cache.

Every primary list record carries the common fields of section 6.1, `active`, a normalized lookup key, and nullable immutable `seed_key`. The seed key is unique within its table, accepted only by chart/profile seed services, and omitted for ordinary records. The shared verbs are `create`, `update`, `show`, `list`, `query`, `activate`, and `deactivate`. They apply to `account`, `customer`, `vendor`, `employee`, `other-name`, `item`, `item-category`, `class`, `term`, `payment-method`, `sales-tax-code`, `customer-type`, `vendor-type`, `job-type`, `sales-rep`, `ship-method`, `customer-message`, `price-level`, `unit-of-measure`, and `custom-field`. Every command is company-scoped.

Every company connection enables and verifies SQLite foreign-key enforcement. Declared foreign keys use restrictive deletion and indexed columns. Polymorphic target/type pairs are validated in the company service within the same transaction. A record id from another company and a nonexistent id return byte-identical `E_RECORD_NOT_FOUND` output with suggestions drawn only from the selected company.

Record resolution tries a ULID first, then `full_name` for a hierarchical list or the noun's declared display key for a flat list, after trimming, NFC normalization, and case folding. Flat tables store a list-unique `name_key`. Hierarchical tables store a sibling-unique `name_key`, table-wide unique materialized `full_name` and `full_name_key`, integer `depth`, and an internal ULID path. A leaf/display name is at most 200 characters, its normalized key at most 400, a five-level visible full name at most 1,004, and its normalized full-name key at most 2,004; excess is `E_VALIDATION`. A primary Row 5 display key cannot contain `:`, including a flat source that may later convert to a hierarchical list. Any normalized display-key, sibling-name, number, initials, code, or full-name collision returns `E_NAME_TAKEN`. Names in separate lists occupy separate namespaces; the same display name may identify a customer and a vendor. Polymorphic references always store record type and id.

Hierarchical lists carry nullable `parent_id`; their colon-separated names and paths are projections of the tree. The root is level one and the maximum depth is five. Parent and name updates validate the complete resulting subtree, reject cycles with `E_HIERARCHY_CYCLE`, reject a result deeper than five with `E_HIERARCHY_DEPTH`, enforce sibling and descendant full-name uniqueness, and call the noun's parent/type compatibility rule. Rename and reparent recompute every descendant's materialized name, key, depth, and path in the same transaction without incrementing descendant record versions or writing descendant audit entries; the command output names the affected descendant ids. A three-level hierarchy is present in the demo and is an acceptance witness; it does not lower the supported maximum.

Shared write behavior is:

- `create` accepts an idempotency key. `update`, `activate`, and `deactivate` use the version, disjoint-merge, blind-write, dry-run, reason, directive, and audit rules in sections 5–7. A blind list write always returns a visible warning naming the unverified current version and fields; generated browser forms always submit the version they rendered.
- Nullable update fields use absent to preserve and null to clear. The CLI represents explicit clearing with `--clear`.
- References supplied by a new write must resolve to active records. Existing references to a record remain readable after it is deactivated. Default reference pickers omit inactive records.
- `deactivate` on a hierarchical parent with active descendants returns `E_ACTIVE_DEPENDENTS`. `--cascade` deactivates the complete active subtree atomically and returns affected ids in deterministic parent-first order. Cross-list dependents are never cascaded; `E_RECORD_IN_USE` returns visible dependent record types and counts.
- `activate` on a child requires every ancestor to be active and never activates an ancestor implicitly. The caller activates ancestors explicitly before the child. No activate cascade exists: the anchor revives an entry together with its subentries in one confirmation, and a Bookflow `--cascade` on `activate` is staged with the list-center pass; until then each row is activated separately, parents first.
- Composite writes, including nested child changes, links, conversions, and cascades, use one database transaction and one audit event with an entry for every logically changed record; internal hierarchy-projection maintenance is not a descendant record change.
- No list or nested child command hard-deletes data. Normalized child rows use stable ids but live inside the owning record's command model and audit snapshot. Array order is position and is not a separate input field. An omitted child collection is preserved; a supplied collection is the complete replacement, with retained ids identifying retained rows and absent prior rows deactivated in the same owner write. New children receive ids; ordinary input cannot reactivate a retired child by id. The whole collection is one concurrency field, except custom values use one logical path per definition id.
- Activating an active row or deactivating an inactive row succeeds with `changed: false`, preserves the version, writes no event, and still validates the row's current structural invariants.
- Dry run performs all reference, dependency, hierarchy, version, and uniqueness checks and returns the prospective result without a row, audit event, migration, or idempotency write.

Dependency policy is fixed by reference kind. Structural parents and required system roles block deactivation. Active item posting-account, tax-agency, unit-set, group/assembly-member, reimbursable-income, and required-child references block deactivation because their owner would cease to be valid. Types, classes, terms, payment methods, sales representatives, ship methods, customer messages, price levels, sales-tax codes, vendor expense-account entries, and other form defaults are soft references: an existing assignment remains readable when its target becomes inactive, but a new or changed assignment must target an active record. Link endpoints and custom definitions with active values use their explicit unlink and definition rules. An inactive dependent owner never blocks its target.

Every list declares its own search fields, equality filters, sort fields, default columns, and additional selectable columns below. `list` accepts `query`, `include_inactive`, repeated `filter` entries encoded as `field=value`, `sort`, and `direction` (`asc` or `desc`). The list definition parses each declared filter to its exact type. Active-only is the default; `include_inactive` returns active and inactive rows, and `filter active=false` returns inactive rows only. Unknown filter or sort fields return `E_LIST_FILTER`; unknown top-level input keys retain the universal `E_VALIDATION` rule. Text search uses trimmed, case-folded containment across the declared fields. The first declared sort field is the default unless the noun states another order. Each sort appends `id` ascending as a stable final key.

JSON list output always returns the complete list output model. Column declarations control human CLI tables and workbench tables. The generated workbench exposes declared search and filter controls, sortable headings, URL-stored selectable/reorderable columns, and reset to the declared defaults. A selected record links to `show`; allowed write actions follow the current role threshold. Row 5 is the first human browser-product checkpoint: the shared shell has persistent company context, grouped primary navigation, a visible current section and primary action, responsive list/detail/form layouts, empty states, and success/error feedback. Discriminated account type, item type, term kind, price-level kind, custom-field kind, and sales-representative source type are chosen before their variant fields render; edits pin the stored discriminator except for an explicitly permitted transition. Reference pickers expose an `Add new …` action when the actor can create that noun; it opens the ordinary create form and returns the created stable id to the unfinished form. The shell remains generated from shared metadata rather than duplicating a bespoke interface for each noun. Spreadsheet editing, CSV import/export, save-and-new workflows, and purpose-built list centers are deferred to section 14.1 and later browser-workflow passes.

Money inputs in this section use the decimal-string API of section 5.6; outputs carry `amount`, `currency`, and `minor_units`, and company storage is signed 64-bit integer minor units. Customer, vendor, item, fixed-asset, item-discount, and vendor-profile money must use home currency; account amounts use account currency; price-level prices and rounding values use the level currency or home currency when the level omits one. No command converts currency implicitly. Percentages store signed millionths of one percent: tax, term-discount, and item-discount are 0 through 100; price-level percentages are -100 through 1,000,000. Quantities store signed micro-units and fields declared nonnegative reject negative values. Unit conversion factors store positive nano-units. Custom number values are canonical decimal strings with at most nine fractional digits and a coefficient fitting signed 64-bit nano-units; zero is `0` and insignificant zeroes are removed. Floats and booleans are rejected. Money excess scale is `E_AMOUNT_PRECISION`; other malformed/excess-scale values are `E_VALIDATION`; range or storage overflow is `E_VALUE_RANGE`. Unit conversion rounds once to six quantity decimals with decimal half-even. Bill-of-material cost rounds each component extension to the currency minor unit with decimal half-even before exact summation.

Current Row 5 authorization remains role-based. Reads require `member`; ordinary lifecycle writes, customer/vendor link operations, other-name conversion, and owner custom-value changes require `standard`; company-setting updates, custom-field definition writes, chart/profile application, and undo require `admin`. Undo also requires the actor to meet the highest role threshold of every command represented by the original event. Registry capability labels are compatibility metadata until Row 7: each lifecycle noun uses its exact noun prefix, with additional `chart`, `profile`, and `undo`; link uses `customer`, and conversion uses `other-name`.

Protected-profile identifiers are never returned or stored in audit snapshots in Row 5. Institution/account routing suffixes and customer payment brand/last-four display data are visible to any company member when populated by their later protected stores. Vendor and employee tax-id suffixes are null below admin. Full identifiers and credentials are never accepted through these list commands.

The list-specific errors are `E_RECORD_NOT_FOUND`, `E_NAME_TAKEN`, `E_VERSION_CONFLICT`, `E_ACTIVE_DEPENDENTS`, `E_RECORD_IN_USE`, `E_INACTIVE_REFERENCE`, `E_HIERARCHY_CYCLE`, `E_HIERARCHY_DEPTH`, `E_SYSTEM_RECORD`, `E_LIST_FILTER`, `E_TYPE_CHANGE`, `E_VALUE_RANGE`, `E_NOT_UNDOABLE`, `E_ALREADY_UNDONE`, and `E_UNDO_CONFLICT`. Not-found suggestions obey section 5.4 and never reveal inaccessible records.

### 11.2 Customers and jobs

Table `customers`. A top-level row is a customer. A row with `parent_id` is a job and may nest under a customer or job through the shared five-level maximum.

Customer and job fields are:

| Group | Fields and behavior |
|---|---|
| Identity | `name`, `company_name`, `salutation`, `first_name`, `middle_name`, `last_name`, `job_title`; `name` is required and is the hierarchical display key |
| Billing address | nullable structured `line1`, `line2`, `city`, `state`, `postal_code`, `country` |
| Shipping addresses | ordered `customer_addresses` child rows with stable id, `label`, the structured address fields, `is_default`, and `active`; at most one active default per customer or job |
| Contacts | ordered `customer_contacts` child rows with stable id, `role` (`primary`, `alternate`, `additional`), `display_name`, salutation, first, middle, last, job title, work/home/mobile/other phone, work/home fax, primary/secondary email, website, external handle, and `active`; at most one active primary and alternate. Each contact also owns ordered `customer_contact_points` rows with stable id, a canonical `kind` from the phone/fax/email/URL/social/messaging/other catalogue, editable `custom_label`, `value`, and `active`; four generic other kinds make relabeling independent of schema changes. |
| Convenience outputs | `contact`, `alt_contact`, `phone`, `alt_phone`, `fax`, `email`, `cc_email`, and `website`, derived from active contact rows and accepted as exact shortcuts using the mapping below |
| Commercial defaults | nullable active `terms_id`, `sales_tax_code_id`, `sales_tax_item_id`, `price_level_id`, `customer_type_id`, `sales_rep_id`, `preferred_payment_method_id`, and `preferred_ship_method_id` |
| Tax and credit | nullable `resale_number`; `credit_limit` as exact home-currency money; the limit is a warning input to later sales forms, not a posting prohibition |
| Delivery | `preferred_delivery_method` (`none`, `email`, `mail`), default `none`; it preselects a later delivery method and never sends by itself |
| Account | nullable `account_number` |
| Protected payment profile | nullable opaque `payment_profile_ref` to an existing protected profile plus display-only brand, last four, expiry month/year, and billing address; no security code or full account/card number is accepted, and the reference remains null until the protected provider exists |
| Relationship | derived nullable `linked_vendor_id` from the one-to-one link in section 11.4 |
| General | nullable `notes`, `default_class_id`, and typed `custom_fields` |
| Balances | derived `current_balance` and `open_balance`; zero and unavailable until ledger support, never writable |

A job also carries `job_status` (`none`, `pending`, `awarded`, `in_progress`, `closed`, `not_awarded`), nullable `job_type_id`, `job_start`, `job_projected_end`, `job_end`, `job_description`, and a job-specific sales-rep override. A projected or actual end cannot precede the start. Closing a job without an actual end succeeds with a warning. The status vocabulary is fixed; the anchor lets each of its status labels be renamed in company preferences. Renamable job-status labels are staged with the company-settings increment and are not part of the current contract.

Jobs inherit scalar address, terms, tax, price-level, customer-type, sales-rep, payment-method, ship-method, delivery-method, and default-class values from the nearest ancestor when the stored override is null. They store `address_mode` and `contact_mode`, each `inherit` or `own`; `own` can hold an explicitly empty collection, while top-level customers always use `own`. `show` returns each stored override, each effective value, and the source record id for an inherited value. Search, filters, sorts, columns, and convenience outputs use effective values. Updating an ancestor changes a descendant's effective value only where the descendant has no override.

Convenience contact fields resolve from the active primary contact, then alternate, then child order. Shortcut mapping is exact: `contact` → primary `display_name`; `alt_contact` → alternate `display_name`; `phone` → primary `work_phone`; `alt_phone` → alternate `work_phone`; `fax` → primary `work_fax`; `email` → primary `primary_email`; `cc_email` → primary `secondary_email`; and `website` → primary `website`. A shortcut update edits or creates the mapped contact and shares the collection's concurrency path. Supplying a shortcut and a child value for the same field with different values returns `E_VALIDATION`. A shortcut on a job whose `contact_mode` is `inherit` is rejected; the caller may atomically switch to `own` and submit the complete effective-or-edited contact collection.

Customer lookup and tables use:

| Concern | Fields |
|---|---|
| Query search | `name`, `full_name`, `company_name`, contact names and points, billing/shipping addresses, `account_number`, customer/job type, sales rep, `notes`, searchable custom fields |
| Equality filters | `active`, `parent_id`, customer or job, `customer_type_id`, `job_type_id`, `job_status`, `sales_rep_id`, `price_level_id`, `sales_tax_code_id`, `preferred_payment_method_id`, `linked_vendor_id` |
| Sort fields | `full_name`, `company_name`, primary contact, phone, `current_balance`, customer type, sales rep, job status, `updated_at` |
| Default columns | `full_name`, `company_name`, primary contact, phone, `current_balance`, customer type, sales rep, `active` |
| Additional selectable columns | account number, postal code, email, payment method, terms, credit limit, price level, tax code/item, job status/dates, delivery and ship method, linked vendor, every custom and common field |

Open-balance and overdue filters, transaction panes, related-transaction actions, and balance links become functional with ledger and form rows. Contacts and address defaults exist in Row 5. Notes, attachments, and the merged activity feed integrate in Row 6. The generated workbench exposes current fields and visibly labels unavailable related panes; purpose-built tree/detail list centers are deferred.

### 11.3 Vendors

Table `vendors` carries `name`, `company_name`, `salutation`, `first_name`, `middle_name`, `last_name`, `job_title`, structured address, and ordered stable `vendor_contacts` child rows with the roles and contact fields declared for customers. Vendor contacts own the equivalent ordered `vendor_contact_points` rows. Their convenience contact outputs and shortcuts use the exact customer mapping. It also carries nullable active `terms_id`, `vendor_type_id`, `default_class_id`, staged-null `billing_rate_level_id`, `account_number`, `print_name_on_check_as`, `credit_limit` as exact home-currency money, `eligible_1099`, `is_tax_agency` default false, `recall_last_transaction` as a nullable company-preference override, `notes`, and typed `custom_fields`. Billing-rate levels arrive with time billing; Row 5 neither accepts nor resolves that reference. An active sales-tax item may reference only an active vendor whose `is_tax_agency` is true; clearing that flag or deactivating the vendor returns `E_ACTIVE_DEPENDENTS` naming the tax item and the required remediation.

An ordered `vendor_expense_accounts` child collection holds at most three active expense or cost-of-goods-sold account references. `tax_id_kind` and `tax_id_last4` are display metadata; `tax_profile_ref` is an opaque reference to an existing protected profile and remains null until that store exists. A full tax identifier is not accepted or returned by Row 5. The full identifier and role-gated reveal/update behavior belong to the identity, tax, and payroll security pass. Vendor `current_balance` and `open_balance` are derived, unavailable until the ledger, and never writable. The one-to-one customer relationship is the derived `linked_customer_id` from section 11.4.

`item_vendor_profiles` are stable child records keyed by item and vendor. Fields are `preferred_rank`, `vendor_item_name`, `purchase_cost` as exact home-currency money, `minimum_quantity` as a nonnegative exact decimal, `lead_time_days` as a nonnegative integer, `manufacturer_part_number`, `availability_notes`, and `active`. Ranks are unique per item and any number of alternates may exist. An item's stored writable `preferred_vendor_id` is the rank-one projection: setting it promotes or creates that vendor's active profile at rank one, and profile reconciliation that changes rank one updates the projection atomically. Vendor and item `show` include the visible profiles. Last purchase date and cost are derived later from transactions.

Vendor lookup and tables use:

| Concern | Fields |
|---|---|
| Query search | `name`, `company_name`, person and contact fields, address, `account_number`, check name, vendor type, item-vendor identifiers, `notes`, searchable custom fields |
| Equality filters | `active`, `vendor_type_id`, `terms_id`, `eligible_1099`, `is_tax_agency`, `default_class_id`, `linked_customer_id` |
| Sort fields | `name`, `company_name`, primary contact, phone, `open_balance`, terms, vendor type, `updated_at` |
| Default columns | `name`, `company_name`, primary contact, phone, `open_balance`, terms, vendor type, `active` |
| Additional selectable columns | account number, check name, credit limit, tax-id last four, 1099 eligibility, tax-agency status, email, postal code, default accounts, billing rate, linked customer, every custom and common field |

Terms and account number populate later purchasing and payment forms. Credit limit and prior-transaction recall produce later form defaults or warnings; neither posts anything in Row 5. Financial filters and transaction panes become functional with ledger and form rows.

### 11.4 Customer and vendor link

Table `customer_vendor_links` carries `customer_id`, `vendor_id`, `active`, and the common fields. One active customer links to at most one active vendor and vice versa. `customer link-vendor <customer> <vendor>` requires both records active, accepts expected versions for both, and rejects an existing relationship on either side with `E_RECORD_IN_USE`. A previously unlinked identical pair is reactivated when neither endpoint has another link. `customer unlink-vendor <customer>` accepts the customer, vendor, and link expected versions and deactivates the link. Link and unlink increment both endpoint versions in one transaction. Link audit entries are ordered customer, vendor, link; unlink entries are ordered link, customer, vendor, and undo plans the safe inverse order. Endpoint entries use action `link` or `unlink`; each endpoint snapshot includes a `counterparty_link` logical field so concurrency and undo observe the relationship. `show` on either record includes the current counterpart plus stable id/version/active metadata for all of that endpoint's current and former pairs, so unlink and deliberate reactivation never guess a concurrency token. The link never changes posting behavior or silently synchronizes fields. A workbench contact-copy action previews and then submits ordinary versioned customer and vendor updates.

### 11.5 Employees

Table `employees` carries `name`, salutation, first, middle, and last name, job title, `print_name_on_check_as`, `employment_type` (`full_time`, `part_time`, `seasonal`, `temporary`, `other`), structured home/contact address, typed phone and email fields, hire date, release date, structured emergency-contact name/relationship/phone/email, `default_class_id`, `notes`, typed `custom_fields`, and display-only `tax_id_last4`. A release date cannot precede the hire date. A released employee may remain active for current non-payroll selection; deactivation is explicit. Birth date, gender, marital status, full tax identifiers, and other protected payroll demographics are explicitly deferred to the role-gated payroll profile rather than stored in general Row 5 master data.

`profile_complete` and `missing_profile_fields` are derived from `company_info.required_employee_profile_fields` and are returned by `show` and `list`. The setting is an ordered array of nonempty alternative-path arrays. A built-in path is one declared non-secret employee output name, with `address.<line1|line2|city|state|postal_code|country>` for address leaves; a custom path is `custom_fields.<definition-ulid>` and must name an active employee-scoped non-secret definition when the setting is written. Each requirement is satisfied when at least one registered path in its array is populated. Renaming a custom definition does not affect its stable-id path, and deactivating a definition referenced by this setting returns `E_RECORD_IN_USE` until the requirement is changed. The default is `[["first_name"], ["last_name"], ["address.line1"], ["address.city"], ["address.state"], ["address.postal_code"], ["phone", "email"]]`. Missing output returns each unsatisfied array intact. Full tax identifiers, payroll withholding, earnings and deductions, paid time off, workers compensation, pay schedules, and direct-deposit data are not accepted in Row 5 and arrive with protected payroll storage.

Employee lookup and tables use:

| Concern | Fields |
|---|---|
| Query search | name, job title, address, phone/email, emergency contact, `notes`, searchable custom fields |
| Equality filters | `active`, released or unreleased, `employment_type`, `profile_complete`, `default_class_id` |
| Sort fields | `name`, `hire_date`, `release_date`, `profile_complete`, `updated_at` |
| Default columns | `name`, phone, email, `hire_date`, `release_date`, `profile_complete`, `active` |
| Additional selectable columns | job title, check name, employment type, address, emergency contact, default class, tax-id last four, every custom and common field |

Employees may be referenced by sales reps and later checks and time entries. Payroll and transaction panes remain unavailable until their rows.

### 11.6 Other names

Table `other_names` carries `name`, `company_name`, salutation, first, middle, and last name, job title, structured address, typed contact fields, `contact`, `account_number`, `default_class_id`, `notes`, and typed `custom_fields`. It holds owners, partners, and payees that are neither customers nor vendors. It may be referenced by later checks, card charges, and time entries, but never substitutes for a customer or vendor on type-specific forms.

`other-name convert <other-name> --to customer|vendor|employee` accepts source `expected_version` and idempotency. It maps name and company/person identity, address, contact points, account number, default class, notes, and custom values whose definitions apply to the target. Customer/vendor defaults remain null and employee hire/emergency fields remain null. Preview lists mapped and source-retained fields. The command creates a new target, deactivates the source, and records `converted_to_type` and `converted_to_id` on the source in one audited event. It never rewrites an existing transaction, audit snapshot, or other historical reference. Future pickers select the target. It never merges into an existing target and returns `E_NAME_TAKEN` if the target name already exists. An already converted row cannot convert again. Undo restores the source, deactivates the created target, and clears the conversion link when no later conflicting dependency exists. Because conversion creates a new record rather than retyping the existing one, history recorded against the source stays on the inactive source and does not appear on the target; the conversion link is how the two are read together. This is a deliberate project choice: the anchor converts the record in place and carries its history with it, and Bookflow copies and deactivates so every historical reference stays exact.

Other-name search covers all identity, contact, address, account-number, notes, and searchable custom fields. Filters are `active`, converted or unconverted, conversion target, and default class. Default columns are `name`, `company_name`, contact, phone, email, and `active`; additional columns are address, account number, conversion target, default class, updated time, custom fields, and common fields.

### 11.7 Items

Table `items` carries the shared hierarchy plus an independent nullable `category_id`. Categories do not replace item parent/subitem hierarchy. Common stored item fields are `name`, `type`, `parent_id`, `category_id`, sales `description`, `purchase_description`, `sales_enabled`, `purchase_enabled`, `price`, `cost`, `income_account_id`, `expense_account_id`, `cogs_account_id`, `asset_account_id`, `deposit_account_id`, `liability_account_id`, `default_class_id`, `sales_tax_code_id`, `manufacturer_part_number`, `barcode`, `unit_of_measure_set_id`, `reorder_point_min`, `reorder_point_max`, nullable stored and writable `preferred_vendor_id`, `notes`, and typed `custom_fields`. Setting `preferred_vendor_id` reconciles rank one in the active vendor profiles, and changing the rank-one profile updates the stored projection in the same write. Money uses exact home-currency values; quantities and percentages use exact decimal strings.

`item_members` are ordered stable child rows with `owner_item_id`, `component_item_id`, exact `quantity`, nullable unit id from the component item's unit set, array-derived position, and `active`. `item_vendor_profiles` use section 11.3. Parent and subitem types must match. Subtotal, group, payment, sales-tax-group, and fixed-asset items cannot have children. Group and assembly membership rejects self-reference and direct or indirect cycles.

Type-specific stored columns are nullable outside their permitted discriminated profile: exact `charge_percent`; `print_members`; exact `discount_amount` and `discount_percent`; `payment_method_id` and `use_undeposited_funds`; exact `tax_percent` and `tax_agency_vendor_id`; exact `assembly_build_point`; `asset_number`, `purchase_date`, exact `original_cost`, fixed-asset `vendor_id`, `location`, `serial_number`, `warranty_expiration`, `disposal_status`, `disposal_date`, exact `disposal_proceeds` and `disposal_costs`, `accumulated_depreciation_account_id`, `depreciation_expense_account_id`, `gain_loss_account_id`, `depreciation_method`, `useful_life_months`, and exact `book_basis` and `tax_basis`. Each money field is stored as minor-units/currency columns, each percentage as integer millionths, and each quantity as integer micro-units. The discriminated input model rejects every non-null field outside the selected type rather than ignoring it.

`type` is one of:

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
| inventory_assembly | stocked item defined by an ordered bill of materials | income, COGS, asset |
| fixed_asset | purchased long-lived asset profile | fixed asset; optional depreciation accounts |

The create and update models are discriminated by type and enforce these fields:

| Type | Required and allowed profile |
|---|---|
| `service` | At least one of sales or purchase is enabled. Sales requires description/rate/tax code/income account. Purchase requires purchase description/cost/expense account and permits vendors. |
| `non_inventory_part` | At least one of sales or purchase is enabled. Each enabled side requires its description, money value, and income or expense account. Resold parts may have both profiles. |
| `inventory_part` | Sales and purchase profiles, income, COGS, and inventory-asset accounts, optional unit set, manufacturer/barcode values, vendors, and reorder points. |
| `inventory_assembly` | The inventory-part profile plus at least one active bill-of-material member and a nullable nonnegative `assembly_build_point` independent of the reorder points. Components may be inventory, non-inventory, other-charge, service, or another assembly; nested assemblies remain acyclic. `bill_of_material_cost` is the exact derived sum of component costs. |
| `other_charge` | At least one of sales or purchase is enabled with the corresponding income or expense account. Nullable exact `charge_percent` from zero through 100 represents a percentage charge and is mutually exclusive with the corresponding fixed price or cost. |
| `subtotal` | Name and sales description only; no account, money, tax, vendor, unit, reorder, or member field. |
| `group` | At least one ordered member and `print_members`; no posting account or stored group price. A zero member quantity means a later form prompts for it. |
| `discount` | Exactly one of `discount_amount` or `discount_percent`, plus one income or expense account and a tax code. A positive value reduces the preceding line or subtotal; later form tax treatment follows the tax code. |
| `payment` | Description, nullable payment-method default, and exactly one deposit treatment: `deposit_account_id` or `use_undeposited_funds`. |
| `sales_tax_item` | Exact `percent`, active tax-agency vendor, and sales-tax-payable liability account. |
| `sales_tax_group` | At least one ordered member, every member an active sales-tax item; `combined_percent` is derived and liability remains attributable to each agency. |
| `fixed_asset` | Requires `asset_number`, description, `purchase_date`, original exact cost, a fixed-asset account, `disposal_status` (`in_service`, `sold`, `disposed`), and `depreciation_method` (`none`, `straight_line`, `declining_balance`, `sum_of_years_digits`, `units_of_production`, `other`). Nullable optional fields are `vendor_id`, `location`, `serial_number`, `warranty_expiration`, exact disposal cost, accumulated-depreciation, depreciation-expense, and gain/loss accounts, positive `useful_life_months`, and exact book-basis and tax-basis money. Disposal date is required outside `in_service`. Disposal proceeds are permitted and required only for `sold`; they are forbidden for `in_service` and `disposed`. Calculations and journal entries are deferred. |

Fields not allowed by the chosen type are rejected rather than ignored. Income references use income or other-income accounts; purchase/expense references use expense, other-expense, or cost-of-goods-sold accounts allowed by the item type; COGS references use cost-of-goods-sold; inventory assets use other-current-asset accounts bearing the inventory role; deposits use bank or undeposited-funds accounts; tax liability uses the sales-tax-payable role; and fixed assets use fixed-asset accounts. `reorder_point_min` and `reorder_point_max` are nonnegative and max is not below min. When unit mode is disabled an item unit set must be null; otherwise it may reference one active set. Item type updates are allowed only among service, non-inventory part, and other charge when the submitted target profile is complete. All changes into or out of inventory, assembly, aggregate, payment, tax, discount, and fixed-asset types return `E_TYPE_CHANGE`; later explicit conversion commands own their accounting effects.

`quantity_on_hand`, `quantity_available`, `quantity_committed`, `quantity_on_order`, `quantity_pending_build`, `average_cost`, and `inventory_value` are derived outputs. Row 5 returns zero with `inventory_values_available: false`; none is writable. Inventory opening quantities and values are later dated transactions. Assembly definitions and bills of material exist in Row 5; assembly build transactions, cost layers, and available-to-promise are deferred.

Item lookup and tables use:

| Concern | Fields |
|---|---|
| Query search | `name`, `full_name`, sales/purchase descriptions, manufacturer part number, barcode, category, vendor item identifiers, `notes`, searchable custom fields |
| Equality filters | `active`, `type`, `parent_id`, `category_id`, sales/purchase enabled, tax code, preferred vendor, unit set, default class |
| Sort fields | `type`, `full_name`, `price`, `cost`, quantity on hand, preferred vendor, category, `updated_at` |
| Default columns | `type`, `full_name`, `price`, `cost`, `quantity_on_hand`, preferred vendor, `active` |
| Additional selectable columns | category, tax code, all account references, reorder points, unit set, manufacturer number, barcode, default class, average cost, inventory value/availability, every custom and common field |

The default sort is type then full name. Inactive items remain visible on historical forms and disappear from new-form pickers. Account, tax, class, unit, category, and vendor changes affect future transactions only. Historical transaction snapshots never change through an item update. Inventory sites and bins, serial and lot instances, landed cost, selectable costing methods, stock transfers, and item images are deferred to inventory and attachment passes.

### 11.8 Item categories and classes

Tables `item_categories` and `classes` carry `name`, `parent_id`, materialized `full_name`, and the shared five-level hierarchy. Five levels is the shared project limit and exceeds the anchor's four-level categories; it is a project choice, not parity, and existing data is never reduced to the anchor limit. Categories classify items independently of their subitem structure. Classes classify later transaction headers and lines and do not replace account, item, customer, vendor, or job types.

Company settings `use_classes` and `prompt_for_class` control later form visibility and warnings without removing class values. Enabling `use_classes` does not turn `prompt_for_class` on by itself; the anchor couples the two. Default linkage is staged with the transaction class-prompting pass; today `prompt_for_class` merely requires `use_classes`. Accounts, customers/jobs, vendors, employees, other names, and items may carry an overridable default class. Both lists search name/full name, filter by active state and parent, sort by full name or updated time, and default to full name, parent, and active columns. Usage count is a derived selectable column that is zero and unavailable before referencing transactions exist.

### 11.9 Terms

Table `terms` carries `name`, `kind` (`standard` or `date_driven`), and active. Standard terms require `due_days` from 0 through 365 and may carry `discount_days` from 0 through 365 plus an exact `discount_percent` from 0 through 100. Date-driven terms require `due_day_of_month` from 1 through 31 and `due_next_month_if_within_days` from 0 through 31, and may carry `discount_day_of_month` from 1 through 31 plus the exact percentage. Fields from the other kind are forbidden; discount day and percentage are supplied together. A day beyond the target calendar month's length resolves to that month's last day.

The term model computes a due date and optional early-discount deadline from any transaction date without writing. `term show` accepts optional `transaction_date`; when present its output includes both calculated dates, and it always includes deterministic due-rule and discount-rule summaries. Later customer/vendor forms use the selected term as an overridable default. Search covers name; filters are active state and kind; sorts are name, kind, and updated time. Default columns are name, kind, the rule summaries, and active. Seeded terms are `Due on receipt`, `Net 15`, `Net 30`, `Net 60`, `1% 10 Net 30`, and `2% 10 Net 30`.

### 11.10 Payment methods

Table `payment_methods` carries `name`, `kind` (`cash`, `check`, `credit_card`, `debit_card`, `gift_card`, `e_check`, `ach`, `other`), and active. It supplies customer and payment-item defaults and later classifies receipts and deposits; it stores no credential. Search covers name; filters are active state and kind; sorts are name, kind, and updated time; default columns are name, kind, and active. Seeded methods are `Cash`, `Check`, `Visa`, `MasterCard`, `American Express`, `Discover`, `Debit Card`, `Gift Card`, `Electronic Check`, `Bank Transfer`, and `Other`.

### 11.11 Sales tax codes

Table `sales_tax_codes` carries `code`, the list's required display key of one through three characters, `description`, `taxable`, and active. The normalized code is unique. A nontaxable customer code makes later sales lines nontaxable; a taxable customer code delegates to each item's code. Additional nontaxable codes may classify exemption reasons. Sales-tax items and groups are item types in section 11.7.

Company tax profile fields introduced with this list are `sales_tax_enabled`, nullable active `default_sales_tax_item_id`, `sales_tax_liability_basis` (`invoice_date` or `payment_receipt`), and `sales_tax_remittance_frequency` (`monthly`, `quarterly`, `annually`). Defaults are false, null, `invoice_date`, and `quarterly`. Enabling tax never silently changes existing customers or items. A later batch operation may preview and mark selected records in one reversible event.

Search covers code and description; filters are active state and taxable; sorts are code, taxable, and updated time; default columns are code, description, taxable, and active. Seeded codes are `Tax` and `Non`.

### 11.12 Supporting profile lists

Each table carries the common fields, `name`, normalized name key, and active, plus the fields and behavior below.

| Table | Extra fields and validation | Use, search, filters, and default columns |
|---|---|---|
| `customer_types` | `parent_id`; shared five-level hierarchy | customer segmentation; search name/full name; filter active/parent; columns full name, parent, active |
| `vendor_types` | `parent_id`; shared five-level hierarchy | vendor segmentation; search name/full name; filter active/parent; columns full name, parent, active |
| `job_types` | `parent_id`; shared five-level hierarchy | job segmentation; search name/full name; filter active/parent; columns full name, parent, active |
| `sales_reps` | `initials`, unique after case folding and at most five characters; `name_type` and `name_id` restricted to an active employee, vendor, or other name | customer/job defaults and later sales forms/reports; search name, initials, source name; filter active/source type; columns initials, source name/type, active |
| `ship_methods` | `display_order`, a nonnegative integer | company/customer defaults and later sales forms; search name; filter active; columns display order, name, active |
| `customer_messages` | `text`, required and at most 101 characters; `display_order` | reusable later sales-form text; search name/text; filter active; columns display order, name, text, active |

The source record behind a sales rep remains a stable id; source renaming changes only the derived display name. A ship method or customer message sends nothing by itself. Seeded ship methods are `Delivery`, `Federal Express`, `UPS`, `USPS`, and `Other`. Seeded customer messages are `Thank you for your business.`, `We appreciate your prompt payment.`, and `Please remit payment at your earliest convenience.` They are ordinary editable rows whose immutable seed keys prevent duplication without overwriting user edits. These seed sets are project choices, not the anchor's shipped sets: the anchor ships a different carrier set (including one carrier omitted here and a differently named postal service) and these customer-message seeds are project examples rather than a verified source seed inventory. The customer-message `name` is a Bookflow addition, since the anchor identifies a message by its text alone. Sales-rep initials are entered, not derived from the source person as the anchor does; person-derived initials and in-place correction of a misspelled source are staged with the list-center pass.

Memorized transaction templates and to-dos are not Row 5 list nouns. Their complete lifecycle remains with the forms and scheduler/activity passes in sections 13 and 14.

### 11.13 Price levels

Table `price_levels` carries `name`, `kind` (`fixed_percent` or `per_item`), nullable ISO currency code, rounding mode (`nearest`, `up`, `down`), positive exact rounding increment, exact signed rounding offset, and active. Company setting `enable_price_levels`, default false, controls form and picker visibility without deleting definitions or customer assignments.

A fixed-percent level requires exactly one signed exact `percent`: a negative value decreases the standard price and a positive value increases it. The value may not be below -100. A per-item level has stable ordered `price_level_items` child rows, unique by item, each with exactly one of an exact fixed `price` or signed exact `percent`, plus `adjustment_basis` (`standard_price`, `cost`, `current_custom_price`). A fixed price uses the level currency or company home currency; the referenced item is active at creation. Rounding applies after the percentage adjustment. Defaults are `nearest`, the smallest home-currency unit as increment, and zero offset. These defaults are project choices: the anchor defaults to rounding up and ships named increment presets (cent, nickel, dime, quarter, half unit, whole unit) with variants that end just below a whole unit. Named presets and the bulk calculator that fills per-item prices from a percentage above or below standard price, cost, or current custom price are staged with the sales-form pricing pass. Per-item percentages are applied live against their basis whenever a price is resolved; the anchor computes and stores prices once. Live application is a project extension.

Customer assignment supplies the default level on later sales forms and each transaction line may override it. No form creates a price level implicitly. Conditional rules over dates, quantities, locations, groups, custom fields, stacking, margins, and guardrails are deferred to the sales-form pricing pass.

Price-level search covers name and per-item item names. Filters are active state, kind, and currency. Sorts are name, kind, percent, item count, currency, and updated time. Default columns are name, kind, fixed percent or item count, currency, rounding summary, and active; every stored field and per-item summary is selectable.

### 11.14 Units of measure

Table `units_of_measure` carries `name`, nullable default purchase, sales, and shipping unit ids, and active. Stable ordered `unit_conversions` child rows carry every unit's stable id, name, abbreviation, `is_base`, and a positive exact decimal `base_factor`, defined as base units per one unit. Exactly one active child is the base and has factor exactly 1. Names and abbreviations are each unique among active children after normalization; every default references an active child in the same set. Defaults belong to the set; the anchor keeps default purchase, sales, and shipping units on each item, so one item bought, stocked, and sold in three units needs no set per combination. Item-level default units are staged with the transaction unit-selection pass; the set-level defaults remain the current contract and are not reduced.

Company mode `units_of_measure_mode` is `disabled`, `single_unit_per_item`, or `multiple_related_units`, default `disabled`. Items reference a set, never free text. Later forms start with the context default and may choose another unit from the same set. Conversion uses exact decimal arithmetic: `target_quantity = source_quantity * source_base_factor / target_base_factor`; no float enters storage or output. A factor referenced by a posted transaction becomes immutable, while new units may be added and unused units deactivated.

Search covers set and unit names/abbreviations. Filters are active state and base unit. Sorts are name, base unit, each default unit, related-unit count, and updated time. Default columns are name, base, purchase, sales, shipping, related-unit count, and active; detail includes every conversion.

### 11.15 Custom fields

Table `custom_field_defs` carries `name`, `kind` (`text`, `number`, `date`, `bool`, `choice`), ordered choices, nonnegative `position`, `required`, typed nullable default, and active. Stable ordered `custom_field_scopes` child rows carry record type plus synchronized definition name/active projections; their partial unique index enforces one active normalized definition name per record type. Record types accepting values in Row 5 are customer, vendor, employee, other name, and item. Definitions may additionally register the exact transaction types in section 10.3 before those forms exist, but no transaction value is accepted until its form arrives. There is no application limit lower than 45 active definitions for one record type. That floor is a project capacity stated per record type; the anchor's published figure is a product-wide total, so the project guarantee is a deliberate superset and existing capacity is never reduced. Typed kinds (`number`, `date`, `bool`, `choice`) with validated values are likewise a project extension over the anchor's text-only fields.

Table `custom_field_values` carries stable id, `def_id`, `record_type`, `record_id`, active state, and canonical text storage. The unique key is definition plus record type plus record id. Clearing deactivates the value row without erasing its stored history; setting it again reuses the row. Inputs and outputs use typed values: a canonical decimal string with at most nine fractional digits and signed-64-bit nano-unit coefficient for number, ISO date, JSON boolean, or string/choice. A value must match an active applicable definition and, for choice fields, one of its declared choices. Every supported list create/update input accepts `custom_fields` keyed only by stable definition id. Names may select a custom-field definition record for `custom-field show/update`, but never serve as mapping keys; an unknown mapping id returns `E_RECORD_NOT_FOUND`, and an inactive id on a new or changed value returns `E_INACTIVE_REFERENCE`. The mapping is a patch: absent id preserves, explicit null clears. Create applies a non-null default, rejects a missing required value without a default, and otherwise omits it. Adding a required definition is allowed for existing records; unrelated updates do not retroactively fail, but a required populated value cannot be cleared. `show` returns active values and preserved values whose definition later became inactive with definition id, current name, kind, and typed value; explicitly cleared values remain in audit snapshots but are absent from the current value projection.

Definition target types and kind become immutable after the first value. Renaming preserves values by id. Removing a choice in use returns `E_RECORD_IN_USE`. Deactivating a definition hides new-entry controls and rejects new or changed values while preserving existing values in show, history, search, filters, and selectable columns. Each definition has its own expected version; owner value edits use the owner's version and one logical path per definition id. Text and choice values participate in general query search; every kind has a typed definition-id filter and selectable column. Custom fields are not a credential, full tax-identifier, health, or payroll-secret store, regardless of their label; the generated guide states that limitation.

Registered command schemas expose `custom_fields` as a typed static mapping keyed by definition id for programmatic clients. The workbench also loads the current active definitions through a runtime field provider and names controls by stable definition id, never by mutable label. The provider submits those ids through the same static command mapping; adapters contain no custom-field business logic.

Custom-field search covers definition name, target type, and choice labels. Filters are active state, target type, kind, and required. Sorts are position, name, kind, target type, and updated time. Default columns are position, name, target types, kind, required, and active. Active searchable list values participate in their owner's query; active definitions are available as typed owner-list filters and selectable columns.

#### Journal header custom-field controls

Journal post/update and register entry expose journal_entry custom fields in
stable definition order using text, exact decimal, date, boolean and choice
controls. Required/default state, explicit clear, empty text, false, zero and
untouched values remain distinct. Current active definitions supply available
controls; selected revision snapshots supply editing originals and historical
labels. More than 45 applicable fields remain reachable. Unrelated updates do
not populate new defaults; inactive captured values remain readable. Details and
writer behavior are specified in [8-custom-fields.md](specs/8-custom-fields.md).

### 11.16 Undo

`undo <event_id>` applies only to a company audit event whose entries are all Row 5 list records. Update, activate, deactivate, cascade, link, unlink, conversion, reparent, and nested-child events receive one atomic compensating write. Undoing a create deactivates the created record and never deletes it. For an update, every field changed by the original event is restored to its before-value when its current value still equals the original after-value; fields changed later but disjoint from the original diff are preserved. A later overlapping field change returns `E_UNDO_CONFLICT` with record, field, current version, and conflicting event details. Current dependency, hierarchy, reference, type, and system-record rules are rechecked and may also return a conflict.

Ledger, migration, rollout, chart/profile-application, undo, and non-list events return `E_NOT_UNDOABLE`. Undo itself is one audited event preserving the caller's ordinary reason or directive and carrying unique `undo_of_event_id`; its summary names the original event. Replaying the same idempotency key and input returns the stored undo result; any new request for the compensated event returns `E_ALREADY_UNDONE`. A field changed away from and back to the original after-value is eligible because the current value governs overlap. Row 5 requires admin and at least the highest current role threshold represented by the original event; Row 7 may additionally enforce the recorded capability conjunction.

### 11.17 Explicit staging

Row 5 stores master-data profiles, list operations, generated CLI/HTTP/workbench surfaces, and derived placeholders. It does not accept an unsupported future field and does not represent deferred data as complete.

- Opening balances are later transactions, never mutable account, customer, vendor, or item balances. Posted/current/open/overdue values, transaction panes, registers, and usage reports become functional with the ledger and forms.
- Assembly definitions and bills of material are current. Build transactions, quantity/cost calculation, inventory sites and bins, serials and lots, landed cost, costing layers/method choices, transfers, and available-to-promise belong to the inventory transaction passes.
- Spreadsheet batch editing, CSV import/export, saved mappings, and merge operations that reassign references belong to section 14.1. Row 5 list tables still declare the fields and columns those operations consume.
- Advanced conditional price rules and actual price application belong to the sales-form pricing pass. Row 5 includes fixed-percent and per-item price levels and customer assignments.
- Notes, files, images, and activity tabs integrate in Row 6. Related transaction creation, delivery, class prompting, tax calculation, and term application arrive with their transaction and browser-workflow rows.
- Full employee tax identifiers, payment-card or bank credentials, provider tokens, role-gated secret access, payroll, withholding, paid time off, and workers compensation require their security/provider/payroll passes. Row 5 stores only opaque protected references and safe display suffixes.
- Memorized transactions and to-dos remain with forms, scheduling, and activity. Purpose-built list-center workspaces are deferred; the generated workbench exposes every current command and field.

### 11.18 Completeness rule

Before a list, form, or report is built, its blueprint section is completed to name every field, option, and behavior the anchor offers on the equivalent screen or report, including defaults, validations, filters, columns, and what each field is used by. The section is the inventory; the row is built against it; the artifact critic checks the built thing against the section. Reports are designed as one set with shared parameters, filters, column conventions, and drill-down, never one at a time. Improvements beyond the anchor, such as commission rates on sales reps tied to employees, are added as separate fields marked as such in the section, after the anchor's set is complete.

## 12. Notes, attachments, and activity

### 12.1 Notes

Table `notes` carries the common fields plus `record_type`, `record_id`, `body` (text, Markdown allowed), `author_id`, `interface`, `at`, `edited_at`, `kind` (`comment` or `system`). Body is nonblank, preserved as entered, and limited to 65,536 UTF-8 bytes. Public creation accepts only comments; system notes are reserved for internal producers. Body text never grants authority or executes markup.

The company-local annotation target registry covers the twenty list types, stable-id owned children, `company_info`, `directive`, `principal`, `audit_event`, `audit_entry`, `customer_vendor_link`, and `note`. Future persistent record types register explicitly. Transient presence, retry bookkeeping, sequence counters, migration metadata and private continuation authentication keys are not annotation targets. Inputs use canonical record type and stable id, never SQL table names. Company identity is the selected company's id. Inactive records retain readable notes; rename, deactivation, owned-child retirement and undo-create preserve target identity and remain permitted. Physical target deletion cannot strand a note.

Commands: `note add <record_type> <record_id> --body`, `note show <note>`, `note edit <note> --body --expected-version`, `note list <record_type> <record_id>`. Reads require membership; add/edit require standard role and the ordinary agent reason gate. Add uses existing request idempotency. Edit requires a positive expected version; a conflicting body edit is rejected, a current-version no-op writes nothing. Original author/interface/time remain unchanged; the common updated fields identify the editor. Editing keeps prior bodies in immutable audit snapshots. Notes are never deleted and do not change their target's business version.

`note list` returns current note versions, newest id first, in pages of 50 by default and at most 200, with at most 262,144 UTF-8 body bytes per page. The byte bound may shorten a page; continuation resumes at the next unreturned note. Output has items, page count, has_more and next_cursor; cursors bind company, target and current permissions. This live list is not a historical report: edits are current and inserts above the cursor appear on first-page refresh. Chronological immutable history belongs to activity. The stored target/id index bounds the page query; principal labels are fetched as one set, not one query per note.

### 12.2 Attachments

Table `attachments`: `sha256`, `size_bytes`, `media_type`, `original_filename`, `uploaded_by`, `uploaded_at`. The file body lives at `attachments/<sha256[:2]>/<sha256>`. Uploading the same bytes twice creates one body and one row.

Table `attachment_links`: `attachment_id`, `record_type`, `record_id`, `linked_by`, `linked_at`, `caption`. One attachment may link to many records.

Commands: `attachment add <record_type> <record_id> <path>`, `attachment link`, `attachment unlink`, `attachment list`, `attachment get <id> --out <path>`. Maximum size per file is a company setting, default 25 MB. Unlinking the last link does not delete the body; `company compact` removes unlinked bodies and is audited.

#### Transfer contract

The registry declares binary input or output separately from the JSON model. One
invocation transfers one body. Add's JSON input contains the target tuple,
basename-only original filename, media type and caption; paths and stream objects
are never JSON fields. Actual SHA-256 and size join validated metadata in the retry
hash. Python supplies a binary stream or sink; CLI input/output paths are opened
only on the calling machine. Content size is at most the lesser of the company
limit and 100,000,000 bytes; the default is 25,000,000 bytes. Counts use received
bytes, including when Content-Length is absent or false. Dry-run input is hashed
without persistent staging.

The shared preparation boundary authenticates the actor, resolves company/target,
validates command input, role, reason/directive and size setting before reading
body bytes. It reserves a filesystem lease while the short authorized snapshot is
still held, then closes database snapshots before I/O. Final execution repeats
authorization, target and limit validation in the ordinary command pipeline.
Downloads verify digest and size before a successful response and use short
authorization rechecks before each output chunk, without retaining database
snapshots across network writes. Row7 additionally binds and rechecks authority
epochs; previously emitted bytes cannot be recalled.

HTTP binary routes use `POST /companies/<id>/transfers/<noun.verb>` for both
directions. `X-Bookflow-Input` contains unpadded base64url-encoded UTF-8 JSON, at
most 8,192 encoded bytes and 6,144 decoded bytes, with an object at its root. Usual
context/authentication/CSRF headers apply. Upload body is
`application/octet-stream`; downloads carry an empty request body. No framework
multipart parser consumes unauthenticated uploads. The browser sends the selected
File directly and encodes form metadata in the header. Output bytes use
`Content-Disposition: attachment` with a sanitized ASCII fallback and RFC5987
UTF-8 filename, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and
verified Content-Length. `X-Bookflow-Output` carries the typed command metadata
using the same bounded base64url JSON encoding as the input header.
Failures before response headers use normal JSON errors;
failures during output truncate the response and never claim a complete download.

Local forwarding retains the existing JSON envelope for ordinary commands. A
binary envelope is at most 8,192 bytes and declares `transfer: {version: 1,
direction: "input" | "output"}`. The server validates kernel peer identity and
metadata before sending a framed JSON ready response. Input ready contains the
effective byte limit. Then each body chunk has an unsigned four-byte big-endian
length of 1–65,536 followed by exactly that many bytes; a zero-length frame ends
the body. The final framed JSON result/error is at most 65,536 bytes. Download
ready includes the verified digest, size and presentation metadata, followed by
body frames and a final JSON completion/error. A client accepts a download only
after matching digest/size and reading the successful final response. Partial
headers/chunks, missing terminal/final frames and timeouts are interrupted I/O.
A forwarded write never falls back to standalone execution after its envelope
has been sent; an interrupted result may have committed and is retried with the
same idempotency key and bytes.

#### Resource lifetime

Each host admits at most eight transfers and at most two per effective principal,
across upload/download and all companies. Admission is immediate: exhaustion or
a filesystem gate returns `E_DB_BUSY`, with no waiting in a database transaction.
Each transfer has a 300-second absolute lifetime and a 30-second transport
inactivity bound measured with a monotonic clock. Input/output chunks are at most
65,536 bytes. A bound violation or disconnected transport reports `E_IO`; excess
body bytes report `E_VALUE_RANGE`, and malformed metadata/framing reports
`E_VALIDATION`. Arbitrary Python streams must cooperate with bounded reads; a
blocked caller-owned read cannot be forcibly interrupted. Such a resource keeps
its lease and the host lock until its actual owner finishes.

A lease holds no database handle. Ordinary writes may run while transfers wait
for I/O. Folder moves, detach/reset and compact take the existing root-wide
filesystem gate, reject new leases, and wait at most five seconds for existing
readers and transfers. A busy transfer fails that filesystem operation rather
than moving its store. A transfer never invokes an exclusive folder operation
while holding its own lease.

Resource state is caller-owned, writer-owned, or closed. A caller owns its private
stage and lease until handing both to a queued writer job. Submission transfers
ownership even if admission subsequently fails: rejection closes it immediately;
accepted work closes it only after job execution and database cleanup. Caller
context exit, timeout or cancellation cannot close a writer-owned stage. A queued
job checks cancellation/deadline before starting; a started commit finishes even
if its response is abandoned. Cleanup runs exactly once, closes temporary files
before releasing capacity/lease, and never deletes a published digest. A cleanup
failure keeps the lease held for retry; cleanup is not reported as success.

Shutdown closes admission and cancels caller-owned transfers so their transport
loops unwind. Accepted writer jobs retain their resources and drain through the
same completion cleanup. Shutdown retains the root lock if readers, transfers or
writer cleanup have not finished; retry shutdown after the owner releases them.
No timeout authorizes deleting another invocation's stage.

#### Collection and recovery

Compact is owner/admin-only and takes filesystem exclusion without an I/O lease.
It processes at most 200 bodies per invocation, reports continuation, and retains
all attachment/link/audit metadata. Linked bodies are never candidates. Under the
writer transaction it records a bounded durable collection intent containing an
operation ID, candidate digests/sizes and the caller's audit/idempotency context.
Initial selection records actual file presence and logical byte size: bodies already
missing contribute zero reclaimed files/bytes, while their metadata is still marked
collected. Those committed totals remain stable if bodies disappear during recovery.
The intent commits before removal. New attachment writes cannot relink candidates
until pending collection converges. Recovery validates that no selected body has
an active link, removes only exact regular digest paths, synchronizes affected
directories, then marks the candidates collected and writes one completion audit
event/idempotency result in a single transaction with intent clearance. Missing
candidate bodies on retry count only once against their committed intent. An
unexpected active link or unsafe filesystem entry halts recovery with `E_IO`.

Unreferenced published bodies and abandoned stage files may be discovered only
under the same exclusion, with no active invocation and a bounded scan cursor.
Discovery never follows symlinks or deletes directories, database files, or
unrecognized names. A collected digest requires fresh verified bytes before
relinking. Read-only verification reports missing/corrupt linked bodies and does
not repair them. Exact metadata migration and collection commands are implemented
together after the transfer ownership boundary is verified.

### 12.3 Activity feed

`activity <record_type> <record_id>` returns, in time order, every audit entry for the record, every note, and every attachment link, each tagged with its kind and actor. `--since`, `--until`, and `--kinds` filter it. This is the chronological job history the GUI shows on any record.

## 13. Work orders and scheduler

Built after release 1. Designed here so release 1 leaves room.

### 13.1 Work orders

The contractor work-order form and explicit estimate acceptance/history are approved
Bookflow extensions; the available reference documents estimates and sales orders,
without a comparable contractor work-order form. The non-posting owning plan is
[Customer work documents](specs/16-customer-work.md): shared stable work-document
identities, immutable revisions and line identities, exact quote prices/costs/taxes,
proposal/SOW → alternative estimate → accepted estimate → work order. Operational
status, completed quantities, billable eligibility, billing and payment are distinct.
Completion creates no financial effect. Notes/files remain inspectable through source
links, with internal content separated from customer-facing scope.

Permanent conversion links support multiple destinations across the broader chain;
financial conversion adds per-source-line allocation records, never a single invoice
pointer. The next owning plan implements full/partial/progress invoice or genuinely
paid sales-receipt conversion while preserving common billing roots and all sources.
Paying an issued invoice uses customer payment, without creating a second sale.
[Customer work and billing](customer-work-and-billing.md) owns the complete workflow.

Stock sales orders are a separate planned document under inventory/fulfillment:
ordered versus shipped/backordered quantities, selected billing, purchase-order
sourcing, pick/pack/ship and partial-shipment policy. Contractor work-order completion
cannot stand in for those inventory effects. Job-master dates remain authoritative
for job-level reports; each work order has its own scheduled and actual dates.

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

Inbound email is not read by Bookflow. An agent harness reads mail and calls commands, such as attaching a receipt or posting a bill. Company settings hold the configured channel per purpose (`invoices`, `statements`, `reports`, `reminders`) and the sender address.

Provider credentials are durable connections, scoped by organization or company, provider, account, and purpose. The hub stores an encrypted credential envelope plus non-secret connection metadata; credentials, authorization codes, access tokens, refresh tokens, API keys, app passwords, and decrypted values never enter a company folder, a backup, an audit snapshot, an error, or a log. The envelope's master key is never stored in the hub. A local interactive installation obtains it from the operating system keyring. A headless installation obtains it through the same key-source interface from an operator-managed secret, environment-mounted secret, or owner-only key file outside the data root.

OAuth providers use authorization code with PKCE, persist refresh tokens across restarts, replace rotated refresh tokens atomically, and refresh access tokens automatically before expiry. SMTP and API-key credentials use the same vault. A connection asks for authorization again only after explicit revocation, an invalidated or expired refresh grant, a scope change, or a provider-required challenge; ordinary restarts and access-token expiry never require the user to sign in again.

Dependency updates for the whole project, including the optional email package, run through the repository's automated dependency update service; an update that passes the test suite merges.

## 14. Reports

Standard financial statement controls and their explicit staged coverage are
listed in [the statement inventory](inventories/financial-statements.md). The
first bounded P&L/balance-sheet implementation supplies standard accrual account
amounts and statement totals; it does not claim full equivalent-screen parity for
the customization and variants listed there.

This is the future reporting contract. Every report declares its source records, grouping keys, date semantics, supported bases, and exact reconciliation checks before implementation. `--json` and `--csv` render the same structured result. Flow reports use inclusive `--from`/`--to` dates; balance and aging reports use `--to` as the as-of date and declare any comparative-period input separately. `--basis accrual|cash` is available only where defined; an unsupported basis returns `E_VALIDATION`, never an unlabeled approximation. The company report basis supplies the default only for a report supporting it. Trial balance and general ledger initially support accrual only.

Financial ledger reports sum immutable posting lines whose batches fall in the requested accounting date range. They include **original, reversal, and replacement batches**; they never filter those effects by a document's current `posted`/`voided` status, by a current-revision pointer, or by the existence of a reversing link. Each exact reversal offsets its target once. General-ledger detail exposes the transaction number/id, document revision, batch kind, reversed/replaced links, effective date, and recorded time. Trial balance account totals reconcile to that detail. Opening balances plus dated movements equal closing balances.

AR/AP and cash-recognition reports join posting effects to document revisions and dated application/allocation history from 10.4. As-of open balances reconcile to the AR/AP control accounts, including unapplied payments/credits separately; they cannot use only today's outstanding-item list. Cash-basis transformations are a distinct reporting calculation over immutable accrual books, not edits to those books. Each supported document type declares how settlement allocations recognize income/expense and balance-sheet components. Direct cash sales/expenses count once at their posting date; applying an already counted receipt, moving Undeposited Funds to bank, and bank transfers cannot count a second receipt or expense. Tax allocations remain attributable to their tax liabilities and agencies. Non-cash credits, inventory/COGS timing, prepayments, loan principal, write-offs, and foreign-exchange components each require explicit basis rules and fixtures in their owning report/form plan; absence of a rule prevents that basis from shipping for affected data. No generic prorating algorithm is a substitute for those rules, and a cash-basis management report is not a claim of tax-return compliance.

Operational reports may read the relevant source documents and subledgers: item sales use commercial lines and their posted/returned quantities; inventory valuation uses movements/cost layers; estimates versus actuals use source links; job profitability joins supported revenue/cost/time attribution; time reports use time entries; reconciliation reports use reconciliation records; budget comparisons use budgets; audit reports use audit records. They do not reconstruct quantities by adding posting legs or restrict themselves to ledger tables. Each operational report reconciles its financial columns to the applicable posting controls while retaining non-posting measures such as estimated quantity. An internal edit reversal is not another customer return or operational sale.

Every report runs against a consistent bounded read snapshot and records company, period, basis, report/schema version, generation time, and the company's audit high-water mark. The accounting date determines the period; recorded time and the watermark identify the information used. An open-period backdated correction can change a later rerun for that period; preserving the original rendered report and its metadata preserves what was previously issued. A future as-recorded cutoff must be implemented across revisions, postings, and applications together, not simulated by filtering current headers. Snapshot labels come from the relevant document revision; optional current master labels are explicitly marked and cannot alter amounts or identities.

Release 2 reports: trial balance, general ledger, profit and loss (standard, detail, by class, year to date comparison), balance sheet (standard, detail), statement of cash flows, AR aging summary and detail, AP aging summary and detail, customer balance summary and detail, vendor balance summary and detail, open invoices, unpaid bills, collections, sales by customer and by item summary and detail, purchases by vendor and by item, inventory valuation summary and detail, inventory stock status, job profitability summary and detail, estimates versus actuals, unbilled costs by job, time by job, sales tax liability, transaction list by date, transaction detail by account, check detail, deposit detail, missing checks, reconciliation summary and detail, budget versus actual, 1099 summary and detail, audit trail. The full set is inventoried from the anchor's report guides before the release is planned (11.18).

Trial balance and general ledger ship with the ledger in release 1.

`report profit-and-loss` accepts inclusive `date_from`/`date_to` and returns
income, cost of goods sold, expenses, other income/expenses, gross profit, net
operating income and net income. `report balance-sheet` accepts as-of `date_to`
and returns assets, liabilities, posted equity, derived prior earnings/current
fiscal-year income, total equity, liabilities plus equity and their difference
from assets. Both currently accept only `basis=accrual`, `include_zero=false`,
`limit=1..200` and an optional authenticated continuation. Account detail is paged;
totals cover the whole statement. Each account row contains its own normal-side
net, not descendants, with current labels and presentation preferences. Nonzero
inactive accounts remain. Non-posting accounts never appear.

Derived earnings negate raw debit-minus-credit income/expense effects, split at
the company's fiscal-year start. They are separate from posted equity. Every
ordinary journal effect is included exactly as entered, including manual transfers
out of income/expense; such transfers change reported profit. There is no inferred
closing operation or automatic closing posting. Any audited company write stales
these two statements' continuations. Browser forms retain filters and show readable
totals/detail plus GL links with the original accounting dates; drill-down opens
current books and flags changed audit watermarks. Current implementation and
shared report mechanics are described in design/architecture.md.

### 14.1 Import and export

`import <noun> <file.csv>` reads one row per record, maps columns to the noun's `create` input model by header name, and runs one `create` command per row through the registry, so every row is validated, audited, and idempotent (the idempotency key is the file hash plus row number). The output reports created, replayed, and rejected rows with their errors. `--dry-run` validates every row and writes nothing. Import of transactions uses the same mechanism with one file per transaction type. Export is `list --csv` on any noun and `report <name> --csv`. IIF import is a later addition that maps IIF sections onto the same imports. Import and export are release 2.

## 15. Adapters

### 15.1 CLI

`bookflow <noun> <verb> [args] [flags]`. Global flags: `--company`, `--json`, `--dry-run`, `--reason`, `--source-ref`, `--idempotency-key`, `--as-token`, `--data-root`. `bookflow --help` and `bookflow <noun> --help` are generated from command definitions and include every flag, every output field, and every error code.

### 15.2 HTTP host

`bookflow serve --bind 127.0.0.1:8123`. Routes are `POST /companies/{company_id}/commands/{name}` with the input model as the JSON body and the output model as the response, and `POST /commands/{name}` for hub commands, where `{name}` is the command name with spaces as dots (`company.update`), which round-trips because names never contain dots. The company-path ULID is authoritative. If `X-Bookflow-Company` accompanies it, the value must be the same ULID after case normalization; a mismatch is `E_VALIDATION` before a company lookup. A company-scoped command called through `/commands/{name}` may use the header with the same selector rules as `--company`. Errors carry the same `{code, message, details}` document as the CLI: 401 unauthenticated; 403 `E_PERMISSION`; 404 the not-found codes; 409 state conflicts including version/idempotency/name/dependency/hierarchy/type/system/chart/undo conflicts and `E_DB_BUSY`; 422 `E_VALIDATION`, `E_AMOUNT_PRECISION`, `E_VALUE_RANGE`, `E_LIST_FILTER`, and `E_INACTIVE_REFERENCE`; 400 every other named error; 500 `E_INTERNAL`. The body is what matters; the status lets a client that never reads it tell not-found, conflict, and invalid input apart. `GET /openapi.json` is generated from every routed command with its schemas, parameters, authentication, and error metadata. Binding to a non-loopback address requires `--allow-network`, and the docs state that TLS termination is the deployer's job. `--secure-cookies` is tri-state: when omitted it is on for non-loopback and off for loopback, and either explicit setting overrides the default. The host also serves the workbench at `/` and static assets under `/static/`.

While the host runs it holds the data root's lock (3.2). On POSIX, a CLI or library call on the same machine reads `<data_root>/host.json` and sends the call, with its own context, over the host's Unix domain socket at `$XDG_RUNTIME_DIR/bookflow/<root-hash>.sock`, falling back to an owner-private directory under the system temporary directory. The runtime directory must be owned by the current uid and mode `0700`; the socket is `0600`; peer identity comes from the kernel. Exact-length framing accepts fragmented socket reads, and the host overwrites the forwarded interface as `cli`. The forwarded JSON envelope and host command handler are independent of the socket pathname and peer-credential mechanism. A future Windows transport uses an owner-restricted named pipe, derives identity from the authenticated pipe client, and passes the same envelope to the same handler. Until that adapter exists, the browser and bearer-authenticated HTTP API are the supported Windows access paths; Windows compatibility does not block the browser product.

Inside the host, reads run concurrently on their own read-only connections and all writes serialize on one writer thread. Each read command holds one explicit SQLite read transaction per opened database, including its revision check and all dependent reads, and releases it on success, error, or cancellation. Hub and company snapshots are individually consistent; they are not a cross-database atomic snapshot. The server-sent event feed drains at most 100 events per short reader session, releases that snapshot before yielding, and reauthenticates between batches. An idle feed holds no reader and waits on event-loop notifications under the canonical database id. SIGINT and SIGTERM mark the host as stopping and wake every idle stream before server cleanup. Session-token liveness fields are ephemeral like presence: unversioned, unaudited, five-minute-throttled, and queued without blocking readers.

Browser sessions: `POST /login` with username and password sets an HTTP-only, `SameSite=Lax` session cookie that maps to a session token in `api_tokens` with kind `session`, expiring after 12 hours of inactivity. The database expiry and browser cookie renew together only when the five-minute liveness throttle fires; SSE never renews the cookie. Cookie-authenticated non-GET routes require the workbench's own header, which a cross-origin form cannot send. A logout with a dead cookie still clears it when that header is present. `user set-password` is routed: a human can change its own password, a hub admin can reset any human's, and a successful change revokes the target's other browser sessions while retaining bearer tokens. API calls from programs send a bearer token. Both resolve to the same context. Every login failure is one code, `E_LOGIN_FAILED`.

### 15.2a Workbench

The generated workbench is the initial functional demo. Once the main accounting
elements are available, the browser interface receives a comprehensive workflow
and visual design pass covering navigation, page composition and interaction on
desktop and phone. The production interface is organized around bookkeeping
tasks; the demo's command cards do not define its final navigation or appearance.
Working screen examples establish the human's direction for that pass.

The workbench is the browser interface used to exercise every command. It is generated from the registry and is complete by construction: when a command is registered, its page exists. Its shared shell preserves the selected company and grouped primary navigation on every page; list, detail, and form pages expose a consistent title, context trail, primary action, empty state, and result feedback. Row 5 is the first human checkpoint for this browser shape. Individual list nouns do not fork their own navigation or visual language.

| Page | Content |
|---|---|
| `/login` | username and password; successful HTMX submission returns a validated same-host `HX-Redirect` to `next` or `/` |
| `/` | the only visible company, else the last company this browser visited when still visible, else the company picker |
| `/c/<company_id>/` | the company home and persistent navigation: Company (`company`); Customers and sales (`customer`, `customer-type`, `job-type`, `sales-rep`, `price-level`, `ship-method`, `customer-message`); Vendors and purchases (`vendor`, `vendor-type`); Employees (`employee`, `other-name`); Items (`item`, `item-category`, `unit-of-measure`); Accounting (`account`, `class`, `term`, `payment-method`, `sales-tax-code`, `chart`); Settings (`custom-field`, `profile`); Audit (`audit`, `undo`) |
| `/c/<company_id>/<noun>` | bounded summary `query` pages with explicit continuation/restart and the noun's declared query, typed filters, active/inactive view, sortable headings, selectable/reorderable columns with reset, and one row link to `show` |
| `/c/<company_id>/<noun>/<id>` | the `show` output as a field table, the record's activity feed, its notes and attachments, and buttons for each verb the role permits |
| `/c/<company_id>/<noun>/<verb>` | a role-authorized form generated from the typed descriptor; discriminated commands choose type/kind before rendering variant fields, references use bounded search suggestions and retain an inactive current value, nested collections preserve stable ids/order, exact numerics remain strings, writes carry reason/directive and rendered versions, preview renders in place, and submit redirects with a session-bound one-use 60-second opaque result flash |
| `/c/<company_id>/audit` | `audit list` with its filters, each event expanding to entries/diffs and offering role-authorized undo only when eligible |
| `/c/<company_id>/reports/<name>` | report parameters form, then the report as a table with a CSV link |

Workbench requests are recorded with interface `http` and client name `bookflow-workbench`. Column choice/order lives only in the page URL. The viewport is declared; navigation collapses while company identity remains visible; forms become single-column at narrow widths; and table containers, never the body, own horizontal scrolling. `use_account_numbers` and `show_lowest_subaccount_only` control all account labels. The shared responsive stylesheet establishes the browser product's navigation, hierarchy, tables, detail pages, forms, and feedback states without bespoke decoration per command. Task-specific workflows use the same HTTP routes. The customer/job workflow provides grouped contact, address and inheritance context, a bounded jobs list, Add job with validated parent context, and grouped editing with preview/save. Reference controls display searchable names while submitting stable ids, invalidate stale selections after typing, and preserve inactive current references. Register-style entry accompanies the general ledger; rarely used commands retain generated forms.

### 15.3 MCP server

`bookflow mcp --token <secret>`. Three tools, so that the agent's context never carries every command's schema at once: `bookflow_list_commands` returns every command name with its one-sentence description and scope; `bookflow_help` returns a command's documentation page, including its input schema, output fields, and error codes; `bookflow_run` takes `command`, `input`, and the context arguments `company`, `reason`, `directive`, `source_ref`, and `idempotency_key`, and returns the output or the error document. The three are generated from the registry like every other adapter.

## 16. Documentation contract

The reader is an agent that has never seen Bookflow and cannot read the source. Every page states facts; none explains reasoning.

| Location | Content | Produced by |
|---|---|---|
| `README.md` | what it is, install, the one command to run, where the docs are | hand |
| `docs/cli/<noun>.md` | every command of that noun: purpose in one sentence, every flag with type and default, output fields, error codes, one example invocation and its JSON output; a long-running command states its lifecycle instead of pretending to return immediately | `bookflow docs generate`, from command definitions |
| `docs/schema/<database>/<table>.md` | every field with type, nullability, meaning, and references; the database directory distinguishes same-named hub and company tables | `bookflow docs generate`, from models and their completeness-checked description metadata |
| `docs/concepts.md` | the command contract, context, concurrency rules, money, company selection, exit codes, stated declaratively | hand, verified by tests where possible |
| `docs/agent-guide.md` | one page for an agent: authenticate, pick a company, record and cite a directive, perform an audited write, read the event feed, handle `E_VERSION_CONFLICT`; every example runnable against the demo company | hand, examples verified by a test |
| `docs/index.md` | the entry point; lists every page | generated |
| `design/architecture.md` | what is built, module by module, with what was verified on real hardware and what was not; written by whoever builds, for the next builder | hand |

`bookflow docs generate` is a local standalone command: it needs no data root and is never exposed over HTTP. Hand-authored pages live as packaged generator resources, so an installed wheel can reproduce the complete marked `docs/` tree. A test regenerates the docs and fails when the result differs from the committed files.

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
| A bounded summary/reference query page or show returns in under 100 ms | Warm in-process execution, 10,000 records in the list, SQLite on local SSD; legacy complete list/export enumeration is not a bounded interactive operation |
| Posting a 20-line transaction completes in under 50 ms | same |
| Trial balance over 100,000 lines returns in under 2 s | same |
| A company database with 100,000 transactions stays under 500 MB excluding attachments | |
| Audit tables occupy at most 6 times the live data they describe, in aggregate | measured with `dbstat` on the fixed fixture of 5,000 creates and 5,000 updates; every write keeps a full after-snapshot for readability and an event row of context, and the current encoding measures near 5 |
| Installed package with dependencies under 60 MB; no service other than SQLite required | |
| CLI cold start: `bookflow --help` under 300 ms; process-cold read timings are recorded, not release gates | Python 3.12, warm disk cache; fresh processes include imports, command construction and execution. Modest misses of the 750 ms read target are nonblocking; material regressions require investigation. Warm interactive budgets remain strict. |

### 18.1 Local diagnostic capture

`BOOKFLOW_TRACE_DIR` enables a finite process-local duration trace before CLI application import, never from an HTTP request. Without it the recorder is inactive. Capture admits at most 10,000 spans over 60 seconds, with at most 256 concurrent reservations and 32 nesting levels. Queue waits use separate virtual lanes and retain diagnostic-only operation ancestry after caller timeout. Export follows normal console/host shutdown; library callers explicitly close their recorder. Dropped, unfinished and truncated coverage is visible in numeric metadata.

Only fixed phase, mode and database-category labels, canonical registry command names, generated diagnostic identifiers, numeric timings and outcomes are collected. SQL, arguments, business identifiers, paths, credentials, reasons and raw errors are excluded. Best-effort private export creates a unique mode-0600 file in an existing owner-private local directory outside every selected root, including selections on rejected commands. Linux directory handles and mount validation enforce this; other platforms refuse export. The process remembers at most 128 distinct protected roots, then refuses export permanently. Files remain until explicitly removed by the operator. There is no telemetry, remote capture control, accounting schema or audit event.

Stages describe API boundaries and inclusive wall time, not exclusive CPU cost. SQLite-internal synchronization remains inside commit timing. Existing connections, explicit custom cursor factories and out-of-factory migration/backup handles have no detailed SQL coverage. Browser network/render timing stays in browser developer tools. Forwarded CLI and host traces have no cross-process parentage. Existing operation budgets are measured without capture; enabled overhead is reported separately.

## 19. Out of scope for release 1, and beyond

Not in release 1: transaction forms other than journal entries, reports beyond trial balance and general ledger, work orders, time entries, scheduler, assembly build transactions, bespoke per-list browser centers, a desktop wrapper, rate fetching, email, bank feeds, encryption at rest.

Four outside-facing integrations are designed as their own passes, each behind a provider interface so the churn of outside services never reaches the core. They come after the internal systems that grow out of the accounting core (CRM, work orders, inventory and receiving, assemblies, shipping), which are the product's priority:

- **Email per company** (section 13.4) ships in release 2 with the `event` and `smtp` channels; the Gmail package follows. Persistent encrypted provider credentials and automatic OAuth refresh live in the hub behind the provider interface, so routine use and restarts do not ask the user to authorize again and a provider change never touches a company's books.
- **Bank feeds.** A `bank_feed_items` inbox per bank or credit card account, with items stored in the Financial Data Exchange account and transaction shape so every provider is a translation and the inbox never changes: transactions arrive from a provider (statement import first: OFX, QFX, CSV, and parsed PDF statements through a maintained open-source statement parser chosen at that design pass; then live connections behind the same interface: SimpleFIN Bridge for its small universal shape, BankSync for its open-banking flow with a CLI of the same noun-verb and JSON shape as Bookflow's, and Plaid for institution coverage; the provider is a company setting and adding one never changes the inbox), are matched to existing transactions or proposed as new ones by rules the company defines (payee pattern to account, class, and memo), and are either auto-posted where a rule says so or held for approval in the inbox. Approval is the default for anything a rule does not cover; the approving user and the rule are recorded on the posted transaction.
- **Tax forms**, after payroll: federal and state filings (payroll returns, 1099s, W-2s, sales tax returns, and the income tax forms the company's entity type calls for) prepared from the ledger and payroll data, kept current through the same versioned data package as tax tables, and submitted through a provider interface where electronic filing exists.
- **Payroll.** Employees, the time clock, and time entries already have their tables. Payroll adds pay schedules, earnings and deduction items, tax tables delivered as a versioned data package updated like `tzdata`, paycheck posting through the ordinary ledger, and filings and payments through a provider interface, so tax-table updates and e-file submission change providers without changing the module.

Never in scope without a new design pass: multi-currency ledgers, non-US tax regimes.

Later releases, each designed as its own pass against this blueprint: customer relationship management (lead generation and tracking, contacts, follow-ups, communication history, pipeline reporting), purchase orders and receiving, assembly build orders and posting, shipping with printable labels, customer letters and statements, marketing lists and mailings, scheduled automated billing and late-payment notices with delivery of delinquent invoices after a set age, calendar integration with work orders and multi-phase project schedules spanning days, an employee time clock (clock in and out per employee, feeding time entries), payroll. Each is a set of lists, transaction types, and reports registered through the same command registry.

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

The remaining release 1 build order is the active spec list in `design/intention.md`; completed components are described in `design/architecture.md`.

Release 1 is done when a stranger, given a fresh machine and the README, can: initialize a data root, create a company, log in to the workbench in a browser, add accounts and customers there, attach a receipt image to a customer, post a balanced journal entry from the workbench and another from the CLI, post a third over HTTP from a second process, list accounts from an MCP client, see all of it in the audit page with correct actors and interfaces, and reproduce all of it on a second copy of the company folder.

Release 2 is forms (section 10.3), reports (section 14), import and export (section 14.1), email delivery (section 13.4), and broader task-specific browser workflows. Release 3 is work orders, time, and the scheduler (section 13). Every product interface is a browser client of the HTTP host; native operating-system transports and wrappers remain optional adapters.
