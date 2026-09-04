# Row 3 — plan

## What this row adds

`bookflow serve`: the HTTP host that exposes every registered command over loopback with the same inputs, outputs, and errors as the CLI, browser login and a company picker, generated workbench pages for every noun and verb, the server-sent event feed, and the local hand-off by which a CLI or library call on the host's machine runs through the host instead of waiting on the lock.

## One core entry point

`core/dispatch.py` gains `execute(cmd, raw_input, ctx, s, *, company_selector, company_source, dry_run)`: input validation (`E_VALIDATION`, `E_CONTEXT_IN_INPUT`), context validation, the dry-run-on-read and selector-on-hub-command usage checks, `run_in_session`, and the error boundary (path redaction for non-hub-admins, OS and SQLite failures to `E_IO`). `run()` becomes: resolve the data root, take the lock, open the hub, load the actor, migrate, then `execute`. The host and `demo reset` call `execute`; no adapter re-implements any of it (blueprint 2).

Registry additions this row: `local_only` (commands that act on the calling process or its OS login and are never served over HTTP: `init`, `serve`, `company use`); `version_source` on update commands, the show command and output field path that supply `expected_version` (`company update` → `company show`, `info_version`); and per noun, in `NOUN_META`, the record type its records are audited under and the positional that identifies a record (`company` → `company_info`, the selected company; `directive` → `directive`, positional `directive`; `audit` → event id, positional `event`). Pages and forms read only these.

## Modules

```
src/bookflow/
  core/dispatch.py           + execute(); run() reshaped around it
  core/forward.py            standard library only: if root.lock is held by a live host, send the call over the host's Unix socket and return its result; used by run() before taking the lock
  core/host.py               Host: holds the data-root lock and a long-lived writable connection per database on one writer thread; reader connections per request; passive checkpoints on a schedule and a truncating checkpoint at shutdown; commit notifications for the event stream
  adapters/http/app.py       FastAPI application factory; routes generated from the registry; OpenAPI from the registry
  adapters/http/auth.py      session cookies and bearer tokens -> actor
  adapters/http/routes.py    command routes, login/logout, events, openapi, health
  adapters/http/local.py     the Unix-socket listener for forwarded calls; peer credentials -> OS login
  adapters/workbench/        pages.py, forms.py, tables.py, templates/, static/ (htmx.min.js vendored; one stylesheet)
  commands/hub_cmds.py       + serve (local_only; hub admin), user set-password, token issue/list/revoke (bearer tokens for human users; agent tokens are row 7)
```

`user add` and `membership grant/revoke` stay in row 7. Row 3's target text names `user set-password`, session cookies, and bearer tokens; that is what this row builds.

## The host process

`bookflow serve --bind 127.0.0.1:8123 [--allow-network] [--secure-cookies]`, run by a hub admin (`E_PERMISSION` otherwise), sets umask `077` for its lifetime, takes the data-root lock, writes `command=serve`, its pid, bind address, socket path, and package version into `root.lock`, migrates the hub at startup with itself as the triggering actor, and serves until interrupted. Companies migrate on their first writable open exactly as dispatch does today, attributed to the request's actor.

- **One writer thread** per host owns a long-lived writable connection per database (hub and each company it has written), opened on that thread; every write request is queued to it and runs there through `execute`. All writes serialize; the concurrency the host offers is reads, which is what SQLite gives.
- **Readers** open their own read-only connections per request on worker threads (`check_same_thread` holds because each connection lives and dies on one thread); nothing serializes reads. Reads never see a writable hub, so a read never completes a pending move.
- **Checkpoints**: the writer runs `PRAGMA wal_checkpoint(PASSIVE)` after every write and a `TRUNCATE` checkpoint at graceful shutdown; `Database.close()`'s truncating checkpoint stays for one-shot CLI opens.
- **Graceful shutdown**: stop accepting requests, finish queued writes, clear the host's own presence rows, checkpoint, release the lock, remove the socket.
- **Reader pool**: keyed by company id, re-resolving the folder from the hub row on every open; `detach` and `demo reset` evict.
- **Per-request Session**: the host builds a new `Session` for every request (hub connection, actor, memberships, config, `os_login` when known, `dry_run`) and shares none across threads.

## Local hand-off

`core/forward.py` runs inside `run()` before the lock: try the non-blocking lock first; only if that fails read the holder; forward only when the holder's `command` is `serve`, its pid is not this process, and its socket answers; otherwise proceed to the normal wait and `E_DB_BUSY`. `init` and `serve` are never forwarded. The call travels as one JSON envelope (`command`, `input`, `company_selector`, `company_source`, `dry_run`, and the caller's full `Context`) over the host's Unix domain socket at `<data_root>/host.sock` (mode `0600`, so only the data-root owner's processes can connect); the host takes the peer's uid from `SO_PEERCRED`, maps it to the OS login, and runs the call with the caller's own context, so a forwarded CLI call is recorded with interface `cli`, its own `session_id`, `client_version`, and hostname, and `company use` writes the forwarder's login table. On Windows the socket is a named pipe with the same envelope and the pipe's client identity. The host and the forwarder compare package versions; a mismatch is `E_VERSION_MISMATCH` naming both. Loopback TCP carries no identity; there is no login header.

## Authentication

- `POST /login` with `username` and `password` (argon2id; `user set-password` sets one: a user always sets their own, a hub admin may set anyone's, and the CLI prompts on stderr when the value is omitted) returns a session cookie: `HttpOnly`, `SameSite=Lax`, `Secure` when `--secure-cookies`, holding a token row of kind `session`. Every login failure, unknown user, wrong password, or no password, is `E_LOGIN_FAILED` (401) after a 500 ms delay. Login and logout are audited hub events; token hashes are masked in snapshots.
- Session liveness: `api_tokens.last_used_at` and `expires_at` (12 hours after last use) are ephemeral columns, exempt from versioning and audit like presence, refreshed on the writer at most every five minutes, never on the read path.
- `Authorization: Bearer <secret>` resolves a `bearer` token issued with `token issue --for <user> --label <label>` (shown once, hashed at rest, revocable, expiring). In this row bearer tokens bind human users only; `client_name` is the token's label when no header is given, and agent tokens with a principal arrive in row 7.
- Cookie-authenticated command routes require the header `X-Bookflow-Workbench: 1`, which the workbench sends on every request and which a cross-origin form cannot; bearer requests are exempt.
- `/login`, `/openapi.json`, and `/health` are open; `/health` returns only the host's pid, bind, version, and the code's head revisions.

## Routes

`POST /commands/{noun}/{verb}` for hub scope and `POST /companies/{selector}/commands/{noun}/{verb}` for company scope; `{noun}` may itself contain a slash for two-word nouns (`hub/audit`), verbs keep their dashes (`set-password`). `{selector}` is a company id, or a display name, or `organization~company` (the tilde replaces the slash so the path stays one segment). The body is the raw JSON input, passed to `execute` untouched, so context keys in the body are `E_CONTEXT_IN_INPUT` and unknown commands are the `E_USAGE` document, never a framework 404 or 422. `?dry_run=true` is the dry run and `E_USAGE` on a read. Context comes from the headers of blueprint 5.2. `local_only` commands are not routed.

Status codes, each with the same `{code, message, details}` document the CLI prints: 200 success; 401 unauthenticated and `E_LOGIN_FAILED`; 403 `E_PERMISSION`; 404 `E_COMPANY_NOT_FOUND`, `E_ORGANIZATION_NOT_FOUND`, `E_EVENT_NOT_FOUND`, `E_DIRECTIVE_NOT_FOUND`, `E_RECORD_NOT_FOUND`; 409 `E_VERSION_CONFLICT`, `E_IDEMPOTENCY_MISMATCH`, `E_NAME_TAKEN`, `E_DB_BUSY`; 422 `E_VALIDATION`; 400 every other named error; 500 `E_INTERNAL`. The additions to blueprint 15.2's table exist because an HTTP client that never reads the body should still be able to tell not-found, conflict, and invalid input apart; blueprint 15.2 is amended to this table.

`GET /openapi.json` is generated from the registry: one operation per routed command with the input and output schemas, the context headers as parameters, the selector forms, `dry_run`, the error document schema, and each operation's error codes. `GET /companies/{selector}/events?after=<seq>&...filters` streams `audit tail` as server-sent events with `id: <seq>`; the host's writer signals a per-database condition after every commit, so subscribers wake on commit rather than polling; a burst larger than `limit` is drained in a loop; `Last-Event-ID` wins over `after`; a keep-alive comment every 15 seconds; token revocation ends the stream. `GET /companies/{selector}/hub-events` does the same for the hub log with the same visibility as `hub audit tail`.

## The workbench

Server-rendered HTML with HTMX; no build step; one stylesheet. Workbench requests are recorded with interface `http` and `client_name` `bookflow-workbench` (blueprint 4.4 reserves `gui` for the product client). The URL is the working company; nothing is stored in the session beyond the token.

Pages, all generated from the registry and `NOUN_META`:

- `/login`, `/logout`; `/`: the company picker from `company list`.
- `/c/{company}/`: nouns with company-scope commands; `/hub/`: hub nouns (`organization`, `company`, `demo`, `upgrade`, `user`, `token`, `hub audit`), each command a page, so every registered command that is not `local_only` has one.
- `/c/{company}/{noun}`: the `list` output as a table with the CLI's preferred columns and an "include inactive" toggle where the input has it; each row links to the record page by the noun's identifying positional.
- `/c/{company}/{noun}/{id}`: the `show` output as a field view, `editing_by` at the top, the record's audit events below (`audit list --record-type <NOUN_META record type> --record-id`), a button per verb the actor's role permits, an HTMX heartbeat calling `presence set` every 30 seconds while the page is open and `presence clear` on leave, skipped for members below `standard`.
- `/c/{company}/{noun}/{verb}`: a form generated from the input model. The workbench's own form route translates form encoding into the command's JSON by one generic rule: a leaf is sent only when its value is non-blank, or when its "clear" checkbox is ticked, in which case `null` is sent; nested names are dotted; checkboxes are booleans; the model types numbers; `reason`, `source_ref`, `directive`, and `idempotency_key` fields become the context headers; an update form reached from a record page carries `expected_version` from the field `version_source` names on the show it rendered, and an update form reached any other way fetches that show first, so the workbench never blind-writes. Submit runs the command through `execute` and renders the output as a field view or the error document with its code and fields.
- `/c/{company}/audit` and `/hub/audit`: `audit list` and `hub audit list` with their filters and `next_before` paging, visibility exactly as the commands give it; a row opens `show`.

## Demo

The seed is unchanged: users and passwords are hub state that row 7 owns, and the README's run command gains `bookflow user set-password` so the demo owner can log in. Section 9.3 records the exemption.

## Error matrix additions

`E_LOGIN_FAILED` (401), `E_TOKEN_EXPIRED` (401), `E_VERSION_MISMATCH` (forwarder and host differ), `E_HOST_UNREACHABLE` (a live `serve` holder whose socket does not answer; the message says to stop that host, never to remove the lock), `E_NETWORK_NOT_ALLOWED` (`serve --bind` outside loopback without `--allow-network`). `serve` returns `E_DB_BUSY` when another process holds the lock.

## Edges

- Does not touch: lists, ledger, attachments, agent tokens bound to principals, `user add`, `membership grant`, MCP.
- `core/forward.py` and `adapters/http/local.py` use only the standard library on the client side; `adapters/http/app.py` imports FastAPI only when `serve` runs, so CLI cold start is unaffected.
- Uvicorn access logs are off; the host logs command names and request ids, never URLs with names or bodies.
- IPv6 loopback forms (`::1`, `::ffff:127.0.0.1`) count as loopback for the bind check.

## Verification the builder will perform

Tests: every routed command called through the library, the CLI, and HTTP with the same inputs and compared outputs and errors including status codes; context keys in a JSON body → `E_CONTEXT_IN_INPUT`; unknown command → `E_USAGE` document; isolation over HTTP and the workbench (a member of A requesting B by id, name, or `organization~company` gets 404 with a body identical to a nonexistent id, and no path in any error); a forwarded CLI call recorded with interface `cli`, its own `session_id`, `client_version`, and hostname, and a forwarded `company use` writing the caller's login table; a process of another uid refused on the socket; `serve` and `init` never forwarded; a CSRF-shaped POST without the workbench header refused; two readers not blocked by a long write; writes serialized; a session refresh not written on reads and not audited; login and logout audited; the SSE stream delivering an event on commit, draining a burst, resuming from `Last-Event-ID`, and closing on revocation; every routed command's page rendering a form with one control per input leaf, an update form carrying `expected_version`, and a submit with blank fields changing nothing; the version-mismatch error; WAL size bounded after N writes with readers attached; a startup migration attributed to the serve actor; `--allow-network` gating. By hand, and this is the human's taste gate: open the workbench in a browser, log in, pick the demo company, edit company information from two browsers to see the conflict and the warning, add a directive, watch presence, and read the audit page.
