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
same registry reads. The application pages read an application's payer as a customer
payment, which is what `application show` itself does, so they serve only the rows a
receipt settled. An application row names which it is: exactly one of
`source_component_key_id` and `credit_source_key_id` carries its capacity, and a row
carrying the second was settled by a credit memo rather than by a payment. The invoice
settlement page reads that key and sends a credit-settled row to the credit memo that
paid it, because the application views answer `E_RECORD_NOT_FOUND` for a payer that is
not a payment and a link to a page that cannot open is worse than no link.

`payments.js` persists amount/row origins through shared
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
    hub_migrations/      Alembic chain "hub": hub0001 (frozen explicit tables), hub0002 (seq, directive_code, idempotency_keys), hub0003 (capability/feature metadata), hub0004–hub0005 (list capabilities), hub0006 (pending config projection), hub0007 (note capabilities), hub0008 (attachment/activity capabilities), hub0009 (agent principal assignments, authority epochs and credential conversion), hub0010 (ledger and report capabilities), hub0011 (customer-work read/write role defaults), hub0012 (permission-administration storage), hub0013 (identity and membership capabilities)
    company_migrations/  Alembic chain "company": co0001 (frozen), co0002 (audit/presence/directives), co0003 (20 supporting lists), co0004 (job delivery inheritance), co0005 (notes), co0006 (attachments, links, collection intent, byte limit), co0007 (journal identities, immutable revisions and postings, numbering prefix, private report cursor key), co0008 (journal header custom ownership), co0009 (commercial sales), co0010 (nonposting customer work and preserving custom scope CHECK widening), co0011 (immutable linked billing and preserving sales amount-price widening), co0012 (exact progress allocation proofs, fractional sales quantities and overlap guards), co0013 (company work preferences)
    migrate.py           + migrate_company(): the one owner of company migrations: migrate entry by the system user, baseline entry, marker, hub projection entry
  hub/
    schema.py            users, api_tokens, agent_principals, agent_authority, organizations, companies, memberships, role_capabilities, features, audit_events, audit_entries; co-located table and column descriptions
    users.py             bootstrap users, create_human(password_hash=...), common() field helper, user_names()
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
  adapters/cli/render.py tables, field views, JSON, errors on stderr; complete nested money values render with exact currency scale in text tables and fields, including money columns first populated after the first row; JSON preserves the command payload; money columns follow business names/numbers before metadata; an empty visible-column selection does not restore hidden identifiers
  adapters/http/app.py   FastAPI app from the registry: /commands/<noun.verb>, authoritative /companies/{id}/commands/<noun.verb>, /login, /logout, async /companies/{id}/events and /hub-events, exact generated /openapi.json, /health; credential/cookie handling and the same error documents as the CLI with HTTP statuses
  adapters/http/auth.py  argon2 passwords (constant-time on unknown users), bearer and session tokens stored as sha256, liveness refresh, login throttle
  adapters/http/local.py LocalListener on the Unix socket: peer identity from SO_PEERCRED, envelope identity fields discarded, 8 MiB frame cap and 30-second accepted-connection timeout
  adapters/workbench/    pages.py (picker, hub/company indexes, bounded list/record/form/audit pages), forms.py (input model -> leaves and command JSON with originals, tri-state booleans, clears, Preview), document_form.py (sales document bands, line grid columns with a hint per head, human labels, the per-line pricing rule in the row panel), document_nav.py (the way back from a document to earlier documents of its type), sales.py and bills.py (what a saved sale or bill shows, and what its correction form opens with), credits.py (what the three credit documents show, which lists their pickers search, the two seeded openings, and the credit apply/unapply routes), document_print.py (the four print routes that answer PDF bytes), list_paging.py (which lists open on the newest record, and the walk forwards and back through a list's pages), naming.py (page titles and column heads in a person's words), workflows.py (customer/job display groups), templates/, static/ (vendored htmx, reference-selection client, content-versioned assets)
  adapters/workbench/    pages.py (picker, hub/company indexes, bounded list/record/form/audit pages), forms.py (input model -> leaves and command JSON with originals, tri-state booleans, clears, Preview), document_form.py (sales document bands, line grid columns with a hint per head, human labels, the per-line pricing rule in the row panel), document_nav.py (the way back from a document to earlier documents of its type), sales.py and bills.py (what a saved sale or bill shows, and what its correction form opens with), document_print.py (the four print routes that answer PDF bytes), list_paging.py (which lists open on the newest record, and the walk forwards and back through a list's pages), naming.py (page titles and column heads in a person's words), routing.py (the one spelling of a noun inside a URL, and the resolution of a path segment back to it), workflows.py (customer/job display groups), templates/, static/ (vendored htmx, reference-selection client, content-versioned assets)
  documents/model.py     command output -> PrintedDocument: parties, header fields, columns, rows, totals, grids, notes; no PDF, no HTTP, no arithmetic
  documents/pdf.py       the one layout: Letter, half-inch margins, repeated column headings, unsplit line items, Page X of Y (reportlab)
  documents/render.py    render(read, company_id, kind, identity) -> Rendered(filename, media_type, content, title); the seam a later attach or send command calls
```

`bookflow/documents/` produces the four customer-facing documents as PDF and is the only
place their layout lives; [printed documents](printed-documents.md) states what each one
carries, which facts are captured and which are current, and what is deliberately absent.
`render` takes a `read` callable rather than a request, so the web routes in
`adapters/workbench/document_print.py`, and any later command that attaches or sends a
copy, produce identical bytes from the same description. Permission and company isolation
stay in the commands `read` runs.

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
- `commands/host_cmds.py` also registers the three identity commands that make more than one
  workstation possible: `user add`, `membership grant` and `membership revoke`. `user add` is
  `hub_admin`, human-only, and creates one human with `users.create_human`; given `--company` or
  `--organization` it makes the first grant in the same transaction and the same audit event.
  Supplying no password generates one and returns it once, in the shape `token issue` uses for its
  secret; supplying one returns null there. Both membership commands resolve their scope through
  `dispatch.resolve_company` / `resolve_organization`, so a scope the actor cannot see answers
  exactly as an absent one does, and then require, from `hub/access.py`, administration of that same
  scope — owner to grant, move or revoke an owner — which is the `admin:members:<role>:<domain>` row
  of the frozen catalog rather than a new model. Both write through one `_grant`/revoke pair, so a
  revoked row is reactivated in place rather than duplicated against the `uq_membership` constraint.
  They are in `publication.MEMBERSHIP_EFFECTS`, so a member who hands back their own access still
  receives the answer: the permit reconciles the actor's own membership change against this request's
  own audit, and `host_cmds.republish_membership` therefore re-checks current scope administration
  only when the target is someone else. Memberships are read fresh on every request, so a revocation
  takes effect on the next call, on the next drain of an open event stream, and through an
  already-issued bearer token, none of which carry authority of their own.
- `user list` and `membership list` read the same identity back. Both decide one `host_cmds.Audience`
  before any user is looked up: a hub administrator sees every scope; a named `--company` or
  `--organization` is resolved by the same `resolve_scope` the writes use and then narrowed to the
  caller's own rows unless `host_cmds.administers_scope` says they administer it; with no scope named
  the audience is the scopes the caller administers plus their own memberships everywhere. A scope
  filter admits the membership rows that reach inside it, by the rule `access.company_role` and
  `access.visible_org_ids` already read, so a company lists the organization-scope memberships that
  cover it and an organization lists the company-scope memberships inside it. `--user` is resolved
  only within that audience, so a name nobody holds and a name held by somebody out of reach both
  answer `E_USER_NOT_FOUND`. `user list` returns `kind`, `hub_admin`, `active`, `added_at` and
  `acts_for`, which names the human an agent principal acts for; agents are labelled, never hidden,
  and `--kind` narrows to one. `membership list` returns the role, the scope, `active` and
  `revoked_at`. Both capture their audience's scopes in the publication permit and re-decide it on
  release through `host_cmds.republish_listing`, so a scope lost between execution and publication
  takes the answer with it.

- `hub/credentials.py` validates active identities and agent principal/assignment/suspension/epoch state for token authentication and issuance. The JSON HTTP/workbench command executor revalidates the admitted bearer or cookie through that same verifier inside the actual writer or reader session, before planning, preview, replay or effects. The private credential handle also checks that token id, user id, kind and principal match admission; no secret enters business input, Context or audit. This execution check preserves authorized self-revocation and does not claim a post-execution publication fence. Agent token principal and epoch are stored with the hash in one insert. Membership grants/denies and capability/feature projections remain compatibility state; full role/capability intersection, atomic membership reductions, reauthorization commands and publication fences remain Row7 integration work.
- `company/journal_models.py`, `journals.py` and `journal_outputs.py` validate and project domestic journals with two through 200 positive, balanced entered lines. Stable headers retain immutable revisions and commercial lines. Posting batches and accounting lines retain exact source allocations. Corrections reverse the previous batch at its original accounting date and append a replacement; void appends an exact reversal and retains the header. SQLite rejects modification/deletion of historical rows and header deletion. Period checks and full-aggregate version conflicts run again in the writer transaction.
- Journal numbering, header pointers, principal snapshots, revisions, posting effects, audit and idempotency share the company transaction. Ledger services preallocate their audit ID and return `Applied(audited=True)` without committing. Dispatch rolls a ledger no-op back to its business savepoint before retaining its retry result, so a ledger no-op does not refresh company principals. Ordinary list no-ops retain their principal-mirroring and projection-repair behavior.
- `company/ledger_reports.py` reads all effective posting batches, including reversals and replacements. Trial balance nets each account; general ledger pages opening/posting/closing rows with running balances computed before slicing. Lossless integer aggregation and numeric text collation avoid intermediate SQLite integer overflow and floating-point conversion. Public values outside signed 64-bit range fail with `E_VALUE_RANGE`. Report continuations authenticate complete state and first-page metadata with HMAC-SHA256 using a private company-local 32-byte key. Signature verification precedes decoded-account use. They bind filters, identity and relevant change watermarks, preserve first-page metadata, and restart with `E_QUERY_STALE` on relevant changes. The key is generated in migration, copied with the company, and excluded from commands, audit snapshots and annotation targets.
- Account show/list/query derive the account's own normal-side balance from posting lines and real history dependencies. Descendants are not rolled up. A posted account cannot be deactivated. Journal browser pages display historical line snapshots, balanced totals, revision links and stable-header notes/files; generated update forms preserve line identities and shown versions.
- `demo reset` accepts the public boolean `include_reference` (default false), with nullable reference company ID/name output. Opt-in creates both companies in the replacement demo organization through the same rollout and command seed paths. Reset always trashes the entire prior demo organization. After-commit seed failures raise `E_PARTIAL_WRITE` with the durable organization/company identities and incomplete company; previously committed seed commands remain saved. The packaged reference-year guide documents source arithmetic and twelve monthly checkpoints, including correction/void gross movements and second-half opening balances.
- The demo includes opening capital, a corrected service journal, an expense, a voided duplicate, bank payments and receipts, a credit-card charge and payment, and a corrected mixed split with a net payment of 10000 USD minor units. Its accrual trial balance as of 2026-12-31 is 663000 USD minor units on each side. Foreign-tagged posting and exchange-rate commands remain following increments. Fine-grained identity and publication controls remain unfinished identity work.
- A single-word command (`upgrade`) has no verb: its noun page is its form, and it submits to `/hub/<noun>`.
- Whether a noun's page is about one record or about the whole thing is `pages._record_selector`, which the navigation grid asks rather than reading `positional`: `payment recovery show` names its record with `recovery_id` and declares no positional, and reading `positional` sent it to a singleton `/self` page and its list rows to a `show` with no record at all, both of which answered 422.
- A noun with a space in it (`bill payment`, `sales-tax payment`, `hub audit`) is spelled with a hyphen inside a URL, because a raw space in an `href` is malformed markup: a browser hides it by encoding on navigation, a strict client refuses the link, and nothing that is not a browser can follow it. `adapters/workbench/routing.py` is the only place that spelling is made and the only place a path segment is resolved back to its noun. Its map is derived from `registry.NOUN_MODULES`, so a noun declared later is spelled and routed without anything being retyped; a segment naming no declared noun is handed to the route untouched, and the literal noun still resolves, so a link saved before the spelling existed still opens its page. `tests/test_workbench_noun_urls.py` holds the round trip and the absence of segment collisions, and the link crawl in `tests/test_row3_host.py` fails on any rendered `href` carrying a character a URL may not carry.
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
- Every routed command has a role-authorized form page with one control per input leaf. Clear wins over a prefilled value and unchanged rendered fields send nothing. Preview writes nothing. Ordinary submit returns 303; HTMX submit returns `HX-Redirect` so the successful destination reaches the address bar. A one-use, session-bound result flash survives one GET without entering the URL or cookie. One browser-level journey proves visible presence, overlapping stale-form conflict without overwrite, directive creation, and the resulting HTTP audit entries. Audit routes and invalid filters, picker schema state, and restricted actions/presence are covered. The link crawl walks page shapes rather than page instances -- an identifier segment collapses to a placeholder and each distinct shape is fetched once, so its cost stays flat as the demo grows while its reach grows with every page added -- asserts the number of shapes it reached, and reads every rendered `href` for a character a URL may not carry.
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

- No command deactivates a user account or maps an OS login to one; `active` is enforced everywhere a
  credential is resolved, and `user list` reports it and hides an inactive principal unless
  `--include-inactive` asks for it, but only a direct write sets it, which is why tests still create
  actors through the repository layer (tests/conftest.py::make_actor). Removing someone's access is
  `membership revoke`.
- `user list` and `membership list` return every row their audience admits; neither pages, as no
  hub-scope `* list` command does.
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


### Money-out documents: check and card charge

`check post` and `card-charge post` are the document surface over the account register.
`company/check_models.py` holds their inputs and `company/checks.py` translates them into a
`RegisterPostInput` and hands it to `registers.translate` and the journal writer, so money
posts by exactly one path and these commands take no accounting decision. `journals.persist_prepared`
is called with the document's own command name, so the audit trail says `check post` rather
than the register underneath it.

Two nouns rather than one with a payment-type flag. A check is drawn on a `bank` account and a
card charge on a `credit_card` account; each refuses the other's account type by name. The
funding account is credited either way, which is `direction: decrease` on the debit-normal bank
and `direction: increase` on the credit-normal card — `DIRECTION` in `check_models.py` holds
that pair and it is the only place the register's vocabulary appears. Only the check carries
`number`, so the check number is absent from the card charge's schema, its CLI flags, its MCP
input schema and its form rather than being accepted and ignored.

`expenses` is one to 199 lines of account, amount, memo, class and optional party, and they must
add up to `amount` exactly. A total that does not is `E_UNBALANCED_ENTRY` before anything is
written, carrying `expense_total`, `amount` and `difference` as exact money plus
`difference_minor_units` signed toward the lines, so the message and the document footer can both
name the difference. The write output is `MoneyOutWriteOutput`: the journal write output plus a
`document` summary holding the kind, the funding account and type, the amount, the exact expense
total and the line count. That summary is what the footer displays; nothing is recomputed in the
browser.

`check show`, `check query`, `check update`, `check void` and `check history` give the document
the lifecycle every other document has, and `card-charge` carries the identical six verbs.
`company/checks.py` derives what `show` and `query` report from the stored revision -- line one is
the funding line and the rest are the expense lines -- so no figure is kept twice. A correction is
the same translation with each surviving line identity retained and `operation='update'`, so the
journal writer appends an exact reversal of the old accounting at its old date and a full
replacement at the new one; a void is that writer's exact reversal at the document's own date.
`check update` accepts a different bank account, which is why `registers.translate` takes
`moving=True`: standing in a register an entry cannot leave the account you are scrolling, but the
bank account is an ordinary field on the check's own form. A field left out of a correction keeps
what was captured, and leaving `expenses` out keeps the saved rows verbatim rather than re-reading
accounts that may have been renamed since.

The same entry is still an ordinary register row, so `register update` and `journal void` keep
working on it and leave it a check; `tests/test_money_out_lifecycle.py` holds both doors open.

`money_out_documents` is what makes `check query` possible. A check, a card charge and a transfer
post the rows `register post` posts, so nothing in the posting distinguishes them; the table is one
immutable row per transaction saying which document a person entered, written inside the same audit
event as the accounting through `journals.persist_prepared(extra=...)`. Inferring the document from
the shape of its lines was the alternative and it is wrong: a hand-typed journal entry crediting a
bank account has a check's shape exactly.

**What the browser still needs.** The six verbs exist on every command surface; the workbench has
only the entry window. `Document.is_document` covers `post` alone, so `check update` renders as a
generated form rather than as the check; `Document.NO_LIST` still sends Cancel to the home board
although the generic list and detail routes now resolve; `Nav.NOUNS` excludes these three, so the
document navigation bar and a history page are absent; and no panel carries a "Checks",
"Credit card charges" or "Transfers" list tile.

**Where an Items tab attaches.** The line collection is named `expenses`, not `lines`. A second
line kind arrives as a sibling collection `items` on the same input models, translated into
allocations appended after the expense allocations in the same ordered list; the header, the
totals, the refusal rule and the stored journal do not move, because a journal revision already
stores an ordered line list with a `kind` per line and the sales documents already put non-journal
line kinds in it. In the window, `document_form.layout` selects the grid and the collection by
noun and returns `lines_title`; a second grid is a second band with its own column tuple beside
this one, and the phone block rules are scoped by `[data-collection-path=...]` so a second grid
gets its own reading order without touching this one. Nothing here builds, implies or reserves
inventory behaviour.

`adapters/workbench/document_form.py` serves both money-out documents from the same
document-window layout as the sales documents: `MONEY_OUT` selects the header band
(`MONEY_OUT_PRIMARY`), the `EXPENSE_GRID` columns and the money-out footer, and `NOUN_LABELS` /
`NOUN_DESCRIPTIONS` put each document's own words on the shared controls. The footer's rows come
from `money_out_totals`, which reads the server's `document` summary after a preview and the
refusal's own figures after a refusal, so the reconciliation is visible on the page that refused.
`pay_to` is a multi-target reference resolved by the `discriminator` declared on
`ReferenceDefinition`, which generalises the mechanism the sales-rep picker already used.

#### The check number, which is not the document number (co0040)

A check number identifies a piece of paper in one bank account's chequebook.
`transactions.number` identifies a document inside Bookflow and comes from the
shared journal series every journal entry draws from. Conflating them made
`report missing-checks` name false gaps on any real company file, and
`accounts.next_check_number` sat unread while it did.

`company/check_numbers.py` owns the cheque number and nothing else:
`canonical()` (the literal as typed, the key uniqueness compares, the place it
takes in a run), `identity()` (allocate, refuse or carry forward), `changed()`,
`rows()` and `write()`. Two tables in `company/money_out_schema.py` hold what it
decides. `check_instrument_revisions` is immutable, one row per revision,
carrying the account and number that revision was written with -- which is why
correcting either cannot rewrite what `check show --revision-number` or `check
history` said before, and why a later print surface will read a cheque as it was
issued. `check_instruments` is the current projection, one row per check,
replaced on a correction.

- **Allocation** happens inside the writer's transaction, never at preview. An
  unnumbered cheque starts at `accounts.next_check_number`, walks past every
  number that account has ever issued -- held now or retired by a correction --
  and leaves the pointer one past what it handed out. An account with no pointer
  starts one past the highest number its own cheques carry; an account whose
  pointer names no place in a sequence -- `EFT`, or `0` -- refuses rather than
  inventing one. The pointer moves with a plain `UPDATE` and does not bump the account's version, the same
  treatment `sequences.next_number` gets in `journals.persist_prepared`: it is a
  hint about where to start looking, and the audited record of what was issued is
  the instrument revision. A stale pointer written back by a concurrent `account
  update` costs a scan, not a duplicate, because occupancy is what decides.
- **An explicit number below the pointer is accepted and does not move it back.**
  Below the pointer is not the same thing as a duplicate. At or above it, the
  pointer moves to one past what was typed. A number a correction carried forward
  without naming moves no pointer at all -- correcting the memo on cheque 5000
  must not jump a book that is only at 3000, and moving that cheque to another
  account must not jump that account's book either.
- **Duplicate policy: refuse, and say so.** `uq_check_instrument_number` is a
  partial unique index over `(account_id, check_number_key)` where
  `origin = 'issued'`, and `check_numbers` raises `E_DUPLICATE_NUMBER` naming the
  cheque that holds the number before the constraint has to. `1001` and `01001`
  are one number, because `check_number_key` strips leading zeros. **Parity
  difference:** the anchor product warns and then lets a duplicate through;
  Bookflow does not, because a hard constraint and warning-and-accept cannot both
  be true, and because every finding the missing-checks report makes is ambiguous
  the moment one number can mean two cheques. The index is partial so that a file
  co0040 upgraded, which can already hold `1001` and `01001` on one account, still
  opens -- and the report tells the person about it.
- **A void keeps the number.** Voiding writes no new revision, so the instrument
  is untouched.
- **A correction retires the number it replaces.** The old account and number
  stay on the superseded revision for ever; nothing holds them; automatic
  allocation still refuses to hand them out; and the report prints them as
  `retired` rather than as a hole. Typing one again explicitly is allowed.
- **Card charges and transfers consume nothing.** `checks.py` sends a chequebook
  request only for the `check` noun, so the card charge keeps the document
  reference the card statement matches and the transfer keeps its own.

`journals.prepare` gained one keyword, `check_instrument`. `company/checks.py` is
the only caller that passes it; every other writer -- the journal editor, the
register -- leaves it None and the cheque identity is copied forward onto the new
revision untouched, because which chequebook and which number are on the paper is
not what those editors are editing. The identity is settled before `unchanged` is
computed, since a correction that only renumbers a cheque changes no accounting at
all and would otherwise read as untouched.

`money_out.resolve` looks a cheque up by the number on its face and **names every
candidate rather than picking one** when two accounts both issued it
(`check_numbers.ambiguous`, `E_VALIDATION` with the account of each). Its old
`t.c.number == selector` fallback still serves the card charge, the transfer and a
cheque addressed by its document reference, and it now refuses more than one row
there too instead of returning `found[0]`. `RegisterRow.check_number` puts the
cheque's number in the bank register's Number column beside the document
reference, which is what makes the register, `check show`, the document window
footer and `report missing-checks` all say one number.

### Vendor bills: the payable side of the invoice

`bill post/update/void/show/query/history` is an accrual purchase document with its own
transaction type. `company/bill_models.py` holds its inputs and outputs, `company/bill_facts.py`
what a revision captures, `company/bills.py` the resolution, posting and reads,
`company/bill_validation.py` an independent check of the aggregate, and
`company/purchase_schema.py` the storage. It is the invoice's lifecycle read on the other side of
the books: an immutable revision per correction, an exact reversal of the old effect at its
original date, a full replacement at the new one, and a void that reverses at the document's own
date and keeps every earlier revision readable. `document_effects.persist` writes the whole
aggregate under one audit event, exactly as the sales documents do.

**The posting.** Each expense line debits its own account for its own amount; Accounts Payable is
credited once, for the total. The payable is one leg because that is what the vendor is owed --
one figure on one document -- and each line's share of it is a `posting_line_sources` row hanging
off that credit, so the attribution is per line without the general ledger showing a payable
credit per line. Every posting line is fully attributed: the expense debits carry an attribution
row of their own. `EXPENSE_ACCOUNTS` in `bills.py` is what a line may debit -- `expense`,
`other_expense`, `cost_of_goods_sold`, `fixed_asset`, `other_asset`, `other_current_asset` --
which keeps bank, card, AR, AP and equity off a free expense row; those are moved by the typed
documents that own them.

**The header.** `ap_account` resolves the named active Accounts Payable account, or the one the
previous revision carried, or the company's uniquely eligible active one; with none or several
and nothing named it refuses and says how many there are, because an arbitrary payable choice is
worse than a question. `terms` is what was typed, else the vendor's own terms when the vendor is
new to this bill, else what the bill already carried, and the due date comes from
`profiles.compute_term_dates` -- the terms owner -- never from arithmetic here. An entered
`due_date` overrides it and survives a correction that changes neither the bill date nor the
terms; `BillProfile.due_date_basis` records which of the three it was.

**Supplier reference.** The vendor's own document number, kept as a plain string in the spelling
it arrived in, beside `supplier_reference_key`: NFC-normalized, trimmed and case-folded, null when
blank. `bills.duplicate_references` reports every other bill from the same vendor carrying that
key, voided ones included, in the read output only. Nothing refuses and nothing in storage forbids
the repeat: the configurable warn-with-acknowledgement and block modes are not adopted, and a
uniqueness constraint would make the warning mode unimplementable.

**What a settlement owner attaches to.** `ap_obligation_keys` is one stable row per bill --
vendor, payable account, currency -- created once at posting and never moved by a correction, so
an application bound to it survives every later revision. `ap_obligation_components` is the
per-revision breakdown, one positive amount per entered line, each naming the exact
`posting_line_sources` row that credited AP for it, which is what an allocation targets when a
payment has to land on particular lines. `bills.applied_totals` is the one function that says how
much has been settled against each payable; it is the only line in `bills.py` that knows a
settlement exists, and it now forwards to `company/ap_settlement.py`. `bill show` and `bill query` project it as
`settlement_current` with gross, applied, open and a status, the same shape `invoice settlement`
uses.

**The Items tab.** A bill carries two grids, `expenses` and `items`, and may be entered on
either or on both. They are two profile tables over one envelope family: every entered line is a
`purchase` envelope in `document_lines`, owned by exactly one of `purchase_expense_lines` and
`purchase_item_lines`. An expense line names its own account; an item line takes the account off
the item it names, captured at the moment of writing, so repointing the item later cannot move
what a stored revision posted. Both then debit that account for their own amount, and Accounts
Payable is credited their sum once. The numbering, the terms, the payable, the reference
detection, the obligation component and the shape of the posting know nothing about which family
a line came from, and `ap_obligation_components` needed no change at all -- it already pointed at
the envelope rather than at the expense profile.

`purchase_profiles` did need one: it gains `item_total_minor_units` beside
`expense_total_minor_units`, and each of the two is widened from positive to nonnegative, because
a bill entered wholly on one tab owes a real zero for the other. The figure that must stay
positive is the revision's own total, which is their sum. Migration `co0033` rebuilds the header
for those two CHECK constraints -- SQLite changes a CHECK no other way -- and gives every existing
bill an item total of zero, which is what it has.

An item line's amount is derived or entered: `unit_cost` gives quantity times cost, rounded half
to even by `sales_calculations.extension`, the same arithmetic an invoice line uses; `amount`
gives the amount outright and records no unit cost; neither gives the item's own standard cost.
`BillItemProfile.amount_basis` records which.

**Only three item families, and why.** A bill admits `service`, `non_inventory_part` and
`other_charge` -- exactly the families `sales_defaults` admits on an invoice -- because each posts
to one account named on the item itself. An `inventory_part` is refused by name with
`E_VALIDATION` and `reason` `inventory_receipt_not_implemented`: receiving stock debits Inventory
Asset and moves quantity on hand, nothing here owns either, and a wrong debit that balances is
worse than a refusal. Inventory valuation, stock movement, purchase orders and item receipts
remain unbuilt.

**An item with one account.** An item that is sold and never bought has no purchase account, and
it is not refused: it has one account rather than none -- the income account it is sold out of --
and the line debits that, which reduces the income rather than recording a cost. The anchor
product posts such a line to that account, so this does too, and returns a warning saying which
account it used and why, which the anchor does not. Refusing a workflow the anchor supports is
not better than the anchor; supporting it with a warning is. The item record guarantees the
account exists: a service, non-inventory part or other charge must have a sales side or a
purchase side, and a sales side requires an income account. `BillItemProfile.account_basis`
captures which of the item's accounts a line used, so a reader can tell one from the other years
later and a correction knows which account types are still eligible for that line --
`bills.ITEM_INCOME_ACCOUNTS` for an income-basis line, `EXPENSE_ACCOUNTS` for every other. The
warning is derived from that captured fact rather than collected as lines resolve, so a grid kept
from the previous revision says the same thing about itself as one just entered. The money is
never guessed: a sales-only item has no standard cost, so the line still asks for a unit cost or
an amount.

**Correcting one grid at a time.** Supplying `expenses` or `items` replaces that grid outright;
the grid left out keeps its lines exactly as captured; an empty list clears one; leaving both out
corrects the header alone. A line identity belongs to the grid it was written on and cannot cross,
because moving it would rewrite what that line was.

Migration `co0025` widens the `transactions` type CHECK and the `document_lines` kind CHECK and
reinstates `document_lines_type_insert` with the bill's mapping, by the same table-rebuild that
`co0020` used, then creates the four purchase tables with their immutability triggers. `bill` was
already a declared `CustomFieldScope`, so custom fields needed only `SUPPORTED_VALUE_SCOPES`.

**The browser surface.** `bill` carries `ui_group` "Vendors and purchases", so it has a
navigation entry, a list page and a record page like every other noun in that group, and the
Enter bill tile on the Vendors panel of the flow board is live.

`bill post` and `bill update` open the document window the sales documents and the money-out
pair already use: `document_form.BILL_PRIMARY` is the header the payables window reads in
(vendor, date, our number, the vendor's `Ref. No.`), `BILL_TERMS` is the band that carries the
terms, the due date they derived, the A/P account and the bill's class, and `BILL_GRID` is the
check's Expenses grid with the two columns a payable adds -- the customer or job a cost belongs
to, and whether it is billable to them. The footer copies the server's own `expense_total`,
`item_total` when there is one, and `total` back, and says which date the bill fell due and which
rule produced it; there is no figure on the face of a bill to reconcile the lines against, which
is the one way the footer differs from a check's.

**The two tabs in the window.** `BILL_ITEM_GRID` sits beside `BILL_GRID` and `layout` returns
`grids` -- one entry per line collection, each with its own columns, hint map and track widths --
rather than one grid and its columns. A document with a single grid renders exactly what it always
did; a bill renders a tab strip over two panels in one band. The panel that is not showing is
`hidden`, and it is still part of the form and still submits, because the command takes both
collections at once and a correction opened on either tab has to save what the other holds. No
control in a grid cell carries `required`, so hiding a panel cannot leave the browser refusing to
submit a form whose invalid control it will not focus. Without script both panels are simply
visible and both grids are enterable: the tab strip is an affordance over a page that already
works. `sales.js` owns the strip, opening on the grid that has lines when only one of them does
and otherwise on Expenses, and remembering the tab a person chose across the swap a preview makes.
The phone block rules are scoped to `[data-collection-path=items]` the way the Expenses rules are
scoped to `expenses`, so each grid gets its own reading order without touching the other.

`adapters/workbench/bills.py` is the payables mirror of `sales.py` and does its two jobs:
`editable_values` is the correction form's comparison baseline, so a header-only correction
reaches the writer without an `expenses` grid and the saved lines stay exactly as captured; it
reads `class_mode` back out of the captured class and its origin, so a line deliberately left
unclassified under a classed bill is not silently reclassified by a correction. `detail_context`
feeds `templates/bill_detail.html`, which shows the captured vendor, the terms and the basis of
the due date, the expense lines with their customer, billable flag and class, the item lines with
their item, quantity, unit cost and amount when there are any, what is still open on the bill, the
duplicate references the command reported, and the posting batches. `editable_values` baselines
both grids, each separately: a leaf equal to its baseline is not submitted, so a correction that
touches one grid replaces that grid and leaves the other exactly as captured, and a header-only
correction reaches the writer with neither. The
Items baseline states exactly one of `unit_cost` and `amount` -- the one the line was entered on,
which is what a null `unit_cost` on the saved line already says -- because a baseline that did not
match what the window renders would resubmit that grid on every correction and silently re-resolve
it against today's records, recapturing a renamed item on a revision that never touched it.
`tests/test_bill_form_browser.py` renames both the account and the item and then corrects each
grid in turn, which is how that is held.

Below 700px the grid becomes one block per line and the saved bill's line table becomes one card
per line, both asserted at 390px in `tests/test_bill_form_browser.py` as the element's own
`scrollWidth` against its own `clientWidth`. Bills are not printed: a bill is an internal
document, so there is no print route beside the four customer-facing ones.

Not built here: a unit-of-measure column on an item line -- `items` carry a
`unit_of_measure_set_id` and `unit_conversions` exists, but a purchase line has no unit of its own
to convert a cost through, so the column would need conversion semantics on the line before it
could mean anything -- and a browser page for `bill history`, which is reachable only through
the command surfaces. The bill detail page reads `settlement_current` back and, while
anything is open on a posted bill, links to the Pay Bills window filtered to that vendor and to
the payments already made against that bill.

### Paying a bill: the settlement side of the payable

`bill pay` and `bill payment show/query/history/apply/unapply/void` settle what a bill owes.
`company/bill_payment_models.py` holds the inputs and outputs, `company/bill_payment_facts.py`
what a revision captures, `company/bill_payments.py` the resolution, posting and reads,
`company/bill_payment_validation.py` an independent check of the aggregate,
`company/ap_settlement.py` the sums, and `company/ap_settlement_schema.py` the storage.

**Two things happen, and they are separate.** The money is one debit to Accounts Payable and one
credit to the account that funded it, posted once at the payment's date; that is the whole ledger
effect. The answer is one `ap_applications` row per settled bill, which posts nothing. So a
bill's open balance is its gross less its active applications, while the vendor's payable balance
is the signed posting sum either way: unapplying a payment reopens the bill and moves no money,
leaving the payment as an unapplied debit against that vendor rather than as missing cash. A
company's Accounts Payable therefore equals the sum of open bill balances only when every payment
is fully applied; the difference is exactly the unapplied payment debits, which is what an A/P
aging report shows as its own line rather than folding into the bills.

**The funding account decides the words, not the method.** A bank account is debit-normal, so
crediting it lowers the balance; a credit card is credit-normal, so crediting it raises what the
card is owed. `FUNDING_KIND` in `bill_payments.py` maps the account type to `bank_cash` or
`card_liability`, which is why a debit card or an EFT out of the bank is bank cash rather than
card debt -- the method is what the vendor was handed, the account is where the money is.
`check_number` is accepted only when the payment method's own `kind` is `check` and the funding
account is a bank; storage carries the second half of that rule as a CHECK, because what makes a
method a check is a row in a list rather than a shape.

**One payee per payment.** Selected bills are grouped by `(vendor, payable account, currency,
funding account, method)`. The funding account and the method arrive on the command and the
currency is the home currency, so what actually splits a selection is the vendor and the payable
it is owed from, and `bill pay` writes one `bill_payment` document per group under one audit
event. `group_count` on the output says how many that was. An explicit `number` is refused when
the selection makes more than one payment, since a number names one document.

**What an application binds.** `ap_source_keys` is one stable row per payment carrying the
vendor, payable account and currency an application must match exactly -- the mirror of
`ap_obligation_keys`. `ap_source_components` is that capacity in positive parts, one per selected
bill, each naming the exact `posting_line_sources` row that debited AP for it. A component is
capacity and not a bill: what binds it to a payable is the `ap_applications` row, so unapplying
frees the component rather than destroying it, and re-applying it elsewhere needs no new storage.
`ap_applications` carries the dated edge and its exact whole-edge inverse, so netting apply minus
unapply per key is exact; a database trigger enforces that an `unapply` reverses a real `apply`
cell for cell, and another enforces that source and target agree on vendor, payable and currency.

**Amounts.** Omitting a bill's amount pays everything still open on it, which is what selecting a
row on a Pay Bills screen means. Paying less leaves the remainder open; paying more is refused
with `E_APPLICATION_CAPACITY` rather than becoming a credit, because a vendor credit is a
document this command does not write. The over-settlement check runs again inside the writer's
transaction against storage, so a concurrent payment that took the money first loses there rather
than at the preview. A payment cannot be dated before a bill it settles, which is what keeps the
payable tied to the open balances at every date.

**Taking it back, and pointing it somewhere else.** `bill payment unapply` writes inverses and
nothing else -- no posting, no new revision -- and names `bills` to detach only some of them;
unapplying what is already unapplied is a no-change result rather than an error.
`bill payment apply` is its inverse and writes as little: new `apply` edges hung on capacity that
already exists, so a payment freed off one bill answers another without a new document. Free
capacity is read per component in entered-line order -- the component's own amount less the
applies standing against it -- and consumed greedily, so applying an amount that straddles a
component boundary leaves one component answering two bills, which the storage allows and
`BillPaymentLineOutput` reports by naming no bill on that line. A row naming no amount takes the
lesser of what is open on the bill and what the payment has left, because unlike `bill pay` the
money exists before the selection does; a named amount is taken as named and refused above either
side with `E_APPLICATION_CAPACITY`. `date` defaults to the payment's own date and may be later,
which is what lets a check answer a bill entered after it was written, but never earlier than the
payment or than a bill it settles. A closed period refuses it, the way `payment apply` refuses
one on the customer side: nothing is posted, but what the books say was open on a date does
change. `bill_payment_validation._source_capacity` is the mirror of its `_settlement`: one refuses
settling a payable past its gross, the other refuses spending a payment past what it carries, and
both read storage inside the writer's transaction so the second of two concurrent writers loses
there rather than at the preview. `bill payment void` reverses the posting batch at its
own date and refuses with `E_HAS_APPLICATIONS` while anything is still applied, which is the
customer receipt's discipline and keeps a void from silently reopening a bill. `bills.prepare`
already refused correction and void of a bill with applications, so a bill that has been paid is
corrected by unapplying first.

Migration `co0026` widens the `transactions` type CHECK and the `document_lines` kind CHECK for
`bill_payment` and reinstates `document_lines_type_insert` with its mapping, by the same
table-rebuild `co0025` used, then creates the four settlement tables with their immutability,
document-type, exact-inverse and exact-party triggers.

**The browser surface.** `bill payment` carries `ui_group` "Vendors and purchases", so its
commands file under Vendors rather than falling through to Hub, and the Pay Bills tile on the
Vendors panel is live at `/pay-bills` with a Bill payments tile beside it at `/bill-payment`.
`adapters/workbench/bill_payments.py` mounts all three routes before the generated noun routes,
because `bill payment` is a two-word noun and a hyphenated path reads better than an escaped
space -- which is now the spelling every workbench link uses for it, so the generated pages
address the same paths, and the detail route reads a segment naming one of the noun's verbs as
that verb's form rather than as a missing payment, exactly as the deposit window does. It holds
no business logic, and `GROUP_FIELDS` is the one place the window's idea of a payee group
lives.

`templates/pay_bills.html` plus `static/pay-bills.js` are the window: one funding account, one
method and one date for the page, an optional vendor filter, and the open bills underneath.
Each row's original amount, due date and open balance come from that bill's own
`settlement_current` as `bill query` returned it, which is the same edge `report unpaid-bills`
now sums in its own `applied` column. Ticking a row fills its payment with the whole open balance, which is what ticking a row
on a Pay Bills screen means; typing over it is a partial payment. The running total and each
group's total are exact minor-unit sums in `BigInt`, never a float.

The window shows the split **before** the save: the funding account, the method and the currency
are one choice for the whole page, so the pair that remains of the command's grouping key is
(vendor, payable account), and one list item per group names the payee, the payable, that group's
total and the bills in it. A check number is offered only where the command accepts one -- a
check-kind method drawn on a bank account -- and is held back when the selection would write more
than one payment, because one number cannot name two checks. One save is one `bill pay`, and the
receipt panel names every payment it wrote, links to each, and lists the bills each settled.

`templates/bill_payment_list.html` pages `bill payment query` newest first with the vendor, bill,
number, check-number, date and status filters the command already takes;
`templates/bill_payment_detail.html` reads one payment back -- who was paid, out of which account
and by what method, the bills it settled with links to each, where its money stands, its dated
settlement edges and its posting batches. Below 700px every one of those tables becomes one block
per row, asserted at 390px in `tests/test_pay_bills_browser.py` as each element's own
`scrollWidth` against its own `clientWidth`.

Not built here: purchase discounts and vendor credits, which need their own documents and an
adopted account-eligibility contract before a settlement can carry a third term; purchase tax;
`bill payment update`, which would move a posted payment's money rather than what it answers;
printing a check; and browser pages for `bill payment apply`, `bill payment unapply` and
`bill payment void`, which are reachable only through the command surfaces. To expose applying,
the Pay Bills window would need a payment-first mode -- pick a payment with unapplied money,
list that vendor's open bills, and fill each ticked row with the lesser of its open balance and
what the payment has left, against `bill payment show`'s `settlement_current.unapplied` as the
running budget rather than a funding account and a date.

### Transfers between the company's own accounts

`transfer post` is the same document surface over the same register.
`company/transfer_models.py` holds its input and `company/transfers.py` translates it into a
`RegisterPostInput` whose selected account is the account the money leaves and whose `category`
is the account it arrives in, then hands that to `registers.translate` and the journal writer.
`journals.persist_prepared` is called with `transfer post`, so the audit trail names the
document. The register's single-category shape is used rather than a one-line allocation list
because a transfer is exactly two legs of one amount: the same memo reaches both legs the way it
does for any categorised register entry, and there is no reconciliation rule to run.

One rule sets both signs: credit the account it leaves, debit the account it arrives in.
`CREDITED_BY` and `DEBITED_BY` in `transfer_models.py` map an account's normal balance to the
register direction that produces each side, and they are the only place the register's
increase/decrease vocabulary appears. A debit-normal account is credited by decreasing and a
credit-normal one by increasing, so a bank paying a card down decreases the bank and debits the
card — which is what paying a card off is — and the same rule read the other way is a cash
advance raising what is owed.

`ELIGIBLE` in `company/transfers.py` is every balance-sheet posting type except
`accounts_receivable` and `accounts_payable`: `bank`, `credit_card`, `other_current_asset`,
`fixed_asset`, `other_asset`, `other_current_liability`, `long_term_liability`, `equity`. Keeping
every profit-and-loss type off both ends is what makes "a transfer changes no profit" structural
rather than a property to be tested for. The two party ledgers are refused although they are
balance-sheet accounts, because every posting line on them names a customer or a vendor
(`journals.line_values`) and a transfer names nobody. `REFUSED` names the rest with the reason and
the document that does own that movement; `ELIGIBLE` and `REFUSED` together cover every member of
`accounts.AccountType`, and `tests/test_transfer_funds.py` holds that to be true so a new account
type cannot fall through. The same account at both ends is refused before the register would
report it as an offset collision, so the message names the account. Both refusals carry
`fields[0].field` as `from_account` or `to_account`, which is what puts the error on the control a
person was filling in.

The write output is `TransferWriteOutput`: the journal write output plus a `document` summary
holding the amount and, for each end, the account id, its name, its type, its normal balance, the
side it took, and whether its own figure went up or down. That summary is what the footer
displays; nothing is recomputed in the browser.

`transfer show`, `transfer query`, `transfer update`, `transfer void` and `transfer history` carry
the same six verbs the money-out documents carry, through the same marker table and the same
translation. The correction worth naming is a changed `to_account`: the journal writer reverses the
whole entry at the transfer's own date and replaces it, so the money leaves the account it went
into and arrives in the new one on that one date rather than on two. `register update` and
`journal void` keep working on a transfer as well.

`transfer` is still in `Document.NO_LIST`, so the window's Cancel returns to the home board and a
successful save lands on the posted journal, even though `transfer query` now gives the generic
list route something to answer with.

In `adapters/workbench/document_form.py` the transfer is the one document with no line grid:
`layout` returns `lines: None`, which leaves the grid band out of the rendered page rather than
drawing an empty one, and `TRANSFER_PRIMARY` plus `TRANSFER_FOOTER` place the whole form.
`transfer_totals` builds the footer from the `document` summary, saying what each end did in that
end's own words — a card or a loan reports what you owe on it. The header band's existing
`repeat(auto-fit, minmax(min(100%, 13rem), 1fr))` is what stacks it one field per row on a phone;
no new CSS was needed and none was added.

Both account pickers search the whole chart, because `ReferenceDefinition` carries no filter and
adding one would trade a refusal that says which account and why for a list that silently omits
it.

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

## Statement of cash flows and income tax summary

`company/cash_flow_reports.py` supplies `report cash-flows` and
`company/income_tax_reports.py` supplies `report income-tax-summary`, both
through the shared registry and reports capability, with the same period
metadata, HMAC continuation, streamed whole-filter totals and signed64 checks as
the other statements. `ledger_reports._state` treats both as account-labelled
financial reports, so their continuations carry the company's fiscal and label
preferences and its audit watermark and any audited write stales them. Neither
adds a table, a column or a posting cost.

The cash flow statement is the indirect method, and it is one identity rather
than a rule set. Because every account's signed net sums to zero at every date,
the change in cash over an inclusive period is net income less the change in the
remaining balance-sheet accounts. Each of those accounts therefore contributes
its own negated signed change: an asset that grew is a use of cash and a
liability or equity that grew is a source. Net income is computed exactly as
`report profit-and-loss` computes it and is the same figure for the same dates.
`totals.difference` is the published reconciliation -- closing cash less opening
cash less net income and the three subtotals -- and is zero whenever the books
balance; closing cash is the total of the bank accounts `report balance-sheet`
reports for the same `date_to`. Row `opening_balance` and `closing_balance` are
on the account's own normal side, so a row's balances match what the balance
sheet prints while `amount` keeps the statement's source-and-use sign.

Which section an account is in has one answer, `accounts.cash_flow_section(type,
declared)`: the section the account itself declares in `accounts.cash_flow_section`
(co0039, nullable, CHECK-constrained to the three sections), falling back to
`accounts.CASH_FLOW_SECTION_BY_TYPE`. That type rule is checked at import against
`accounts.STATEMENT_FAMILY`: operating takes receivables, payables, the other
current assets and current liabilities and credit cards; investing takes fixed
and other assets; financing takes long-term liabilities and equity; `bank` is the
cash the statement explains. An account type added to the chart vocabulary
without a section raises on import rather than dropping out of a statement that
would still look balanced. `cash_flow_reports._section_sql` is that same
resolution written once for SQLite over the same mapping, and
`test_cash_flow_reports.py` pins the two answers together over every type and
every declaration.

**No account type and no system role distinguishes depreciation or amortisation
from any other account of its type**, which is what the column exists for. The
add-back itself needs no help -- it reaches the statement as the credit against
the fixed asset -- but the type rule reports it under investing, where the anchor
reports it under operating. An accumulated-depreciation account declaring
`operating` moves that row, and only that row. Nothing is backfilled and nothing
declares itself by default, so an upgraded company's statement is the one it
already had; and because sectioning only chooses which subtotal a change lands
in, net change in cash and closing cash are the same figures either way. A
profit-and-loss account may declare only `operating`, since the statement reports
every income and expense effect inside net income, which it reports under
operating; the reference company declares it on both Depreciation Expense and
Accumulated Depreciation. Which balance-sheet accounts get their own row is still
decided by type, never by the declaration, so an expense account can never be
counted twice.

`report income-tax-summary` is the only reader of `accounts.tax_line`. It groups
income and expense account activity for an inclusive period by that value, one
`tax_line` row carrying the group's own total and its account count followed by
the accounts that make it up, with a group for accounts that have none whose
typed `tax_line` stays null and whose `display_tax_line` is "Unassigned". Each
posting line's normal side is chosen from `accounts.NORMAL_BALANCE` before it is
summed, so no aggregate is ever negated in SQL. Totals cover every income and
expense account whatever the filter shows, which makes `totals.net_income` the
figure `report profit-and-loss` reports for the same two dates. A group total
covers the whole group even when its accounts fall on a later page.

Workbench `statements.py` projects both without accounting logic, through
`cash_flows.html` and `tax_summary.html`; a tax-line row carries no drill-down
because it is not an account. Both are on the home window's Reports tile and in
the report group page.

## Receivables aging, open invoices and customer statements

`company/receivable_reports.py` supplies `report ar-aging`, `report
open-invoices` and `report statement` through the shared registry and reports
capability, with the same
period metadata, HMAC continuation, streamed whole-filter totals and signed64
checks as the other reports. The aging and the open-invoice list take `as_of`, the
single inclusive bound the report metadata carries as `period.date_to`; the
statement takes `date_from` and `date_to` and carries both; no schema, cached
balance or posting-cost change is introduced.

Both start from Accounts Receivable posting effects on or before `as_of` and add
two signed rows for every active settlement application: a negative one against
the invoice, under the customer its current revision names, and a positive one
against the paying receipt, under the party its permanent component key owns. An
application is active when it is an `apply` dated on or before `as_of` with no
`unapply` dated on or before `as_of`. The redistribution moves an amount between
two rows and never creates or destroys one, so the aging total is the Accounts
Receivable balance the balance sheet and trial balance report for the same date,
whatever the settlement history is. Amounts are grouped losslessly twice: once
per document and party, once per party and column, both through
`bookflow_sum_int`, which accepts its own lossless integer text.

An invoice ages on the captured due date of its current revision; everything
else that reaches receivable -- unapplied customer credit, a receivable journal
entry, a statement charge, which has no terms and so no due date -- ages on its
accounting date. Column edges are computed once in Python
as four exact whole-day boundary dates and compared in SQL as ISO text, so no
calendar arithmetic and no float ever enters the query; an as-of date inside the
first 90 days of year 1 clamps instead of underflowing. An invoice due exactly
30 days before `as_of` is 1-30 and one due exactly 31 days before is 31-60;
`bucket_of` is the Python twin of the same rule and both are pinned by test.
The columns, the edges, the boundary-date helper and both evaluators live in
`company/aging.py` and are read from there by the receivables and the payables
reports alike, so 1-30 is the same number of days on both sides of the books.

Aging rows are customers and jobs in hierarchy-name order, one row each, holding
Current, 1-30, 31-60, 61-90, Over 90 and a total; a row whose columns are all
zero -- a paid invoice, a voided one, a fully applied receipt -- is omitted,
which cannot move a total. A receivable posting under no customer, or under a
party that is not a customer, keeps its own row labelled "No name" so the tie
survives it. Open invoices are unpaid and partly paid invoices only -- a statement
charge is receivable and ages, but is not listed here and cannot be settled;
see *Statement charges* below -- oldest due date first, with due date, days past due, column, original amount, applied
amount and remaining balance, optionally filtered to one customer or to past-due
rows; it excludes credit, so it exceeds Accounts Receivable by whatever credit
stands unapplied.

### The customer statement

`report statement` reads the same effects along the date axis instead of the age
axis: for one customer, or for every customer with a balance or with activity, an
opening balance, then that customer's rows in date order with a running balance,
then the closing balance, with the aging columns at the foot for the same
customers as of `date_to`.

A row is one document's effect on one customer's receivable on one date, grouped
losslessly at that grain. Rows and balances come from the same three signed
sources the aging sums, with one difference: a settlement whose paying component
key and whose invoice customer are the same customer contributes no row, because
its two halves are equal and opposite inside that one customer and printing both
would show a balance moving that never moved. Where they differ -- a receipt
owned by one customer settling another's invoice -- both halves are printed, on
the two statements they belong to. Across the whole report those halves still
cancel. No command available today produces that second case: new cash is owned
by the party whose invoice it settles, `payment apply` demands a funding
component key already owned by the invoice's customer, and re-parting an invoice
that carries an active settlement is refused with `E_HAS_APPLICATIONS`. That is what makes the identity exact rather than incidental: a
customer's closing balance is the same expression `report ar-aging` sums for that
customer, so the two agree row for row, and the closing total is Accounts
Receivable on the balance sheet for `date_to`. New cash is owned by the party
whose invoice it settles, so a parent's receipt that pays a job's invoice already
lands on the job's statement for the settled part and on the parent's for the
rest.

Opening is every effect dated before `date_from`; closing is every effect on or
before `date_to`; the rows between them are the effects in the period whose
grouped amount is not zero. A voided document therefore has no row because its
reversal carries the original date and the pair is worth nothing on that date --
no status is consulted anywhere in the query, and a period containing the day the
document was written still shows nothing for it. A customer who owed nothing, was
owed nothing and did nothing is not printed at all, which cannot move a total; a
customer named in the request always gets a statement, even an empty one. The
running balance is a window partitioned by customer and computed before the page
slice, so page two continues the balance rather than restarting it, and totals
and the aging foot cover the whole match on every page. `E_INTERNAL` guards
three arithmetic identities on every run: opening plus charges plus credits
equals closing, the aging columns sum to the aging total, and that total equals
the closing balance.

Their continuations extend the shared HMAC state with customer presentation, the
settlement identity count and the company audit watermark, because applications
post nothing and the posting-effect watermark alone cannot see an apply or an
unapply. The customer filter's resolved stable ID rides in the cursor, so
renaming that customer stales the continuation instead of failing to resolve on
page two; `ledger_reports._state` takes `account_scoped=False` for that, which
keeps the ID out of the posting-account filter.

Workbench `receivables.py` and `receivables.html` project the first two results
without accounting logic: an aging column table with jobs indented under their
parent and each customer linking to their own open invoices, and an open-invoice
table linking each number to its invoice. `customer_statement.py` and
`customer_statement.html` do the same for the statement, reusing the
`document-detail` and `basic-report` blocks rather than a third copy of them: the
whole-report balances and the aging foot are the `report-totals` grid, and the
rows are a `document-lines` table that becomes one labelled block per row below
700px, checked at 390px as the table's own scrollWidth against its own
clientWidth. Each row opens its document where it was written and each customer
opens their own statement. The page says in words that it is a statement to read
on screen: this product prints nothing and delivers nothing, and a browser
witness asserts that no control on the page offers to. All three carry a Next
page form that preserves the validated filter and signed cursor. The home
window's Reports tile names all three, and the Customers panel's Statement tile
is live and lands on the statement form. No section repeats the page's own title:
every report page is titled with the report, from `naming.REPORTS`, rather than
with the command that produces it.

## Payables aging and unpaid bills

`company/payable_reports.py` supplies `report ap-aging` and `report
unpaid-bills` through the same registry and `reports` capability, with the same
period metadata, HMAC continuation, streamed whole-filter totals and signed64
checks as their receivables twins. Both take `as_of`, the single inclusive bound
the metadata carries as `period.date_to`. No schema, cached balance or posting
cost is introduced: they read `posting_lines` and `posting_batches` exactly as
the other reports do.

Both start from Accounts Payable posting effects dated on or before `as_of`,
signed credit minus debit because a payable is credit-normal, then move each
active settlement application from the payment that supplied the cash onto the
bill it settles, and group the result by the vendor the posting line names and
by document. Entering a bill credits Accounts Payable, correcting one reverses
the old credit and posts a new one, voiding one reverses at the original date,
and paying one debits it, so a document's net is what is still owed on it
whatever its history is; the settlement movement transfers between two rows and
never creates or destroys one, so the aging total is still exactly what the
balance sheet reports for Accounts Payable on the same date, and `report
ap-aging` ties to it by construction rather than by agreement. `E_INTERNAL`
guards the columns summing to the total on every run, and guards each unpaid
bill's balance being its amount less what was applied to it. Amounts are grouped
losslessly through `bookflow_sum_int`, twice, exactly as the receivables reports
group them.

A bill ages on the captured due date of its current revision; everything else
that reaches payable -- a bill payment with capacity nothing is applied to, and a
vendor credit or adjustment entered as a journal, which `journals.py` already
requires to name a vendor -- ages on its accounting date.
Columns and edges are `company/aging.py`'s, so an A/P column and an A/R column of
the same name are the same number of days.

Aging rows are vendors in name order, one row each, holding Current, 1-30,
31-60, 61-90, Over 90 and a total; a row whose columns are all zero -- a paid
bill, a voided one, a fully applied payment, a correction that took a bill to
nothing -- is omitted, which cannot move a total. A payable posting under no
vendor keeps its own row labelled "No name" so the tie survives it. Unpaid bills
are unpaid and partly paid bills only, oldest due date first, with
vendor, bill date, due date, days past due, column, the vendor's own reference,
bill amount, applied amount and open balance, optionally filtered to one vendor
or to past-due rows; because it lists bills it excludes vendor credit and
unapplied payment capacity, so it exceeds Accounts Payable by whatever credit
stands unattached.

**A paid bill is not on a report called unpaid bills.** `_UNPAID` keeps its
`d.net!='0'` filter and `d.net` now carries the settlement movement, so a bill
settled to nothing leaves the report because it is worth zero, exactly as a
voided bill does and exactly as a paid invoice leaves `report open-invoices`.
The aging drops the same zero row, so the two payables reports never disagree
about what is outstanding, and neither total moves when a bill is paid off.
`settlement_status` therefore has only the two answers a listed bill can
truthfully give, `unpaid` and `partly_paid`.

`_EFFECTS` carries the settlement union that `receivable_reports._EFFECTS`
already has: an apply dated on or before `as_of` that no unapply dated on or
before `as_of` has taken back moves its amount off the bill's row and onto the
paying document's, before anything is bucketed or totalled. An unapply is a
whole-edge inverse carrying the same amount as its apply -- enforced by a storage
trigger and relied on by `ap_settlement.applied_totals` -- so excluding the
reversed apply outright is the whole of the netting. The vendor the transfer
belongs to is `ap_obligation_keys.vendor_id`, which `bill_validation` requires to
equal the vendor of every revision of the bill and which the
`ap_applications_exact_party` trigger requires to equal the paying source's
vendor, so both halves land on the same party the posting lines name and a
settlement can never move a balance between two vendors the way a parent and a
job can on the receivables side. `_UNPAID` reads three independent
`bookflow_sum_int` columns -- the bill's own payable posting as `gross`, the
active applications as `applied`, and the transferred `net` as the balance -- and
`unpaid_bills` checks `gross - applied == net` in Python, with
arbitrary-precision integers, on every row it totals.

Their continuations extend the shared HMAC state with vendor presentation and the
company audit watermark, so any audited company write stales a continuation --
including an A/P settlement, which posts nothing and which the posting-effect
watermark alone could not see. The vendor filter's resolved stable ID rides in
the cursor under `account_scoped=False`, so renaming that vendor stales the
continuation instead of failing to resolve on page two.

Workbench `payables.py` and `payables.html` project both results without
accounting logic: an aging column table with each vendor linking to their own
open bills, and an unpaid-bill table linking each number to its bill. Both carry
a Next page form that preserves the validated filter and signed cursor, and the
filter form drops the `cursor` leaf like every other paged report --
`pages.CURSOR_FREE_REPORTS` is the one set the workbench branches on and the one
the host test reads, so a report added to one of the presentation modules is
covered without being named again. Both pages are titled from `naming.REPORTS`,
and the home window's Reports tile names every registered report. A staled
continuation on any report page now shows the restart note, not only on trial
balance and general ledger.

## Dimensional profit and loss: by job and by class

`company/dimensional_statements.py` supplies `report profit-and-loss-by-job` and
`report profit-and-loss-by-class`. They are the profit and loss split sideways,
not a second statement: the row set, the account order, the section arithmetic and
the whole-statement totals come from `financial_statements` itself, through
`_query`, `_net` and the extracted `profit_and_loss_totals`, so the Total column
is `report profit-and-loss` for the same dates account by account.

A column is one value of one posting-line dimension. The job report reads
`name_id` only where `name_type` is `customer`, so a vendor, an employee or an
other name on an expense line is not made into a job; the class report reads
`class_id`. A line with no such value goes to an explicit Unassigned or
Unclassified column, never nowhere, because a dropped cell would stop the columns
adding across to the statement. Both invariants are enforced at run time: the
columns must sum to each section total and a row's cells must sum to its own
account total, or the report raises `E_INTERNAL` rather than printing a figure
that does not tie.

The row door is one predicate wider than the statement's. `financial_statements`
prints an income or expense account whose period net is not zero
(`PL_PRESENT`); a split prints any account that took a posting in the period
(`PL_POSTED`), because an account netting to zero company-wide can be real money
on one job and its opposite on another. The extra rows carry a zero total, which
is what the statement itself shows with `include_zero`.

Columns are bounded without being lost. They are the dimension values with
activity, in hierarchy-name order; past the requested `columns` count the
remainder is folded into one Other column that states how many values it holds
and carries their summed section totals, and the Unassigned column is never
folded. Continuations join the financial family in `ledger_reports`, whose
watermark already includes the company audit sequence, so renaming the customer or
class a column is headed with restarts the report.

Workbench `statements.py` and `dimensional_statement.html` project the result.
A column-per-job table is the one report here that is wider than a screen on
purpose, so the table scrolls inside its own wrapper while the page never does,
the account column and the Total column are pinned to the two edges at desktop
width, and a net-income-per-column list sits above the table so the figure the
reader came for needs no sideways scrolling at all. At phone width nothing is
pinned: the shared `document-detail` rules turn each row into a labelled card, so
every cell names its own column and no job can be scrolled past unseen.

## Unbilled costs and collections

`company/unbilled_costs.py` supplies `report unbilled-costs`: billable work
recorded against a customer or job on or before `as_of` and not yet invoiced, one
row per work line under a subtotal row per customer or job. The billing state of
each line is not derived here. `billing_queries` now owns three extracted
functions -- `free_amounts`, `line_state` and `scope_fractions` -- and the billing
window, the tax forecast and this report all call them, so what "partly billed"
means and how much scope is left cannot drift between the report and the command
that acts on it. Which sources are listed comes from the same place:
`billing_queries.billable_source` is the eligibility expression `billing` uses for
its own `can_invoice`, and `current_owner` keeps an estimate whose work order has
taken the work over from being counted twice. States are dispositioned against the
declared `BillingLineState` rather than a retyped list, so a new state is listed
rather than silently filed as nothing to do.

`company/collection_reports.py` supplies `report collections`: the aging summary
restricted to customers with something at or past `minimum_bucket`, each with the
contacts recorded against them and their overdue invoices beneath. The aging
arithmetic is `receivable_reports`' own -- `AGING_ROWS` and `OPEN_INVOICE_ROWS`
are the expressions `ar_aging` and `open_invoices` page, read whole here -- so the
two reports cannot disagree about what one customer owes on one date. A customer
whose chased columns net to nothing owes nothing overdue and is not printed, which
is what keeps an unapplied credit aged on its own date off a chase list. Contacts
follow the customer's own contact rule through
`party_cmds._customer_collection_owner`, so a job that inherits its contacts is
chased through the customer it is named under. The totals here are the overdue
part of receivables and are not Accounts Receivable, which `report ar-aging`
reports in full.

Both project through workbench `receivables.py` and the worklist tables in
`receivables.html`: a collections row carries `tel:` and `mailto:` links for every
recorded number and address, and an unbilled-cost line links to its source
document's billing window, which is the page that turns the row into an invoice.

## Transaction detail by account, and the holes in a check sequence

`report transaction-detail` and `report missing-checks` add nothing to the
schema: both read the posting and document history that was already there.

**Transaction detail is the general ledger, printed line by line.** One SQL
builder in `company/ledger_reports.py` now writes the account walk both reports
page -- `_effects(scope)` for the effects and each account's four figures, and
`_ledger(scope)` for `selected`, `running`, `flat` and `ordered` on top of it.
`_GL` is that builder under `ONE_ACCOUNT`, the single `:account` filter every
earlier report uses; `_DETAIL` is the same text under `ACCOUNT_SET`, a JSON
array of stable IDs read through `json_each(:accounts)`. There is one copy of
the balance arithmetic and one copy of the traversal, so the general ledger and
the detail report cannot drift into two meanings of an opening balance. The
running balance is still the window computed over the whole account before any
page is sliced, which is what lets a page boundary fall anywhere.

What transaction detail adds is the two columns that make it a drill-down. The
party, the class and the line description were already captured on every posting
line; the split account is derived per page by `_SPLITS`, which counts the legs
of each printed line's own posting batch and names the one other account when
there are exactly two, and `MANY_SPLITS` (`-SPLIT-`) when there are more.
Nothing about a document type enters that derivation, so a document family
nobody has written yet splits correctly on the day it lands. `TRANSACTION_TYPES`
now lives once in `company/ledger_schema.py`, which builds the `ck_transaction_type`
CHECK from it and which both report row models read, replacing the closed
`Literal` the general ledger row used to retype.

`accounts` is a list rather than a scalar, so `ReportCursor` gained
`account_ids` beside `account_id`: the IDs the first page resolved ride in the
signed continuation, and renaming a selected account stales the page instead of
failing to resolve on page two. A cursor minted before the field existed decodes
with none, which is what a single-account report carries anyway. An empty list
and an absent one normalize to the same request in the input validator, so a
browser form that submits its empty repeated control does not invalidate its own
continuation.

**Missing checks reads the cheque's own number.** `company/check_reports.py`
starts from `check_instruments` (co0040): one row per check, carrying the bank
account whose chequebook the number came from and the number itself. It read
`transactions.number` until co0040, and that is what made it name false gaps on
any real company file -- a check posts as a journal entry, so a number the shared
document series gave a transfer, a card charge or a hand-typed entry read here as
a hole in somebody's chequebook. The marker still decides what is a check:
`check_instruments` is only ever written for a transaction carrying the
`money_out_documents` check kind, so a hand-typed entry that happens to credit a
bank account is never listed. A number is a position in a sequence only when it
is one to eighteen ASCII digits; anything else is a real check number with no
place between two others and is counted as `unnumbered_checks`.

Three kinds of finding. `lag()` over each account's *accounted* numbers -- the
ones a cheque holds now plus the ones a correction gave up -- produces the holes;
`count(*) > 1` per number produces the repeats; and a number that appears on a
`check_instrument_revisions` row and on no current cheque is printed as
`retired`, with the cheque that gave it up shown at the number it carries today.
A retired number is accounted for and is never handed out again, so counting it
as missing would send somebody hunting for a cheque that is sitting in the books
under another number. A voided check keeps its number because the paper it was
written on is still gone.

**What the report says it cannot know.** Numbers co0040 carried over are marked
`origin = 'migrated'`, and a gap at or below an account's highest migrated number
is flagged `legacy_uncertain` and counted in `legacy_uncertain_gaps` rather than
asserted as a missing cheque: those numbers came out of the shared series and
what is stored cannot say which of them were ever cheques. `disclosure` carries
that sentence whenever the file holds any. It carries a second when the file
holds bill payments made by cheque on a bank account: those numbers live on
`ap_payment_profiles.check_number`, are typed rather than allocated, and this
report does not place them yet -- so a hole it prints may be one of them.

Its continuation extends the shared HMAC state with the company audit watermark,
because every check write is audited and nothing else this report reads can move
without one.

Workbench `transaction_detail.py`/`.html` and `missing_checks.py`/`.html`
project both results without accounting logic, reusing the `document-detail
basic-report` blocks so the phone cards and the print table come from the
stylesheet rather than from a third copy. Both are in
`pages.CURSOR_FREE_REPORTS`, both are titled from `naming.REPORTS`, and the home
window's Reports tile names both alongside the nine that were there before. A
detail row opens the document it came from, resolving the record noun from the
registry rather than from a hand-listed map, and a gap row opens the checks on
either side of it. Because a repeated control cannot be carried by a scalar
query name, a report GET now also accepts the collection's own `c:`/`collection:`
keys, which is what makes the account-set filter linkable at all.

## Period summaries: where the money came from and where it went

`company/summary_reports.py` supplies `report sales-by-customer`, `report
sales-by-item`, `report sales-by-rep` and `report expenses-by-vendor` through the
same registry and `reports` capability, with the same period metadata, HMAC
continuation, streamed whole-filter totals and signed64 checks as the aging
reports. All four take `date_from` and `date_to`, the inclusive bounds the
metadata carries as `period`. No schema and no cached balance is introduced: they
read `posting_lines`, `posting_batches`, `accounts` and the immutable document
profiles the sale already wrote.

**None of them selects on a document type.** An income effect is an effect on an
account whose profit-and-loss section is `income`; a cost effect is one whose
section is in `financial_statements.COST_SECTIONS`, which is derived from
`PL_SECTIONS` and `DEBIT_TYPES` rather than written a second time. So
`report expenses-by-vendor` covers bills, cheques, credit card charges, vendor
credits and expense journal entries without naming any of them, and a document
family added later is in these reports the day it posts. The sales reports cover
the single `income` section, which is what makes their totals the `income` total
`report profit-and-loss` prints for the same dates; `tests/test_summary_reports.py`
asserts that equality on every one of the three.

Sales by customer groups income by the customer dimension the posting line
carries. A job is its own row and is never folded into its parent, so each row
names `parent_id` and `parent_label` and the rows are ordered by
`full_name_key`, which places a job directly under the customer it is named
under. Income named to a party from another list, and income named to nobody, is
the one row labelled `receivable_reports.NO_CUSTOMER`.

Sales by item reads the income leg's own `posting_line_sources` attribution
through to the entered line's one-to-one item profile, so a correction, a void
and a credit memo take the units and the money back off the row they were added
to, each at the sign its own posting carries. `ITEM_LINE_TABLES` is read off
`schema.metadata` -- a profile keyed on `document_line_id` carrying an item, a
base quantity and a line net -- so `sales_line_profiles` and
`credit_line_profiles` are both covered and a third family joins without an
edit. Income that reached no item at all is both a row labelled `No item` and
the `no_item_income` total, and `E_INTERNAL` refuses to print figures where item
income plus no-item income is not the period's income. Quantity is the item's
base unit, so lines entered in different selected units add up; a line priced by
allocation carries no quantity, which sets `quantity_complete` false and leaves
`average_price` absent rather than dividing by a short quantity. Average price is
income over quantity rounded once to the cent for reading and nothing sums it.

Sales by rep reads the representative the sale itself captured, from
`posting_batches.revision_id` through the revision's own `profile_snapshot`, and
never from `customers.sales_rep_id`. That is the whole of the report: a customer
reassigned today has not moved sales already made, and a correction that changes
the rep reverses the old one at the old revision and posts the new one at the
new, because a reversal batch names the revision it reverses.
`COMMERCIAL_PROFILE_TABLES` is read off `schema.metadata` the same way the item
lines are, so a credit memo's rep is counted beside an invoice's; a snapshot with
no representative yields SQL NULL and lands in the row labelled `Unassigned`.

Expenses by vendor takes the vendor the expense line itself names, and where the
line names none, the single vendor named anywhere on the same posting batch. A
bill writes its vendor onto every leg and needs no fallback; a cheque and a card
charge carry the payee on the funding line only, so without it every cheque ever
written would be filed under no vendor. Where a batch names two vendors neither
is the document's and the line stays unattributed, in the row labelled
`payable_reports.NO_VENDOR`. A bill payment posts nothing to a cost account, so
it can never double-count what the bill already charged.

Each report streams every row the filter selects to build its totals, then pages
with LIMIT/OFFSET, so a total is the whole filter's on every page. Each checks
its printed rows against an independent sum of the underlying effects and raises
`E_INTERNAL` rather than printing a breakdown that does not add up. Percentages
are exact integer coefficients in millionths of a percentage point
(`core/exact.PERCENTAGE_SCALE`), null where the period total is zero because a
share of nothing is not zero; no ratio is ever a float. `ledger_reports._state`
labels these rows off `customers`, `items`, `sales_reps` and `vendors`
respectively, and takes no settlement state, because applying a receipt moves
nothing on an income or cost account.

Workbench `summaries.py` and `summaries.html` project all four without accounting
logic, deriving only the two-place percentage a person reads, by integer
arithmetic. A customer row links to that customer's statement for the same
period and a vendor row to that vendor's open bills; an item and a
representative have no such report yet, so those rows carry no link rather than
one that goes somewhere unrelated. `pages.CURSOR_FREE_REPORTS` gains them by
containing `Summary.COMMANDS`, so nothing names the commands a second time. All
four are titled from `naming.REPORTS` and listed on the home window's Reports
tile.

## Customer work documents

[Customer work](customer-work.md) owns the nonposting proposal, alternative
estimate and work-order contract. The shared registry exposes21 commands; all
three nouns have create/update/copy/show/query/history plus proposal estimate,
estimate work-order and work-order complete. Structured browser forms and detail,
history and source views cover desktop and phone. Work completion records quantities
and operational times without posting a sale or claiming payment. Resulting state
invariants are checked before tax preparation parses captured facts, so completion
with a missing actual start or an end preceding its start returns field-specific
`E_VALIDATION`; the browser retains the form for correction.

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

The Row9 test ledger tests/mcp_coverage.py explicitly classifies all 330 registered
commands by routed JSON, advisory, finite poll/local follow, binary direction,
local lifecycle or standalone protocol. Four-interface execution scenarios cover
325 hosted commands. Five local lifecycle commands map actual local execution
witnesses and explicit installed-MCP/HTTP rejection boundaries; they are not
counted as hosted execution parity. The ledger gate rejects unclassified or
pending execution rows. This is execution coverage, not full Row9 acceptance.
The actual all-command form check emits schema-path/control/variant/context
mapping, separate from the still-incomplete browser interaction/output/success
mapping and parent-owned fresh blind acceptance.

The material-variant ledger groups 2072 hosted schema alternatives into 21
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

No workbench page is titled with a command name and no table heads a column with a
field name. `adapters/workbench/naming.py` is presentation only: `heading` titles every
generated form and report page (`document_form.heading` keeps the three sales documents,
whose titles carry the document's own number), `list_heading` titles every list with its
records, and `column_label` is registered as the `label` Jinja filter so any template
humanises a raw key the same way. `column_label` is a rule first — the storage suffixes
(`_minor_units`, `_id`, …) and the underscores come off and what is left is
capitalised — and reads its corrections from the maps already written in
`document_form.py` and `query_catalog.py` rather than starting a third one.

`SalesQueryInput` and `WorkQueryInput` take `direction` (`asc` default, `desc`), which
reverses the accounting-date then stable-id order exactly. The cursor contract needs no
new guard: `query.page_state` fingerprints the whole input except the cursor, so a
continuation minted in one direction is rejected as an invalid cursor in the other, the
same way `ledger_reports` hashes its own report input. `adapters/workbench/list_paging.py`
opens the lists of written documents (`invoice`, `sales-receipt`, `proposal`, `estimate`,
`work-order`) on `desc` and every other list on the order its records already carry, and
builds the list's paging controls. Because a query command mints only a forward
continuation, the walk back lives in the page's own address: `trail` carries the cursors
of the pages already passed through and `page` carries the number, bounded at
`DEPTH` entries so the URL cannot grow without limit; past that the walk back stops
being offered and the first page — which needs no cursor — is offered instead. List
filter values are translated by `forms.query_value`, the same typed translator the
generated forms use, so a flag arrives as a flag and `unset` means no filter at all,
which is what the estimate list's All availability sends.

Invoices, sales receipts and estimates render as a document window instead of the
generated field list. `adapters/workbench/document_form.py` is presentation only: it
places the typed leaves into a header band, one line grid, a footer band and two
collapsed advanced sections, supplies human labels, and copies the server's computed
money for display. Every control keeps the name the generated form gives it, so the
browser submits the same command input an agent sends; the module places each leaf
exactly once and anything it does not name reaches the reader in the advanced
sections rather than disappearing. Line rows keep the shared collection contract
(`data-collection`, `data-collection-items`, `data-collection-item`, the add
template) so reference pickers, numeric entry, add/remove and line-origin
preservation are unchanged. The Amount column is the server's computed net and is
read-only; the whole-line amount price is a separate pricing input. Nothing on the page
is calculated in the browser, and `sales-receipt` and `estimate` omit the invoice's
payments-applied and balance-due footer. Save & New returns to a fresh document.

Every column head carries a short hint from `COLUMN_HINTS`, shown once under the head
rather than once per row, because a bookkeeper reading the grid asked what Unit meant
next to Quantity; the same words are the control's own description wherever the row
panel holds it instead. The unit column is headed `Unit of measure` for the same reason
and is shown for every company. The pricing selector is not a column: it chooses which
of the exclusive price inputs is live — a rule for the whole line rather than one of its
numbers — so it lives in the row's own `More on this line` panel beside `net_amount`,
under its generated `price-mode:` name, and the columns are the values a person reads
left to right.

**Above 700px the line grid is a table that scrolls inside its own container; below it,
each line is a block.** A line item carries more horizontal information than a phone can
show, and a grid that only kept the *page* from scrolling still dragged a ~1030px table
through a ~340px window. Under the breakpoint `.line-row` stops being a grid row the way
`.line-row-allocated` already does and becomes a two-column block: item and description
full width with their labels above, the small numbers paired two to a row (quantity
beside unit, rate beside tax) by `order` on `data-line-column`, the server's amount as an
emphasised right-aligned footer, `More on this line` collapsed under it, and the row's
own controls — including the Remove that releases a billed-from-quoted-work line's scope
— at the end of the block. `.line-cell-label` is visually hidden only while the column
heads exist; below the breakpoint it becomes a real visible label, positioned statically,
because an absolute one previously widened the layout viewport. The measurement that
holds this is the grid's own `scrollWidth == clientWidth` at 390px, not the page's.

Saved invoice and sales-receipt lines use the semantic `.sales-lines` table. On screens
at most 700px wide each row becomes a grouped card with visible field labels, full-width
item/description and quoted rate, paired quantity/unit and net/tax, and emphasised gross.
The footer retains currency, net, tax and gross within the phone width. Pricing mode is
in collapsed captured-rule details. These responsive rules apply only to screens; print
uses the normal table. Saved proposal, estimate, work-order and journal detail tables
use `document-detail.css`, linked by the base template. Explicit document selectors and
a screen-only 700px breakpoint present semantic table rows as paired phone cards with
full-width prose, visible field labels and emphasized totals; desktop and print retain
tables. Work quantities, completion, billability, quoted-source indicators and
annotation links retain their captured values, with price mode and markup in the
collapsed internal cost/rules details. Journal cards retain debit/credit, optional
original currency/rate and totals; revision accounting-history tables use the same
scoped layout.

Every sales document page carries a toolbar back to the documents already written.
`adapters/workbench/document_nav.py` builds it and `templates/document_nav.html` renders
it, on `invoice`, `sales-receipt` and `estimate` and nowhere else. On a form it is links
only and opens no read; the new-document form also carries a recent list the browser
fetches from `/c/{company}/_recent/{noun}` after the form has rendered, so writing a new
document never waits on reading old ones. A correction form carries no arrows, because
stepping off it would discard what was typed. The saved-document page carries Previous
and Next, which walk the sequence the noun's own query command pages: accounting date,
then the document's stable id, which is the list's order and the order they were
entered. Voided sales and inactive estimates stay in that sequence — the sales list
shows voided documents by default, and a document outside its own sequence would have no
arrows while you stand on it — so `estimate query` is called with `active=None`. A step
with nowhere to go is disabled text carrying no destination, never a link. The arrows
cost one bounded query on the saved-document page while a company's documents of that
type fit in one page (200), and three past that size: the page-sized probe that
establishes it, then a descending window anchored on the document's own date for the step
back and an ascending one for the step forward, each the exact reverse of the other. The recent list asks `direction`
for the newest few directly. Company size disables neither control; what is not answered
past one page is the document's position in the whole list, which would cost an unbounded
count, so no position is shown rather than a wrong one. A document neither window can
place says so instead of guessing.

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
catalog, installs visibility, or writes policy state. It accepts only a root at
`storage.migrate.HEADS['hub']`, read from that constant rather than pinned, and
stamps what it read; any other revision fails `schema_mismatch`. Independent complete SQL
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


### Identity and membership capability defaults (hub0013)

Hub `hub0013` follows `hub0012`; company history is unchanged. It seeds the six
`role_capabilities` rows the command registry already declared and the chain had
never inserted: `membership` at `authenticated` for `readonly`, `standard`,
`admin`, `owner` and `hub_admin`, and `user` at `hub_admin` for `hub_admin`.
Without them a root leaving `legacy` mode denies every role, owners included,
`user add|list|set-password` and `membership grant|revoke|list`.

The insert reads the existing rows first and writes only those absent, so a root
upgraded twice and a root already carrying one of the six both succeed against
the `(role, capability, required_role)` primary key. Both the read and the insert
address `main`, so a same-named ordinary TEMP table takes neither.

`tests/test_migration_chain.py::CURRENT_ROLE_CAPABILITY_SEED` is derived from the
migration chain's own `ROLE_CAPABILITY_SEED` constants, newest table-clearing
migration onward, and `test_frozen_role_capability_seed_matches_registry` asserts
it equals the registry projection. The chain is the frozen side of that equality;
deriving it from the registry instead would make the assertion vacuous.

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
serialized source facts. That storage increment supplied no command surface;
the registered deposit commands are described under *Deposit commands* below,
and Delete execution and reconciliation certificates remain absent.

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
versions over the company's posting families. The registry covers every document
type `ledger_schema.TRANSACTION_TYPES` declares, and it is written against that
tuple rather than against a retyped copy: journal entries (including register
translation), payment cash, sales receipt control/net, retained invoice net
recognition, persisted deposit bank keys, the funded leg of a bill payment,
customer refund or sales tax remittance, and the settled documents -- bill,
credit memo and vendor credit -- which post no statement leg at all and prove it
by refusing one rather than by returning nothing. `Producer` in
`reconciliation_models.py` is that same tuple. An unadapted producer is not a
partial result: `enumerate_graph` refuses the whole graph, so one of them makes
every account it touches unreconcilable, and
`tests/test_reconciliation_adapters.py` fails the moment the ledger admits a type
the registry does not name. These modules register no command, write no
database, and do not activate `deposit_dependencies.RECONCILIATION`.

References use their producer's stable commercial line plus role, or the
existing deposit bank key, or -- for a funded document -- the document itself,
because one payment is one statement line however many bills it answers. Account
is version data. Receipt control lines share one remittance movement;
net-recognition credits have a separate movement.
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

## Deposit commands

`commands/deposit_cmds.py` registers `deposit post`, `deposit update`, `deposit
void` and `deposit sources`. Each is a translation of the owned private aggregate
and takes no accounting decision: the planner calls `deposit_lifecycle.prepare`,
the applier calls `deposit_persistence.execute` inside the company transaction
dispatch opens, and the aggregate writes its own audit event, so the command
returns `Applied(..., audited=True)` with no dispatch entries of its own. A
recovered permanent operation is returned by the planner and the applier writes
nothing. Registration puts deposits on the CLI, HTTP, MCP and workbench like any
other command; the home window's *Make deposit* tile is live and lands on
`/deposit/post`.

`company/deposit_outputs.py` is the closed public projection. The private
`LifecycleOutput` carries physical posting, attribution and captured profile
facts that belong to the ledger rather than to a command result, so the wire
contract exposes the document, the receipts it banks, the money entered beside
them, the cash-back line, each bank account's statement movement, the membership
claims and releases, the posting batches and the audit event. `deposit sources`
projects candidates the same way and adds the authorized count and subtotal of
the whole filter. Every amount is minor units plus currency.

`deposit_persistence.compose` is the one producer of an aggregate's rows, output,
touches and operation receipt, and it writes nothing; `_execute` adds the DML and
`preview` returns the same output with every identity this operation would
allocate replaced by its logical token, so a dry run publishes no provisional
physical ID. Preview and commit therefore cannot disagree.

Hosted requests carry their authenticated producer on `Session.credential`, set by
`adapters/http/execution.run_hosted` from the credential it already revalidated
and cleared when the request leaves. The deposit commands pass it as the binding,
so `deposit_dependency_history.execution_binding` revalidates the actual bearer or
session cookie instead of deriving an OS login the host does not have. A local
session leaves it None and the private layer builds its `OSBinding` as before.

Correcting or voiding a deposit needs the authenticated `dependency_guard` its own
dry run issues; committing without one is `E_PREVIEW_STALE`. Claiming a receipt
bumps that receipt's header version, so a replacement reads its members back
through `deposit sources` with `for_deposit`.

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

### Private G3 durable deposit composition (co0024)

`deposit_drafts`, immutable full `deposit_draft_revisions`, source/additional rows
and permanent draft row keys store incomplete composition without financial writes.
`deposit_selections` owns separate child revisions and atomically accepts only their
sources into an unchanged parent header/additional/custom/cash-back context.
B1 requires all reciprocal heads before the final `PRAGMA main.foreign_key_check`
inside the nonposting rollback boundary. Financial draft consumption belongs to
the financial owner: ordinary post/update and coordinate replacement write the
consumption links before their final whole-main foreign-key check and persisted
reciprocal validation. Coordinate consumption uses the existing C savepoint; the
nonposting draft writer is never invoked as its child.

The private draft provider expands a complete owned immutable revision below the
unchanged bounded inline inputs. Current authority, cash, claims and reference
eligibility are checked independently from captured display and custom values.
A consumption receipt binds the original draft revision/hash, exact row identity
map, financial operation and event. The consumed header advances once without
rewriting its composition revision. Identical edits still consume once and retain
a permanent no-effect operation. Exact authorized recovery returns the original
effect with separately labeled current deposit/draft state and performs no write.

Memo patch omission retains; null and empty text are distinct entered overrides;
`memo_action: restore_source` is exclusive and restores captured source origin.
Ordinals are persistent display/paging identities; newly added sources appear last.
Select-matching resolves the complete authorized match set atomically with no
aggregate cap. Full immutable snapshots cost O(manifest) storage per edit and
full authority/source validation costs are not a responsiveness waiver.

Private draft/source query admission requires current applicable organization or
exact-company membership for actor and actual fixed principal. Existing role helpers
resolve the highest applicable role; unrelated scopes are excluded and installation
hub-admin status supplies no automatic company-book access. It additionally runs the existing complete resource checks. Shared discovery/publication/full-C cutover remains separately owned.
Historical draft/child/member/operation roots are authorized before JSON decode,
filtering, counts or pages. Opaque company-keyed candidate tokens exclude destination
writes and unrelated audit changes; item tokens bind their exact immutable revision.

`DraftCreate.copy_from_voided` is distinct from editing a posted deposit: a new
unconsumed identity with no edit pin or reused number, retaining original row and
source provenance. Copy records the current source header version; copied source
`captured_header_version` retains the original cash evidence endpoint. Unavailable,
changed or claimed sources remain visible as stale, never silently removed. The
original voided transaction, claims and any consumed draft are untouched. Source
refresh is explicit. Financial posting allocates fresh destination identities
for copied rows; only a genuine edit retains the eligible original financial keys.
Complete manifests are not limited to a single source page.

Custom values retain typed canonical values, definition/choice snapshots and
origins; blank required values report incomplete state. Existing definitions lack
print visibility, so `print_visible=None` records unavailable provenance rather
than an invented default. The print owner must resolve this before full G3.
Public command registration, current full-C publication integration, GUI/MCP
continuation, reconciliation activation and Delete remain separately owned
requirements.

### Private stored deposit reads

`deposit_queries` supplies typed `show`, `query`, `items` and `history`;
`deposit_print_data` supplies complete captured print data without rendering.
These functions require an existing authenticated binding and caller-owned read
snapshot. They are not registered public commands or hosted publication routes.

`deposit_read_authority` admits historical transaction, operation and draft roots
before sensitive decoding. `deposit_read_facts` loads complete stored graphs and
`deposit_read_validation` checks captured source endpoints, immutable row evidence,
financial equations and lifecycle projections. `deposit_read_manifest` pins the
owned field, edge and codec inventory. Repeated draft references reuse validated
results only within one `load_complete` invocation; consumption validation remains
independent. No result survives the call or crosses snapshots.

Selected immutable revisions remain separate from freshly observed current
metadata. Dated states sum recorded effects through the supplied effective date.
Items continuations pin the selected revision while rechecking current authority.
Query and history continuations bind their complete authorized result relation;
an authorized malformed aggregate fails the complete query. Company-keyed tokens
use separate read domains without exposing audit sequence or permission epochs.

Print data includes every selected row and cash allocation, captured issuer and
custom values. Unknown historical print visibility stays null. A line reduced to
zero retains its semantic occurrence and ordinal, with no invented zero-value
cash allocation; restoring its amount restores funding under that identity.
Reports and public GUI/MCP integration remain separate consumers.

### Private deposit reports and historical UF control

`deposit_reports.detail`, `deposit_report_print.print_data`, and
`deposit_report_uf.uf_bridge` consume current authenticated read snapshots. They
are private APIs, not registered commands. The read owner supplies complete
validated deposit revisions, effects, claims, bank versions, operations and
consumed-draft links; print assembly reuses that loaded snapshot. Current
business totals remain distinct from signed original/reversal/replacement
movements. Opening, period, closing and role totals carry their selected
population qualifier, never an account-balance claim.

`deposit_cash_history.load_cash_history` admits complete source graphs before
loading bodies, proves immutable rows and historical header endpoints through
`deposit_dependency_history.History`, and reuses the source owner's cash
partition plus the read owner's exact posting lifecycle proof. It retains
never-deposited, replaced, voided, direct-bank and inactive-reference histories.
The generic `deposit_sources.project_cash` reports the actual captured cash
account; the existing UF `project` contract is unchanged. Direct-bank effects
never become UF receipts. Claim releases use their actual inverse batch dates.
The separate whole-company UF section computes receipt cash minus net claims,
compares all UF ledger lines, and reports the unexplained scope difference.
It is complete or typed unavailable, never partial/zero. Base authentication
errors, corrupt history and I/O are not converted into unavailable sections.

The existing read cursor codec now admits the fixed report-detail domain while
preserving its old domains. Fingerprints cover complete selected rows/totals and
conditional UF facts, exclude observation clocks/global audit sequences, and
are checked in each fresh snapshot. Unrequested UF loads nothing; a still-denied
section contributes its state only. Private ReadEvidence accompanies the
results; it is not a public permission certificate. Registration, fullC fresh
publication, GUI/MCP workflows, CSV/HTML/device rendering and future Delete
integration remain separately required. No schema, financial posting algorithm,
report recognition policy or reconciliation activation changes here.

### Deposit receipt picker

The generated `deposit post` workbench form defaults to inline mode and supplies its
operation key. `deposit-picker.js` populates the bank selection from `account list`
and paginated receipt selection from `deposit sources`, including captured source
versions. It writes the existing typed collection controls. The shared generated
form and sales preview transport own submission, fingerprint invalidation and retries;
no browser accounting is added. The preview and session-bound success flash show
server bank totals and receipt amounts. The integration has no registered deposit
show command, so success returns to the company home with that confirmation.
Advanced mode and custom fields remain available; correction/draft workspaces are
separate from this ordinary new-deposit picker. Exact JSON uses getRandomValues for
its temporary integer-token prefix, including on plain HTTP LAN workstations.

Work completion with an end timestamp but no start names `actual_start` (record when work began); timestamp-order checks still handle an end before its supplied start.

Item profile validation reports missing type and enabled sales/purchase fields together in E_VALIDATION.fields. Zero prices and costs remain valid. Item creation shows sales/purchase setup guidance; accounting reference and profile rules remain authoritative.

Workbench forms use business-facing context labels and reason guidance. The JavaScript requirement is shown only when scripting is disabled; internal transport details are not ordinary form instructions.

General-ledger opening, posting and closing rows expose current_account_name, current_account_number and display_account_label using the existing report account-display rule and company preferences. Captured account_snapshot remains historical; current labels do not rewrite it. Ordering, arithmetic and pagination are unchanged.
Saved deposit reads coexist with the current deposit write aggregate (2026-09-09).
`deposit show`, `deposit items`, `deposit query` and `deposit history` use the
reviewed closed public models, field manifest and projection producers from
history-public, with a sealed reader-bound publication proof on offline, hosted,
forwarded CLI, MCP and browser execution. Only those command names enter this read
path; post/update/void/sources keep their current lifecycle, persistence and
publication owners. `deposit history` pages the deposit's own recorded events in
audit order in its own continuation domain; the operation recovery key an entry was
submitted under is declared private and never reaches the wire. The read authority's
reference vocabulary is checked against the existing list definitions and
permission catalog, without importing the audit/history projection family.
The existing financial reader accepts selective note/attachment acquisition and
a shared observation time; its financial/draft derivation is unchanged. Release
checks reauthenticate the original producer and selected company without rebuilding
the projection. Permission self-observation retains `activated=False`; only fixed
schema reflection metadata is cached, never permission decisions or root facts.
Workbench record routes reserve registered verb paths for their forms, link the
saved confirmation to the real detail, and page composition separately in phone
cards. Update/void forms take their optimistic version from the public current
state. Deposit query remains unregistered; this increment does not complete the
broader deposit/history module.

Basic browser reports (2026-09-09): the statement presentation owner also renders
trial balance and general ledger, using only the existing typed command results.
The basic-report template shows exact supplied totals separately from page counts,
current account labels, debit/credit/net or running balances, and expandable
captured posting facts. Company-local account links open the current ledger for
the reported period. Continuation submits the retained validated filters in its
own form; the visible filter form omits the cursor and restarts after a stale or
invalid request while preserving the attempted filters. Shared document-detail
screen-only cards retain one semantic table for desktop and print; report CSS is
scoped to .basic-report. Calculation, report inputs, cursor validation and financial
history remain owned by the unchanged core commands.

Public deposit query phase (2026-09-09): the existing authenticated read family
now also produces a closed DepositQueryPage. It admits the selected company,
checks each complete connected aggregate, loads existing financial facts once for
the admitted set, projects governed headers/received-from references, then filters,
sorts and pages. Whole-match totals reuse deposit_queries.query_totals; no posting,
draft or financial-validation algorithm changes. An explicitly denied graph is
omitted; unclassified missing/permission evidence fails the complete query instead
of publishing partial counts/totals. Bank reference redaction preserves admitted
amounts, while an explicit bank filter requires reference admission before lookup.
Public query cursors bind every non-page input including defaults, sort/direction,
and the complete projected relation; page-size changes follow the existing offset
pager. Query publication retains a company-scoped page proof rather than a single
revision pin, with unchanged D11 identity/credential/company release checks. Public
writes and source discovery keep their existing execution path.

The saved-deposit list at `/c/{company}/deposit` renders the registered public query page through the deposit workbench adapter. Filters and continuation tokens remain command inputs; all whole-match and revision/effective amounts come from the public projection, and the whole-match totals are labelled apart from the rows on this page because they are computed over the whole match and do not move with page size. A voided deposit keeps its revision amount in those totals and contributes no effective bank movement. A semantic table becomes screen-only phone cards using document-detail styles, keeping its explicit table/row/columnheader/cell roles at phone width. Detail, revision and composition links carry a validated same-company list return location and fall back to the plain list for anything else. Changed filters start a fresh result, previous and next retain them, and a staled or invalid continuation keeps the entered filters and offers an explicit restart. An explicit bank filter matching no account here refuses as `E_RECORD_NOT_FOUND` on field `deposit_to`: the list selector's own refusal carries open suggestions, which a closed public deposit failure cannot hold, so an unnarrowed refusal leaves the captured-outcome path and reaches the reader as a permission denial rather than a correctable filter mistake.

## Customer credit memos, and the settlement edge they share

**One receivable settlement edge, two kinds of source.** `applications` and
`application_allocations` stay the single place an invoice's due falls, and each gained one
nullable column — `applications.credit_source_key_id` and
`application_allocations.credit_source_component_id` — so a credit settles an invoice through
exactly the rows a receipt does: the same `settlement_line_keys` ordinals, the same recognition
roles, the same exact-inverse rules, the same allocation function. The alternative, a parallel
`credit_applications` pair, would have required a branch in twenty existing readers — every one
a place a credit could be forgotten — and would have split the `applications.id` namespace that
six other owners address a settlement row by. Nothing is backfilled: NULL is the true value of
both new columns for every row ever written, and which kind of source a row carries is derived
from which column is present rather than stored as a classification something else can
contradict. `applications_one_source` is the fence that keeps exactly one of them present and
owned by the paying document.

`posting_line_sources` gained no column. Credit attribution points **outward**:
`credit_components.posting_source_id` and `credit_tax_components.posting_source_id` name the
attribution row, where a receipt's attribution row names the payment component. That is the
same inversion `ap_obligation_components` established, and it is why the credit tables could be
added without touching the posting schema at all.

**What a credit memo is.** `credit_profiles`, `credit_line_profiles` and
`credit_tax_components` mirror `sales_profiles`, `sales_line_profiles` and
`sales_tax_components` cell for cell, because a credit memo is a sale read backwards. Its
accounting is the invoice's, reversed: each line debits its captured income account for its net
and its captured tax liabilities for their cells, and Accounts Receivable is credited the gross
**once**, with one attribution row per line on that single credit. Those attribution rows are
what `credit_components` name, which is how a later application can land on particular lines —
the shape `bills.py` established for the payable. Saving a credit applies nothing.

**Two documents in one noun, told apart by what a line claims.** A *standalone* line names its
own item and is priced by `sales_defaults.resolve_line` and the same tax calculator an invoice
uses. A *returned* line names a source invoice line and is priced by nothing at all: every net
and tax cent is taken from what that invoice captured. A document is all of one kind, and mixing
is refused — the tax calculator rounds across a whole document, so a document holding one
calculated cell and one captured cell would carry a tax total that is neither.

**The endpoint rule** (`credit_returns.py`) decides every cent a return carries. A captured
source line of base quantity `Q` and net `N` gives a returned half-open interval `[a,b)`
exactly `floor(N·b/Q) − floor(N·a/Q)` cents, and a captured tax cell `T` gives the net interval
`[u,v)` exactly `floor(T·v/N) − floor(T·u/N)`. Both are differences of one cumulative function
at two endpoints, so any disjoint set of intervals telescopes to exactly `N` and exactly each
`T` whatever order they were claimed in, releasing an interval and claiming it again returns
the identical cents, and no interval can be worth a cent another interval also claims. A
running remainder has none of those properties, which is why none is used. `credit_source_claims`
stores the intervals and not the money, because the money is a function of the interval and the
capture, and two facts that can disagree are worse than one fact and a rule. The residue still
returnable on a line is the complement of its active claims, lowest first; asking for more than
that is `E_RETURN_EXHAUSTED` with nothing written.

**One number series with invoices.** `document_effects.NUMBER_FAMILIES` maps a document type to
the family it draws its numbers from, and an invoice and a credit memo share the invoice series:
a credit memo takes the next invoice number, the way the anchor product issues one, so a
customer reads one unbroken run across both documents. `uq_transaction_type_number` is per type
and cannot say that, so the partial unique index `uq_transaction_receivable_number` refuses the
same number on an invoice and a credit memo, for a generated number and an explicit one alike.

## Applying a credit, refunding one, and the rest of a credit memo's life

**Applying a credit posts nothing.** The credit memo already moved the money: it debited the
income the sale recognised and credited the customer's receivable. What an application changes
is which of the two a reader sees them against, so `customer-credit apply` writes settlement
rows and never a posting line, and the trial balance is identical cent for cent either side of
it. What moves is the invoice's due, the credit's remaining worth, and where the A/R aging puts
the money — the aging total is the receivable control before, during and after.

**One application per credit component.** A credit's capacity is one `credit_components` row per
credited line, and an `applications` row names exactly one of them, drawing the components down
in id order; a request spanning two credited lines writes two application rows against the same
invoice. That is not an accident of implementation. `payment_restatement.validate` re-derives an
invoice correction's split and requires **one allocation per target component per application**;
a single application drawing on two source components would need two rows for one target
component and would fail there. Keeping the draw inside one component per application keeps the
invoice-side rule intact, and `payment_restatement.prepare` then copies the source columns from
the application's own first allocation, which is exactly right when they all name one component.

**Where a credit's remaining worth is computed: `credits.facts`, once.** Available is capacity
less active applications less active refund consumptions, and `remaining` breaks the same
arithmetic down per component. Both subtractions live in one function so no reader can take one
and miss the other; `credit-memo show`, `credit-memo query`, `customer-credit apply`,
`customer-refund post` and the refund's independent validator all read it.

**`receivable_reports._EFFECTS` joins the union, not the payment keys.** Its `settled` CTE used
to inner-join `payment_component_keys`, which would have silently dropped every credit
application: the report's total would still have tied to Accounts Receivable, but the invoice
would have aged at gross with the credit aged separately as a negative. The join is now over
`settlement_sources`, a literal `UNION ALL` of the payment keys and the credit keys — the two
tables carry the same `(transaction_id, id, party_id)` triple deliberately, so the union is one
projection and the two branches cannot drift.

**What a refund is.** `customer-refund post` debits the customer's receivable and credits the
bank account the money left, and posts nothing else. No income leg and no tax leg: the credit
memo already reversed the sale, and touching either again would take the same revenue down
twice. `refund_validation.py` asserts that by account *type*, because a second reversal is
invisible in a total that still balances. `registers.REGISTER_TYPES` already admits a
receivable register, so a check debiting A/R for a customer posts the cash correctly and
consumes nothing at all — that double-spend is what the typed document closes.

**Consumed once.** `customer_refund_consumptions` is an immutable positive row naming one credit
source key and one credit component, with a unique inverse column mirroring `uq_payment_unapply`.
A refund is not an `applications` row and the rule that keeps the two apart is stated once:
**`applications` edges settle obligations; refunds consume sources.** A settlement row targeting
a refund would be dropped by `_EFFECTS`'s `settled_party` inner join to `sales_profiles`, which a
refund has none of — the right total by accident. A refund is also not an obligation: it has no
line components for an allocation to attribute to, and nothing about it is due.

**The three dispositions, and their exclusivity.** Retain, apply elsewhere, refund — the three
the anchor product's own Available Credit dialog offers. They are mutually exclusive by one
subtraction: after a refund the credit cannot also be applied, and after an application it
cannot be refunded beyond what is left. Both answer `E_CREDIT_UNAVAILABLE` and write nothing.

**Void.** `customer-refund void` reverses the accounting at the refund's own date and writes an
exact release for every consumption it made, so the credits it paid out are worth again exactly
what they were worth before. `credit-memo void` reverses the credit at its own date, releases
every source interval it claimed — re-returning a released unit yields the identical cents, by
the endpoint rule — leaves the number occupied and every revision readable. A credit something
still stands on is refused rather than quietly released: `E_HAS_APPLICATIONS` for a live
application, `E_HAS_REFUND` for a live refund.

**An invoice correction cannot contradict a live claim.** The credit side already failed closed:
`credits._returned` compares a live claim against the line as it stands and answers
`E_SOURCE_CORRECTION_CONFLICT` rather than pricing a return from a line that has moved. The
invoice side did not fail at all, so a correction could reprice a line an issued credit was
built from, in silence. `credits.require_claims_intact`, called from
`payment_invoice_corrections.prepare` — the single door every `invoice update` goes through —
is the other half of that fence, comparing a claimed line's base quantity, its net, and the tax
cells the credit's own cells were captured from. An issued credit is never repriced from a
corrected source, so the only two honest answers are "leave the claimed line alone" and
"refuse"; correcting an *unclaimed* line of the same invoice still works, because a claim names
the permanent line occurrence.

**`payment_invoice_corrections` had to learn about the second source kind.** It resolved every
`paying_transaction_id` through `payment_facts`, which resolves a document of type `payment`, so
an invoice with an applied credit answered `E_RECORD_NOT_FOUND {record_type: payment}` on every
correction — `payment_restatement` itself was correct but unreachable through the only door.
`_source_facts` now branches on the stored document type, `settlement_versions` accepts a credit
memo as readily as a receipt, and a credit reports its current settlement through
`credits.settlement_current_output`, which is the receipt's own shape because
`credit_source_keys` carries the same party/receivable/currency triple `payment_component_keys`
does.

**Credit correction.** `credit-memo update` implements the unconsumed correction path through
`credit_corrections`, the shared credit commercial resolver and document persistence. Omitted
lines copy their exact captured quantities, item facts, tax cells and source intervals; supplied
lines replace the grid with retained permanent line identities where supplied. Claims are
released and retaken in the same writer transaction as the immutable revision and exact
reversal/replacement postings. The validator compares reversal legs and attribution to storage,
checks exact claim inverses and verifies replacement arithmetic separately. Expected versions,
preview fingerprints, idempotent retries and both accounting dates remain guarded. Exact no-op
patches create no history and work independently of active applications/refunds.

**Unresolved correction scope.** Actual changes to applied/refunded credits remain unimplemented
pending the settlement/refund correction contract. Customer/AR changes also remain unimplemented:
the model accepts them and storage permits different immutable source keys for distinct
customer/AR/currency dimensions, but the current readers select the first key for a transaction.
Changing the revision alone cannot change settlement ownership; selecting and reading the
current dimension key consistently remains unimplemented. This is a partial increment,
not completion of the credit update lifecycle.

`customer-refund` has no update either, and for
the settled reason: a refund is one customer, one amount, one date and one account, so changing
any of them makes it a different refund and the document carries one revision for life. A
refund's source is a credit memo only; refunding unapplied payment overage needs
`payment_facts.available` to gain the consumption term and is not built. There is no recovery
family. Price allowances against a source line,
stocked returns and cost restoration, cross-party (parent↔job) credit, cash-basis treatment, the
refund's reconciliation producer and print are outside the release entirely and are refused
rather than approximated; the command help says so.

## The three credit documents in the browser

**`ui_group` is what files a noun, and its absence is misfiling rather than absence.**
`pages._grouped_nouns` falls through to "Hub" for any noun without one, so `credit-memo`,
`customer-refund` and `customer-credit` were listed under Hub on every company page and
`vendor-credit` with them. All four carry one now, in `registry.NOUN_META_OVERRIDES`: the three
receivables nouns under "Customers and sales" and the vendor credit under "Vendors and
purchases". `vendor-credit` had no override row at all, so its `record_type` was the derived
`"vendor-credit"` rather than `"transaction"` and its record page asked the audit trail for a
record type nothing writes; the row fixes that in the same place.

**All three open in the document window the invoice and the bill already use.**
`document_form.py` gains them: a credit memo is the invoice's window — the same priced grid,
the same footer, the same tax rule — with the two columns that make a row a return instead of a
sale (`source_invoice`, `source_line`) and without the addresses, which belong to the invoice it
credits, or the price-level machinery, which prices new work rather than taking a sale back. A
vendor credit is the bill's Expenses grid with `billable` removed. A refund's grid is neither:
its rows are the credits being spent, one credit memo and how much of it goes out.
`document_form.refund_totals` and `vendor_credit_totals` copy the server's own figures the way
`bill_totals` does; nothing on any of the three pages is arithmetic done in the browser.

**The two seeded openings, and why they seed *attempted* rather than *originals*.** A return is
written against an invoice and a refund against a credit, so both windows open from the document
they answer: `/credit-memo/post?invoice=<id>` fills one returned row per line of that invoice
already naming the source invoice and the source line, and `/customer-refund/post?credit_memo=<id>`
(or `?customer=<id>`) fills the sources with what is still available. Both write into the form's
`attempted` controls. An `originals` map is a correction's comparison baseline and
`forms.translate` drops every leaf equal to it, so seeding there would silently submit an empty
grid. `credits.return_rows` and `credits.refund_rows` are the two projections; the invoice's own
page carries the link that opens the first.

**The apply surface carries the versions.** `customer-credit apply` needs `expected_version` on
the credit *and* on every invoice it touches, and `unapply` needs each application's id with the
invoice's version beside it. `/c/<company>/credit-memo/<id>/apply` reads them and puts them in
its own hidden fields: the credit's version from `credit-memo show`, each invoice's from the
`settlement_current` its own `invoice query` row carries, and the standing applications from
`invoice settlement` — read only for the invoices that have money applied, so the page costs one
query plus one settlement read per settled invoice rather than one per invoice. A version that
has moved is answered by the command with `E_VERSION_CONFLICT` and shown on the page; nothing
here refreshes a stale version and retries, because that would apply a credit to an invoice the
person never saw. `credits.py` mounts both routes ahead of the generated
`<noun>/<record>/<verb>` route, which would otherwise read `apply` as a command name.

**Where the pickers are declared.** These three nouns have no Row 5 list definition to hang a
`ReferenceDefinition` on, so `credits.FORM_DEFINITIONS` holds them and `pages._form_definition`
reads it as the last fallback after a list definition and a domain form definition. There is no
picker for `source_invoice`, `source_line` or `credit_memo`: a picker searches a list command,
and a document is not a list. The seeded openings are the answer to that, not a workaround —
they are how a return and a refund are actually written.

**Reading them back.** `credit_memo_detail.html`, `vendor_credit_detail.html` and
`customer_refund_detail.html` render one saved document each from `credits.detail_context`, and
`document_nav` gains all three so the arrows step between them exactly as they do between bills.
The credit memo's page says what the credit is worth now and offers the two things that can be
done with it; the vendor credit's says what it still has free and which bills it answers; the
refund's says which credits it paid out and what each was worth first. Below 700px every grid
and every table becomes one block per row, asserted at 390px in
`tests/test_credit_windows_browser.py` as each element's own `scrollWidth` against its own
`clientWidth`.

**The three tiles are live.** Credit memo and Refund on the Customers panel, Vendor credit on the
Vendors panel. `tests/test_credit_windows_browser.py` performs the availability contract rather
than asserting it: it clicks each tile on the home board, enters a document through the page the
click lands on, saves it, reads the rendered figures back, and then applies a credit from its own
page and reads the invoice's Balance Due. `tests/test_home_window.py` moved its "not built yet"
stand-in from vendor credits to purchase orders, because vendor credits now have commands, a
route and a page and can no longer stand for something that does not.

**Still not in the browser.** `credit-memo void`, `customer-refund void` and `vendor-credit void`
are reachable only as generated forms from their record pages; `vendor-credit apply` and
`unapply` have no page of their own, so a vendor credit is pointed at a bill through the command
surface; `customer-refund` and `vendor-credit` have no `history` command to page; and nothing
prints a credit memo.

## Sales tax liability, and the document that remits it

**Why the liability is derived rather than stored.** `sales_tax_components` already records, for
every posted sale, which agency each tax cell belongs to and which liability account it credited.
Nothing needed to be added to know what is owed; what was missing was a read that asks the
question and a document that can answer it. `sales-tax liability` starts from every posting
effect on an account whose `system_role` is `sales_tax_payable`, dated on or before the as-of
date, signs it credit-minus-debit because a tax liability is credit-normal, and attributes each
effect to an agency.

**Three attributions, one rule.** A sale's tax leg names its component through
`posting_line_sources.tax_component_id`; a credit memo's tax leg is named by
`credit_tax_components.posting_source_id`; a remittance's liability leg is named by
`sales_tax_payment_profiles.liability_posting_source_id`. A reversal is resolved by following
`posting_line_sources.reversed_source_id` back to the attribution it inverts, which is why
voiding an invoice, a credit memo or a remittance moves the report by exactly what the document
moved. An effect no attribution claims — a journal entry posted straight at the liability — is
reported on its own row with no agency rather than dropped. That is what makes the report's total
the account's own balance for the same date, and both the per-row identity
(`tax_charged − tax_credited − remitted + unattributed = balance`) and the totals are checked in
Python, where an integer is exact and unbounded.

**Accrual only, and why that is the honest answer.** `company_info.sales_tax_liability_basis` has
two settings, and `sales_defaults.resolve_line` refuses to post a taxable sale at all unless it is
`invoice_date`. So a company on `payment_receipt` has never recorded a tax component, there is no
deferred-tax account, and no posting moves tax from unearned to payable — a cash-basis figure
could not be computed from anything stored. Both `sales-tax liability` and `sales-tax pay` refuse
with `E_TAX_BASIS_UNSUPPORTED` rather than answering with an accrual number under a cash-basis
policy.

**A dedicated document, not a check.** A check names an account; it does not say whose liability
fell, and there is no second table that could say it afterwards. `sales_tax_payment_profiles` is
the one-to-one header of a remittance revision — agency, liability account, funding account and
kind, method, check number, the period end it answers, and the attribution row that debited the
liability. Its accounting is one debit to the captured liability account and one credit to the
funding account; that is the whole ledger effect. The anchor product's own guidance is explicit
that a check written to a tax agency is an error to be voided and re-entered through the proper
feature, and this is the structural reason why.

**No settlement edge.** Sales tax is a balance, not a set of documents, so a remittance answers
nothing and there is nothing to apply, unapply or re-point. A partial remittance leaves the
remainder owed because the balance is the arithmetic of the postings — which is also why the
remainder stays right afterwards: a later invoice raises it and a credit memo lowers it without
the remittance being revisited. `amount` defaults to everything owed through `through_date`;
more than that is refused with `E_APPLICATION_CAPACITY`, and the check is made again inside the
writer's transaction so two remittances racing for one balance cannot both post.

**Immutable, void only.** There is no `sales-tax payment update` and no `history`. A remittance
has three facts — the agency, the amount and the date — and changing any of them makes it a
different remittance, so the correction path is void and write again. One revision for the life
of the document is also what lets `liability_posting_source_id` name one posting attribution for
ever rather than one per revision, and a history walk over a document that can only ever have one
revision would report nothing the `show` does not.

**What this increment deliberately does not do.** There is no sales tax adjustment document, so an
agency's balance can only be changed by a sale, a credit memo, a remittance or a hand journal
entry — and a hand journal entry lands in the unattributed row rather than on an agency. There is
no browser page, no multi-agency remittance, and no demo seed extension. `company_info`'s
`sales_tax_remittance_frequency` is captured at company creation and read by nothing here: the
period a remittance answers is `through_date`, supplied per document.

## Vendor credits, and the second kind of money that settles a bill

`vendor-credit post/show/query/void` and `vendor-credit apply/unapply` record what a vendor owes
back and point it at the bills it answers. `company/vendor_credit_models.py` holds the inputs and
outputs, `company/vendor_credit_facts.py` what a revision captures,
`company/vendor_credits.py` the resolution, posting, reads and settlement,
`company/vendor_credit_validation.py` an independent check of the aggregate, and
`company/vendor_credit_schema.py` the storage. The settlement edge, the sums and the capacity
arithmetic are `ap_applications`, `company/ap_settlement.py` and `company/bill_payments.py`'s own
`_selected`, `_free_capacity` and `_compatible`, imported rather than restated.

**The bill read backwards.** Entering a credit debits Accounts Payable by the total and credits
each captured purchase account by its own line amount. The line grid, the eligible accounts, the
payable resolution and the class rules are `bills.py`'s, so a credit can never be credited against
an account a bill could not be owed out of. `document_lines` carries the credit's lines with kind
`purchase` — the same envelope family a bill uses, because it is the same grid — which is why
`co0032` widened the document-line type guard and left `document_lines` itself untouched. A credit
has no terms and no due date: nobody owes it on a date.

**A source, not a payable.** A vendor credit creates no `ap_obligation_keys` row.
`payable_reports._UNPAID` lists documents of type `bill`, so an obligation key would put a credit
on the unpaid-bills list at a negative balance as a document nobody owes. What it creates instead
is an `ap_source_keys` row of kind `vendor_credit`, carrying the same `(vendor, payable account,
currency)` triple a bill payment's source carries, with one `ap_source_components` row per
credited line naming the exact `posting_line_sources` row that debited AP for it. `source_type` is
now a two-value column, and `ap_source_keys_transaction_id_type` checks a source against its own
declared kind rather than one hard-coded word, so a credit can never mint a source calling itself
a bill payment.

**`applied_totals` stays one number.** `bills.applied_totals` answers total settled, whatever
settled it. `settlement_output` derives `open = gross − applied` and a status from that scalar and
four call sites destructure it, so a bill settled in full by a credit reads `paid`, leaves
`report unpaid-bills` and ages to nothing with no branch anywhere asking which kind of money did
it. Composition is a separate, additive read: `ap_settlement.applied_by_source_kind` joins the
edge to its source key, and `settlement_current.sources` lists one row per kind, so a bill detail
page can say "46254 credit, 53746 cash" without any caller of the scalar learning a new shape.
Kinds that net to nothing are omitted.

**The payables reports needed no edit.** `payable_reports._EFFECTS` joins its `settled` CTE to
`ap_obligation_keys` only, and takes the paying document straight from
`ap_applications.source_transaction_id`, so a second kind of paying source moves through it
unchanged: before an application a credit is a negative AP row aged by its own date, and after one
its row nets to zero and is omitted. `_UNPAID` already reads the real applied sum. `aging.py`
stays one rule for both sides.

**What this does not do.** There is no `vendor-credit update`, no `history` and no demo seed
extension. A credit takes its own number series rather than sharing the bill series:
the anchor product's shared-sequence behaviour is a receivables rule about invoices and credit
memos, and the payables document it credits is numbered by the supplier's own reference. Purchase
discounts, purchase tax and vendor refunds are their own documents and none of them exists.

## Statement charges, and the invoice line with no invoice around it

`statement-charge post/show/query/void` charge one customer's account directly.
`company/sales_models.py` holds the three inputs, `commands/statement_charge_cmds.py` the four
commands, and everything else is the invoice's, unchanged: `sales.prepare`, `sales.commercial`,
`sales.show`, `sales.page`, `sales._business_postings`, `sales_defaults.resolve_header` and
`resolve_line`, `sales_validation.validate`. There is no `statement_charges.py`, and that is the
design rather than an economy — a statement charge *is* one invoice line, so writing it a second
implementation would be writing a second answer to what an invoice line posts.

**Why it is a sales document and not its own.** `receivable_reports._EFFECTS` builds the aging
and the statement from Accounts Receivable posting lines keyed by `name_id`, so any document that
debits receivable under a customer already ages and already appears; only `StatementRow.entry`'s
closed `Literal` had to admit the new word. Settlement is the part that is not document-agnostic:
`settled_party` inner-joins `sales_profiles` for the paying side's customer, and
`applications_exact_party` checks the same table. A charge stored in its own header table would
have needed both widened, which is precisely the hole that would have dropped every credit
application out of A/R aging when credit memos landed — a wrong report that still balances.
Storing the charge in `sales_profiles` and `sales_line_profiles` means the settlement increment is
a widening of type filters and two trigger words, not a change to how a report adds up.

**The header is the line.** The anchor product's charge is a single row — item, quantity, rate,
amount, description, class — so the input has no `lines` collection; `item`, `quantity`, `unit`,
`rate` or `amount`, `description` and `tax_code` sit on the header and a `lines` property builds
the one `SalesLineInput` the shared resolver reads. Two lines is an invoice. `description` becomes
the revision memo unless a separate `memo` is given, because the revision memo is what
`report statement` prints and a charge whose statement row says nothing is a charge nobody can
read. The surface carries no shipping address, ship date, ship method, sales rep, customer message
or purchase order: a charge is not shipped and not sent, only summarised. Those facts are still
*captured* onto the revision from the customer, exactly as an invoice captures them; they are not
asked for. `use_defaults` and `refresh_defaults` are `ClassVar` constants, not inputs — both are
questions about a correction, and a charge is only ever posted.

**No due date, by construction.** An invoice ages on the due date its terms compute; a charge has
no terms and no invoice, so its own date is the only date it could age by.
`sales_defaults.resolve_header` skips the terms and due-date resolution for anything that is not
an invoice, and `co0034` widens `ck_sales_profile_type_due` so `due_date IS NULL` is the stored
fact that says so rather than a convention. `_EFFECTS.dated` already read `CASE WHEN
t.type='invoice' THEN p.due_date ELSE r.date END`, so the aging needed no edit at all.

**Its own number series.** `document_effects.NUMBER_FAMILIES` is untouched: a charge is not in the
invoice/credit-memo shared run, and `uq_transaction_receivable_number` is deliberately left naming
those two, so a charge numbered 1 is never refused because invoice 1 exists. The anchor numbers
them separately for the same reason.

**Nothing can settle one, and it says so.** `applications_paid_transaction_id_type` admits a
document of type `invoice` and nothing else, and `co0034` does not touch it. Naming a charge where
an invoice goes therefore fails, and `payment_queries.invoice_facts` catches that failure, asks
whether the selector named a statement charge, and answers with `found_type` and a sentence saying
what was found — so the boundary is reported rather than read as a typo. What settling one would
need is in *What this does not do* below.

**What `co0034` does.** Three CHECK constraints widened by table rebuild — `transactions`
(twelfth document type), `sales_profiles` (a commercial type with no due date) and
`custom_field_scopes` (a charge takes custom fields like every other document) — and
`document_lines_type_insert` rewritten to pair `statement_charge` with the `sale` envelope.
`document_lines` itself is not rebuilt: a charge's line is a `sale` envelope, which the stored
kind CHECK already admits. No table is created, nothing is backfilled and no settlement row,
trigger or capacity changes.

**What this does not do.** No `statement-charge update` and no `history`: a wrong charge is voided
and re-entered, which is what a sixty-dollar document is worth. No browser page and no `ui_group`,
so nothing registers a tile that goes nowhere; the window it wants is the customer register, and
`registers.py` would need to read a customer's Accounts Receivable rows the way it reads a bank
account's. No demo seed extension. No finance charges — a different anchor feature that computes
its own amounts from an aging.

And, the real one: **a payment cannot settle a charge.** Doing it needs, in one increment:
`applications_paid_transaction_id_type` and `payment_selection_items_invoice_id_type` widened to
admit `statement_charge` (a migration, since both are triggers);
`payment_queries.invoice_facts` and the three `sales.resolve(..., 'invoice')` calls in
`payment_selection` widened to either type; `payment_preparation._candidate_query`'s
`t.type='invoice'` condition widened, which is what makes a charge appear in `payment invoices`;
`settlement_line_keys_transaction_id_type` widened, since the per-line settlement keys an
invoice writes are what `application_allocations_owned_sources` verifies; `payment_recovery`'s
`ck invoice_type` CHECK and its `invoice_type='invoice'` writes; `payment_dependencies`'s
`owner_type` pair; and `receivable_reports._OPEN`, whose `document_type='invoice'` filter is why
`report open-invoices` cannot list one. The storage under all of it already fits — `applications`
and `payment_selection_items` both foreign-key `transactions.id` with no type column, and a charge
already writes the `sales_profiles` row `applications_exact_party` joins — so this is a widening
of nine type filters and four triggers, not a data-model change. What it is not is small, and
half-doing it is how a settled charge disappears from an aging that still balances.
## Purchase orders, and the bill entered from one

`purchase-order post/show/query/update/void/history` records what was ordered from a vendor.
`company/purchase_order_models.py` holds the inputs and outputs,
`company/purchase_order_facts.py` what a revision captures, `company/purchase_orders.py` the
resolution, reads, lifecycle and conversion, `company/purchase_order_validation.py` an
independent check of the aggregate, and `company/purchase_order_schema.py` the storage
(`co0035`, five new tables, nothing existing rebuilt).

**It posts nothing, structurally.** A purchase order is not a `transactions` row. It has no
`posting_batches`, no `posting_lines` and no `ap_obligation_keys`, so the trial balance cannot
move because an order was written — there is no code path that could move it, rather than a rule
somebody has to keep. `purchase_order_validation` asserts the absence directly, and
`tests/test_purchase_order.py` reads the trial balance through `report trial-balance` before and
after and compares the rows and totals verbatim. The two metadata fields that always differ
between any two report calls straddling a write — `generation_time` and `audit_watermark` — are
cut before the comparison; everything else is the accounting.

**Its own family, not the estimate's.** `work_documents` is the other non-posting document with
lines, and its shape is copied here: stable document, immutable whole revisions, stable line
identities across revisions, a permanent conversion link. Its *content* is not, because every
column of `work_revisions` and `work_lines` is sell-side — a customer that must exist, an item
that must exist, a price level, a pricing basis, quoted tax, a billing root a later invoice
consumes in fractions. Reusing those tables would mean making `customer_id` and `item_id`
nullable for every estimate already written, and teaching every customer-work read to filter a
kind out; those reads are the quotes list a bookkeeper looks at, and one missed filter is a
purchase order appearing in it. What is reused is the machinery that is genuinely shared:
`document_effects`, `versioning`, `audit`, the `query` cursor, `sales_calculations.extension`,
and `bills.py`'s own account, terms, class and party resolution rules.

**One grid, two kinds of row.** `purchase_order_lines` holds item lines and expense-account lines
in one ordered table, exactly one of `item_id` and `account_id` per row, because the order's two
tabs are one grid: line 3 is line 3 whichever tab it was typed on. `quantity` and `rate` are
stored together or not at all, and the amount is `sales_calculations.extension(quantity, rate)` —
the same half-even rounding a sale's line extension uses. A stated `amount` that contradicts the
product is refused rather than silently replaced. An item line captures the item's own purchase
destination account (`expense_account_id`, else `cogs_account_id`, else `asset_account_id`), which
is what the bill line later debits.

**Where it stands, and closing.** `open`, `partly_received`, `closed`, `voided`. The first three
move through `purchase-order update`, which appends a new immutable revision like any other
correction, because closing is reversible and this codebase already puts reversible state changes
on `update` — `estimate update` is where `accepted` and `declined` live. There is deliberately no
`close` verb: a second door onto one of three values would be the only place in the product where
a status has two. Closing is a person's decision because item receipts do not exist; nothing
derives it. `void` is the exception, exactly as it is for an estimate: it needs a reason, it is
terminal, and after it the order can never become a bill. An order posts nothing, so voiding one
reverses nothing.

**Becoming a bill.** `bill post` takes an optional `purchase_order`. `bills._from_order` resolves
it, refuses a voided or already-consumed one, and merges the order's vendor, terms, class, memo
and lines into a copy of the caller's input — anything the caller supplied wins, so a delivery
that arrived short is entered by supplying `expenses` and letting the rest carry. The vendor is
the one field that may not be overridden: a bill owed to somebody else is not this order's bill,
and nothing downstream would notice, because the conversion row records which order became which
bill and not who either was owed to. An already-consumed order refuses with `E_WORK_DEPENDENCY`,
the estimate's own code, rather than `E_HAS_APPLICATIONS`, whose standing message tells the
caller to unapply a settlement that does not exist. The copy is
local to `prepare`; `plan.data['input']` stays the caller's own words, so `apply` re-derives from
the order inside the writing transaction rather than trusting what the preview read.
`purchase_orders.consume` then builds the order's side of the same write — a closing revision
carrying the lines forward, and one `purchase_order_conversions` row — and
`document_effects.persist` gained a `companion` parameter that joins those rows to the bill's own
audit event and writes them after the bill's. One command, one event, both documents in it.
`purchase_order_conversions` is UNIQUE on both `source_document_id` and
`destination_transaction_id`, so an order becomes at most one bill and a bill comes from at most
one order; "already billed" is a fact in storage, not a status that could be edited around.
`bill show` and `bill query` report it as `purchase_order_id`.

**The consumption is permanent, including through a void.** Voiding the bill does not free the
order: the conversion row stands and `UNIQUE(source_document_id)` refuses a second one, so the
replacement bill is entered outright and carries no `purchase_order_id`. That is deliberately
stronger than the estimate's rule, where an allocation is released when the sale it sits on is
voided, and it is the cost of making "already billed" a storage fact rather than a query. If
partial or repeated conversion is ever wanted, this is the constraint to revisit first: drop
`UNIQUE(source_document_id)` and gate on the destination bill's status instead.

**What this does not do.** Item receipts, inventory valuation and purchase tax are the inventory
owner's and none of it exists, so an order for an inventory part whose destination is Inventory
Asset cannot be billed — `bills._posting_accounts_active` refuses a system-role account, which is
the right refusal from the wrong owner. There is no hand-built browser page: the home-board tile
stays grey waiting on one, and the *generated* workbench pages answer (the list page renders and
shows the order) but land under "Hub" in the all-commands grid, because `pages._grouped_nouns`
falls through to Hub for any noun without a `ui_group` and a `ui_group` with no page behind it
would be a claim this cannot make. `vendor-credit` sits in exactly that seat already. Also not
done: the demo seed, custom fields on an order (the snapshot column is there and empty, as a
vendor credit's is), partial or repeated conversion, purchase discounts, a closing-date gate
(nothing posts, so no period can be closed against it), and `report open-purchase-orders`.
