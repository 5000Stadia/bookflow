# Bookflow — architecture

What is built, module by module: package skeleton, registry, data root, hub, organizations, companies, demo, CLI, company audit, versioned writes, presence, idempotency, directives, record notes, the event feed, the host process, HTTP routes, tokens, the POSIX local hand-off, the workbench, immutable domestic journals, accrual trial balance and general ledger reports, and generated command and schema documentation.

## Layout

```
src/bookflow/
  __init__.py            lazy exports: connect, Client, BookflowError, Money (nothing heavy imports at package load)
  bootstrap.py           standard-library console launcher; optional local capture before CLI import; conservative root exclusions even on parser errors
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
    performance.py       finite opt-in local Chrome duration trace; fixed labels, diagnostic-only ancestry, virtual queue lanes, bounded reservations, private Linux export
    fs.py                filesystem type detection (Linux mountinfo; macOS statfs; Windows drive type) and check_local()
    locks.py             RootLock: <data_root>/root.lock, exclusive for the whole command, holder info, BOOKFLOW_LOCK_TIMEOUT
    perms.py             private_umask() (077), is_private_dir()
    moves.py             rename_noreplace() per platform; move_dir() with case-only hop
    lazy.py              LazyModule: command modules import services lazily so the CLI builds without SQLAlchemy
    clock.py             the business time source (now, now_iso, parse_iso); tests replace now; diagnostic elapsed time uses perf_counter_ns separately
    versioning.py        check_update(): versioned, blind, and disjoint-field merge rules; history_from_entries() folds audit diffs to top-level fields
    idempotency.py       input_hash(), lookup() (mismatch, expiry), store() with in-progress state
    audit.py             write_event_to() over either database with seq; snapshot codec; secrets stored as sha256 prefixes
    host.py              Host: holds the root lock as "serve", one writer thread with pooled writable connections, explicit pool release before folder operations, per-request read-only sessions, event-loop subscriptions keyed by database id, deduplicated credential-refresh jobs, host.json descriptor; a timer thread running an idle RESTART checkpoint and an hourly session sweep; begin_shutdown() wakes streams before final stop
    forward.py           stdlib only: private runtime-directory validation, socket_path(), read_descriptor(), call_host(), try_forward() (run() sends every non-bootstrap call to a live host over its Unix socket)
  storage/
    paths.py             data root resolution; display-name normalization and name_key; folder derivation, collision choice, reservation; markers
    engine.py            Database (sqlite3 + SQLAlchemy Core); explicit read-only snapshots, verified WAL/FULL/foreign-key writers, writable-transaction detection and exception-safe cleanup; percent-encoded URIs; create=True only for init/rollout
    traced_sqlite.py      capture-enabled per-connection native subclasses; bounded statement classification, execute/fetch/transaction timing, caller factories preserved
    migrate.py           HEADS constants; classify(); backup via sqlite backup API; migrate_to_head(); Alembic loaded only when migrating
    hub_migrations/      Alembic chain "hub": hub0001 (frozen explicit tables), hub0002 (seq, directive_code, idempotency_keys), hub0003 (capability/feature metadata), hub0004–hub0005 (list capabilities), hub0006 (pending config projection), hub0007 (note capabilities), hub0008 (attachment/activity capabilities), hub0009 (agent principal assignments, authority epochs and credential conversion), hub0010 (ledger and report capabilities)
    company_migrations/  Alembic chain "company": co0001 (frozen), co0002 (audit/presence/directives), co0003 (20 supporting lists), co0004 (job delivery inheritance), co0005 (notes), co0006 (attachments, links, collection intent, byte limit), co0007 (journal identities, immutable revisions and postings, numbering prefix, private report cursor key)
    migrate.py           + migrate_company(): the one owner of company migrations: migrate entry by the system user, baseline entry, marker, hub projection entry
  hub/
    schema.py            users, api_tokens, agent_principals, agent_authority, organizations, companies, memberships, role_capabilities, features, audit_events, audit_entries; co-located table and column descriptions
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
    records.py           explicit persistent annotation targets, including inactive list and owned-child identities; selected-company primary-key lookups
    attachment_store.py  bounded SHA-256 scan, owned invocation staging, verified no-replace publication and held-descriptor reads
    attachment_gc.py     bounded durable collection intents, recovery and orphan discovery
    query.py             strict bounded query inputs, reference rows, scoped/permission-bound continuation contract
    query_providers.py   SQL-first noun selection and summary/reference projection; audit-watermark continuation validation
  commands/
    common.py            OrganizationOutput, CompanySummary builders, Empty/ListInput/NameInput
    hub_cmds.py          init (bootstrap path run_init), upgrade, organization new/list/show/rename, company new/list/use/attach/detach, demo reset, hub audit list/show
    company_cmds.py      company show (+ info_version, editing_by), company rename, company update, directive add/list/show/deactivate, presence set/clear
    audit_cmds.py        audit list/show/tail (company) and hub audit list/show/tail, one implementation over either database; seq cursors; per-entry visibility
    note_cmds.py         note add/show/edit/list; existing audit/retry pipeline, required edit versions, bounded body and keyset pages
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

Hub username resolution uses `hub/users.py`: Unicode NFC and case folding through a connection-local SQLite function, at most two candidate rows, and no match for ambiguous names. This lookup scans the users table; no schema migration or stored-name rewrite is required. Login resolves across all user kinds and active states before enforcing human/active status, verifies the password outside the read snapshot, then rechecks the username, user ID and password hash in the writer transaction before issuing a session. Password and token self-service resolves the selected account ID before allowing a case-variant username. Human creation rejects existing normalized names. OS-login mappings and passwords retain case sensitivity.

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
- Hub head `hub0009` adds assigned agent principals, authority epochs and token epoch bindings. Legacy agents become suspended without inferred assignments; existing agent tokens are revoked. Conversion and its safe system audit event commit in the same migration transaction. Human credentials and history survive. Hub head `hub0010` adds domestic ledger and report capabilities. Company head `co0007` adds journal document and posting history with retention triggers and a journal number sequence.
- `hub/credentials.py` validates active identities and agent principal/assignment/suspension/epoch state for token authentication and issuance. Agent token principal and epoch are stored with the hash in one insert. Membership grants/denies and capability/feature projections remain compatibility state; full role/capability intersection, atomic membership reductions, reauthorization commands and publication fences remain Row7 integration work.
- `company/journal_models.py`, `journals.py` and `journal_outputs.py` validate and project domestic journals with two through 200 positive, balanced entered lines. Stable headers retain immutable revisions and commercial lines. Posting batches and accounting lines retain exact source allocations. Corrections reverse the previous batch at its original accounting date and append a replacement; void appends an exact reversal and retains the header. SQLite rejects modification/deletion of historical rows and header deletion. Period checks and full-aggregate version conflicts run again in the writer transaction.
- Journal numbering, header pointers, principal snapshots, revisions, posting effects, audit and idempotency share the company transaction. Ledger services preallocate their audit ID and return `Applied(audited=True)` without committing. Dispatch rolls a ledger no-op back to its business savepoint before retaining its retry result, so a ledger no-op does not refresh company principals. Ordinary list no-ops retain their principal-mirroring and projection-repair behavior.
- `company/ledger_reports.py` reads all effective posting batches, including reversals and replacements. Trial balance nets each account; general ledger pages opening/posting/closing rows with running balances computed before slicing. Lossless integer aggregation and numeric text collation avoid intermediate SQLite integer overflow and floating-point conversion. Public values outside signed 64-bit range fail with `E_VALUE_RANGE`. Report continuations authenticate complete state and first-page metadata with HMAC-SHA256 using a private company-local 32-byte key. Signature verification precedes decoded-account use. They bind filters, identity and relevant change watermarks, preserve first-page metadata, and restart with `E_QUERY_STALE` on relevant changes. The key is generated in migration, copied with the company, and excluded from commands, audit snapshots and annotation targets.
- Account show/list/query derive the account's own normal-side balance from posting lines and real history dependencies. Descendants are not rolled up. A posted account cannot be deactivated. Journal browser pages display historical line snapshots, balanced totals, revision links and stable-header notes/files; generated update forms preserve line identities and shown versions.
- The demo includes opening capital, a corrected service journal, an expense and a voided duplicate. Its accrual trial balance as of 2026-12-31 is 640000 USD minor units on each side. Foreign-tagged posting, exchange-rate commands, transaction custom-field values and the functional register remain following increments. Fine-grained identity and publication controls remain unfinished identity work.
- A single-word command (`upgrade`) has no verb: its noun page is its form, and it submits to `/hub/<noun>`.
- `docs generate` is a rootless standalone command: no data root, lock, actor, capability, forwarding, or HTTP route. It renders all registered commands including standalone tooling, validates examples and schema descriptions, and copies packaged prose resources. Generation accepts only an absent, empty, or exactly marked real directory; it refuses symlinks and unrelated trees, validates a sibling stage, swaps it atomically, and restores the previous complete tree if publication fails. `--check` performs a read-only byte/path comparison and reports sorted missing, extra, and changed paths as `E_DOCS_STALE`.
- The cold-start test budgets `bookflow --help` below 300 ms; neither root help nor command discovery imports FastAPI, uvicorn, or the workbench.
- Suite duration and process-cold read timings are diagnostic rather than release budgets. Cold root help retains its 300 ms gate; warm interactive queries retain their independent 100 ms gate. CLI tests record per-command cold timings as test properties for comparison without attributing the entire duration to imports.
- Tests that need a seeded data root copy one built once per session (tests/conftest.py::_seeded_template) rather than running rollout each time. Rollout now runs the company migration chain, applies a chart, and installs the profile seed manifests, about 2.5 s; the copy is about 2 ms and the tree is byte-identical, so each test still gets its own isolated root. This halved the suite, 752 s to 365 s.

## Verified on this machine (Linux, ext4, Python 3.12)

Record notes use a company table and target/id index. Bodies are at most65,536
UTF-8bytes; pages contain at most200 notes and262,144 body bytes. Edits preserve
original attribution and immutable audit history. Notes and attachment links do
not change their target's version or prevent soft retirement. The workbench's
shared Notes, files and activity panel appears on record details and existing
record forms, including customer/job workflows. Annotation saves preserve unsaved
master fields. Readonly users have read/download controls; stale note edits retain
the draft. Activity pages read immutable audit snapshots, limit SQL candidates
before decoding, batch actor names, and bind chronological continuations to a
fixed high-water and the authorized target/filter scope.

Attachment commands share registry TransferDescriptor metadata and one binary
resource outside JSON. Authorization-only preparation validates company, target,
role, reason/directive and company limit before consuming bytes; final execution
rechecks authority and binds actual digest/size to idempotency. The company limit
is configurable through company update, default25,000,000bytes, maximum100,000,000.
Uploads retain first-upload metadata for duplicate bytes; active links are unique
per attachment/target, and unlink/relink preserves separate historical occurrences.
No-op writes retain successful retry results without extra audit events.

`OwnedStage` registers retryable cleanup before initialization or reading.
Incremental writes are at most65,536bytes. Dry runs hash without creating a stage.
Publication reverifies, fsyncs and closes the staging writer before a no-replace
hard link, then synchronizes shard/store directories. Existing digest paths are
verified and synchronized too. Rollback removes only the invocation's temporary;
published bytes can remain as orphans. `open_verified` returns a read-only held
file descriptor after exact digest/size verification. Symlinks and non-regular
bodies are refused; unsupported hard-link filesystems fail closed.

Hosts admit eight transfers globally and two per principal, with a300-second
absolute lifetime and30-second transport inactivity limit. Leases hold no database
connection. Preparation reserves a lease while its authorized reader is still
admitted, then closes the snapshot. Downloads use short authorization rechecks
between chunks. Accepted writer jobs own their staged resources through database
cleanup even when callers time out; failed cleanup retains slots and root ownership
for retry. Standalone execution holds RootLock through transfer and cleanup.
HTTP cancellation marks the request owner closed immediately. An active preparation,
read or write worker retains its resources until the actual worker returns and then
cleans them; cancelling its async waiter cannot abandon a stage or close an active
descriptor. Accepted writer jobs retain their existing ownership through completion.
Shutdown cancels caller I/O and retains the root lock while owners remain active.
Arbitrary synchronous Python streams and cleanup callbacks must cooperate; they
cannot be forcibly interrupted.

Python accepts input_stream/output_stream separately from command JSON. CLI opens
paths only on the calling machine, defaults upload filename/MIME from that path,
and publishes verified downloads through a private temporary and no-replace final
link. HTTP transfers use raw authenticated bodies and bounded X-Bookflow-Input
base64url metadata; output includes no-store/nosniff, disposition, size, SHA-256 and
X-Bookflow-Output typed metadata. Local forwarding validates kernel identity and
metadata before ready, frames chunks at65,536bytes and requires successful final
JSON after the terminal body frame. Interrupted forwarded writes never fall back
to standalone execution after transmission begins. Browser downloads verify actual
size and SHA-256 before handing a file to the browser; local HTTP uses the bundled
SHA-256 fallback when SubtleCrypto is unavailable.

Company compact is owner/admin-only, processes at most200 bodies and retains all
attachment/link/audit history. Root-wide filesystem exclusion drains existing
readers and transfers before candidate selection. A bounded durable intent commits
before body removal; completion synchronizes directories, marks metadata collected,
writes one original-context audit/idempotency result and clears intent atomically.
Recovery precedes new attachment write admission and never runs while owning a
transfer lease. Dry runs never recover or delete; pending recovery is E_DB_BUSY.
Orphan discovery examines at most512 directory entries per invocation, with a
continuation in completion audit snapshots. Directory-cookie discovery is supported
on64-bit Linux; other platforms report incomplete discovery. Cookies are local
filesystem continuation hints; directory replacement restarts discovery and a
completed traversal wraps to the beginning. Collected bytes require a fresh
verified upload before linking again. Company copies include the attachment folder.
The demo seed attaches a packaged one-page PDF and comments to a customer, account
and company using the registered command path.

The notes checkpoint passed the complete correctness suite: 759 tests in 484.93 seconds, with 11 dependency/schema-order/JUnit-property warnings and 95 generated documentation files. The same run recorded cold root help at 219.98 ms, company list at 532.38 ms, customer list at 779.55 ms and term list at 596.80 ms; cold reads are diagnostics, while root help and the 10,000-record warm-query gate passed their strict limits. Customer/job and nested reference workflows have real-Chrome desktop/narrow-screen witnesses. Human browser acceptance of the wider list workbench remains pending.

Local tracing has 40 recorder, SQLite and integration witnesses, including original-error/durable-result parity, CLI parser rejection, private export, exhausted captures, HTTP cleanup and late queued writes. A synthetic exported capture opens in Perfetto UI v58.3 with all nine slices on three separate caller/writer/virtual-wait tracks and zero parser errors.

Alternating traced/untraced in-process host operations on one disposable seeded root produced these measurements (12 samples per mode after per-host warmup; no concurrent tests):

| Operation | Off median / maximum ms | On median / maximum ms |
|---|---:|---:|
| Bounded customer query | 20.59 / 23.61 | 21.70 / 24.88 |
| Customer show | 19.73 / 22.53 | 21.48 / 115.71 |
| Versioned customer update | 17.37 / 36.14 | 19.58 / 105.49 |

These small-fixture host calls include session setup/cleanup but exclude host startup and network transport; they do not replace the strict 10,000-record fixture. With 12 samples, nearest-rank p95 equals the observed maximum. Each enabled host capture contains 462 events with no drops or unfinished reservations. Export after host shutdown costs 4.09 ms median and 9.46 ms maximum. Five alternating cold-process samples per mode give help medians 229.81/234.89 ms and company-list medians 527.38/553.83 ms (off/on, including export). Cold maxima are 236.06/243.01 ms and 537.87/560.65 ms respectively. An initial independent run gave warm off/on medians of 21.13/21.22 ms for query, 20.08/21.86 ms for show, and 16.72/18.98 ms for update, with enabled show/update maxima of 131.13/103.61 ms. In the repeat, both large enabled outliers coincided with generation-2 garbage collection; no exclusive attribution or enabled tail-latency guarantee is claimed. Trace allocation can shift collection timing. Normal budgets remain untraced.

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

Company and hub navigation cards place wrapping action links below each noun label. Grid columns shrink to the available width. Real-Chrome checks in `tests/test_row5_browser_acceptance.py` assert that headings and links remain inside their cards at viewport widths from 280 to 1280 pixels.

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
- Agent creation, assignment and reauthorization commands are not yet exposed. Existing agents remain suspended after upgrade. Credential invariant tests provision eligible authority explicitly through isolated repository fixtures; direct dispatch reason/provenance tests also construct internal agent sessions. CLI/Python token mode and full current-authority execution/publication fencing remain Row7 work.
- The full budget fixture (5,000 creates and 5,000 updates) is run on demand with `BOOKFLOW_BUDGET_N=5000`; it measured audit 8.1 MB against live 1.6 MB, ratio 5.16, in 192 s; the default suite runs 200 rows.
- `--follow` on `audit tail` is CLI-only and polls under the data-root lock every two seconds until the host exists.
- The idle checkpoint is `RESTART`, which resets the WAL but never shrinks the file. A reader that pins a snapshot across a long burst leaves the file at its high-water mark until the shutdown `TRUNCATE`.
- Each writer completion currently attempts a PASSIVE checkpoint across the pooled hub/company handles before reading event watermarks; idle and shutdown checkpoints also remain. This is maintenance, not the FULL-commit durability boundary. Cost as the number of pooled companies grows remains unmeasured; the 4 MB WAL witness above is a fixture observation, not a general storage limit.
- Query continuations currently encode transparent base64 JSON, including the company audit sequence; they are not confidential or authenticated tokens. Every page independently validates scope/query/permission binding and reauthorizes. Before capability-based audit restrictions ship, continuation and stale-error metadata must stop disclosing audit counts to a reader without audit access. Clients must not rely on cursor internals.
- Folder gates currently affect reader admission across the whole root, including credential reads and other organizations' streams. Cursor resumption is tested generally; a folder-move-specific reconnect witness and narrower availability scope remain follow-ups.
- `serve --bind <address>:0` is `E_VALIDATION` before the network gate is reached, because the port range is checked first.
- `E_IDEMPOTENCY_MISMATCH` (409) and `E_INTERNAL` (500) are in the status map but are not exercised over HTTP.
- The generated `/openapi.json` is asserted for shape and coverage, not validated against an OpenAPI schema validator.
