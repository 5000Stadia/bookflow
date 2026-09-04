# Row 3 — plan

## What this row adds

`bookflow serve`: the HTTP host that exposes every routed command over loopback with the same inputs, outputs, and errors as the CLI, browser login landing in a company (the picker only when the company is ambiguous), generated workbench pages for every routed noun and verb, the server-sent event feed, and the local hand-off by which a CLI or library call on the host's machine runs through the host instead of waiting on the lock.

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
  adapters/http/local.py     the socket listener for forwarded calls: peer credentials -> OS login
  adapters/workbench/        pages.py, forms.py, templates/, static/ (htmx.min.js vendored; one stylesheet)
  commands/host_cmds.py      serve (local_only, bootstrap-style like init), user set-password, token issue/list/revoke
  storage/hub_migrations/    hub0003: membership capability overrides, frozen role capability defaults, inert feature flags
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
- **Commit signal**: after a commit that advanced `audit_events.seq` for a database, the writer records the canonical database key and new seq, then wakes each subscribed `asyncio.Event` through its owning loop's thread-safe callback; advisory writes and refreshes signal nothing.
- **Shutdown**: Bookflow's SIGINT/SIGTERM handlers mark the host as stopping and wake every stream before Uvicorn waits for active requests. Final cleanup stops the local listener, closes reader connections, drains the writer queue, clears `http` presence rows in companies the writer has open, runs a `TRUNCATE` checkpoint, removes `host.json` and the socket, and releases the lock.
- Session token rows expired more than a day are swept hourly by the writer as a system event.

## Local hand-off

`core/forward.py`, inside `run()` before the lock: read `host.json`; if present and its socket answers, send the call; if the socket refuses, treat it as no host and take the ordinary lock path (`E_DB_BUSY` after the timeout). `init` and `serve` are never forwarded; a forwarder inside the host process (pid match) never forwards. The Unix socket lives at `$XDG_RUNTIME_DIR/bookflow/<sha256 of the data root, 16 hex>.sock`, else `<tmpdir>/bookflow-<uid>/<hash>.sock`. The runtime directory must be owned by the current uid and mode `0700`; the socket is mode `0600`. A stale socket is unlinked before bind. The envelope is one length-prefixed JSON object: `command`, `input`, `company_selector`, `company_source`, `dry_run`, and the caller's `Context`; exact-length reads accept fragmented frames. The host discards the envelope's `actor_id`, `actor_kind`, `on_behalf_of`, and `company_id`, forces interface `cli`, and rebuilds identity from the peer's uid (`SO_PEERCRED` on Linux; `getpeereid` on macOS) mapped through `config.toml`. Other OS users use HTTP with tokens. The host and forwarder compare package versions before opening the socket; a mismatch is `E_VERSION_MISMATCH` whose message says to stop the host or upgrade the CLI. Windows local hand-off is not implemented in this row.

## Authentication

- `POST /login` with `username` and `password`: argon2id, verified against a fixed dummy hash when the user has none so timing does not enumerate users; at most five concurrent attempts per source and a 500 ms delay on failure; every failure is `E_LOGIN_FAILED` (401). Success sets the cookie: `HttpOnly`, `SameSite=Lax`, `Secure` by default on non-loopback binds and off on loopback, overridden by `--secure-cookies`. Login and logout are audited hub events; token hashes are masked.
- Every cookie-authenticated non-GET route, API and page alike, requires the header `X-Bookflow-Workbench: 1`; the workbench sends it on every request through HTMX (`hx-headers` on the body), so plain non-JavaScript form submission is unsupported and the page says so. A cookie without the header is `E_WORKBENCH_HEADER` (403).
- `Authorization: Bearer <secret>` resolves a `bearer` token issued with `token issue --user <user> --label <label>` (shown once, hashed at rest, revocable, optionally expiring). Only a human actor may issue a token. A human may issue for itself; a hub admin may issue for any user. An agent target requires `--principal <human>`, fixed into that token, and only a hub admin may issue it in this row. An agent credential cannot issue another token. A non-admin naming any other target gets the same capability-bearing `E_PERMISSION` whether or not it exists. `token list` omits revoked and expired tokens unless `include_revoked` is true.
- `user set-password` is routed and available from the workbench. A human may set its own password and a hub admin may reset any human password; agent actors are refused. Non-admin requests for another or unknown username return the same `E_PERMISSION`. A successful change revokes every other browser session for the target while preserving the initiating session and all bearer tokens. Row 3 has no current-password challenge because this command cannot target another user without hub-admin authority.
- `session_id` is the token id for cookie and bearer requests alike. Session liveness: reads count as activity; a stale read enqueues at most one non-blocking, five-minute-throttled, unversioned, unaudited refresh to the writer. Session `expires_at` is 12 hours after last use, and the browser cookie is renewed only with that throttled refresh, never on an SSE response. Logout with an expired or revoked cookie still clears it and still requires the workbench header.
- No credential, an unknown, expired, or revoked token: `E_UNAUTHENTICATED` (401, infrastructure code) with `details.reason`. `/login`, `/openapi.json`, and `/health` are open; `/health` returns liveness only.

## Routes

`POST /commands/{name}` for hub scope and `POST /companies/{company_id}/commands/{name}` for company scope, where `{name}` is the command name with spaces replaced by dots (`company.update`, `user.set-password`, `hub.audit.list`), which round-trips because names never contain dots. A company path contains its ULID and is authoritative. If `X-Bookflow-Company` is also present it must be the same ULID after case normalization; a mismatch is `E_VALIDATION` before any company or visibility lookup. The header-only form on `/commands/{name}` retains the ordinary selector rules for a company-scoped command. The body is the raw JSON input passed to `execute` untouched: context keys in the body are `E_CONTEXT_IN_INPUT` naming the header for each key; unknown names are the `E_USAGE` document; `local_only` names are `E_USAGE` saying so. `?dry_run=true` is the dry run for writing commands. Context headers: `X-Bookflow-Reason`, `X-Bookflow-Directive`, `X-Bookflow-Source-Ref`, `Idempotency-Key`, `X-Bookflow-Client-Name`, `X-Bookflow-Client-Version`.

Status codes, each with the `{code, message, details}` document: 200; 401 `E_UNAUTHENTICATED`, `E_LOGIN_FAILED`; 403 `E_PERMISSION`, `E_WORKBENCH_HEADER`; 404 the not-found codes; 409 `E_VERSION_CONFLICT`, `E_IDEMPOTENCY_MISMATCH`, `E_NAME_TAKEN`, `E_DB_BUSY`; 422 `E_VALIDATION`; 400 every other named error; 500 `E_INTERNAL` with a generic message and `details.request_id`, the traceback logged by that id.

`GET /openapi.json`: exactly one operation per routed command with input and output schemas, authentication, the context headers, the authoritative company path/header contract, write-only `dry_run`, the error document schema, and the operation's complete error codes. `GET /companies/{company_id}/events?after=<seq>&...filters` and `GET /hub-events?...` stream `audit tail` and `hub audit tail` as server-sent events with `id: <seq>`. Cursor input is validated before streaming and `Last-Event-ID` wins over `after`. The async generator uses no worker while idle; each drain is one worker call that opens, uses, and closes its reader on that worker, drains until a batch is short, and subscribes under the canonical company id. Sequence comparison plus the event-loop notification closes the drain/wait race. A keep-alive comment is sent every 15 seconds. Revocation, expiry, company loss, disconnect, and shutdown close the stream and release its subscription; a final `error` event is sent when the connection still permits it.

## The workbench

Server-rendered HTML with HTMX; no build step; one stylesheet. Workbench requests are recorded with interface `http` and `client_name` `bookflow-workbench`. Page URLs carry company ids; nothing but the token is stored in the session.

- `/login` (with `next`, a path on this host, so a deep link returns to where it was going), `/logout`; `/`: lands in a company without a click: the only company the user can see, else the company this browser used last (a per-browser cookie set on every company index visit, carrying no identity), else the picker; `/companies`: the picker from `company list`, showing a company's schema state and linking to `upgrade` when it is behind.
- `/c/{company_id}/`: nouns with company-scope commands; `/hub/`: hub nouns; every routed command has a page.
- `/c/{company_id}/{noun}`: the `list` output as a table with the shared preferred columns and an "include inactive" toggle where the input has it; each row links to the record page by `NOUN_META`'s identifier.
- `/c/{company_id}/{noun}/{id}`: the `show` output as a field view, `editing_by` at the top, the record's audit events below, a button per verb the actor's role permits, an HTMX heartbeat calling `presence set` every 30 seconds and `presence clear` on leave, rendered only for record types the presence input model accepts and for members of role `standard` or above.
- `/c/{company_id}/{noun}/{verb}` and, for update verbs, `/c/{company_id}/{noun}/{id}/{verb}`: a form generated from the input model after the company and role are authorized. Update forms exist only with a record id in the URL and are linked only from record pages. Fields are prefilled from the show; each carries a hidden original; a leaf is sent only when it differs from its original, while a checked clear box always sends `null`; nested names are dotted; booleans are tri-state controls mapping unset, true, false to absent, true, false; the model types numbers; context fields are rendered only where the registry flags allow them and become headers; `expected_version` is a visible, prefilled, clearable input from `version_source`; secret fields render as password inputs. Preview runs the dry run and renders in place. Submit uses POST-redirect-GET: a process-local opaque flash id, bound to the session token, holds the output for one GET or 60 seconds. The result is never placed in the URL or cookie, and the result GET is `no-store`.
- `/c/{company_id}/audit` and `/hub/audit`: the list commands with their filters and `next_before` paging, visibility exactly as the commands give it; a row opens `show`.

## Demo

The seed is unchanged; the README's run command gains `bookflow user set-password` so the demo owner can log in. Section 9.3 records that users and passwords are hub state.

## Error matrix additions

`E_UNAUTHENTICATED` (infrastructure, 401), `E_LOGIN_FAILED` (401), `E_WORKBENCH_HEADER` (403), `E_VERSION_MISMATCH`, `E_NETWORK_NOT_ALLOWED` (`serve --bind` outside loopback without `--allow-network`). `serve` returns `E_DB_BUSY` when another process holds the lock.

## Edges

- Does not touch: lists, ledger, attachments, `user add`, `membership grant`, assigned agent-principal sets, MCP.
- Hub revision `hub0003` adds nullable JSON-text `grants` and `denies` on memberships, the frozen `role_capabilities(role, capability, required_role)` seed, and `features(scope_type, scope_id, feature, enabled, enabled_by, enabled_at, source)`. These compatibility fields are inert until row 7; the company schema remains `co0002`.
- All writes serialize on the writer thread; the target's "writes queued per database" is built as one queue because nearly every company write also writes the hub, and the intention row is amended to say so.
- The CLI's cold start is unaffected: nothing under `adapters/http` or `adapters/workbench` is imported unless `serve` runs, and `core/forward.py` is standard-library only.
- Uvicorn access logs are off; the host logs command names and request ids, never URLs with names or bodies.

## Verification the builder will perform

Automated verification covers library/CLI/HTTP parity; exact generated OpenAPI coverage; status and error documents; context isolation; authoritative company-path validation and non-enumeration; CSRF; human/admin password policy and session revocation; agent issuance refusal; token expiry; non-blocking deduplicated liveness and cookie renewal; fragmented local frames, peer identity, forced CLI interface, runtime-directory ownership, and pre-socket version mismatch; concurrent readers and serialized writers; async stream cursor validation, canonical wakeups, the drain/subscribe race, disconnect cleanup, more than 40 idle subscribers, revocation, and real-process SIGINT cleanup; role-filtered workbench pages, clear/unchanged form translation, one-use POST-redirect-GET results, audit filters, picker schema state, and every rendered link; migration backup and attribution; direct checkpoint and recursive-redaction witnesses; WAL bounds and session sweeping. The remaining human taste gate opens the workbench in two browsers, exercises a conflicting company edit, adds a directive, observes presence, and reads the audit page.
