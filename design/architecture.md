# Bookflow — architecture

What is built, module by module. Rows 1 to 3 (package skeleton, registry, data root, hub, organizations, companies, demo, CLI; company audit, versioned writes, presence, idempotency, directives, the event feed; the host process, HTTP routes, tokens, the local hand-off, and the workbench) are the current state.

## Layout

```
src/bookflow/
  __init__.py            lazy exports: connect, Client, BookflowError, Money (nothing heavy imports at package load)
  client.py              Client.run / use_company / attribute form; builds Context with interface "python"
  core/
    registry.py          Command, Plan, Applied, Touched, @command, REGISTRY, load_all()
    dispatch.py          run(): data root, locality, root lock, umask 077, hub open, actor, company, roles, plan/apply, audit, config
    context.py           Context (blueprint 5.2); CONTEXT_FIELD_NAMES
    session.py           Session (open databases, actor, memberships), Actor, now_iso(), localize()
    errors.py            every E_ code and its message; BookflowError with exit_code and to_dict()
    ids.py               ULID new_id / is_ulid / normalize_ulid
    money.py             Money (minor units + code), currency table from data/currencies.csv, parse/format/JSON form
    models.py            ListOutput, WriteOutput, CommonFields, redact_paths()
    config.py            config.toml: [users.<login>] user_id/default_company; atomic save; os_login() from uid
    fs.py                filesystem type detection (Linux mountinfo; macOS statfs; Windows drive type) and check_local()
    locks.py             RootLock: <data_root>/root.lock, exclusive for the whole command, holder info, BOOKFLOW_LOCK_TIMEOUT
    perms.py             private_umask() (077), is_private_dir()
    moves.py             rename_noreplace() per platform; move_dir() with case-only hop
    lazy.py              LazyModule: command modules import services lazily so the CLI builds without SQLAlchemy
    clock.py             the one time source (now, now_iso, parse_iso); tests replace now
    versioning.py        check_update(): versioned, blind, and disjoint-field merge rules; history_from_entries() folds audit diffs to top-level fields
    idempotency.py       input_hash(), lookup() (mismatch, expiry), store() with in-progress state
    audit.py             write_event_to() over either database with seq; snapshot codec; secrets stored as sha256 prefixes
    host.py              Host: holds the root lock as "serve", one writer thread with pooled writable connections, per-request read-only sessions, commit signals for event streams, host.json descriptor; a timer thread running an idle RESTART checkpoint (no reader attached, writer idle, default 30 s) and an hourly sweep of session tokens expired more than a day as one system event; checkpoint_now() and sweep_now() run the same work through the writer
    forward.py           stdlib only: socket_path(), read_descriptor(), call_host(), try_forward() (run() sends a call to a live host over its Unix socket; never for local_only commands)
  storage/
    paths.py             data root resolution; display-name normalization and name_key; folder derivation, collision choice, reservation; markers
    engine.py            Database (sqlite3 + SQLAlchemy Core on one connection); percent-encoded file URIs; read-only (query_only) and writable (WAL, checkpoint on close) opens; create=True only for init and rollout; io_error() translation
    migrate.py           HEADS constants; classify(); backup via sqlite backup API; migrate_to_head(); Alembic loaded only when migrating
    hub_migrations/      Alembic chain "hub": hub0001 (frozen explicit tables), hub0002 (seq, directive_code, idempotency_keys)
    company_migrations/  Alembic chain "company": co0001 (frozen), co0002 (audit tables, presence, idempotency_keys, directives, sequences)
    migrate.py           + migrate_company(): the one owner of company migrations: migrate entry by the system user, baseline entry, marker, hub projection entry
  hub/
    schema.py            users, api_tokens, organizations, companies, memberships, audit_events, audit_entries
    users.py             bootstrap users, common() field helper, user_names()
    access.py            memberships, org_role(), company_role() -> (access, role), visibility filters, role_satisfies()
    organizations.py     create (folder + marker + row), get, bump
    companies.py         register, get, list_visible, update, delete_company_rows, delete_organization_rows
    audit.py             write_event() with prefixed/compressed snapshots, decode, visible_record_ids() and visible_event_ids_filter() (per-entry visibility)
    moves.py             complete_company_move() / complete_org_move(): finish a pending move from any state with a version bump and a move event; effective_path() and display_path() for read-only opens
  company/
    schema.py            company_info (9.1 inventory), principals
    info.py              read_info, upsert_principal/upsert_actor, principal_names, write_display_name_copy (raw)
    rollout.py           create_company_folder(): stages 2-4 with cleanup; writes the company_info create entry
    presence.py          set/clear/live_for/prune; 90 s TTL; never audited
    directives.py        add/resolve/deactivate/list_all; SI-<n> codes from sequences, never reused
  commands/
    common.py            OrganizationOutput, CompanySummary builders, Empty/ListInput/NameInput
    hub_cmds.py          init (bootstrap path run_init), upgrade, organization new/list/show/rename, company new/list/use/attach/detach, demo reset, hub audit list/show
    company_cmds.py      company show (+ info_version, editing_by), company rename, company update, directive add/list/show/deactivate, presence set/clear
    audit_cmds.py        audit list/show/tail (company) and hub audit list/show/tail, one implementation over either database; seq cursors; per-entry visibility
    host_cmds.py         serve (bootstrap path run_serve, bind parsing, the --allow-network gate), start_serving()/ServeHandle, migrate_everything(), make_local_handler(), user_for_login(); user set-password; token issue/list/revoke
  demo/seed.toml         Demo Holdings LLC / Demo Plumbing Co
  adapters/cli/app.py    Typer app generated from the registry; nested fields -> --a-b flags; global options per scope; --interactive; error rendering
  adapters/cli/render.py tables, field views, JSON, errors on stderr
  adapters/http/app.py   FastAPI app from the registry: /commands/<noun.verb>, /companies/{id}/commands/<noun.verb>, /login, /logout, /companies/{id}/events and /hub-events (SSE), /openapi.json, /health; context from headers; the same error documents as the CLI with HTTP statuses
  adapters/http/auth.py  argon2 passwords (constant-time on unknown users), bearer and session tokens stored as sha256, liveness refresh, login throttle
  adapters/http/local.py LocalListener on the Unix socket: peer identity from SO_PEERCRED, envelope identity fields discarded
  adapters/workbench/    pages.py (picker, hub and company indexes, generated list/record/form/audit pages), forms.py (input model -> leaves; form -> command JSON with originals, tri-state booleans, clears, Preview), templates/, static/ (vendored htmx, stylesheet)
```

Multi-word nouns (`hub audit`) become nested CLI groups and attribute chains on the client (`client.hub.audit.list()`).

Registry index `NOUN_MODULES` maps modules to nouns; the CLI loads only the module for the invoked noun (root help loads none), which keeps cold start flat. `Command` carries kind (read/write/advisory), truth (which database commits first and holds the idempotency row), accepts_idempotency_key, clearable, streams, capability, feature.

## Runtime facts

- Every command takes `root.lock` exclusively for its whole duration; `init` creates the root first. Lock timeout 5 s, `BOOKFLOW_LOCK_TIMEOUT` overrides (tests use 0.2).
- Hub opens writable when the command writes hub or config (the audit event lives in the hub); company opens writable only when the command writes company.
- Writable opens migrate behind-head databases after a backup and record an `upgrade` event; read-only opens of a behind-head database return `E_SCHEMA_BEHIND`.
- `apply` runs inside one hub transaction and one company transaction started by dispatch; commands that need more than one hub transaction (rename with move, organization move, demo reset, upgrade) commit and reopen transactions themselves and return `audited=True`.
- Paths in outputs, error details, and audit snapshots are nulled for non-hub-admins in dispatch (`redact_paths`, `redact_error`); OS and SQLite failures become `E_IO` at the dispatch boundary; `E_DB_BUSY` carries only the holder's command and hold time.
- A writable hub open completes any pending organization move (all of them) and the selected company's pending move before the command plans (hub/moves.py); read-only opens and dry runs use whichever folder exists and write nothing. Dry runs open every database read-only and never migrate.
- Dispatch order under the lock: config; hub open; actor; hub migration; pending organization moves; company resolution and role; company open (pending move, migration, display-name copy); directive resolution; reason gate (any write by an agent or system actor); idempotency lookup; plan; dry-run return; apply with principals upserted at the start of the company transaction (rolled back on a no-op), events per database, truth-ordered commits, projection repair as a hub entry, idempotency store; config write. `run_in_session` runs a command inside an open session (demo seed; later the host).
- Company audit events carry every context column; `company_info` snapshots exclude `display_name` and store `tax_id` as a short hash; the before-state is derived from the previous entry's after for every action but create, baseline, migrate, delete.
- Schema migrations record a `migrate` entry by the system user with `on_behalf_of` the triggering actor, and rewrite the company marker and hub projection.
- `Client.use_company` and `company=` on a call are step 1 of selection; `BOOKFLOW_COMPANY` is step 2; the saved default is step 3.
- The host holds the data-root lock as `serve` for its whole run. Every write and advisory request runs on one writer thread with long-lived writable connections; reads open and close their own read-only connections on the request thread. Every HTTP and workbench handler hands its blocking work to the threadpool, so one long write never holds the event loop.
- `local_only` keeps a command off the HTTP routes (`init`, `serve`, `company use`, `user set-password`); the local socket still carries it, because the peer's OS login is known there. Only bootstrap commands (`init`, `serve`) are never forwarded. `try_forward` decides; `run()` no longer filters.
- The event stream re-resolves its credential on every wake-up, so a revoked or expired token ends the stream with a final `error` event.
- A single-word command (`upgrade`) has no verb: its noun page is its form, and it submits to `/hub/<noun>`.
- Cold start with row 3 present: `bookflow --help` about 230 ms and `bookflow serve --help` about 255 ms; neither imports FastAPI, uvicorn, or the workbench.
- The suite is 216 tests in about 132 s on this machine (Linux, ext4, Python 3.12); tests/test_row3_host.py is 46 of them, in about 34 s.

## Verified on this machine (Linux, ext4, Python 3.12)

- The row 1 done sequence through the library and the CLI, compared field by field (tests/test_row1_flow.py::test_library_and_cli_agree).
- Folder copied to a second data root and attached: identical `company_info`, identical database dump, identical directory listing, original creator's name resolved through `principals`.
- Second process during a command: `E_DB_BUSY` with the holder's command through the CLI and the library.
- File modes under umask 022: every file 0600, every directory 0700.
- Cold start: `bookflow --help` about 220 ms (root help loads no command module); `bookflow company list --json` about 550 ms end to end (`BOOKFLOW_BUDGET_MS` overrides the test on slower machines); SQLAlchemy is not imported for help.
- Names containing `#`, `%`, `?`, and spaces in company, organization, and data-root names; a wheel built with `uv build` carries the currency table.
- Migration of a behind-head company with a synthetic revision: backup, `migrate` event, marker and projection updated (tests/test_hardening.py::test_synthetic_migration).
- Demo reset repeated on one root; trash accumulates one folder per reset.

Row 3, all in tests/test_row3_host.py against a host running in the test process:

- Every routed read command returns the same JSON over HTTP as through the library, compared field by field with ULIDs and timestamps replaced (`test_every_routed_read_returns_the_same_document_over_http_as_in_the_library`).
- Write error documents and their statuses match the library's: `E_VALIDATION` 422, `E_PERMISSION` 403, `E_COMPANY_NOT_FOUND` 404, `E_VERSION_CONFLICT` 409.
- Context keys in a body are `E_CONTEXT_IN_INPUT` naming the header; unknown and local-only names are `E_USAGE` documents.
- A member of one company asking for another by path id, by `X-Bookflow-Company`, or through a workbench page gets the same 404 body as a nonexistent id, and no error that member can provoke names a path on this machine.
- Login sets the cookie, non-GET cookie requests need `X-Bookflow-Workbench: 1`, and login and logout are both hub events; the four `E_UNAUTHENTICATED` shapes (no credential, unknown, expired, revoked) each carry `details.reason`.
- A forwarded CLI call is recorded with interface `cli`; a forged `actor_id` is ignored; a forwarded `company use` writes the caller's login table; another uid is refused; `serve` and `init` are never forwarded; a descriptor whose socket refuses, and one whose pid is dead, both fall back to the lock path; a version mismatch over the socket is named.
- Two reads finish in under a second while a one-second write sits on the writer; two concurrent updates serialize into consecutive versions; five quick reads on one bearer refresh `last_used_at` once.
- The stream drains a burst of ten writes, resumes from `Last-Event-ID`, and ends with an `error` event when its bearer is revoked.
- Every routed command has a form page with exactly one control per input leaf; the update form carries `expected_version` and the hidden originals; Preview runs the dry run and writes nothing; a submit renders a result page linking back; a form resubmitted unchanged writes no event.
- `--allow-network` gates a non-loopback bind and the dry run reports the cookie decision (`secure_cookies` true off loopback, false on it).
- A company rewound to `co0001` is migrated at startup, and the company's audit shows the `upgrade` event by the system user with the serving hub admin as `on_behalf_of`.
- After 300 company updates through the writer with a reader attached, the company WAL is under 4 MB once the idle checkpoint runs; `checkpoint_now()` logs a result per connection and `sweep_now()` deletes only sessions expired more than a day, as one `session sweep` event by System with the token hash absent.
- A host built with sub-second timers checkpoints and sweeps on its own, and both threads stop with the host.

## Not verified on hardware

- macOS `statfs` and `renamex_np` branches, Windows drive-type and `MoveFileExW` branches: unit paths only, no real run.
- A refusal on a real network mount: not attempted on this machine (no NFS, CIFS, or sshfs available); `E_NETWORK_SHARE` is exercised with mocked mount tables only.
- macOS `LOCAL_PEERCRED` (`getpeereid`) and the Windows named-pipe hand-off: only the Linux `SO_PEERCRED` path has run; the code raises `E_UNAUTHENTICATED` on any other platform.
- A non-loopback bind with TLS in front of it: `--allow-network` and the `Secure` cookie default are exercised as a dry run only, never as a real listener off loopback.

- Row 2 done sequence: two admins updating company info through the library and the CLI (conflict on overlapping fields, merge on disjoint ones, blind-write warning naming the previous writer and principal, dry runs reporting the same), missing-history conflict, null and `--clear`, closing date needs admin, projection repair and `E_PARTIAL_WRITE`, agent reason gate and directives through a dispatch-level agent session, idempotent replay and mismatch, presence never audited, seq cursors, hashed tax id, principals on a copied folder, append-only across the codebase, and the budget fixture at a reduced size (`BOOKFLOW_BUDGET_N=5000` runs the full one; measured ratio printed).

## Known gaps carried to later rows

- `--help` lists options and positionals but not output fields or error codes (row 4).
- No `user add` or `membership grant`; tests insert users through the repository layer (tests/conftest.py::make_actor).
- The currency table holds 155 codes; the remaining ISO 4217 codes are added on request.
- Agents cannot yet exist through any surface; the CLI maps an OS login only to a human user, and tests build an agent session at the point row 7's token resolution will fill (`Session.actor`, `Context.on_behalf_of`).
- The full budget fixture (5,000 creates and 5,000 updates) is run on demand with `BOOKFLOW_BUDGET_N=5000`; it measured audit 8.1 MB against live 1.6 MB, ratio 5.16, in 192 s; the default suite runs 200 rows.
- `--follow` on `audit tail` is CLI-only and polls under the data-root lock every two seconds until the host exists.
- `user set-password` is `local_only`, so it is not an HTTP route; the plan's route example names `user.set-password`. It is forwarded over the local socket.
- The idle checkpoint is `RESTART`, which resets the WAL but never shrinks the file. A reader that pins a snapshot across a long burst leaves the file at its high-water mark until the shutdown `TRUNCATE`.
- `serve --bind <address>:0` is `E_VALIDATION` before the network gate is reached, because the port range is checked first.
- `E_IDEMPOTENCY_MISMATCH` (409) and `E_INTERNAL` (500) are in the status map but are not exercised over HTTP.
- The generated `/openapi.json` is asserted for shape and coverage, not validated against an OpenAPI schema validator.
- The human's browser taste gate on the workbench (two browsers conflicting, presence, the audit page) has not been run.
