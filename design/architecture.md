# Bookflow — architecture

What is built, module by module: package skeleton, registry, data root, hub, organizations, companies, demo, CLI, company audit, versioned writes, presence, idempotency, directives, the event feed, the host process, HTTP routes, tokens, the POSIX local hand-off, the workbench, and generated command and schema documentation.

## Layout

```
src/bookflow/
  __init__.py            lazy exports: connect, Client, BookflowError, Money (nothing heavy imports at package load)
  client.py              Client.run / use_company / attribute form; builds Context with interface "python"
  core/
    registry.py          Command, Plan, Applied, Touched, @command, REGISTRY, load_all(); exact authorization text and rootless standalone runners
    dispatch.py          run(): data root, locality, root lock, umask 077, hub open, actor, company, roles, plan/apply, audit, config
    context.py           Context (blueprint 5.2); CONTEXT_FIELD_NAMES
    session.py           Session (open databases, actor, memberships), Actor, now_iso(), localize()
    errors.py            every E_ code and its message; BookflowError with exit_code and to_dict()
    ids.py               ULID new_id / is_ulid / normalize_ulid
    money.py             Money (minor units + code), currency table from data/currencies.csv, parse/format/JSON form
    models.py            ListOutput, WriteOutput, CommonFields, redact_paths()
    config.py            config.toml: user mappings/defaults; hub-committed pending projection overlay and durable file publication; os_login() from uid
    durability.py        private unique metadata temporaries, file/directory synchronization, move-parent synchronization
    fs.py                filesystem type detection (Linux mountinfo; macOS statfs; Windows drive type) and check_local()
    locks.py             RootLock: <data_root>/root.lock, exclusive for the whole command, holder info, BOOKFLOW_LOCK_TIMEOUT
    perms.py             private_umask() (077), is_private_dir()
    moves.py             rename_noreplace() per platform; move_dir() with case-only hop
    lazy.py              LazyModule: command modules import services lazily so the CLI builds without SQLAlchemy
    clock.py             the one time source (now, now_iso, parse_iso); tests replace now
    versioning.py        check_update(): versioned, blind, and disjoint-field merge rules; history_from_entries() folds audit diffs to top-level fields
    idempotency.py       input_hash(), lookup() (mismatch, expiry), store() with in-progress state
    audit.py             write_event_to() over either database with seq; snapshot codec; secrets stored as sha256 prefixes
    host.py              Host: holds the root lock as "serve", one writer thread with pooled writable connections, explicit pool release before folder operations, per-request read-only sessions, event-loop subscriptions keyed by database id, deduplicated credential-refresh jobs, host.json descriptor; a timer thread running an idle RESTART checkpoint and an hourly session sweep; begin_shutdown() wakes streams before final stop
    forward.py           stdlib only: private runtime-directory validation, socket_path(), read_descriptor(), call_host(), try_forward() (run() sends every non-bootstrap call to a live host over its Unix socket)
  storage/
    paths.py             data root resolution; display-name normalization and name_key; folder derivation, collision choice, reservation; markers
    engine.py            Database (sqlite3 + SQLAlchemy Core); explicit read-only snapshots, verified WAL/FULL/foreign-key writers, writable-transaction detection and exception-safe cleanup; percent-encoded URIs; create=True only for init/rollout
    migrate.py           HEADS constants; classify(); backup via sqlite backup API; migrate_to_head(); Alembic loaded only when migrating
    hub_migrations/      Alembic chain "hub": hub0001 (frozen explicit tables), hub0002 (seq, directive_code, idempotency_keys), hub0003 (capability/feature metadata), hub0004–hub0005 (list capabilities), hub0006 (pending config projection)
    company_migrations/  Alembic chain "company": co0001 (frozen), co0002 (audit/presence/directives), co0003 (20 supporting lists), co0004 (job delivery inheritance)
    migrate.py           + migrate_company(): the one owner of company migrations: migrate entry by the system user, baseline entry, marker, hub projection entry
  hub/
    schema.py            users, api_tokens, organizations, companies, memberships, role_capabilities, features, audit_events, audit_entries; co-located table and column descriptions
    users.py             bootstrap users, common() field helper, user_names()
    access.py            memberships, org_role(), company_role() -> (access, role), visibility filters, role_satisfies()
    organizations.py     create (folder + marker + row), get, bump
    companies.py         register, get, list_visible, update, delete_company_rows, delete_organization_rows
    audit.py             write_event() with prefixed/compressed snapshots, decode, visible_record_ids() and visible_event_ids_filter() (per-entry visibility)
    moves.py             complete_company_move() / complete_org_move(): finish a pending move from any state with a version bump and a move event; effective_path() and display_path() for read-only opens
  company/
    schema.py            company_info (9.1 inventory), principals; co-located table and column descriptions
    info.py              read_info, upsert_principal/upsert_actor, principal_names, write_display_name_copy (raw)
    rollout.py           create_company_folder(): stages 2-4 with cleanup; writes the company_info create entry
    presence.py          set/clear/live_for/prune; 90 s TTL; never audited
    directives.py        add/resolve/deactivate/list_all; SI-<n> codes from sequences, never reused
    query.py             strict bounded query inputs, reference rows, scoped/permission-bound continuation contract
    query_providers.py   SQL-first noun selection and summary/reference projection; audit-watermark continuation validation
  commands/
    common.py            OrganizationOutput, CompanySummary builders, Empty/ListInput/NameInput
    hub_cmds.py          init (bootstrap path run_init), upgrade, organization new/list/show/rename, company new/list/use/attach/detach, demo reset, hub audit list/show
    company_cmds.py      company show (+ info_version, editing_by), company rename, company update, directive add/list/show/deactivate, presence set/clear
    audit_cmds.py        audit list/show/tail (company) and hub audit list/show/tail, one implementation over either database; seq cursors; per-entry visibility
    host_cmds.py         serve (bootstrap path run_serve, bind parsing, the --allow-network gate), start_serving()/ServeHandle, migrate_everything(), make_local_handler(), user_for_login(); user set-password; token issue/list/revoke
    docs_cmds.py         standalone docs generate/check command; imports the renderer only when invoked
    query_cmds.py        lazy registry generation of typed query models and commands for all 20 company list nouns
  documentation/
    generate.py          deterministic command/schema/resource projection; complete-tree freshness comparison; sibling staging and atomic swap with rollback
    introspection.py     recursive Pydantic field facts, deterministic valid output samples, and SQLAlchemy column facts
    examples.py          exactly one model-valid deterministic invocation per registered command
    resources/           packaged concepts.md and executable agent-guide.md sources copied into each generated tree
  demo/seed.toml         Demo Holdings LLC / Demo Plumbing Co
  adapters/cli/app.py    Typer app generated from the registry; concrete invocations construct only their command parser, noun help includes its full surface; nested fields -> --a-b flags; declared integer flags parsed at the adapter boundary; global options per scope; --interactive; error rendering
  adapters/cli/render.py tables, field views, JSON, errors on stderr
  adapters/http/app.py   FastAPI app from the registry: /commands/<noun.verb>, authoritative /companies/{id}/commands/<noun.verb>, /login, /logout, async /companies/{id}/events and /hub-events, exact generated /openapi.json, /health; credential/cookie handling and the same error documents as the CLI with HTTP statuses
  adapters/http/auth.py  argon2 passwords (constant-time on unknown users), bearer and session tokens stored as sha256, liveness refresh, login throttle
  adapters/http/local.py LocalListener on the Unix socket: peer identity from SO_PEERCRED, envelope identity fields discarded, 8 MiB frame cap and 30-second accepted-connection timeout
  adapters/workbench/    pages.py (picker, hub/company indexes, bounded list/record/form/audit pages), forms.py (input model -> leaves and command JSON with originals, tri-state booleans, clears, Preview), workflows.py (customer/job display groups), templates/, static/ (vendored htmx, reference-selection client, content-versioned assets)
```

The repository root carries `uv.lock`; source-checkout documentation trials sync a dedicated environment inside their disposable trial root, then use `uv run --frozen --no-sync`, so executing the guide does not update the checkout's lock or replace packages in a shared environment.

Multi-word nouns (`hub audit`) become nested CLI groups and attribute chains on the client (`client.hub.audit.list()`).

Registry index `NOUN_MODULES` maps modules to nouns; the CLI loads only the module for the invoked noun (root help loads none), which keeps cold start flat. `Command` carries kind (read/write/advisory), truth (which database commits first and holds the idempotency row), accepts_idempotency_key, clearable, streams, capability, feature, exact authorization text, and an optional standalone runner. Standalone commands are omitted from database capability projections and routed surfaces unless a caller explicitly requests them.

## Runtime facts

- Offline commands take `root.lock` exclusively for their duration; hosted commands run under the host-held root lock with one writer and concurrent read snapshots. `init` creates the root first. Lock timeout is 5 s; `BOOKFLOW_LOCK_TIMEOUT` overrides it.
- Hub opens writable when the command writes hub or config (the audit event lives in the hub); company opens writable only when the command writes company.
- Writable opens verify WAL, `synchronous=FULL` and foreign keys, migrate behind-head databases after a durably published backup, and record an `upgrade` event. Read-only opens begin a deferred SQLite transaction after SQLAlchemy setup; revision checks and dependent reads use that snapshot. Behind-head reads return `E_SCHEMA_BEHIND`. Hub and company snapshots are independent, not cross-database atomic.
- `apply` runs inside one hub transaction and one company transaction started by dispatch; commands that need more than one hub transaction (rename with move, organization move, demo reset, upgrade) commit and reopen transactions themselves and return `audited=True`.
- Paths in outputs, error details, and audit snapshots are nulled for non-hub-admins in dispatch (`redact_paths`, `redact_error`); OS and SQLite failures become `E_IO` at the dispatch boundary; `E_DB_BUSY` carries only the holder's command and hold time.
- A writable hub open completes any pending organization move (all of them) and the selected company's pending move before the command plans (hub/moves.py); read-only opens and dry runs use whichever folder exists and write nothing. Dry runs open every database read-only and never migrate.
- Dispatch order under the lock: config; hub open; actor; hub migration; pending organization moves; company resolution and role; company open (pending move, migration, display-name copy); directive resolution; reason gate (any write by an agent or system actor); idempotency lookup; plan; dry-run return; apply with principals upserted at the start of the company transaction (rolled back on a no-op), events per database, truth-ordered commits, projection repair as a hub entry, idempotency store; committed config intent and durable file projection. Nested after-commit demo commands do not inherit the outer config-dirty flag. `run_in_session` runs a command inside an open session (demo seed; later the host).
- Company audit events carry every context column; `company_info` snapshots exclude `display_name` and store `tax_id` as a short hash; the before-state is derived from the previous entry's after for every action but create, baseline, migrate, delete.
- Schema migrations record a `migrate` entry by the system user with `on_behalf_of` the triggering actor, and rewrite the company marker and hub projection.
- `Client.use_company` and `company=` on a call are step 1 of selection; `BOOKFLOW_COMPANY` is step 2; the saved default is step 3.
- The host holds the data-root lock as `serve` for its whole run and acquires it before binding TCP; `host.json` is published only after both listeners are ready. Every write and advisory request runs on one writer thread with long-lived writable connections; reads own explicit read-only snapshots for their command lifetime and close them on success or failure. Reader accounting also covers credential reads. Shutdown closes admission for readers and queued writes, drains admitted work, and only releases the root lock after final cleanup. Pooled company handles are released before a company or organization folder move, trash/reset, detach, upgrade, or attach. Even a write that raises after a durable commit runs the checkpoint-and-notify pass; checkpoint failure cannot suppress the sequence read or subscriber wake. Credential liveness refreshes are deduplicated and queued without waiting for the writer. A discarded writer hub is reopened on the writer thread before its next operation.
- `local_only` keeps `init`, `serve`, and `company use` off HTTP. On POSIX, the local socket carries every non-bootstrap command because the peer's OS login is known there; only `init` and `serve` remain in the calling process. The forwarded JSON envelope and command handler do not depend on Unix socket names or peer-credential APIs, leaving a seam for an owner-restricted Windows named-pipe transport. Accepted connections time out after 30 seconds and frames over 8 MiB are refused before their bodies are read. `serve` requires a human hub admin. `user set-password` is routed and enforces human self-service or hub-admin reset in its planner.
- The event stream validates its cursor before sending a streaming response. Each worker call drains at most 100 events, closes its snapshot before yielding frames, and immediately redrains a full page. It reauthenticates between batches. Idle streams wait on `asyncio.Event` without a reader or worker. Canonical database ids and subscribe/redrain sequence comparison close the drain/wait race. Disconnect and shutdown unregister subscriptions.
- Company API paths require a ULID. An accompanying `X-Bookflow-Company` must be the same ULID after normalization; mismatch is `E_VALIDATION` before visibility lookup. Header-only company selection through `/commands/<noun.verb>` retains the ordinary selector rules.
- Session and bearer liveness refreshes are throttled to five minutes. A browser session's database expiry and cookie `Max-Age` renew together; SSE does not renew the cookie. Password changes revoke every other session for the target and preserve bearer tokens.
- Hub head `hub0006` adds recoverable pending configuration contents. Company head is `co0004`. Membership grants/denies and capability/feature projections remain compatibility state; enforcement remains role-based until row 7. The planned agent-authority epochs and immutable ledger document/posting contracts are not implemented tables.
- A single-word command (`upgrade`) has no verb: its noun page is its form, and it submits to `/hub/<noun>`.
- `docs generate` is a rootless standalone command: no data root, lock, actor, capability, forwarding, or HTTP route. It renders all registered commands including standalone tooling, validates examples and schema descriptions, and copies packaged prose resources. Generation accepts only an absent, empty, or exactly marked real directory; it refuses symlinks and unrelated trees, validates a sibling stage, swaps it atomically, and restores the previous complete tree if publication fails. `--check` performs a read-only byte/path comparison and reports sorted missing, extra, and changed paths as `E_DOCS_STALE`.
- The cold-start test budgets `bookflow --help` below 300 ms; neither root help nor command discovery imports FastAPI, uvicorn, or the workbench.
- Suite duration and process-cold read timings are diagnostic rather than release budgets. Cold root help retains its 300 ms gate; warm interactive queries retain their independent 100 ms gate. CLI tests record per-command cold timings as test properties for comparison without attributing the entire duration to imports.
- Tests that need a seeded data root copy one built once per session (tests/conftest.py::_seeded_template) rather than running rollout each time. Rollout now runs the company migration chain, applies a chart, and installs the profile seed manifests, about 2.5 s; the copy is about 2 ms and the tree is byte-identical, so each test still gets its own isolated root. This halved the suite, 752 s to 365 s.

## Verified on this machine (Linux, ext4, Python 3.12)

Storage/query correction measurements on Python 3.12.3, SQLite 3.45.1 and SQLAlchemy 2.0.52, with ext4 on a local NVMe drive:

| Operation | Baseline median ms | Correction-tree median ms | Samples per tree |
|---|---:|---:|---:|
| Fresh-root init through Python | 222.6 | 232.3 | 5 |
| Company creation with general chart through Python | 306.5 | 311.6 | 5 |
| First demo seed through Python | 1361.4 | 1434.1 | 5 |
| Replacement demo seed through Python | 1291.2 | 1354.1 | 5 |
| Process-cold CLI company list, one demo company | 547.3 | 553.9 | 7 |

Baseline is commit `319e656`; the measured correction source manifest is SHA-256 `37e68dd165369b592ec70803ef66ffaf5f2e06619becc1d6dfc0fdfd48aa13a8`, taken before the final integration commit. The manifest hashes sorted source paths and contents, excluding bytecode. Measurements alternate tree order after an excluded warmup, use independent disposable roots, and include ordinary commit/metadata work. Python timings exclude import/client setup; CLI timings include a fresh process with warm filesystem caches. These are whole-tree comparisons, not attribution to synchronization alone and not final-tip or cross-platform guarantees. Read-only commands do not exercise FULL commits.

At `fb4ad0e`, focused verification measured process-cold customer list at 784.69 ms best of three and term list at 592.94 ms. The 10,000-customer bounded-query fixture measured summary/reference pages at 40.22/27.78 ms and broad/contact/missing/late-match searches at 84.99/84.74/94.22/80.48 ms; SQL count stayed at nine for both 10 and 200 returned rows. Exact performance is machine/workload dependent; the executable warm-query gate remains 100 ms.

- The complete initialization and company-rollout sequence through the library and the CLI, compared field by field (`tests/test_row1_flow.py::test_library_and_cli_agree`).
- Folder copied to a second data root and attached: identical `company_info`, identical database dump, identical directory listing, original creator's name resolved through `principals`.
- Second process during a command: `E_DB_BUSY` with the holder's command through the CLI and the library.
- File modes under umask 022: every file 0600, every directory 0700.
- Cold start: `bookflow --help` remains inside its 300 ms test budget on this machine (root help loads no command module); `BOOKFLOW_BUDGET_MS` overrides the test on slower machines, and SQLAlchemy is not imported for help.
- Names containing `#`, `%`, `?`, and spaces in company, organization, and data-root names; a `bookflow-core` wheel built with `uv build` carries the `bookflow` import package, currency table, and `bookflow` console entry point.
- The same wheel carries the hand-authored documentation resources and can import the generator directly from the wheel archive to reproduce the complete documentation tree without a source checkout.
- Migration of a behind-head company with a synthetic revision: backup, `migrate` event, marker and projection updated (tests/test_hardening.py::test_synthetic_migration).
- Demo reset repeated on one root; trash accumulates one folder per reset.

HTTP host, local hand-off, and workbench verification in `tests/test_row3_host.py`, `tests/test_row3_local_hardening.py`, and `tests/test_row3_workbench_remediation.py` against a host running in the test process:

- Every routed read command returns the same JSON over HTTP as through the library, compared field by field with ULIDs and timestamps replaced (`test_every_routed_read_returns_the_same_document_over_http_as_in_the_library`).
- Write error documents and their statuses match the library's: `E_VALIDATION` 422, `E_PERMISSION` 403, `E_COMPANY_NOT_FOUND` 404, `E_VERSION_CONFLICT` 409.
- Context keys in a body are `E_CONTEXT_IN_INPUT` naming the header; unknown and local-only names are `E_USAGE` documents.
- A member of one company asking for another by path id or through a workbench page gets the same 404 body as a nonexistent id. A mismatched path/header pair returns the same 422 without looking up either id. No error that member can provoke names a path on this machine.
- Login sets the cookie and returns a validated same-host `HX-Redirect` for workbench submissions; non-GET cookie requests need `X-Bookflow-Workbench: 1`; login and logout are both hub events; the four `E_UNAUTHENTICATED` shapes (no credential, unknown, expired, revoked) each carry `details.reason`.
- A forwarded CLI call is recorded with interface `cli`; forged actor, principal, company, and interface fields are ignored; one-byte frame fragments are assembled; unsafe runtime directories are refused; a forwarded `company use` writes the caller's login table; another uid is refused; `serve` and `init` are never forwarded; a descriptor whose socket refuses, and one whose pid is dead, both fall back to the lock path; a pre-socket version mismatch is named.
- Two reads pass a barrier and finish in under a second while a one-second writer job is active; mutation routing through the writer is detected. Two concurrent updates serialize into consecutive versions. A stale credential read remains non-blocking behind an occupied writer and five concurrent refresh attempts enqueue one job.
- The async stream drains a burst, resumes from `Last-Event-ID`, validates bad cursors as ordinary 422 documents, wakes under lowercase ids, catches a real commit between first drain and subscription, closes readers/subscriptions after a mid-batch disconnect, and leaves the worker pool available with more than 40 idle subscribers. A live `serve` process with an idle stream exits promptly on SIGINT and removes its descriptor and socket.
- Every routed command has a role-authorized form page with one control per input leaf. Clear wins over a prefilled value and unchanged rendered fields send nothing. Preview writes nothing. Ordinary submit returns 303; HTMX submit returns `HX-Redirect` so the successful destination reaches the address bar. A one-use, session-bound result flash survives one GET without entering the URL or cookie. One browser-level journey proves visible presence, overlapping stale-form conflict without overwrite, directive creation, and the resulting HTTP audit entries. Audit routes and invalid filters, picker schema state, restricted actions/presence, and rendered links are covered.
- `--allow-network` gates a non-loopback bind. The tri-state cookie helper proves non-loopback defaults secure and explicit false is retained; a busy port returns the deliberate redacted `E_IO`. `serve` has no write-only CLI flags or dry run.
- A company rewound to `co0001` is migrated at startup, and the company's audit shows the `upgrade` event by the system user with the serving hub admin as `on_behalf_of`.
- After 300 company updates through the writer with a reader attached, the company WAL is under 4 MB once the idle checkpoint runs; `checkpoint_now()` logs a result per connection and `sweep_now()` deletes only sessions expired more than a day, as one `session sweep` event by System with the token hash absent.
- A host built with sub-second timers checkpoints and sweeps on its own, and both threads stop with the host.

Generated-documentation verification in `tests/test_docs_generation.py`, `tests/test_docs_command.py`, `tests/test_docs_schema_metadata.py`, and `tests/test_agent_guide.py`:

- Every registered command, nested input leaf, recursive output field, CLI/context option, exact authorization rule, capability, feature, HTTP locality/route/header, and declared error is projected into its noun page. One input and complete output example per command validates through the corresponding Pydantic model.
- Every current hub and company table and column has a non-empty co-located description. The rendered SQL type, nullability, defaults, keys, indexes, references, and meaning match SQLAlchemy metadata, and the current metadata structure matches databases created from the frozen migration chains.
- Generation is byte-identical across working directories. Freshness detects changed, missing, and extra files without writing; symlink, unrelated-tree, stage-failure, and swap-failure witnesses preserve the original destination.
- The literal standard-library agent-guide program runs against a real ephemeral host and proves an authenticated directive-citing update, overlap conflict without overwrite, retry from the current version, audited actor/interface/directive facts, cursor polling, and SSE resumption. Its bootstrap covers installed and source-checkout invocation, isolated-root and port selection, token capture, HTTP readiness, rerun behavior, receipt verification, orderly shutdown, and guarded cleanup guidance.

## Not verified on hardware

- macOS `statfs` and `renamex_np` branches, Windows drive-type and `MoveFileExW` branches: unit paths only, no real run.
- A refusal on a real network mount: not attempted on this machine (no NFS, CIFS, or sshfs available); `E_NETWORK_SHARE` is exercised with mocked mount tables only.
- macOS `getpeereid`: only the Linux `SO_PEERCRED` path has run.
- Windows local hand-off: named-pipe transport is deferred. The browser and bearer-authenticated HTTP API are the supported Windows paths; the portable forwarding envelope and host handler remain the seam for a later authenticated named-pipe adapter.
- A non-loopback bind with TLS in front of it: the network gate and cookie decision are exercised without a real off-loopback listener or TLS terminator.

- Command-contract verification: two admins updating company info through the library and the CLI (conflict on overlapping fields, merge on disjoint ones, blind-write warning naming the previous writer and principal, dry runs reporting the same), missing-history conflict, null and `--clear`, closing date needs admin, projection repair and `E_PARTIAL_WRITE`, agent reason gate and directives through a dispatch-level agent session, idempotent replay and mismatch, presence never audited, seq cursors, hashed tax id, principals on a copied folder, append-only across the codebase, and the budget fixture at a reduced size (`BOOKFLOW_BUDGET_N=5000` runs the full one; measured ratio printed).

## Known gaps carried to later rows

- No `user add` or `membership grant`; tests insert users through the repository layer (tests/conftest.py::make_actor).
- The currency table holds 155 codes; the remaining ISO 4217 codes are added on request.
- Agents cannot yet exist through any surface; the CLI maps an OS login only to a human user, and tests build an agent session at the point row 7's token resolution will fill (`Session.actor`, `Context.on_behalf_of`).
- The full budget fixture (5,000 creates and 5,000 updates) is run on demand with `BOOKFLOW_BUDGET_N=5000`; it measured audit 8.1 MB against live 1.6 MB, ratio 5.16, in 192 s; the default suite runs 200 rows.
- `--follow` on `audit tail` is CLI-only and polls under the data-root lock every two seconds until the host exists.
- The idle checkpoint is `RESTART`, which resets the WAL but never shrinks the file. A reader that pins a snapshot across a long burst leaves the file at its high-water mark until the shutdown `TRUNCATE`.
- Each writer completion currently attempts a PASSIVE checkpoint across the pooled hub/company handles before reading event watermarks; idle and shutdown checkpoints also remain. This is maintenance, not the FULL-commit durability boundary. Cost as the number of pooled companies grows remains unmeasured; the 4 MB WAL witness above is a fixture observation, not a general storage limit.
- Query continuations currently encode transparent base64 JSON, including the company audit sequence; they are not confidential or authenticated tokens. Every page independently validates scope/query/permission binding and reauthorizes. Before capability-based audit restrictions ship, continuation and stale-error metadata must stop disclosing audit counts to a reader without audit access. Clients must not rely on cursor internals.
- Folder gates currently affect reader admission across the whole root, including credential reads and other organizations' streams. Cursor resumption is tested generally; a folder-move-specific reconnect witness and narrower availability scope remain follow-ups.
- `serve --bind <address>:0` is `E_VALIDATION` before the network gate is reached, because the port range is checked first.
- `E_IDEMPOTENCY_MISMATCH` (409) and `E_INTERNAL` (500) are in the status map but are not exercised over HTTP.
- The generated `/openapi.json` is asserted for shape and coverage, not validated against an OpenAPI schema validator.
