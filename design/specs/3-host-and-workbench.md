# Row 3 — plan

## What this row adds

`bookflow serve`: the HTTP host that exposes every routed command over loopback with the same inputs, outputs, and errors as the CLI, browser login and a company picker, generated workbench pages for every routed noun and verb, the server-sent event feed, and the local hand-off by which a CLI or library call on the host's machine runs through the host instead of waiting on the lock.

## One core entry point, one boundary

`core/dispatch.py` gains `execute(cmd, raw_input, ctx, s, *, company_selector, company_source, dry_run)`: input validation, context validation, the dry-run-on-read and selector-on-hub-command usage checks, hub-admin completion of pending organization moves (writer only), `run_in_session`, and the error boundary. The boundary is one function, `guard(fn, allowed)`, that translates OS and SQLite failures to `E_IO` and redacts paths; it wraps everything `run()` does before an actor exists with `allowed=False`, wraps `execute`, and wraps the host's own session building, so no failure leaves core untranslated or unredacted. `run()` becomes: resolve the data root, try the hand-off, take the lock, open the hub, load the actor, migrate, `execute`. The host and `demo reset` call `execute`.

Registry additions: `local_only` (`init`, `serve`, `company use`: never routed; an HTTP call to one returns `E_USAGE` saying it is local only); `version_source` on update commands naming the show command, its identifying positional, and the output field that supplies `expected_version` (`company update` → `company show`, none, `info_version`); `secret` on input fields (password inputs, CLI prompts, docs flag); `NOUN_META` derived from the registry, record type defaulting to the noun and the identifier to the show command's first positional, with one override each for `company` (record type `company_info`; the identifier is the selected company) and `audit` (`event`), and a registry test that fails when a noun with `show` or `list` resolves to no record type or identifier. The preferred-column list moves from the CLI renderer to `core/models.py` so every adapter reads one table.

## Modules

```
src/bookflow/
  core/dispatch.py           + execute(), guard(); run() reshaped
  core/forward.py            standard library only: hand a call to a live host over its socket; used by run() before the lock
  core/host.py               Host: lock holder, one writer thread with long-lived writable connections, per-request readers, checkpoints, shutdown, commit signals, connection release hooks
  adapters/http/app.py       FastAPI factory; routes generated from the registry; OpenAPI from the registry; imported only by serve
  adapters/http/auth.py      session cookies and bearer tokens -> user id; login rate limit
  adapters/http/routes.py    command routes, login/logout, events, hub-events, openapi, health
  adapters/http/local.py     the socket listener for forwarded calls: peer credentials -> OS login
  adapters/workbench/        pages.py, forms.py, tables.py, templates/, static/ (htmx.min.js vendored; one stylesheet)
  commands/hub_cmds.py       + serve (local_only, bootstrap-style like init), user set-password, token issue/list/revoke
  storage/hub_migrations/    hub0003: api_tokens gains last_used_at throttling columns are already present; adds `host_sessions` nothing; (no schema change expected: see Authentication)
```

`user add` and `membership grant/revoke` stay in row 7.

## The host process

`bookflow serve --bind 127.0.0.1:8123 [--allow-network] [--secure-cookies/--no-secure-cookies]` is a bootstrap-style command with its own path (like `init`): it declares no writes, defines no `--dry-run`, requires a hub admin, sets umask `077` for its lifetime, takes `RootLock(root, "serve")`, opens the hub writable on the writer thread, migrates the hub and then every registered company (`upgrade` semantics, attributed to the serve user), starts the socket listener, writes `<data_root>/host.json` (pid, bind, socket path, package version) only after the socket listens and the HTTP server is bound, and serves until interrupted, exiting 0.

Connections and threads:

- **The writer thread** owns the writable hub connection and a long-lived writable connection per company it has written, all opened on that thread. Every write and advisory request is enqueued to it. A request thread only authenticates (credential or peer login to a user id) and enqueues; the writer builds the request's `Session` on its own connections, resolves actor, memberships, company row, and role immediately before `plan`, and runs `execute`. After every `execute` the writer asserts no transaction is open, rolls back if one is, and discards a connection that raised an unusable-state error.
- **Readers** run on request threads with connections opened for that request and closed after it: read-only hub and read-only company. Dry runs are reads. No reader connection outlives a request and none holds a transaction between requests. `check_local` results are cached per path for the host's lifetime.
- `open_company` on the writer's pooled connection re-reads `company_info` (zone, version) on every request, migrates only on the first open, and never closes the pooled connection; `Session.close_company` releases the Session's reference. Commands that open a company database themselves (`upgrade`, `attach`) run on the writer, and the writer drops its cached connection to that company afterwards.
- **Moves and trash**: before any command moves or trashes a folder (`rename --move`, organization moves, pending-move completion, `detach`, `demo reset`), the writer closes every connection it holds to the affected companies, and to every company under an organization being moved, and reopens by re-resolving the hub row.
- **Checkpoints**: the writer runs a `PASSIVE` checkpoint after each write and a `RESTART` checkpoint on an idle timer whenever no reader is attached; results are logged, never swallowed.
- **Commit signal**: after a commit that advanced `audit_events.seq` for a database, the writer signals subscribers with the new seq; advisory writes and refreshes signal nothing.
- **Shutdown**: stop accepting; end streams; close reader connections; drain the writer queue; clear `http` presence rows in companies the writer has open; `TRUNCATE` checkpoint with a verified result; remove `host.json` and the socket; release the lock.
- Session token rows expired more than a day are swept hourly by the writer as a system event.

## Local hand-off

`core/forward.py`, inside `run()` before the lock: read `host.json`; if present and its socket answers, send the call; if the socket refuses, treat it as no host and take the ordinary lock path (`E_DB_BUSY` after the timeout). `init` and `serve` are never forwarded; a forwarder inside the host process (pid match) never forwards. The socket lives at a short path outside the data root: `$XDG_RUNTIME_DIR/bookflow/<sha256 of the data root, 16 hex>.sock`, else `<tmpdir>/bookflow-<uid>/<hash>.sock` in a `0700` directory; a stale socket is unlinked before bind. The envelope is one JSON object: `command`, `input`, `company_selector`, `company_source`, `dry_run`, and the caller's `Context`; the host discards the envelope's `actor_id`, `actor_kind`, `on_behalf_of`, and `company_id` and rebuilds them from the peer's uid (`SO_PEERCRED` on Linux, `LOCAL_PEERCRED` on macOS, the pipe client's token on Windows) mapped through `config.toml`, so identity never comes from the sender. Only the data-root owner's processes can connect (`0600` socket in a `0700` directory; an owner-only DACL on the Windows named pipe); other OS users use HTTP with tokens. A forwarded CLI call is recorded with interface `cli`, its own `session_id`, `client_version`, and hostname; a forwarded `company use` writes the caller's login table. The host and forwarder compare package versions; a mismatch is `E_VERSION_MISMATCH` whose message says to stop the host or upgrade the CLI. A test sends a forged `actor_id` and asserts the audit row shows the peer's user.

## Authentication

- `POST /login` with `username` and `password`: argon2id, verified against a fixed dummy hash when the user has none so timing does not enumerate users; at most five concurrent attempts per source and a 500 ms delay on failure; every failure is `E_LOGIN_FAILED` (401). Success sets the cookie: `HttpOnly`, `SameSite=Lax`, `Secure` by default on non-loopback binds and off on loopback, overridden by `--secure-cookies`. Login and logout are audited hub events; token hashes are masked.
- Every cookie-authenticated non-GET route, API and page alike, requires the header `X-Bookflow-Workbench: 1`; the workbench sends it on every request through HTMX (`hx-headers` on the body), so plain non-JavaScript form submission is unsupported and the page says so. A cookie without the header is `E_WORKBENCH_HEADER` (403).
- `Authorization: Bearer <secret>` resolves a `bearer` token issued with `token issue --for <user> --label <label>` (shown once, hashed at rest, revocable, expiring). `token issue`, `token list`, `token revoke`, and `user set-password` act on the caller's own user or, for hub admins, on anyone; a non-admin naming another user gets `E_PERMISSION` whether that user exists or not. Bearer tokens bind human users in this row; `client_name` is `X-Bookflow-Client-Name` or the token label; `client_version` is `X-Bookflow-Client-Version`. Bootstrap: a hub admin issues the first token on the CLI, or a logged-in user issues their own from the workbench's token page.
- `session_id` is the token id for cookie and bearer requests alike. Session liveness: reads count as activity; a read enqueues a throttled (five minutes), unversioned, unaudited `last_used_at` refresh to the writer; `expires_at` is 12 hours after last use.
- No credential, an unknown, expired, or revoked token: `E_UNAUTHENTICATED` (401, infrastructure code) with `details.reason`. `/login`, `/openapi.json`, and `/health` are open; `/health` returns liveness only.

## Routes

`POST /commands/{name}` for hub scope and `POST /companies/{company_id}/commands/{name}` for company scope, where `{name}` is the command name with spaces replaced by dots (`company.update`, `user.set-password`, `hub.audit.list`), which round-trips because names never contain dots; blueprint 15.2 is amended to this form with that reason. Company paths carry the company id; a name or `Organization/Company` selector goes in the header `X-Bookflow-Company`, resolved with the same rules and errors as `--company`. The body is the raw JSON input passed to `execute` untouched: context keys in the body are `E_CONTEXT_IN_INPUT` naming the header for each key; unknown names are the `E_USAGE` document; `local_only` names are `E_USAGE` saying so. `?dry_run=true` is the dry run. Context headers: `X-Bookflow-Reason`, `X-Bookflow-Directive`, `X-Bookflow-Source-Ref`, `Idempotency-Key`, `X-Bookflow-Client-Name`, `X-Bookflow-Client-Version`.

Status codes, each with the `{code, message, details}` document: 200; 401 `E_UNAUTHENTICATED`, `E_LOGIN_FAILED`; 403 `E_PERMISSION`, `E_WORKBENCH_HEADER`; 404 the not-found codes; 409 `E_VERSION_CONFLICT`, `E_IDEMPOTENCY_MISMATCH`, `E_NAME_TAKEN`, `E_DB_BUSY`; 422 `E_VALIDATION`; 400 every other named error; 500 `E_INTERNAL` with a generic message and `details.request_id`, the traceback logged by that id.

`GET /openapi.json`: one operation per routed command with input and output schemas, the context headers as parameters, the company header, `dry_run`, the error document schema, and the operation's error codes. `GET /companies/{company_id}/events?after=<seq>&...filters` and `GET /hub-events?...` stream `audit tail` and `hub audit tail` as server-sent events with `id: <seq>`; each wake-up runs one `execute` on a fresh reader connection and drains until a batch is short; `Last-Event-ID` wins over `after`; a keep-alive comment every 15 seconds; a wake-up whose session cannot be built (revoked token, expired session, detached company, shutdown) ends the stream with a final `error` event carrying the code.

## The workbench

Server-rendered HTML with HTMX; no build step; one stylesheet. Workbench requests are recorded with interface `http` and `client_name` `bookflow-workbench`. Page URLs carry company ids; nothing but the token is stored in the session.

- `/login`, `/logout`; `/`: the company picker from `company list`, showing a company's schema state and linking to `upgrade` when it is behind.
- `/c/{company_id}/`: nouns with company-scope commands; `/hub/`: hub nouns; every routed command has a page.
- `/c/{company_id}/{noun}`: the `list` output as a table with the shared preferred columns and an "include inactive" toggle where the input has it; each row links to the record page by `NOUN_META`'s identifier.
- `/c/{company_id}/{noun}/{id}`: the `show` output as a field view, `editing_by` at the top, the record's audit events below, a button per verb the actor's role permits, an HTMX heartbeat calling `presence set` every 30 seconds and `presence clear` on leave, rendered only for record types the presence input model accepts and for members of role `standard` or above.
- `/c/{company_id}/{noun}/{verb}` and, for update verbs, `/c/{company_id}/{noun}/{id}/{verb}`: a form generated from the input model. Update forms exist only with a record id in the URL and are linked only from record pages. Fields are prefilled from the show; each carries a hidden original; the workbench's form route translates form encoding by one generic rule: a leaf is sent only when it differs from its original, or when its clear box is ticked (`null`); nested names are dotted; booleans are tri-state controls mapping unset, true, false to absent, true, false; the model types numbers; context fields are rendered only where the registry flags allow them and become headers; `expected_version` is a visible, prefilled, clearable input from `version_source`; secret fields render as password inputs. Every write form has a Preview button that runs the dry run and renders the preview marked as such, and a Submit that runs the command and redirects to the record page (POST-redirect-GET).
- `/c/{company_id}/audit` and `/hub/audit`: the list commands with their filters and `next_before` paging, visibility exactly as the commands give it; a row opens `show`.

## Demo

The seed is unchanged; the README's run command gains `bookflow user set-password` so the demo owner can log in. Section 9.3 records that users and passwords are hub state.

## Error matrix additions

`E_UNAUTHENTICATED` (infrastructure, 401), `E_LOGIN_FAILED` (401), `E_WORKBENCH_HEADER` (403), `E_VERSION_MISMATCH`, `E_NETWORK_NOT_ALLOWED` (`serve --bind` outside loopback without `--allow-network`). `serve` returns `E_DB_BUSY` when another process holds the lock.

## Edges

- Does not touch: lists, ledger, attachments, agent tokens bound to principals, `user add`, `membership grant`, MCP.
- All writes serialize on the writer thread; the target's "writes queued per database" is built as one queue because nearly every company write also writes the hub, and the intention row is amended to say so.
- The CLI's cold start is unaffected: nothing under `adapters/http` or `adapters/workbench` is imported unless `serve` runs, and `core/forward.py` is standard-library only.
- Uvicorn access logs are off; the host logs command names and request ids, never URLs with names or bodies.

## Verification the builder will perform

Tests: every routed command through the library, the CLI, and HTTP with the same inputs and compared outputs and errors including status codes; context keys in a body → `E_CONTEXT_IN_INPUT` naming headers; unknown and local-only names → `E_USAGE` documents; isolation over HTTP and the workbench (a member of A requesting B by id or header gets 404 with a body identical to a nonexistent id, no path in any error); a forwarded CLI call recorded with its own context, a forged `actor_id` ignored, a forwarded `company use` writing the caller's login table, another uid refused, `serve` and `init` never forwarded, a refused socket falling back to the lock path; a CSRF-shaped POST to an API route and to a page route without the header refused; two readers not blocked by a long write; writes serialized; a read enqueuing at most one throttled refresh; login and logout audited; the stream delivering on commit, draining a burst, resuming from `Last-Event-ID`, and ending on revocation with an error event; every routed command's page rendering a form with one control per input leaf, an update form carrying `expected_version` and hidden originals, a submit with untouched fields sending nothing, a Preview running the dry run, and a redirect after submit; `E_UNAUTHENTICATED` shapes; the version-mismatch error; WAL bounded after N writes with a reader attached (definition: after the writer's idle checkpoint the WAL is under 4 MB); startup migration attributed to the serve actor; `--allow-network` gating and the cookie `Secure` default. By hand, and this is the human's taste gate: open the workbench in a browser, log in, pick the demo company, edit company information from two browsers to see the conflict, add a directive, watch presence, and read the audit page.
