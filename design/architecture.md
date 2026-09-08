# Bookflow — architecture

What is built, module by module: package skeleton, registry, data root, hub, organizations, companies, demo, CLI, company audit, versioned writes, presence, idempotency, directives, record notes, the event feed, the host process, HTTP routes, tokens, the POSIX local hand-off, the workbench, immutable domestic journals, accrual trial balance and general ledger reports, and generated command and schema documentation.

## F14 durable payment recovery (implementation candidate; independent review pending)

The company-only co0018 migration follows co0017 and adds four recovery tables.
`payment_recovery_models.py` defines a complete canonical attempted edit, including
immutable local A and shared B baselines. `payment_recovery.py` owns upload, seal,
comparison, publication, abandonment and replacement. Eleven ordinary registry
commands expose these operations through every adapter. The active pointer blocks
ordinary selection mutation and fresh financial consumption until the entire
attempt is applied or explicitly discarded. Publication produces one next revision
of the original selection; recovery never creates a replacement cash intent.

Immutable per-action request/receipt snapshots include ordinary reason identity.
Current complete-graph authorization precedes both transport-cache and permanent
receipt recovery. A writer that loses an exact-action race rolls back its transaction,
including principal/cache maintenance, before returning the original receipt. The
historical authority registry includes received attempted invoice targets even when
removed, aborted or superseded, and follows the original consumed operation.

Comparison uses the existing exact calculator only for explicit new calculate
entries. Entered and authenticated A/B calculated amounts remain fixed; derived
headers are stored as the final resolved sum or null. Every current fact page and
publication binds the complete generation and relevant dependency fingerprint.
The workbench commits the whole intent to IndexedDB before begin, resumes immutable
acknowledgements, and requires explicit complete comparison confirmation. Main and
reference seeds append three active nonfinancial workflow examples, preserving the
original command prefixes and financial deltas. F14/Row22 closure still requires the
full timing, interruption and independent artifact acceptance recorded in the
Builder handoff; this section does not claim that review has passed.

## Row22 receipt and settlement increment (implementation candidate)

The co0014 migration preserves existing raw rows and local schema extensions while
adding payment preferences, immutable payment components, applications and allocation
evidence, durable operation recovery, and shared selection history. Its frozen DDL
and guards are independent of current metadata. `payment_schema.py` and
`payment_guards.py` describe the current model; `payment_validation.py` independently
checks positive monetary legs, exact-party capacity, attribution and proportional
allocation before persistence.

`payment_calculations.py` owns integer allocation and entered/calculated draft
semantics. `payment_selection.py` persists reusable drafts without financial effects;
`payment_preparation.py` discovers invoices and exposes separately labelled payer
and family net AR. Page bounds limit delivery, not the size of one remittance.
`payments.py` implements receipt creation and application of existing exact-party
credit. Settlement-only header changes retain commercial revisions and postings.
`payment_pages.py` recomputes prospective pages from the original typed intent and
facts fingerprint, and reads immutable committed operation items.

`payment_operations.py` and the narrow `Command.permanent_recovery` dispatch hook
recover an authorized exact permanent retry before the new-write reason gate,
without writing presence, provenance or financial state. `payment_authority.py`
uses existing permissions for the complete historical payment/work graph and
composite audit evidence. CLI commands share these models and services; no adapter
implements separate accounting behavior.

`payment_corrections.py` replaces receipt content with exact old-date reversals and
new balanced postings, changing only payer capacity while retaining permanent job
ownership. It restates source attribution at original application dates without
changing target splits. Explicitly retaining a reference preserves its captured
facts; new assignments require active masters, and replacement posting accounts
still require activity. `payment_invoice_corrections.py` coordinates commercial
invoice edits and immutable settlement evidence in the same audit/transaction;
`payment_restatement.py` compares complete chronological allocations by durable
line ordinal and semantic net/tax facts. Unchanged allocations keep their old
physical revision references. `payment_cancellation.py` unapplies exact current
allocations and requires explicit unapply before receipt void. A new no-effect
operation has its own audit receipt; exact permanent replay has no new writes.

`payment_dependencies.py` signs bounded audit-baseline guards and reconstructs
owned headers in batches. Comparisons resolve each event's owned commercial
revisions to actual business fields, distinguish its actor from the latest writer,
and mark malformed or unowned history unknown. `payment_history.py` exposes
immutable receipt/application/allocation chains and effective-date projections
under current recorded knowledge, separately from all committed current capacity.
Projection basis, generation time and audit watermark identify that distinction;
pre-obligation and voided states are not labelled paid. Permanent request matching
normalizes exact money through owning input-model types, retaining omissions,
ordered intent and unrelated custom data. All preparation continuations bind
relevant payer and selected-party lineage independently of unrelated draft writes.
Current payment queries aggregate and filter in SQL before bounded delivery;
prospective cursors instead bind exact original intent, financial fingerprint and
last logical item identity without persisting a preview.

`adapters/workbench/payments.py` installs receive/apply/correction, payment query,
shared-selection, application-history and dated invoice-settlement pages over the
same registry reads. `payments.js` persists amount/row origins through shared
selection commands, retrieves complete prospective effects before enabling Save,
and retains the exact submitted operation across ambiguous response loss. The
form stays locked until recovery resolves. It uses the existing 200-row public
read limit for a fresh prospective continuation recipe without changing financial
intent, its fingerprint or the 50-row initial descriptor. No browser allocation
calculator or permission system is introduced. `invoice-settlement.js` retrieves
the complete invoice-correction recipe; the ordinary editor retains its commercial
draft while explicitly reviewing current settlement versions. Captured receipt
facts, internal printing and current availability remain distinct. Company forms
expose all three payment preferences independently. Numeric-entry metadata uses
the existing exact calculator, including calculator-only Enter. `exact-json.js`
preserves accepted integer minor units as BigInt across JSON reads, shared-draft
writes and formatting, including values above JavaScript's safe-integer range.
The candidate grid exposes original/current revision identities and gross amounts
separately. Payer edits invalidate executable previews and draft-derived displays;
Save & New clears those displays while retaining the documented defaults.
Stale review renders saved/current money and retained entries, retrieves every
bounded diagnostic page, and distinguishes change-event actors from latest writers.
Every receipt-intent construction checks that the visible payer is resolved, even
when a shared selection already exists. Rejected selection patches remain separate
from the saved baseline and current shared revision. Review compares all three,
rebases only those patches onto the explicitly reviewed version, and retains other
writers' row edits. A second concurrent change requires another review. Pending
edits cannot be discarded by refresh, clear or suggestion controls. Workbench
actions serialize with visible busy/queued feedback instead of dropping activation.
Invoice and funding baselines refresh together in a bounded selection update.
When the stale graph exceeds that update's public bound, explicit review creates
a complete recovered shared selection through ordinary bounded preparation
commands, retains the original selection, and identifies the replacement in the UI.
Consumed selections cannot enter that copy path. Existing-credit forms distinguish
the retained allocation budget's unallocated amount from current available payment
credit; preview continues to use the authoritative settlement projection.
Invoice correction pages render proposed received/applied/available or gross/applied/due
balances for each identifiable affected document before Save becomes available.

Preparation candidates and saved-draft query pages batch their SQL projections;
history filters operation ownership before decoding recorded effects. Composite
audit disclosure batches owned-row reads within one decision and database snapshot,
retaining unresolved-evidence rejection and historical work-link checks. These are
read-only changes; the separate complete 10,000-record calibration remains required.

The active payment examples append exactly33 commands to each frozen295/201 demo
prefix. `payment-expected.json` enumerates independent gross/net/storage oracles;
each company gains bank17000, AR1000 and income18000 cents. Raw prior financial
and audit rows remain unchanged. `docs/customer-payment-workflows.md` maps
CP01–CP33 to implemented receipt controls and required dependent increments.

Row23's reviewed execution-credential revalidation is integrated here. This
candidate does not complete Row22: independent GUI acceptance, calibrated
interactive query budgets, actual integrated Row9 MCP journeys and the fresh
blind-user exercise/interview remain required. Parent owns integration and
acceptance; receipt-core completion does not close the comprehensive goal.

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
    hub_migrations/      Alembic chain "hub": hub0001 (frozen explicit tables), hub0002 (seq, directive_code, idempotency_keys), hub0003 (capability/feature metadata), hub0004–hub0005 (list capabilities), hub0006 (pending config projection), hub0007 (note capabilities), hub0008 (attachment/activity capabilities), hub0009 (agent principal assignments, authority epochs and credential conversion), hub0010 (ledger and report capabilities), hub0011 (customer-work read/write role defaults)
    company_migrations/  Alembic chain "company": co0001 (frozen), co0002 (audit/presence/directives), co0003 (20 supporting lists), co0004 (job delivery inheritance), co0005 (notes), co0006 (attachments, links, collection intent, byte limit), co0007 (journal identities, immutable revisions and postings, numbering prefix, private report cursor key), co0008 (journal header custom ownership), co0009 (commercial sales), co0010 (nonposting customer work and preserving custom scope CHECK widening), co0011 (immutable linked billing and preserving sales amount-price widening), co0012 (exact progress allocation proofs, fractional sales quantities and overlap guards), co0013 (company work preferences)
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
  demo/reference.toml    Opt-in Reference Plumbing Co, explicit 2026 journals
  demo/reference-expected.json  Independent monthly/annual balances and gross movements
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
- `hub/credentials.py` validates active identities and agent principal/assignment/suspension/epoch state for token authentication and issuance. The JSON HTTP/workbench command executor revalidates the admitted bearer or cookie through that same verifier inside the actual writer or reader session, before planning, preview, replay or effects. The private credential handle also checks that token id, user id, kind and principal match admission; no secret enters business input, Context or audit. This execution check preserves authorized self-revocation and does not claim a post-execution publication fence. Agent token principal and epoch are stored with the hash in one insert. Membership grants/denies and capability/feature projections remain compatibility state; full role/capability intersection, atomic membership reductions, reauthorization commands and publication fences remain Row7 integration work.
- `company/journal_models.py`, `journals.py` and `journal_outputs.py` validate and project domestic journals with two through 200 positive, balanced entered lines. Stable headers retain immutable revisions and commercial lines. Posting batches and accounting lines retain exact source allocations. Corrections reverse the previous batch at its original accounting date and append a replacement; void appends an exact reversal and retains the header. SQLite rejects modification/deletion of historical rows and header deletion. Period checks and full-aggregate version conflicts run again in the writer transaction.
- Journal numbering, header pointers, principal snapshots, revisions, posting effects, audit and idempotency share the company transaction. Ledger services preallocate their audit ID and return `Applied(audited=True)` without committing. Dispatch rolls a ledger no-op back to its business savepoint before retaining its retry result, so a ledger no-op does not refresh company principals. Ordinary list no-ops retain their principal-mirroring and projection-repair behavior.
- `company/ledger_reports.py` reads all effective posting batches, including reversals and replacements. Trial balance nets each account; general ledger pages opening/posting/closing rows with running balances computed before slicing. Lossless integer aggregation and numeric text collation avoid intermediate SQLite integer overflow and floating-point conversion. Public values outside signed 64-bit range fail with `E_VALUE_RANGE`. Report continuations authenticate complete state and first-page metadata with HMAC-SHA256 using a private company-local 32-byte key. Signature verification precedes decoded-account use. They bind filters, identity and relevant change watermarks, preserve first-page metadata, and restart with `E_QUERY_STALE` on relevant changes. The key is generated in migration, copied with the company, and excluded from commands, audit snapshots and annotation targets.
- Account show/list/query derive the account's own normal-side balance from posting lines and real history dependencies. Descendants are not rolled up. A posted account cannot be deactivated. Journal browser pages display historical line snapshots, balanced totals, revision links and stable-header notes/files; generated update forms preserve line identities and shown versions.
- `demo reset` accepts the public boolean `include_reference` (default false), with nullable reference company ID/name output. Opt-in creates both companies in the replacement demo organization through the same rollout and command seed paths. Reset always trashes the entire prior demo organization. After-commit seed failures raise `E_PARTIAL_WRITE` with the durable organization/company identities and incomplete company; previously committed seed commands remain saved. The packaged reference-year guide documents source arithmetic and twelve monthly checkpoints, including correction/void gross movements and second-half opening balances.
- The demo includes opening capital, a corrected service journal, an expense, a voided duplicate, bank payments and receipts, a credit-card charge and payment, and a corrected mixed split with a net payment of 10000 USD minor units. Its accrual trial balance as of 2026-12-31 is 663000 USD minor units on each side. Foreign-tagged posting and exchange-rate commands remain following increments. Fine-grained identity and publication controls remain unfinished identity work.
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
- Row 5 list documentation now marks deliberate divergences from the anchor as project choices rather than parity (blueprint 11.8, 11.12, 11.13, 11.15): the shipping-method and customer-message seed sets, the customer-message `name`, five-level categories, the 45-per-record-type custom-field floor, typed custom-field kinds, and live per-item price percentages. Still staged, with their owning passes named in the blueprint: item-level default units (11.14), rounding presets and the per-item bulk price calculator (11.13), person-derived sales-rep initials (11.12), automatic class-prompt linkage (11.8), an activate cascade (11.1), and renamable job-status labels (11.2, company-settings increment). The other-name conversion history consequence is stated in 11.6. None of these changes application behaviour or data.


### Account registers

`company/register_models.py` defines complete entry, allocation, calculator and
receipt contracts. `registers.py` resolves them to domestic journal lines again
inside the writer transaction and calls `journals.persist_prepared` with the
outer command name. One audit event covers each changing register write.
`register_query.py` combines an authenticated general-ledger page with immutable
revision summaries, normal-side movements and a separate all-date balance.
Period metadata stays frozen across pages; the all-date balance has its own
fresh generation time and audit watermark. Register cursors bind the query,
permissions, company and current account display facts with a private company key.

`adapters/workbench/register.py` installs balance-sheet account register routes.
The register browser composer posts through the same command API and shows the
receipt in place. Split allocations retain their entered identities and explicit
class inheritance modes. General journals outside the editable register shape
remain readable and link to the journal editor. The browser retains one bounded
pending write per tab, including its payload and retry key, before submission;
uncertain results require resolving that same intent before another write.


### Ledger performance measurements

Audit entry snapshots are encoded in their existing format and inserted as one
parameter batch inside the caller's write transaction. Each touched record keeps
its individual audit entry ID, event ID, versions and before/after snapshots.
An error partway through entry insertion rolls back the event and business write.

With 10,000 accounts and over 100,000 posting lines on the local SSD, twenty warm
resident-host samples of a 20-line journal have a median of 36.67 ms and maximum
of 39.29 ms. Host startup/shutdown are outside the samples; dispatch, current
permission checks, writer validation, audit and durable commit are inside.
Repeated offline library calls include per-call connection open/close and
checkpoint work; their median is 63.37 ms, maximum 123.46 ms across twenty samples.

Historic storage-correction cost (2026-09-04), an older and different workload from
the ledger fixture above and not comparable to it: switching writable opens to
`synchronous=FULL` with durable metadata replacement was measured on this machine
against the pre-change tree using five fresh-root samples per operation and seven
process-cold reads. Medians moved from 222.6 to 232.3 ms for a fresh-root `init`
(+9.8 ms), 306.5 to 311.6 ms for `company new` with the general chart (+5.1 ms),
1,361.4 to 1,434.1 ms for a first `demo reset` (+72.7 ms), 1,291.2 to 1,354.1 ms for
a replacement `demo reset` (+62.9 ms), and 547.3 to 553.9 ms for a process-cold
`company list --json` (+6.6 ms; slowest sample 571.2 ms against the 750 ms bound).
These are whole-tree deltas, not isolated fsync attribution.

Six library trial-balance reads over 100,514 posting lines return the independently
expected 5,665,000 USD minor units on each side. The five subsequent warm samples
have a median of 1,140.13 ms and maximum of 1,154.92 ms. This fixture uses 500
200-line journals posted through public commands and a bulk-created master-data
fixture. Its database occupies 441,896,960 bytes. This is not the separate
100,000-transaction storage-budget witness, which remains outstanding.


## Journal custom fields

`company/journal_custom_fields.py` plans and validates header values for the
`journal_entry` scope through the shared custom-field owner-slot service.
Journal and register writes accept patches keyed by definition ID. The writer
checks the original revision, effective slots, captured metadata and typed values
before writing any effects. Slot changes, the immutable revision, reversal and
replacement postings, audit entries and the retry receipt commit together.

Each revision captures the definition and value IDs, kind, name, position,
definition version, canonical value and applicable choice ID and display label.
Unchanged values retain those facts unless `refresh_defaults` explicitly captures
current metadata. `journal show` and `journal history` project the selected
revision without live definition lookups. Clearing retains an inactive value
slot; setting it again reuses its ID. A custom-field-only correction creates a
normal revision and balanced reversal/replacement with zero net account change.

The demo includes Work order and Source values, a metadata rename with preserved
historical labels, and a split payment whose work order is corrected, cleared
and restored through the same value slot.


Journal/register custom-field kind expectations are part of the shared command
input and are checked against the writer's current definitions. Browser drafts
carry the original kinds through preview, errors and uncertain retries. A local
explicit current-type action permits a deliberate reinterpretation. The final
aggregate validator separately checks the custom plan's patch, creating state
and refresh flag against the original prepared command.

With custom-field planning enabled and no custom values supplied, 20 resident-host
samples of a 20-line journal on the same 10,000-account fixture have median
43.57 ms and maximum 45.84 ms. Garbage collection is enabled. These samples
exclude host startup and include dispatch, authorization, company writer, audit
and commit. They do not measure a journal populated with dozens of custom values.

## Pure captured-policy tax calculation

`company/tax_calculations.py` computes exact tax from resolved nonnegative line
nets, captured flat rules, document-local tax ordinals and one home currency.
It implements separate-component half-even, line-combined half-up and
invoice-combined half-up. Compatible invoice buckets compare economic rule
identities, rates, agencies and accounts; descriptive provenance is retained
without changing bucket membership. Combined amounts are distributed by exact
remainder, stable tax ordinal and binary tax-item identity. Zero cells remain
visible. Intermediate integers are unbounded; persisted monetary totals are
checked against signed64 bounds at every aggregation level.

Sales posting and correction now resolve nets/rules before a complete tax pass.
`tax_policy.py` owns the three policy values and field-specific explicit/default/
legacy_implicit origins. `tax_attribution.py` assigns prospective immutable tax
ordinals independently of settlement keys and recomputes each bucket/cell from
captured commercial nets and rules during effect validation. The calculator's
result models remain structural projections, never persisted-effect validators.

Company co0015 appends the policy default and six immutable tax tables without
rebuilding existing tables. Existing companies retain line_component_half_even;
rollout explicitly selects invoice_combined_half_up. SalesProfile v1 bytes remain
readable unchanged; v2 requires captured policy/origin. SalesLineProfile pricing
versions stay 1/2/3. Revision-owned attribution snapshots and composite line
mappings contain exact cells and stable tax ordinals outside pricing facts. New
live revision outputs expose tax_calculation_details; old stored retries omit
that projection when it was absent. Semantically unchanged legacy corrections
write no commercial revision or tax keys. Ordinary changed legacy revisions
capture legacy_implicit until explicit policy/default selection.

Sales updates retain captured policy; use_defaults reselects the company policy,
and refresh_defaults only refreshes default origin. Reversals use stored cents.
The existing payment correction hook consumes revised component cents and retains
its own immutable settlement keys, cash, version guards and permanent recovery.
Work writers capture policy and origin in WorkFacts2/profile2, with WorkLineFacts2
for document-allocated cells. Exact integer discriminators and matching root/profile
versions are checked before normalization. WorkFacts1/profile1 and legacy line
arithmetic remain distinct. Changed legacy agreements may capture their implicit
policy without rewriting old rows; linked legacy lines retain their original
version1 basis including ordered component cents.

Basis2 contains captured economic agreement and complete ordered tax rules, excluding
only the specified operational fields and derived tax/gross. Allocation3 explicitly
identifies this basis; allocation1/2 retain their legacy meaning. Co0016 performs the
reviewed narrow preserving rebuild of work_billing_allocations, widening three CHECKs
and the exact allocation-shape discriminator. Frozen DDL, known guards, rowid/storage
class/quote/bytes comparisons, restored local DDL, integrity and FK checks bound the
migration. The existing migration runner restores FK enforcement after success or
rollback. Co0015 and earlier migrations remain unchanged.

Work conversion, progress, released-span rebilling and linked sale corrections use
the captured source policy and destination tax ordinals. Economic source protections
remain; compatible independent scope can redistribute derived cells on retained
lines. Existing receipt confirmation predicates and complete payment restatement
apply to the new exact gross/components. Historical postings and basis facts remain
immutable. Copy and relocation preserve captured knowledge and permanent recovery.

Remaining forecasts stream complete free intervals without constructing an oversized
posting selection. They calculate all remaining billable nets together in current
source order, return prospective destination ordinals, exact attribution, fingerprint
and bounded eligibility reasons. The 200-span line/2000-span conversion caps still
apply to execution. Prior and cumulative figures are actual postings. Bounded net
recovery and fewer-complete-line recovery remain explicit, including progress-disabled
companies; forecasts reserve no entitlement.

Shared descriptions and browser details expose policy/source, stable tax order,
captured rules, bases and allocated cents. Print opens marked accounting disclosures
for printing and restores their prior state afterward. Active invoice examples add
AR33/income30/tax3 cents, and the active work chain adds AR6/income5/tax1 cents per
seed company. Exact preexisting manifest bytes remain a prefix. Independent review
and parent-owned combined-candidate acceptance are required; this implementation
description does not close Row24.

## Manual rates and foreign journal conversion

Company migration co0008 adds the declared versioned exchange_rates table.
Rate set/show/query share company dispatch, current ledger permissions, exact
expected versions, previews, audit and idempotent receipts. Rate continuation
uses the existing company paging contract authenticated with the company report
key. No provider or network path is installed.

core/exchange.py validates canonical positive decimal rate strings and converts
positive integer Money through arbitrary-width integer intermediates and one
half-even rounding. company/journal_foreign.py selects exact-date or explicit
manual rates, preserves unchanged captured originals by stable line identity,
and validates generated conversions against original command inputs and saved
lines. Domestic lines require no rate-table lookup. Journal corrections and voids
retain the existing single audit/ledger/retry transaction and exact reversals.

Journal output exposes home Money and original Money separately. Generated edit
forms submit original tagged money, and a separate refresh_rates input controls
repricing; changing date or master metadata alone preserves conversion history.
The dedicated register displays home effects of foreign entries and routes their
edits to the journal form. The demo includes a yen receipt and a later quote change
that does not modify its captured rate. Reference-year accounting oracles are
independent of the added ordinary-demo journal.

The foreign increment passes independent artifact review, including mutations of
rounding, captured-rate preservation, original-command validation, browser
original-money input and currency filtering. The complete suite produced 1,789
passes and four test failures: a Save-redirect observation race, a read-command
inventory missing the two rate reads, and two obsolete seeded-balance assertions.
Test-only corrections and all nine affected/adjoining checks passed; application
code is identical to that complete run. All 1,793 distinct tests are covered.

A quiet resident-host measurement with foreign support enabled, normal garbage
collection and the existing 10,000-account / over-100,000-posting-line fixture has
20 samples: median 45.21 ms, maximum 47.35 ms for a 20-line domestic journal. All
20 are below 50 ms. This is a measured sample, not a universal tail guarantee.

## Actual transaction-count storage witness

A separate witness at baseline `18e1d5c` uses 100,000 public journal posts through
the resident host writer. Every journal is the minimum balanced two-line shape:
USD 1.00 debit to Checking and credit to Opening Balance Equity, with the general
chart, normal snapshots, audit and durable commits. There are no attachments,
corrections, foreign amounts or custom values. It is distinct from the earlier
100,000-posting-line performance fixture.

The closed, checkpointed company database is **1,246,519,296 bytes**. Including
the hub gives **1,246,703,616 bytes**. The 500,000,000-byte company budget fails.

| Company allocation | Bytes |
|---|---:|
| Ledger tables | 348,459,008 |
| Ledger indexes | 217,784,320 |
| Audit tables | 522,493,952 |
| Audit indexes | 156,651,520 |
| Other tables and indexes | 1,126,400 |
| Reserved SQLite page | 4,096 |

All 100,000 transaction/revision/batch identities, 200,000 entered lines and
posting lines, per-batch balance, source links, audit counts and foreign keys
are verified. The closed root is copied and selected complete public journal
receipts reopen unchanged. WAL, synchronous FULL, foreign keys and normal garbage
collection remain enabled. No vacuum, history removal or application change is
used to reduce the result. Diagnostic repairs and pauses do not support a latency
claim from this run.

Ledger tables and indexes alone occupy 566,243,328 bytes. Audit-only reductions
cannot meet the complete company budget. Repeated issuer and account snapshots
identify a candidate for lossless storage experiments, but measured repetition
is not a measured saving. Storage remediation remains a separate unfinished pass.

## Standard accrual financial statements

`company/financial_statements.py` supplies `report profit-and-loss` and `report
balance-sheet` through the shared registry and reports capability. It streams
grouped immutable ledger effects using the existing arbitrary-precision sum;
signed64 checks apply to every account and output total before paging. No schema,
closing transaction, cached balance or posting-cost change is introduced.

P&L computes normal-side income/COGS/expense/other amounts for inclusive dates.
Balance sheet computes as-of assets/liabilities/posted equity and splits raw
income/expense effects into prior earnings and current fiscal-year income. It
adds those residual earnings to posted equity once. Ordinary manually entered
transfers affect the reported result; there is no special closing inference.

Account rows are own-account nets with IDs, full/current/display labels, number,
type, parent and active state. Section/name-key/ID order is stable. Totals cover
all accounts on every bounded page. Statement continuation extends the shared
HMAC state with complete account presentation/classification, company fiscal and
label preferences and company audit watermark. Any audited company write stales
these continuations, preserving their original generation metadata across pages.
Existing TB/GL continuation semantics remain unchanged.

Workbench `statements.py` and `statement.html` project these results without
accounting logic. The filter form restarts a report; a separate Next account page
form preserves its validated input and cursor. All report submissions display
read results directly rather than a saved-write flash. GL drill-down carries the
source watermark only as browser context, compares it with returned metadata and
warns when the books changed. Both demo seed files execute both read commands.
The reference-year guide supplies examples and independent annual totals.

Warm resident-host reads at f009b49 on the retained local fixture of 10,000 accounts,
104,194 posting lines and 713 transactions, normal GC and no concurrent test workers:
five measured reads after one warmup per report gave median/max 1182.77/1195.94 ms
for trial balance, 897.67/899.82 ms for P&L and 1266.23/1283.83 ms for balance sheet.
Company record/audit counts were unchanged. This is a bounded read sample, not
the separate 100,000 transaction storage workload or a universal latency guarantee.

## Customer work documents

[Customer work](customer-work.md) owns the nonposting proposal, alternative
estimate and work-order contract. The shared registry exposes21 commands; all
three nouns have create/update/copy/show/query/history plus proposal estimate,
estimate work-order and work-order complete. Structured browser forms and detail,
history and source views cover desktop and phone. Work completion records quantities
and operational times without posting a sale or claiming payment.

company/work_models.py defines typed inputs; work_facts.py captures commercial and
operational facts; work_defaults.py resolves captured and current selections;
work.py builds/persists whole revisions and permanent conversions; work_validation.py
independently verifies source ownership, original intent and exact aggregates.
work_schema.py declares five work tables. Work profiles share commercial defaults
with ordinary sales without requiring financial control accounts or changing old
sales JSON. work_outputs.py and work_cmds.py project the shared contract.

Company co0010 preserves the stored custom_field_scopes definition, local writable
columns, generated values, constraints, indexes, views and triggers while widening
the known record-type CHECK. Unsupported definitions reject before alteration.
Immutable revision/line/root/link guards and composite foreign keys fence ownership.
Hub0011 seeds all nine customer-work role/requirement combinations, matching the
command registry. Granular capability overrides remain the Row7 authority target.

Operational conversions append one same-facts source revision and one linked
destination atomically. Company-wide permanent request identity survives cache
expiry; matching cached conversion receipts refresh the current destination through
a read-only command replay callback after ordinary authorization/input matching.
Whole-version conflicts, retired line identities and accepted alternative selection
remain enforced. Reopening normalizes the previous end timestamp before validating
the new aggregate; invalid caller combinations return E_VALIDATION.

Both seed manifests retain their previous171/76command prefixes byte-for-byte and
append30/31commands covering all21work commands, alternative acceptance, completed
quantities, permanent replay, independent copies, markup/amount/unknown-cost/zero
pricing, nonbillable lines, custom false/clear/history and source notes/files.
These operational extensions add no ledger effects.


## Linked work billing

[Whole-line work billing](work-billing.md) defines six commands under
estimate and work-order: invoice, sales-receipt and billing. company/billing.py
resolves captured source facts and financial choices, then appends the sale,
source revision/version, allocations, permanent conversion and audit in one company
transaction. billing_validation.py independently checks the selected roots, source
scope/provenance and exact destination facts. billing_edits.py freezes consumed
source economics and retained linked sale lines; corrections carry immutable
allocation rows and removal or void releases only their own active spans.

billing_queries.py derives consumption from posted transactions' current revisions.
Billing reads show current ownership, quantities, no-charge/nonbillable states,
remaining exact amounts and bounded destination history. Full sale revisions expose
billing_sources with internal snapshots; history summaries expose compact
billing_links. Both operational and financial conversions share permanent company
key identity across their durable stores, including replay after generic cache
expiry and after void. Composite resource requirements and conditional source
reads are checked before cached responses or preview; granular grants/denies remain
Row7. The MCP adapter uses these same registered predicates.

Amount-priced sales retain net_amount and positive descriptive quantity with a
null unit price. Version2 amount profiles have an explicit pricing basis; version1
unit-price facts retain their original serialized form. Quantity-only edits retain
explicit amounts. Explicit amount/rate/default-reset conflicts reject. Captured
quote rates and income/liability mappings remain authoritative for billing, with
current eligibility checked before posting. Each installment calculates ordinary
tax once per line/component from its allocated net; quoted tax is informational.

Company co0011 widens the known sales_line_profiles price constraint and adds
pricing_basis while preserving all prior columns, values and local schema objects.
Unknown table definitions reject before alteration. Immutable conversion and
allocation tables use composite ownership keys, permanent-key collision guards and
active-root guards on allocation insertion and transaction revision activation.

The [progress billing contract](progress-billing.md) extends these commands
with quantity, net amount, original-scope percentage and exact released-allocation
selection. billing_math.py allocates integer entitlement spans and computes exact
net endpoint differences; billing_facts.py defines immutable proofs and rational
quantities. billing_selection.py resolves current source choices. billing_checks.py
independently reconstructs selection, source economics and pending proof amounts.
Version3 allocated sales facts retain the original quoted quantity/rate separately
from exact billed fractions. Receipt corrections that change gross require an exact
amount_received when any linked-work history exists. Ordinary receipt omissions
remain compatible; explicit received totals always validate.

billing_progress.py projects previous, current, cumulative and remaining work from
current posted allocations plus validated pending allocations, before persistence.
Remaining tax is a forecast on remaining net; cumulative tax/gross use actual
installment amounts. Replays omit this boundary projection. Billing reads expose
current consumption and attributed stale-preview errors include real sale releases.

Company co0012 preserves existing column order and local objects while allowing
nullable raw quantities only for exactly fractional allocated facts. Version2
allocation rows store basis hashes and bounded canonical spans with 160-bit hex
coordinates. SQL guards reject malformed proofs, incompatible active bases and
overlap on allocation insertion or sale revision activation. Version1 allocations
retain whole-root meaning and existing conversion keys retain their request bytes.

## Customer work preferences

The [customer-work preferences contract](customer-work-preferences.md) defines the
controls, historical effects and current availability shown by each command.

Company co0013 appends estimates_enabled=true, progress_billing_enabled=true and
close_estimates_after_billing=false without rebuilding company_info. Creation and
versioned company updates expose strict booleans; explicit null rejects.
company/work_preferences.py projects stored/effective policy, per-setting audit
provenance, estimate-entry gates and exact bounded-recovery eligibility. Creation
controls leave existing source history, financial billing and work orders available.

Disabled progress permits remaining work, selected complete remaining lines and
net-only recovery of the exact current recommendation for roots with more than200
free spans. Recovery output is current guidance, never a reservation. A failed
net-only recovery with a fingerprint returns E_PREVIEW_STALE before range resolution;
without one it returns E_FEATURE_DISABLED. Other partial modes reject disabled
before fingerprint comparison. Authority and source-version checks precede policy.
Preference attribution identifies each field's latest actual edit, with creation
provenance for never-edited fields, separately from allocation/release attribution.
Company show exposes these authorized latest preference changes so blocked browser
entry routes can render the shared disabled-feature error before displaying a form.
Settings links remain limited to company administrators.
Company-home shortcuts use the same new-estimate visibility rules as lists and
record actions. A rejected recovery form keeps its attempted amount visible;
an explicit per-line button adopts the displayed current recommendation without
changing the other draft inputs and invalidates the preview before another save.

Financial fingerprints include progress enablement and effective closure for direct
estimates. Estimate enablement and dormant close settings are excluded. New estimate
operations fingerprint estimate enablement alone. Permanent request hashes retain
their original serialization and committed replay precedes new-work gates.
Replay previews describe the original effect in the past tense, explicitly state
that no new closure occurs, and label separately read availability as current.
Only new conversion previews describe their effect and availability as prospective.

Final positive billable net billing directly from an accepted active estimate makes
it inactive only with progress disabled and automatic closure enabled. Billing's
existing single source revision carries that change atomically. billing_validation.py
reconstructs required closure from all current roots, current policy and independently
validated pending allocations; it rejects omitted or spurious closure. Acceptance,
status and every other captured source fact remain unchanged. Work orders never close
their upstream estimate. Voiding a bill releases scope without reactivation.

SalesWriteOutput.source_effect is the conversion's immutable before/after availability
and versions; source_current is separately authorized current availability. Replay
reconstructs the original effect from adjacent immutable source revisions. Ordinary
sale writes return null for both. Browser forms show conditional closure and retained
history, independent of payment and physical completion.

Both seeds retain their exact283/189command prefixes and append12 preference commands.
Each exempt10.00 invoice ends voided and its estimate is explicitly reactivated;
settings return to defaults. Reference gross debits and credits each increase1000 on
AR and Service Income from October2 onward, including annual and second-half totals.
All reference net values remain unchanged. The reference source-effect oracle derives
these four legs independently; Row18's manifest witness remains scoped to its41commands.

Sale conflict diagnostics resolve an expected header version through its unique,
valid audit snapshot and transaction-owned revision ID. They compare commercial
semantics and expected/current status independently of revision numbering, with a
conservative version marker for otherwise identical headers. Missing or damaged
history retains unknown-field E_VERSION_CONFLICT. journals.version_meta accepts
an optional defensive history decoder for sales; other callers retain their
existing decoder. Diagnostic reads never write, and latest-writer attribution is
separate from the changes since the expected version.

## Numeric browser entry

The [numeric entry contract](numeric-entry.md) owns calculator behavior and the
separate proposed fractional-cent rate option. Workbench numeric_metadata.py adds
explicit scale/currency metadata to generated fields, repeated controls and custom
numbers. numeric-context.js resolves current currency, billing mode, Clear/Keep,
default and enabled-state semantics. numeric-entry.js evaluates bounded BigInt
fractions and automatically rounds to field precision without executing input text.
Currency suffixes retain their code; zero-decimal currencies round while integer
controls require whole results. Numeric Enter never submits, including plain or
already resolved values. Capture handlers normalize before HTMX collection; late
unresolved requests are canceled. Ordinary input/change events invalidate sales
previews. Register Record/Recalculate normalize before constructing payloads;
retained retry bytes are not reevaluated. Posted money and command schemas retain
their existing contracts.


## MCP transport and publication

`commands/mcp_cmds.py` supplies the rootless `bookflow mcp` stdio launcher.
The optional official SDK owns protocol framing and the three tools; catalog,
help and input/output schemas come from the command registry. Help bridge v2
uses usage/input_schema/output_schema/full, with complete input constraints in
usage and a catalog page default of20. Mixed bridge versions reject explicitly.
`core/context_options.py` normalizes inactive/unsupported context across adapters;
`core/company_selection.py` applies caller-owned explicit/environment/configuration
precedence. Process sessions and client labels are provenance, not authority.

`adapters/mcp/runtime.py` connects host-owned intents to the shared executor and
registered transfers. Each intent has one execution identity under both reference
aliases. Preparation, accepted callbacks, delivery slots, bounded retained receipts
and original absolute deadlines have separate owners; recovery does not dispatch
committed work. `intents.py` accounts retained value graphs, while host-owned cleanup
keeps queued/active work pinned until its actual owner exits. `client.py` handles
permitted input/output paths, hashing, binary streaming and atomic verified files.
`files.py` verifies directory and descriptor lineage; these file capabilities are
POSIX-only, with Linux tested and no macOS/WSL execution claim. `inspection.py`
provides authorized bounded navigation of mapped, verified result files.

`framing.py` authenticates complete JSON/binary delivery using ordered bounded
records and terminal sizes/digests. `json_validation.py` validates syntax without
assembling scalars, keys, numeric lexemes or object paths. Validation holds one
decoded record, eight token-prefix characters, three error-field markers and a
stack proportional to JSON nesting depth. Inline result assembly and an explicitly
requested inspection item remain separate from this syntax-validation memory.
`BOOKFLOW_MCP_JSON_SECONDS` configures JSON delivery between30 and86400seconds;
preparation, retained-result and binary lease deadlines remain independently owned.

`core/publication.py` retains a value-only authority certificate and rechecks the
original credential, actor, memberships, selected/returned registrations and shared
conditional predicates before result publication. Pure dependency-resolution
failures become E_PERMISSION, authentication loss remains E_UNAUTHENTICATED,
and operational/certificate failures keep their operational category. This does
not rewrite a valid original command error. Exact original token-self-revocation
and audited own-detach effects have narrow postcondition rules. Middleware checks
headers/body publication and delayed workbench receipts; committed writes are
never reexecuted or compensated to recover a response.

CLI collection flags decode JSON arrays for list/tuple/set/frozenset annotations.
`adapters/typed_defaults.py` converts true/false text controls for an explicitly
selected boolean custom-field definition kind, leaving core validation unchanged.
The CLI supplies --kind with a boolean default; generated browser forms use the
selected or retained kind and a boolean selector; definition scope objects from
show are projected to the scope names accepted by update. Text false stays text, omission
stays omission, and explicit clear remains null.

MCP response classification is explicit. A pre-intent rejection of the shared
context/selection/authorization check carries the private command_rejection
response marker. Its AdmissionRejection guard reruns only that pure check under
current credentials and requires the identical rejection before publication.
Only this admission response is preserved as an unframed original command error.
All later command output/error documents use verified J/T framing. Other HTTP
errors are adapter/protocol/publication errors and retain the known operation
reference, stage and uncertainty. Recovery status/release/upload observations
have separately validated schemas and are never business completion. Control
JSON is limited to one64KiB record; full business/error documents are streamed
without that limit. Verified destinations and classification are exposed under
_meta.bookflow_transport.

PublicationPermit represents invalid business input with input_error and no
validated input model. Such a permit cannot finish as a successful execution or
rehydrate as one. It guards the original rejection with current credential,
actor/membership and selected-company authority. MCP preparation can deliver this
rejection, shared conditional/transfer-preparation errors and an upload body-limit
error without queuing a business execution. Its rejection delivery owns cleanup
and can retain the same guarded error receipt. Recovery of a failed download
redelivers only its error document; it never reopens the missing binary resource.

The Row9 test ledger tests/mcp_coverage.py explicitly classifies all301 registered
commands by routed JSON, advisory, finite poll/local follow, binary direction,
local lifecycle or standalone protocol. Four-interface execution scenarios cover
296 hosted commands. Five local lifecycle commands map actual local execution
witnesses and explicit installed-MCP/HTTP rejection boundaries; they are not
counted as hosted execution parity. The ledger gate rejects unclassified or
pending execution rows. This is execution coverage, not full Row9 acceptance.
The actual all-command form check emits schema-path/control/variant/context
mapping, separate from the still-incomplete browser interaction/output/success
mapping and parent-owned fresh blind acceptance.

The material-variant ledger groups 1963 hosted schema alternatives into 16
structural groups; these counts are not browser journeys. Command-specific
witness links cover authored address/default/payee, definition kinds, calculation,
line origins, explicit Any JSON, discriminator visibility and hub destinations at
1280/390. Each reference identifies its actual test scope, not every command that
shares a schema. Parent-owned payment-selection navigation, newer integrated
commands, independent review and combined/blind acceptance remain open.

Generated forms expose explicit empty text for text definition defaults and empty
lists for optional collections. Clear/null takes precedence where the command
allows clearing; untouched inputs retain omission semantics. Declared Any values
can use explicit JSON mode, including exact integer money objects, while ordinary
text mode remains text. Repeated JSON-mode controls retain submitted row identity
through preview/error ordinal renaming. Numeric entry ignores JSON-mode values and
rows overridden by an empty-list action. The company picker table owns horizontal
scrolling within the shared responsive shell.

Generated workbench forms preserve every model branch declared by a Pydantic
string discriminator. A single discriminator control exposes the combined choices;
branch fields carry alternatives of conjunctions, including nested payment preview
intents. Browser visibility disables inactive controls and the shared form decoder
also ignores inactive branch fields. Bookkeeping validation remains in the original
input models; no payment permissions, defaults or financial effects are redefined.
Payment publication retains owned transaction, selection, operation, payer and
composite audit identities through `core/publication_payment.py`. Fresh checks use
the shared `company/payment_authority.py` predicates over complete owning graphs,
including off-page operation effects and cleared selection history. Filtered
payment aggregates retain the shared work-access projection as well as returned
record identities. Plain invoice summaries retain their own authority boundary;
sharing a receipt with another work-linked invoice does not expand that policy.
Permanent recovery may close its company reader; dependency capture reopens
read-only without re-running recovery, planning or write maintenance. Original
rejections retain the shared error-publication path. Full Row9 acceptance remains
open; installed SDK workflows are not blind-agent acceptance.

Company revision co0017 adds ten nonunique, nonpartial BINARY indexes declared in
`company/read_indexes.py`. The frozen migration checks every reserved name and
required column affinity/expression/collation before creating any persistent
index. Existing table definitions, rows, local objects and statistics are retained.
No migration, seed or read runs ANALYZE or PRAGMA optimize. The captured payer-label
expression uses the fixed `$.payer.label` path and empty-string default. Indexes
are maintained by SQLite on ordinary writes; they store no derived balances.
Historical co13→co14 tests use their frozen co14 schema and public command source;
co16→co17 preservation and fresh-chain checks live in `test_payment_read_indexes.py`.
## Existing-master browsing projections

`company/query_models.py` defines strict custom criteria, selected-row descriptors and bounded metadata inputs. `query_catalog.py` expands the authoritative list declarations into public show-field/alias catalogs, derives filter controls and supplies paged discovery. Each master has `query options`; custom choices use its `kind=choices` and stable definition selector. Dynamic definition and choice metadata never embeds an unbounded option collection. Company dispatch and the existing audit-watermark/permission cursor remain authoritative.

`query_projection.py` adds only selected SQL expressions and typed custom predicates to the existing providers. Decimal custom values compare through an exact signed-64-bit nano-unit function, never SQLite REAL. Choice matching uses the active normalized choice identity within its definition; a retired option cannot match a later option reusing its label. Missing current values remain null. Definition deactivation preserves searchable/filterable values. Text equality is literal and case-sensitive; containment follows the existing normalized Unicode search.

Explicit `columns` returns stable id/version/label/active plus a `values` mapping, ordered typed `columns` descriptors and `matching_total`. Legacy omitted-column responses retain their exact four top-level fields and page-size `count`; reference projections cannot select columns. Query fingerprints omit absent new inputs for continuation compatibility, and include any supplied projection/filter criteria. New projections use effective customer inheritance and existing protected-field disclosure rules.

Owned collection columns return a count and shared `query children` coordinates. That read returns at most 200 ordered public child rows, an exact total and a snapshot continuation. Existing full `show`/`list` contracts remain unchanged. Workbench detail tables page through these collection reads. `adapters/workbench/browsing.py` translates URL controls and formats the shared results; `browsing.js` provides named, keyboard/touch column and filter controls. Decimal values remain strings. Browser state is URL-only and does not write settings or accounting state. Legacy filter URLs remain accepted.

Selected custom-filter queries materialize matching IDs once per statement for both the exact total and bounded page. A process-local 128-entry LRU caches compiled SQLite statement structures across equivalent engine instances; it stores no rows, authorization decisions, sessions or connections, and every execution binds current parameters. Money filter controls translate exact human amounts into the existing minor-unit filter contract using BigInt, without rounding.

Master query `ids` optionally restricts matches to at most 64 stable IDs, intersecting
all other criteria under the same current authority and snapshot. Omission preserves
legacy response/fingerprint behavior. Workbench retained reference filters resolve
labels and inactive state through these bounded reference queries, grouped by target
noun; they do not fetch full records or change the ID-valued predicate. Selected
creation/update timestamps use the shared viewer/company timezone formatter.

The list browsing integration adds twenty query-options and five query-children
command identities to the explicit MCP execution ledger. The per-master witness
compares Python, CLI, HTTP and actual SDK replies, complete reference/metadata/child
pages and unchanged company data. Selected default columns cover their first page;
this is not a blind-agent or complete custom-filter interaction claim. The six-way
custom-filter schema group links dedicated text/date chooser cases at1280/390
while retaining untested operators, malformed-date interactions and generated-form
branch cases.

Payment discovery uses explicit owned covering sources and a cohort-first SQLite
join order. Preparation builds fixed parameterized SQL per request; its authority
fragment is compiled from the owning predicate inside the actual outer header
scope, retaining correlation and positional bound values. Context and complete
posting-graph authorization still use their existing owners. Driver-native rows
preserve the complete typed/canonical baseline and signed continuation contract;
no statement/result cache or global driver/session change is involved. Invoice delivery starts from its selected identities, retains the
original revision-one amounts, and leaves the complete candidate/version/lineage
and funding baseline in the cursor fingerprint. Suggestion baselines contain the
complete authorized identity/version/current monetary relation as transient tuples;
no balances or results survive a read snapshot. A transient SQLite flattening
barrier evaluates each suggestion candidate's live sum once before testing positive
due. Invoice text filters precede live-sum evaluation, and bounded delivery reuses
the already-resolved context and funding facts. CP02 aggregates each party once
with bookflow_sum_int, then combines unbounded Python integers before checking the
disclosed payer/family totals; even out-of-range party intermediates can cancel.
Payment searches carry only IDs through their existing window-count/filter/cursor
contract, then fetch the selected display fields in the same read snapshot. Sales
query pages fetch headers and revision/profile summaries only for selected IDs.
Payment history retains complete resolved-participant operation membership, batches
at most 200 audit identities per lookup, and renders stored commercial revisions
without constructing discarded current settlement. Missing profiles or audit
identities fail instead of returning empty history. The full 34-case 10k performance
gate is not yet satisfied; retained builder measurements include every miss.


### Explicit audit publication cohorts

`company.payment_authority.authorize_events` checks supplied event occurrences in
cohorts of at most 200 using `_EventCohort` projected facts. It performs the scalar
permission sequence for every occurrence, including duplicates and explicit events
outside the audit filter's trigger enumeration. `authorize_event` remains the scalar
entry point. Empty input performs no reads or checks; missing entry sets retain the
scalar outcome. Complete historical graphs and deferred graph errors use the same
owning traversal rules.

For audit roots, `core.publication_payment.check` batches consecutive events. Every
intervening non-event root completes before the next event run loads. Cohort facts
live only within that check's fresh publication snapshot; command snapshot caches
and earlier permit checks confer no authority. Deferred graph and permission
failures occur in occurrence order. A genuine operational prefetch failure may
preempt an earlier permission failure and aborts without fallback or a pending
send. Existing transport checks prevent the failed pending send; previously
successfully authorized bytes cannot be recalled. No transport buffering is added.
The 200-event/ID bound does not bound total graph size or establish a latency SLO.

Payment publication checks group contiguous audit-event, transaction, exact
payment-selection and other root occurrences. Audit events retain their owning cohort; transaction runs
load at most 200 occurrences at a time on the current publication reader.
Unique IDs bound the projected root facts; duplicate occurrences retain their
original order and read/write gates. Each occurrence validates its root and all
one-step historical application targets before ledger and conditional work
permission. Known missing facts are raised at that occurrence; a database/I/O
failure during the cohort load aborts immediately without fallback or retry.

Selection publication reads complete saved-item, recovery-attempt, revision-funding
and consumed-operation ownership in cohorts of at most 200 root occurrences.
Projected facts are shared only inside the current publication reader; every fence
reloads them. Duplicate occurrences retain their ledger/work gates and write flags,
known content failures remain root-ordered, and clear-event null invoice references
are skipped. Complete D references must exist; work linkage uses D plus one historical
application step, without extending transaction/payer R1 validation to selection H.
Driver failures during current-cohort reads abort without fallback or pending send.
Query pre-count filtering, epoch/work-access fences, rendering and accounting remain
unchanged. SQL-growth and compatibility evidence accompany this increment; the
original 34-case under-100ms performance gate remains separate and open.

The empty/no-operation 201-selection HTTP witness recorded82 statements/64 SELECTs
at both limits10 and200, versus190/172 and2470/2452 with the frozen scalar checker.
Every response retained its three fresh publication checks. This SQL-count result
is not a measured under-100ms performance pass.

Payer publication builds a recursive `UNION` customer family and distinct
historical customer AR posting contributors B, including inactive descendants
and reversals. It validates the payer and B plus one-step application targets H
with a Boolean query, then passes B to the existing relational authority query
(which adds H itself). Results and bindings have fixed cardinality; SQLite may
still visit the complete history. Missing payer or application-only target
references fail with unresolved payment evidence before permissions. Scalar
helpers for other root kinds retain their existing validation contract.

These facts live only within each fresh publication-reader snapshot. Capture,
credential and membership checks, work-access projection comparisons, all
response/retained-result fences and scalar fallback families remain in their
existing owners. No facts or authorization results are cached between checks.

Audit cohorts resolve a payment recovery active-key identity directly to its
persistent selection before looking for any physical barrier row. Every
selection-family owner retains all historical ordinary items, revision funding
sources and recovery attempt targets without state/action filtering. A consumed
operation becomes a frontier edge through the existing operation owner, retaining
all captured participants. Projected reads retain the200-ID bound and root-local
errors; facts expire with the current check. Selection/recovery query audit-epoch
roots remain separate boundaries in grouped publication dispatch.

### Explicit-grant-only live admission (Delete prerequisite G0)

`core.registry.EXPLICIT_GRANT_ONLY_CAPABILITIES` marks the four
`transaction.{journal_entry,invoice,sales_receipt,payment}.delete` contracts
independently of command import order. `Command.explicit_grant_only` additionally
marks company commands such as future saved-operation inspection whose family is
not yet known. The marker is registry metadata, never a request/context option.
Registration rejects nonboolean markers and bootstrap/standalone or hub routes
for marked commands. Authorization help labels them as not activated.

`hub.access.require_explicit_grant(session, capability)` unconditionally raises
`E_PERMISSION` with the fixed `capability_not_activated` reason. Roles, including
owner and hub administrator, cannot bypass it. There is no configurable provider,
activation flag or public override. A future reviewed granular resolver must
replace this owner with current effective actor/principal enforcement; test-only
monkeypatching supplies no production activation mechanism.

`require_command_activation` checks the primary marker/capability and every
explicit-grant-only static resource before ordinary company authorization.
`dispatch._permanent_recovery` explicitly invokes it before authorization opens a
company or calls any saved-fact recovery hook. `require_resource` applies the
same gate before its existing visibility/role checks, so dynamic family graph
owners can reuse it. Successful retained publication permits recheck command
activation before opening target data; original rejection delivery is preserved. Commands without markers retain their existing behavior.
No financial Delete or operation-inspection command is registered by G0. The
private frozen permission catalog, role defaults, schema, setup and full C
publication policies remain separate prerequisites.

### Inert permission-administration storage (B1)

Hub `hub0012` follows `hub0011`; company history is unchanged. It adds membership
and aggregate agent-administration versions/provenance, fresh-context metadata,
and private singleton `permission_state`, initially generation1 in `legacy` mode
with no catalog. Migration neither changes capability defaults nor activates A's
policy evaluator. Historical membership/assignment IDs, authority epochs,
credentials, raw overrides and audit values remain intact.

Conditional initialization of suspended authorities is admitted on the migration
runner's locked connection before any new DDL. When that initialization needs an
UPDATE, any persistent or TEMP trigger attached to `agent_authority` (regardless
of identifier case or trigger event) rejects the upgrade with a fixed
`unsupported_authority_backfill_trigger` cause. No local trigger is disabled or
recreated. With no required backfill, no UPDATE is issued. Failure retains the
original schema and data through the existing rollback/backup mechanism.
Every hub0012 admission read, column/table DDL, backfill and singleton INSERT
explicitly addresses `main`; same-named ordinary TEMP tables retain their entire
schema and data. Both trigger catalogs are explicitly database-qualified.

`ProposalRows` and `derive_proposal(old, *, old_catalog, new_catalog, changes,
generation, full_defaults=None)` construct complete hypothetical facts without
SQL or policy admission. `old` must be trusted current-transaction `load_root`
output. Both initiating and final constructions use that same old anchor;
public dataclass constructors and matching digests do not authenticate its
origin or freshness. `DerivedFacts(root, base_stamp, provenance)` labels the
result `derived_from_loaded_old`, never independently queried proposed SQL.

Patches contain full typed replacement/upsert rows. Unmentioned rows remain
unchanged. Users/authorities replace existing identities only; user kind,
owner and hub-admin fields cannot change. Membership identity/user/scope is
immutable and a new membership requires a new physical and logical identity.
Assignments upsert by their composite identity; revocation is an explicit row,
not omission. Scope rosters and company parents cannot be patched. Expected
keys start from old SQL keys plus permitted insertions/default replacement and
are cross-checked against full canonical rows. The constructor reuses B1's
strict validation, catalog normalization and row/bundle digest ownership.

`full_defaults=None` retains actual old defaults, while `()` explicitly empties
them. Generation is the old value or its in-range successor, never an automatic
increment. B2 owns deployment admission/full-default requirements for catalog
replacement, semantic row/counter effects and visibility. Existing
`assemble_pair`/A own dense old/new comparison. Final SQL observation can compare
the complete root directly to the prepared root; tokens, audit and state update
provenance outside RootFacts require B2's separate expected-state checks. Legacy
activation, new identities/scopes and installed visibility remain outside this
constructor.

`hub.permission_snapshot` is a private read-only dependency. `load_root` accepts
an already consistent `Database` transaction and an explicitly reviewed
`CatalogBundle`; it never opens a root or company, imports the registry to infer a
catalog, installs visibility, or writes policy state. Independent complete SQL
key/parent projections are checked against typed row materialization on that same
snapshot. Revoked and inactive rows remain represented; even revoked malformed
policies fail strict preflight. Root defaults replace shipped defaults before the
canonical catalog digest is derived. In `policy_v1`, stored catalog, digest,
version and actual defaults must all agree with the supplied build descriptor.
Catalog JSON uses strict dataclass decoding and A's normal form, including role
and threshold rank. Unknown fields, duplicate keys/names and forbidden Delete
defaults fail with static private error categories rather than supplied values.

`CatalogBundle` is trusted private build provenance supplied by the deployment
owner, not a user payload or self-attestation of source completeness. Its source
commit, exclusions and inventory digest remain separate from A's descriptor hash;
the existing complete catalog tests verify this executable's accepted inventory.
Metadata-only B1 changes retain the combined recovery build’s reviewed `7b2d4c2` catalog version.

`observe_pair` preserves complete raw-union `LogicalObservation` values alongside
its `SnapshotPair`. `assemble_pair` returns that same SnapshotPair contract.
Required organization/company `pending_path` fields come from the full main
registry snapshot and participate unchanged in its row digest. Scope keys and
revoked membership rows remain raw and complete; retirement never filters SQL.
Legacy serialized roots missing the required field must be freshly loaded.

The private lexical classifier accepts slash, backslash and mixed separators,
without platform or filesystem inspection. Relative nonempty components exclude
empty/dot/dot-dot components, NUL, rooted and ASCII drive-prefixed paths. A first
component exactly `trash` with a following component is retirement; bare `trash`
is invalid. Case and original strings are preserved. An organization retires its
companies and future-company scope; a company can also retire through its own
pending destination. Ordinary moves retain logical presence.

Observations contain raw presence/parent/path and logical presence/retirement
cause for every raw old/new scope, plus the complete subject/membership product.
Membership values on absent scopes are None while their raw rows remain intact.
A receives exactly the union of the two logical live rosters and their dense
slots, using its actual types and `validate_comparison`. Scopes absent on both
sides remain explicit in the observations, outside A's callable scope domain.
Every logically live organization contributes a future-company scope. Complete
raw-domain visibility is validated before the A subset is selected: absent
scopes require explicit false, including both-absent tombstones. True, missing,
duplicate or malformed facts reject; no live-policy default is supplied.

`ScopeRows` and `derive_scope_proposal` separately support complete typed scope
upserts/removals plus ordinary ProposalRows against final scope keys. Existing
company parents cannot move; retired destinations cannot be rewritten or cleared.
New scopes must be live, with live parents. Removing an organization requires
all children explicitly removed; membership removals must exactly cover all
removed scopes, revoked rows included, with no removal/upsert overlap. New or
reactivated membership on retired scopes rejects. Expected keys derive separately
from old SQL keys and explicit changes. Users/agents and their identity constraints
remain those of the ordinary constructor. No SQL or semantic increments occur.
Both initiating and final proposals share one loaded old anchor; subsequent
cleanup uses a freshly loaded retired anchor. Authority-input roots omit general
registry metadata, tokens and audit, which still require separate full-state
expected-write checks. These private APIs do not wire reset/read/publication
consumers or activate retirement policy in the current runtime.

Eligible humans come from
unrevoked explicit assignments to present active humans, never ownership or token
existence. A provider must explicitly supply complete governed visibility on both
sides; no production provider or membershipless-administrator decision is included.
Legacy roots may be read for strict preflight, but policy-to-policy assembly rejects
legacy mode: the actual legacy-observation/activation bridge remains B3/C work.
This dependency does not itself implement administration, token reconciliation,
structural writer routing, command/editor activation, or completion of Row7/B3.


### Private permission administration (B2)

`hub.identity_admin` owns six conditional private intents: put/revoke membership,
set user activity, replace the whole unrevoked principal assignment set, explicitly
authorize an agent, and replace a trusted deployment catalog with complete actual
root defaults. Public dataclass constructors authenticate neither build provenance
nor actors. These services have no command, editor, dispatch or structural-writer
connection. Legacy mode rejects; activation remains B3/C/D work.

Every preview/apply resolves a token binding through the existing credential owner
on the supplied current hub transaction, compares its admitted identity and then
checks the current active human and required scoped/admin role. TEMP tables/views
shadowing hub names reject before that owner's unqualified queries can run.
Scoped visibility is established before target disclosure. Editing/revoking an
active owner or granting owner requires scoped owner; a revoked historical owner
can be restored into a lower role by an administrator. Business Delete permission
is not delegation authority. System targets and removal of the last active human
hub administrator reject. Expected target versions/epochs are checked even for
semantic no-ops; NULL/empty spelling and all raw rows remain unchanged on no-op.

The coordinator supplies an already-open consistent transaction; apply requires
its BEGIN IMMEDIATE serialization guarantee. `Database.write_transaction` alone
is not proof of that lock mode. The service uses a savepoint and never commits.
It loads one complete OldFacts, derives InitiatingProposal and FinalExpected from
that same old anchor using B1's public constructor, and delegates dense comparison
to `assemble_pair`/A. Visibility is explicitly supplied and complete on both sides;
unknown predicates fail closed. No production visibility supplier is installed.
Generation and all row/token/authority increments, safe audit bytes and the exact
allowed main-storage image are prepared before DML. Final independent root, token,
user/state provenance and raw rowid/type/value fingerprints must match that
expectation. Unanticipated local main-table side effects reject and roll back;
this is not a policy recomputation after writing. The service opens no company,
configuration or attachment and performs no filesystem/network side effect.

`hub.agent_authority` materializes A's complete union-agent results. Binding,
principal or own loss and changed inequality suspend as specified, bump epoch
once, and revoke every still-unrevoked credential for that agent, including expired
credentials, both kinds, all principals and null/old issuance epochs. Existing
revocations, hashes, issuance/creation fields and unrelated rows stay exact.
Assignment state plus suspension has one aggregate version effect. Retained
inactive assignments remain stored; only additions/reactivations require active
humans, equal proposed signatures and confirmation. Removals remain admissible
with unequal or empty survivors. Restoration never authorizes implicitly; only
explicit authorization with required permitted-use/fresh-context assertions clears
suspension, retaining epoch and never reviving tokens. Further real losses while
suspended still reconcile. User deactivation separately invalidates that user's
credentials; an already-inactive no-op does not repair stray credentials.

`hub.permission_admin_audit` prepares event/entry ULIDs, sequence, one supplied
validated operation timestamp and whitelisted encoded envelopes before DML, using
the existing codec and sequence semantics. It inserts exactly that manifest with
bound main-qualified SQL. One private event includes all derived entries. State
entries use record_id `1` and generation versions with full canonical catalog,
defaults and update provenance. Credential/password hashes, labels and liveness
fields never enter prepared audit payloads. Encoding/insertion and final-state
failures roll back all service effects. Preview allocates no durable IDs/times.

`core.identity_admin_binding` produces private operation-bound inputs for B2 and
current company authority. `offline_operation` obtains process OS identity and
holds RootLock from file capture through transaction exit. `hosted_operation`
requires the actual Host writer Thread and held root lock; it consumes an existing
peer-derived OSBinding or secret-bearing internal TokenBinding after dequeue.
It captures file configuration before BEGIN, then gives main.pending_config on
that exact connection precedence. Changed/missing admitted mapping, root, actor,
principal eligibility or epoch rejects. No Config.load/second connection enters
this transaction producer. Preview uses a consistent read transaction; apply
starts BEGIN IMMEDIATE. Ordinary readonly opens' existing transaction is retained.

The invocation reauthenticates before every preview/apply/resource decision.
B2's OSOperation lifetime guard associates Database/root/request/purpose and
expires on scope exit or transaction end. The enclosing actual command owner
still owns commit; these scopes never commit and roll back outstanding work on
exit. BoundOperation returns private B2 prepared/effect records, not publishable
responses. No registered command, editor or legacy-mode activation consumes it.

`hub.permission_runtime` supplies complete membership-governed visibility across
the raw old/new subject/scope union, reusing snapshot's retirement classifier.
Inactive/absent subjects and retired scopes are false. Company visibility requires
applicable organization/exact-company membership, including for installation
administrators. Exact-company membership permits parent discovery but does not
create organization membership, sibling or future-company authority. Existing A
role/default/administrative contracts are unchanged. Current company requirements
use A's actor/bound-human intersection after credential validation; conditional
source/record graph checks remain with their existing owners.

Its explicit build bundle retains the frozen capabilities/defaults/actions and
adds current reconciliation source declarations and payment source positions.
It does not replace persisted defaults or auto-adopt a catalog on a root. Legacy
activation and a catalog transition remain separately admitted operations. The
runtime bridge is not wired into legacy access, audit, recovery or publication;
complete projection/cursor cutover and retirement continuation remain required.

The producer must not leave coordinator savepoints open between entering
`OSOperation` and calling preview/apply: validation rotates the operation savepoint
with RELEASE, which also releases every savepoint nested after it. Coordinator
savepoints must enclose the operation or finish before validation; the B2 service
savepoint is created after validation.
There is no configurable visibility supplier or public mapping factory. Private
producer evidence does not establish live projection or activation safety.

Prepared effects are private and cannot authorize a later apply. The visible
portion omits hidden agents/tokens, global generations and catalog digests; even
event IDs and summaries require C's future projection. Existing audit readers are
not safe for these compound events. C must implement entry/summary/cursor filtering
and authority-commit/publication fencing before any live route is connected.
These modules do not complete B3 structural integration, C/D activation or Row7.
No command/resource-owner inventory changed; the accepted `7b2d4c2` catalog and
`50b259f8` descriptor remain unchanged.

### Payer reference indexes

Company revision co0019 adds `ix_co19_posting_party_transactions` on
posting_lines(name_type, name_id, account_id, transaction_id) and
`ix_co19_applications_targets` on applications(paying_transaction_id,
paid_transaction_id). All co0017 indexes remain. The additions cover historical
payer contributor and application-target references without changing payment
queries, exact balances, authority graphs or publication snapshots.

The revision validates both reserved names and complete main-table DDL before
creating either index. TEMP target shadows and reserved-name collisions are
rejected. Local extensions are supported when their complete stored table DDL
can be reproduced in the unchanged isolated SQLite probe; unavailable private
functions/collations produce a safe unsupported-local-DDL failure before
persistent index creation. Required keys remain ordinary TEXT-affinity columns
with BINARY collation. Old rows and local objects are not rewritten.

### Private transport admission prerequisite

`Host.publication_admission` orders private validation generations and bounded
HTTP/local handoffs. Conservative actual-owner commits close this gate; transport
ordering does not activate B2, retirement or full permission publication.
`serve` selects the owned h11 protocol and asyncio loop. Middleware validates
outside the mutex, then supplies a generation-bound frame to every ASGI send.
The actual asyncio selector transport's outgoing buffer accepts each bounded
immutable byte segment under the mutex. Selector registration, flow-control and
error callbacks occur after unlocking. The adapter deliberately depends on this
qualified asyncio transport implementation and fails for unsupported transports;
upgrades require the actual protocol barrier witnesses. Ordinary ASGI test clients
without the protocol extension retain predicate checking only, not commit ordering.

Closing the private gate invalidates all pending generations in constant work;
writer acknowledgment requires neither event-loop progress nor response cleanup.
Accepted transport prefixes cannot be recalled. Rollback does not revive canceled
frames. A canceled response is aborted without a successful body terminator.
Header serialization is limited to65536 bytes (including fixed framing allowance);
large bodies are segmented without introducing a business-result size cap. Fixed
protocol100/400/500/503 messages contain no business data. Empty finals and chunk
terminators belonging to a business response remain guarded.

Local operation-owned sockets use nonblocking short writes and readiness outside
the gate. The F1 successor below supplies fresh phase/current OS publication
proofs without resetting cumulative accepted bytes. Generic cooperative Python streams retain their existing
contract and are never called while admission holds its mutex. Transfer lease
cleanup remains independent. The complete-frame local receiver and verified MCP
terminal remain client relay boundaries, not remote cancellation acknowledgments.

The actual SSE generator captures a PublicationPermit per drained audit batch;
only the current batch guard is retained and it also covers idle heartbeat output.
This does not implement opaque cursors or typed audit projection. Client MCP file
publication remains after complete terminal verification: its owner fsyncs the
private temporary file, links the final destination and fsyncs the directory.
Those client operations are outside the host gate; no hard real-time filesystem
bound or client-buffer recall is claimed. Interrupted relay data never becomes a
successful result or complete output file. Exhaustive commit routing, projection,
query freshness, governed visibility and full surface acceptance remain separate.

The G0 inventory successor pins the permission catalog's source to
`3f926ee62543d308dad8f60e707b4cabd82fc755`. Unlike the historical B1/B2
metadata-only increments above, this corrects the conditional-source inventory:
`payment_authority.authorize_publication_selections` calls `require_resource`
at payment_authority.py:637 using `_PublicationSelectionCohort.requirements`.
Reads require ledger.read/member; writes require ledger.post/standard. Both
also require customer-work at the corresponding threshold when any complete
historical selection item, recovery item, revision payment, consumed-operation
transaction, or their directed historical paid target has work allocation.
Missing or malformed evidence remains fail-closed with the existing owner;
this static inventory neither runs nor replaces its graph predicates.

The additional ResourceSource and source version change the canonical catalog
manifest digest. Commands, capability/threshold universes, company/admin actions,
and seed defaults are unchanged; no new grants or Delete availability result.
The old 7b2 catalog remains a historical receipt, not an exact current source
inventory. Persisted catalogs are decoded against their own version and digest;
this correction performs no database rewrite or automatic catalog replacement.
Any later replacement still belongs to the existing typed administration contract.

### Actual commit-owner transport ordering

`core/commit_hooks.py` records operation ownership at the actual transaction and
filesystem publication owners. `Session.commits` is a typed trusted hook; hosted
writer sessions receive the host's instance, while standalone sessions retain
existing exclusive-root ownership. It is not command input or an activation flag.
Conservative production commits close the host admission gate before visibility.
One barrier belongs to the outer operation, including nested finalizers; it reopens
only after all related stores resolve. Existing F1 response-wide release evidence,
staged h11 bytes and current local/OS permits govern retries and interruption.

Dispatch, explicit finalized appliers, login/logout, migration/rollout, structural
moves/recovery, GC, config projection and maintenance each declare their owner.
Token refresh's actual autocommit UPDATE is explicitly a nonreducing liveness
extension. Config mapping becomes effective at the original pending-intent commit;
file projection is later. Source/work/recovery-changing owners are conservative,
not classified by command verb or financial amount. No policy resolver changes.

Related commits share an outer operation. Scope exit reports committed, unchanged,
rolled-back or durable-partial outcomes only after watched transactions settle;
otherwise the operation stays pending until existing writer cleanup resolves it.
Old generations are never restored after rollback. Hooks neither add transactions
nor own rollback/retry algorithms. The admission owner's `finish_commit` retains
the explicit boolean outcome classification, including durable-partial as committed,
but does not branch on it: close invalidates old generations even on rollback.
The hook notification seam takes the admission mutex only for constant-work close
and finish; it does no event-loop waiting, response enumeration or filesystem I/O.
Waiter notification is scheduled outside the mutex. Database tracking at each
commit is constant work. Independent literal owner inventory and
real owner-branch tests live in `tests/test_commit_hooks.py`. Their test-only gate
observer proves routing order; it is not live transport/permission acceptance.

### Private deposit G1 storage and effects

`deposit_models`, `deposits`, `deposit_validation`, `deposit_sources`,
`deposit_resolution`, `deposit_composition`, and `bank_effects` implement private
strict deposit inputs, exact funding/disposition graphs, stored and prospective
whole-cash adapters, current-fact checks and bank/card statement identities.
Payment credit ownership remains separate from the payer's cash dimensions.
Sales net/tax identities retain commercial-line/tax-item ordering. Source plans
retain their complete settlement, billing, custom-field and date evidence and
are revalidated without invoking a child applier. Payment and sales-update source
fingerprints are compared exactly; existing sales void has no source fingerprint
and retains its version contract. Coordinated all-active unapply/void, aggregate
identity mapping and one-event persistence remain lifecycle responsibilities.

Company revision `co0020` adds deposit envelopes, immutable semantic occurrences,
funding/offset/header components, allocation cells, exact claim/release history,
a unique current receipt claim, and versioned bank-effect keys/current pointers.
It preserves old history and local SQLite objects and adds no deleted status.
New source attribution proves both the deposit-owned envelope and the separately
owned receipt attribution. Null deposit attribution is omitted from older
serialized source facts. This private increment registers no deposit commands,
GUI, public lifecycle, Delete execution or reconciliation certificates.

`storage.migrate.FeatureRevision` and `feature_admission` provide the shared
migration-ancestry boundary for independently owned features. Unknown revisions
are denied. A feature before its declared revision is absent; an active feature
requires its real resolver. `deposit_dependencies.RECONCILIATION` explicitly
records that reconciliation persistence is not yet installed. Its owner must
install revision metadata and the resolver together with its feature migration;
there is no active-feature empty fallback.

### Response-wide F1 with actual commit ordering

`ResponseRelease` counts final/business bytes across headers, chunks, SSE and
terminal frames in the same mutex as their actual bounded socket/buffer handoff.
HTTP100 interim bytes have a separate protocol-only counter. An invalidated
zero-final-byte response may wait and revalidate its captured result; any previous
final byte or uncertain handoff forbids retry. Rollback never restores tickets.
The owned `AdmissionCycle` encodes each h11 part once, keeps those pending bytes
through zero-release revalidation, and marks completion/advances keep-alive only
after admitted EndOfMessage. It retains upstream event handling with only the
cycle constructor replaced. Header bounds include protocol defaults. Fixed
protocol errors cannot replace an already aborted business response.

One absolute30s F1 cutoff starts at first release, not business execution. Earlier
local/transfer/MCP deadlines cap it; later phases cannot extend it. HTTP waits are
loop tasks, local waits use their dedicated connection thread, and both observe
disconnect/shutdown. Validation workers hold no reader while waiting. Flow-control
waits detect invalidation too. Writer finish only schedules the loop notification
outside the mutex; a stopped loop or saturated validation pool cannot block COMMIT.

Local replies now use the same captured publication permit with typed `OSBinding`:
effective login mapping and current actor are checked at dequeue and release;
existing fixed principal bindings are rechecked without fabricating an API token.
OS active-user eligibility stays the existing resolver's policy. Captured token,
source, payment/recovery graph and narrow own-effect checks remain unchanged.
Ready/body/final local transfer phases replace their guard but retain cumulative
bytes. An empty preparation/output lease is surrendered before a zero-release
wait and reacquired from the same current digest/size/store facts, with its original
deadline and no command replay. Consumed input is closed/aborted, never rebuilt.
MCP intent/recovery delivery exposes its actual transfer to this same lifecycle.
Post-send local truncation is unknown outcome, never automatic offline fallback.

Offline downloads verify into an owned disk spool under RootLock, release that
lock, then deliver the complete captured file to a possibly blocking/short-writing
caller sink under the original deadline. Hosted client relay, SDK complete-message
verification and link-based destination publication retain their D1 ownership.
This is not remote recall or an overwrite-semantics change.

Production commit hooks close conservative operations and retain the classified
nonreducing refresh/projection/expired-cleanup exceptions. Their shared
depth/operation/outcome state is bound to the actual host writer Thread before
startup; wrong/missing writer ownership rejects before state mutation. Readers
never commit through the shared hook. FullC projector/granular/retirement policy,
minimal authority-losing own-effect acknowledgments and opaque cursors remain
separate acceptance gates. The chosen explicit company-membership policy is not
activated by transport ordering. Later deposit integration must preserve its
`feature_admission` changes and run the exact metadata/admission witness.

### Private ordinary deposit lifecycle (G2 increment)

`deposit_lifecycle.prepare` resolves a complete ordinary inline post/replacement/
void under the caller's company snapshot. `deposit_persistence.execute` requires
an already-owned writer transaction, re-prepares the exact intent, compares its
fingerprint/opaque dependency guard and independently validates every owned
financial row before DML. It neither dispatches a child source command nor
commits. Immutable revisions, row/component occurrences, release/claim history,
source-header coalescing and stable bank-effect versions share that transaction.
The independent persistence validator checks full component ownership and bank
amounts as well as the exact arithmetic validator's equations.

Company `co0021` appends permanent deposit-family operations, complete target
indexes and ordered typed receipt items; `co0020` is unchanged. First no-effect
operations receive one audit/operation record without financial changes. Exact
currently-authorized recovery reads immutable effects plus current state and
writes nothing; mismatch proceeds through ordinary new-write admission. Its
hook-compatible adapter remains private and unregistered. Existing payment and
sales-receipt update/void planners and final appliers reject active deposit
claims through the migration-chain feature resolver. Pure source preparation
remains available unchanged to the future atomic coordinator.

This increment does not activate deposit public commands, drafts, Delete,
coordination/all-active-unapply, granular permission setup, reconciliation,
full-C delivery, or G3 queries/UI. Private deposit history A supplies authenticated immutable baselines and
attributed, paged business conflicts; this is not a claim of complete G2. Complete replacement
source-memo omission selects the captured source memo; explicit null retains
G1's entered blank. Account activity remains required for fresh postings;
exact historical inverses do not re-admit those accounts.


### Captured account use admission

`company/accounts.py` treats immutable typed commercial account references as permanent uses when an account's type or currency changes. Fixed SQL paths cover sales profiles/lines/tax components, work revisions/lines, payment profiles and deposit Effect profiles. Actual Alembic feature ancestry controls which queries exist on the current snapshot; unavailable old-schema families are skipped, unknown schemas are not interpreted as empty. The private revision view exposes the existing connection's driver only for marker admission, including the conn-only undo domain wrapper. No document visibility filter or count enters this business integrity predicate. Existing posting/master error precedence remains, followed by generic captured-use errors.

`company/sales.py` validates current mapped-account role eligibility at its existing replacement admission seam. Saved labels/mappings remain immutable: an already-ineligible capture requires an explicit eligible refresh/remap for a new effect. Historical reads, exact no-ops and exact original-date void inverses are preserved. Neither check repairs old postings or alters reconciliation identity. The query inventory covers accepted co20 deposit Effect storage; a future deposit lifecycle/draft shape must be checked at integration.

### Private reconciliation statement adapters (R0a)

`company/reconciliation_models.py`, `reconciliation_adapters.py` and
`reconciliation_proof.py` provide private immutable statement references and
versions over the accepted company co0021 source families. The closed registry
covers journal entries (including register translation), payment cash, sales
receipt control/net, retained invoice net recognition and persisted deposit bank
keys. These modules register no command, write no database, and do not activate
`deposit_dependencies.RECONCILIATION`.

References use their producer's stable commercial line plus closed role, or the
existing deposit bank key. Account is version data. Receipt control lines share
one remittance movement; net-recognition credits have a separate movement.
Journal lines and deposit roles remain distinct even on the same account.
Immutable revision/business-batch anchors change on metadata replacements;
removal and void retain the former account and explicit transition provenance.
Deposit inactive versions preserve the exact persisted business anchor and event.

Account population resolves current versions before the date filter. Independent
raw-leg coverage, unique ownership, exact inverse/source bijections and dated GL
sums must all agree. Inverses never become additional statement items. Sums use
unbounded Python integers; stored effect amounts remain within signed i64.
Source currency is captured home currency. A foreign account produces a typed
unsupported result after complete source authority; its GL completeness is not
attempted and no original amount is reconverted. Home accounts with foreign
original journal facts remain supported at captured home units.

Every call uses the caller's existing company snapshot and current resource
owner. Historical applications, operation participants, recovery attempts and
deposit sources remain authority-bearing after release/abort/void. A hidden
source denies the whole account without exposing its identifiers or counts.
No authority result or graph survives a request. There is no live granular-policy
or publication integration claim: future public consumers must capture and
revalidate their complete disclosure roots at their current publication fence.

`prepare_prospective` retains the actual source Plan or actual materialized G2
bundle alongside the complete changed versions. Bounded public previews and a
source composition's UF `cash=None` cannot substitute for that aggregate.
Register adapters require the actual inner journal Plan. Future B/C coordination
must retain and validate every participant under its single writer; this private
projection does not implement or commit that coordinator. Foreign-account
prospective aggregates return unsupported, never a partially proved change set.

Persistent reconciliation links, opening coverage, drafts, certificates,
amendments, undo, reports, public interfaces and foreign statement/FX units remain
separate required ownership and acceptance gates. No certificate membership or
clearing decision transfers implicitly to a replacement version.


Private deposit history A (`deposit_dependency_models/history/pages`) reconstructs
complete owner and negative-relation facts from company audit entries and owned
immutable revisions, including receipt settlement, work ancestry, claims, bank
versions and original selector/default choices. Opaque recipes bind the exact
original input/context, relevant immutable endpoint and existing actor/principal
execution binding using the database-owned report key. No global audit position
is serialized. Every capture/issue/reconstruction/comparison/page rechecks current
resource admission; pages follow complete comparison, default50/max200. Ordinary
owner-version errors use the same proven history and an explicit inspection recipe.
New-post issuer names use the authorized selected hub company row with its latest
name-changing/create entry in one read snapshot; corrections retain their original
issuer and do not read hub name history. Missing/contradictory owned evidence is
unknown history; ordinary changes, invalid recipes and authority loss are distinct.
Automatic deposit numbering is reconstructed from co21 permanent automatic-post
receipts, with the live series checked against those effects, and number occupancy
has explicit negative relations. The private persister retains the existing
preview credential and revalidates its produced guard without inserting internal
guard fields into original request provenance. These readers do not write or
repair company copies. Full-C audience/publication integration, public deposit
commands, aggregate source persistence and performance acceptance
remain separate; no alternate identity binding or live permission policy is added.

Deposit history orders company events by their company sequence first, then hub
issuer events by their separate hub sequence, with stable record/field ties.
It does not claim a cross-database chronological sequence; each event retains its
timestamp. PostInput may omit a caller-supplied guard for a fresh post. The private
Prepared result still carries the guard produced during preparation, and execute
passes it back as expected_guard when repreparing before DML. Thus executing a
prepared post rechecks its baseline even when the original input omitted a guard;
a supplied input guard additionally checks an earlier caller preview. Exact
permanent recovery skips that old business guard under current admission.
Reconciliation currently provides only checked pre-feature absence, not a signed
read-set anchor. Active resolver admission fails closed; its future integration
must add the actual reconciliation dependencies rather than silently treating
that status call as a captured fact.

### Private reconciliation storage N (co0022; activation OFF)

`company/reconciliation_schema.py` describes 44 additive company tables. The
frozen co0022 migration creates empty storage, preflights every planned object
name across attached/local namespaces before DDL, and leaves existing objects
and rows untouched. It adds no index to an existing table. Reciprocal deferred
FKs admit complete effect/attempt subtypes in one transaction; immutable evidence
rejects update/delete. Mutable chains, revisions, claims and attempt barriers
have explicit transition guards. An attempt's `audit_event_id` identifies its
latest admitted state event; its `created_*` fields retain original attribution.

`reconciliation_storage_validation.validate` accepts complete explicit new-table
rows, referenced old-table rows, an already authorized source Graph, and explicit
historical capture Graphs for openings/certificates. It performs no database read,
backfill, repair or authorization. It checks ownership even when a migration has
FKs disabled, proves physical source history/current heads, and checks captured
populations, whole movements, arithmetic, lineage, claims, receipt tails/hashes,
and staged intent subtypes/chunks. Canonical-intent data is separate from original
request evidence. Storage envelopes are not future public command admission
models. Statement side counts count movements; sums count all component amounts.

Event effects retain one transition per event/key, a required new version,
its exact predecessor (null only for creation), and the causal source audit.
Private validation derives predecessors from the validated producer histories
and checks complete transition coverage for the event's source audit, including
zero-amount metadata and inactive transitions. Typed operation targets retain the
source transaction. No contextless impact or free-text cause is stored. Event
before/after account/cutoff deltas and certificate-to-current selected impacts
are different projections over these immutable facts; neither is a cached value.

`deposit_dependencies.RECONCILIATION` remains None. A separately reserved future
activation revision must rebuild/backfill authoritative history, install the real
resolver and all five source-writer fences coherently, and meet its old-binary,
full-C, public contract and interface gates. This increment supplies none of those
behaviors. The accepted source has no public `company verify` command; private
validation and SQLite integrity/FK checks do not claim to implement that command.

Opening evidence has closed `transaction` and `transaction_attachment` kinds.
Both retain a real transaction FK; attachment evidence additionally retains the
attachment and link FKs. SQL and private validation compare the link's transaction
type, retained transaction ID and attachment ID independently. Transaction-only
evidence has null attachment columns. These checks prove identity, not audience
admission. Direct opening attachments require later record-owner registration;
link retirement/collection and current authority remain activation obligations.

Private deposit source coordination prepares one typed source action and a complete
deposit replacement or void. Keyless payment correction/cancellation cores retain
full owning Plan.data; public wrappers retain their permanent payment envelopes.
The all-active cancellation set includes every application, allocation and affected
invoice without a transport-page cap. Source effects, current claims, prospective
cash, work/tax/custom facts and bank redirections are independently validated;
header changes coalesce once. Explicit typed identity maps preserve persistent
component/row identities and arbitrary memo/custom values. Ordinary writers still
reject claimed sources. This preparation performs no child DML or aggregate write.

Coordinate guards bind the complete submitted intent and current actor/principal
through the existing execution binding. Source selectors, captured price versions,
unit aliases including retired children, and refreshed source defaults reconstruct
from their actual immutable owner histories. Sparse new-contact audit images decode
the existing contact producer's nullable fields; missing required fields and wrong
child ownership remain unknown history. Shared sales fresh issuer capture uses the
dispatch-pinned authorized company name; nonrefresh corrections retain their saved
issuer. Only coordinated refreshed-source guards additionally carry the selected
hub name-changing/create anchor, coherent with that pin. Deposit correction issuers
stay immutable. Aggregate persistence, permanent coordinate recovery, preserving
operation-schema extension, Delete/drafts, public workflows and full-C publication
remain separate prerequisites.

### Private reconciliation successor models and preparation

The private `reconciliation_commands_models` module describes strict inputs,
retained draft/proposal and attempt leaves, and typed candidate/history/report
values. Its name-to-model inventory is not a command registration. Parsed JSON
arrays become immutable domain tuples without coercing their element types.
`reconciliation_preparation.snapshot` consumes complete N rows, actual R0 source
and capture Graphs, referenced rows and explicit current authority participants;
it runs the accepted aggregate validator. Participant coverage is not an authority
credential. Future runtime owners must obtain the real complete authority graph
before preparation and repeat admission at each release/commit fence.

Draft transformations retain saved versions, require whole producer movements,
reject stale replacements without explicit accept-current, and preserve proposals
and terminal states. Attempt chunks retain exact ordered typed payloads and
receipts; seal checks counts, hashes and semantic targets, and apply changes only
private values. N tables remain empty outside owned tests. Query cores compute
whole-result facts and return bounded deterministic slices; their offsets and
digests are not public authenticated cursors. Public cursor signing, fixed actor/
principal/OS binding and publication remain activation work.

Report projection keeps as-certified arithmetic and captured member displays,
one-certificate selected-key replacement impact, and the opening/predecessor-key
cumulative reconstruction separate. Current mapping precedes account/date filters.
Amendment preparation derives mandatory and explicit-seed suffixes, validates
whole replacement/invalidation manifests, and rebuilds adjacency in dated order.
A staged predecessor FK guards the retained predecessor; a later aggregate
persister must assign and link the new certificate IDs in that derived order.
Undo prepares invalidation without a financial inverse.

Proposal previews retain exact ordinary journal Plans, validate them through R0
and the existing current period owner, and never apply them. Force amounts derive
from exact difference, with bank/card sign conventions. Permanent recovery retains
original JSON/presence/context independently of canonical captured intent and
returns immutable effect separately from current state after explicit authority
coverage. Actual original-action admission and storage lookup remain runtime
obligations. Source-action arms retain present owning input types only; they do
not implement deposit B/C's keyless compound persistence or its permission gates.

Insertion targets now use the exact owned decoded replacement revision's
account/date; existing certificate identity is certificate ID alone, independent
of mode/account/predecessor. This joined semantic key is enforced by N's private
validator and attempt seal, **not SQL UNIQUE**. SQL retains typed owner/revision
FKs, discriminator shapes and final active account/date uniqueness. No co0022
DDL/metadata change is made; future persistence must validate before DML.

Certificate/seed attempts require an explicit ManifestContext containing the
owning before-source graph, current authorized participants, captured chain
versions and opening actions. Seal derives mandatory plus explicit seeded closure,
loads exact owned open draft revisions and reconstructs the complete dated suffix.
Observed predecessor IDs guard retained topology; they never dictate output
adjacency. Seal rejects all resulting account/date collisions, including insertion
versus replacement and two replacements, plus missing/extra suffix and stale
revision/chain facts. Apply repeats these checks against the current snapshot.
Member/proposal-only attempts keep their whole-selection/current checks and need
no invented source-change context. Returned immutable attempts retain the complete
manifest for future persistence; staging never certifies zero or posts money.
Financial amendment preparation additionally proves whole selection, authentic
opening partition and exact per-statement zero using derived adjacency.
No resolver, backfill, public capability, live draft writer or activation is added.

### Private reconciliation review corrections (F1–F5)

Proposal preview binds supplied revision IDs exactly to the draft's saved set,
with no omissions, extras, substitutions or duplicate IDs, before statement
arithmetic. Explicit force proposals also belong to that saved revision set;
preview has no unsaved-proposal exemption. Ordinary Plan identity, prospective
owner validation, current period and full authority remain required.

Every private report/query/component/history/draft-show/attempt-item/progress/
operation-item/recovery reader takes a Snapshot and explicit current authority
participants. Read admission checks complete retained/current graph coverage
before account support/proof, content, filters, totals and paging. Snapshot
construction alone is not authority for a later read. The actual runtime owner
must obtain these participants from current authority and supply the complete
required graph, including historical attempts/operations and captured worlds;
there is no Boolean permission or synthetic Session. This private whole-snapshot
proof is not a paging latency claim or the future publication fence.

ReconciliationError now derives from the project's BookflowError. Existing
shared codes (including query/preview/version staleness) retain their exact codes
and serialization. Until public registration, private closed reconciliation
reasons use E_VALIDATION with a fixed reason field, or E_INTERNAL for invalid
owned source. Activation must register the planned domain codes/statuses and
query-only factory path; this increment does not mutate the public error catalog.
Known adapter Unsupported/Corrupt and owning content-format failures convert to
these typed outcomes; IO/DB failures and existing BookflowErrors propagate through
their existing owners. No raw source exception text becomes returned details.

Seal explicitly checks the current edit barrier, allowing its own attempt only.
Missing-range inspection validates contiguous chunk indices, receipts and hashes,
and returns the exact unreceived ordinal suffix (zero ranges when complete),
without a 200-item aggregate cap. Terminal sealed/applied attempts must be complete.
Proposal removal is an immutable draft-link revision; cancellation is the parent
draft's terminal revision/operation. Proposal headers have no terminal column in N.
Thus unconsumed proposal availability is derived from current parent state and
revision links; historical revisions and consumption records remain intact.
No schema contradiction or new proposal-state column is introduced.

Semantic attempt-target uniqueness has **no database constraint behind it**:
it is enforced only by the aggregate validator and seal. SQL still independently
checks typed ownership/shape and the final active account/date projection, but
cannot enforce the joined draft-header date key. Future persistence must run the
aggregate checks before DML; no co0022 rewrite or activation is included here.

## Private deposit coordinate persistence

`deposit_coordinate_persistence` consumes the complete validated keyless source and deposit proposal in one caller-owned writer transaction. Payment, sales and carried-billing collectors declare their row/touch families; source financial producers and ordinary claimed-source fences remain authoritative. The deposit posting lookup admits only the exact typed prospective source alongside stored facts. Source rows, both custom plans, deposit revisions/cells/releases/claims, bank versions/current pointers, one coalesced update per affected header and one permanent operation share one event. The independent coordinate validator checks original full source rows, deposit equations and full owned images, generated identities, item receipts and audit bytes before business insertion. A nested savepoint checks the caller's initial FK state and the complete final deferred FK state explicitly; rollback preserves unrelated valid caller work. This private service does not commit or invoke child appliers.

Coordinate v2 receipts retain typed complete source before/inserted rows, source-owner effect collections, deposit effects, the generated identity manifest and full transaction authority targets. Recovery revalidates the actual execution binding and the complete saved graph before comparing intent; original effects are immutable and current states are loaded separately. Permanent item pages use explicit per-kind models and authenticated actor/principal-bound cursors. Preview pages apply only declared typed generated-reference substitutions to those collections. Immutable pages authorize the full saved graph on every call and do not reload unused current-state payloads. Coordinate audit row kinds have private scalar/cohort evidence mappings, without adding public annotation target types. Ordinary v1 history and audit encoding remain supported; coordinate history is selected by command/version before decoding.

Company co0023, parent co0022, rebuilds only the two deposit operation tables to extend their command/item CHECKs, retaining raw values and supported local DDL. Shared audit preparation uses the existing encoder and preserves the ordinary writer's default behavior. No public deposit coordinator, draft/provider, Delete, reconciliation activation or permission/publication activation is supplied by these private owners.

Private coordinate persistence exposes `execute_applied` for a future owned adapter:
its `Applied` is audited and not finalized, since the caller still owns commit.
No registered command consumes it. Original pages decode complete per-kind models;
preview pages expose a distinct `PreviewPage` logical-reference projection after
validating those same complete per-kind models. Logical IDs and `aggregate/at`
are not stored ID/date values. Saved root/index authority precedes inspecting
request corruption, and every available request/effect root is admitted before
reporting completeness differences. This does not activate publication or an API.

co0023 drops/recreates only the transitive dependent view/trigger closure and the
two rebuilt tables' indexes. Conservative literal/comment identifier mentions
remain dependencies when SQL syntax cannot disambiguate them. Unrelated guards
stay installed. Final unchanged external SQL and full foreign-key declarations
are compared against their original values, in addition to raw-value and FK checks.
Ordinary deposit validation retains financial/identity checks, the original
validated no-effect return, then changed-source admission before remaining row
checks. Ordinary preparation/execution still rejects stale unchanged requests.

The coordinate evidence-owner completion also protects pre-existing audit events;
it can add roots and deny history previously admitted with incomplete authority.
It adds these mappings to the private evidence catalog, with no new public target
or capability and no pure permission-catalog pin refresh:

| Evidence kind | Table | Identity field |
|---|---|---|
| deposit_profile | deposit_profiles | revision_id |
| deposit_row_key | deposit_row_keys | id |
| deposit_component_key | deposit_component_keys | id |
| deposit_component | deposit_components | id |
| deposit_cash_cell | deposit_cash_cells | id |
| deposit_membership | deposit_memberships | id |
| bank_effect_key | bank_effect_keys | id |
| bank_effect_version | bank_effect_versions | id |
| work_billing_allocation | work_billing_allocations | id |
| sales_profile | sales_profiles | revision_id |
| sales_line_profile | sales_line_profiles | document_line_id |
| sales_tax_component | sales_tax_components | id |
| sales_tax_line_key | sales_tax_line_keys | line_id |
| sales_tax_attribution | sales_tax_attributions | revision_id |
| sales_tax_attribution_line | sales_tax_attribution_lines | document_line_id |

The private runtime catalog bundle's `SOURCE_COMMIT` identifies the verified
accepted resource-inventory base `3990636d2a2d3c683a0e806e6635a4a64c0075b0`,
not the final commit containing the bridge. Union review checks full actual
resource call-site parity. Changing this provenance changes the bundle identity
digest, even when the policy descriptor and inventory digest are unchanged.
Existing historical descriptors are not automatically adopted or rewritten;
they still require the separately admitted catalog transition.

### Private audit projection recovery

`hub.audit_projection` and `core.publication_audit` provide a private typed
history service through an authenticated reader and retained publication proof.
Each release check uses a fresh reader. Public audit/activity commands still use
their existing contracts; this service does not activate granular permissions.

Payment selection header, immutable revision context, and set/remove/clear item
history use closed models in `audit_projection_legacy`. Entered/calculated/
unresolved amount origins and the original field-presence state are preserved.
Current denied reference fields use the established null projection; entitled
fields absent from a historical capture are not added from model defaults.

Ordinary payment receive/apply/unapply/update/void operation captures use
command-discriminated original intents and closed recorded result models. Stored
input is validated against its owning command schema without substituting today's
result. Captured provided-field lists preserve omission, excluding internal fields
and currently denied references. Monetary effects and historical posted/voided
state remain original after later corrections. Operation items use five typed
collection variants. Required captured references reject null before permission
projection; permitted masking does not make null captured input valid.

Internal hashes, guards, execution reasons and directive metadata are excluded
from these views. Actor and principal identities use current identity disclosure.
Committed receipts contain no prospective preview-page descriptors. Field-set
conformance compares ordinary payment inputs, results and stored header columns
against their declared models.

Invoice-update operation captures retain the complete original invoice result,
including its commercial lines and settlement effects. The edit becomes the current
invoice; the retained operation is internal retry and audit evidence. Invoice
settlement document changes contain typed invoice and payment balances. Their
settlement keys and allocation rows have an explicit invoice-update producer.
Original command inputs and captured results are validated against their owners;
current reference permissions apply to both. The checked-in field inventory covers
the invoice-specific models and rejects unclassified schema growth.

Saved payment-recovery audit records use closed begin/upload/seal/apply/abort/replace
request variants and typed receipts. Header, chunk, attempted item and active-barrier
records have explicit producer/action allowances. Apply and abort append selection
revisions; replacement preserves the selection and replaces the active barrier.
Captured request omission, exact amount origins and retained calculation identities
are preserved. Internal intent/request hashes, attempt generations, freshness
assertions and free-text context provenance are omitted. Actor identities use the
same current identity disclosure as other operations. Unchanged projected headers
are omitted even when their raw version changes during an upload.

Deposit operation variants,
complete producer/format conformance, no-entry semantics and public cursor/transport
cutover remain required. The
private implementation is not a complete audit or full-C release.

Private recovery audit decoding binds each begin/seal/terminal and upload request snapshot to its stored SHA-256 before typed projection omits internal fields. Stored strings use their exact UTF-8 bytes; already-decoded object input uses the original producer canonical encoding. Body/digest binding is separate from request-action/receipt-hash agreement, which remains checked for every retained phase. Original audit bytes are never rewritten.
