# Row 3 — plan

## What this row adds

`bookflow serve`: the HTTP host that exposes every registered command over loopback with the same inputs, outputs, and errors as the CLI, a browser login and company picker, generated workbench pages for every list, record, and command, the server-sent event feed, and the local hand-off that lets a CLI or library call on the same machine forward to a running host.

## Modules

```
src/bookflow/
  adapters/http/
    app.py                 FastAPI application factory; routes generated from the registry; OpenAPI from input and output models
    auth.py                bearer tokens and browser session cookies -> Session actor (hub api_tokens with kind session or bearer)
    routes.py              POST /commands/{name} (hub scope), POST /companies/{selector}/commands/{name} (company scope), GET /companies/{selector}/events (SSE), GET /openapi.json, /login, /logout
    errors.py              BookflowError -> status codes per blueprint 15.2; the same JSON error document as the CLI
    forward.py             client side: if root.lock names a live host, send the command to it over loopback and return its result
  adapters/workbench/
    pages.py               routes for /, /login, /c/{company}/, /c/{company}/{noun}, /c/{company}/{noun}/{id}, /c/{company}/{noun}/{verb}, /c/{company}/audit, /c/{company}/activity/{type}/{id}
    forms.py               input model -> HTML form (one input per leaf field, typed, description, choices, defaults, expected_version hidden field from the show it rendered)
    tables.py              list output -> table with the same columns the CLI prefers; record output -> field view with nested sections
    templates/             Jinja2: base, login, picker, index, list, record, form, audit, result, error
    static/                one stylesheet, htmx.min.js (vendored), no build step
  core/host.py             HostRunner: holds the data-root lock for its lifetime, one hub connection, a reader pool per company, one writer queue per database; runs commands through run_in_session on a worker thread
  commands/hub_cmds.py     + serve (hub; long-running), token issue/list/revoke (hub admin; session tokens are issued by /login), user set-password, user add (hub admin), membership grant/revoke (admin or owner on the scope)
```

`serve`, `token *`, `user *`, and `membership *` are row 7 in the intention; this row builds the subset the host cannot run without: `serve`, `user set-password`, session tokens issued by `/login`, and bearer tokens for programs. Row 7 keeps agent tokens bound to a principal, `user add`, and `membership grant/revoke`. The intention rows 3 and 7 are amended to say so in the same change.

## The host

`bookflow serve --bind 127.0.0.1:8123 [--allow-network]` opens the hub, takes the data-root lock, writes its pid and bind address into `root.lock`, and serves until interrupted. Inside the process:

- Reads run concurrently: each request opens its own read-only connections (hub and company) on a worker thread; nothing serializes them.
- Writes queue: one writer lock per database path inside the process; a write request takes the hub writer lock and, for company scope, the company's writer lock, in that order, runs `run_in_session` on a worker thread, and releases. Two writes to different companies run concurrently; two writes to one company serialize.
- Every request builds a `Context` with interface `http` (or `gui` when the request carries the workbench's header), `client_name` from the `User-Agent` or the `X-Bookflow-Client` header, `client_host` from the peer address, `session_id` from the token or cookie, `request_id` fresh, and `reason`, `source_ref`, `directive`, `idempotency_key` from the headers of blueprint 5.2.
- Schema migration happens once at startup, never per request.

Locality: the host refuses to start on a non-local data root like any command. `--allow-network` is required to bind outside loopback; the help and the README say TLS termination is the deployer's job.

## Forwarding

`core/dispatch.run` reads `root.lock` before trying to take it. When the file names a live host (pid alive, bind address present), the command is sent to `POST http://<bind>/commands/...` with the caller's OS login in a loopback-only header the host accepts only from peers on the same machine (`X-Bookflow-Local-Login`, verified by comparing the peer address to loopback), and the host's JSON response is returned as the command's output or raised as its error. So a CLI call while the host runs never waits on the lock and never sees `E_DB_BUSY`. The client and the CLI need no change; forwarding lives under `run`.

## Authentication

- `POST /login` with `username` and `password` (argon2id, `user set-password` sets one; a user with no password cannot log in) sets an HTTP-only cookie holding a session token: a row in `api_tokens` with `kind = session`, `expires_at` 12 hours after last use, refreshed on each request.
- `Authorization: Bearer <secret>` resolves a token row of kind `bearer` (issued with `token issue --for <user> --label`, shown once) or `session`.
- The actor is the token's user; `on_behalf_of` is the token's `on_behalf_of` (null for humans in this row; row 7 binds agents).
- Every other route returns 401 with the error document when neither is present; `/openapi.json` and `/login` are open.

## Routes

`POST /commands/{name}` for hub scope and `POST /companies/{selector}/commands/{name}` for company scope, where `{selector}` is a company id, `Organization/Company` percent-encoded, or a display name, and `{name}` is the command name with spaces as dashes (`company-update`). The body is the input model as JSON; `?dry_run=true` is the dry run. The response is the output model as JSON, status 200. Errors: 400 with the error document for named errors, 401 for missing or bad token, 403 for `E_PERMISSION`, 404 for `E_COMPANY_NOT_FOUND`, `E_ORGANIZATION_NOT_FOUND`, `E_EVENT_NOT_FOUND`, `E_DIRECTIVE_NOT_FOUND`, `E_RECORD_NOT_FOUND`, 409 for `E_VERSION_CONFLICT`, `E_IDEMPOTENCY_MISMATCH`, `E_NAME_TAKEN`, `E_DB_BUSY`, 422 for `E_VALIDATION`, 500 for `E_INTERNAL`; every status carries the same `{code, message, details}` document the CLI prints. `GET /openapi.json` is generated from the registry with one operation per command and the input and output schemas. `GET /companies/{selector}/events?after=<seq>&...filters` streams `audit tail` results as server-sent events, one event per message with `id: <seq>`, polling the database every two seconds inside the host; a client reconnecting with `Last-Event-ID` resumes. `GET /health` returns the host's pid, bind, and schema revisions.

## The workbench

Server-rendered HTML with HTMX for partial updates; no JavaScript build step; one stylesheet; readable, unstyled beyond that. Pages, all generated from the registry:

- `/login`, `/logout`.
- `/`: the company picker from `company list`; selecting sets the working company in the session.
- `/c/{company}/`: the noun index: every noun with company-scope commands, plus hub audit for hub admins.
- `/c/{company}/{noun}`: the `list` output as a table with the CLI's preferred columns, an "include inactive" toggle where the input has it, and a row link to the record page.
- `/c/{company}/{noun}/{id}`: the `show` output as a field view, `editing_by` at the top, the record's audit events below (`audit list --record-type --record-id`), and a button per verb the actor's role permits; the form for an update carries `expected_version` from the show it rendered, so the workbench never blind-writes; opening the page calls `presence set` and leaving it calls `presence clear`, both through HTMX.
- `/c/{company}/{noun}/{verb}`: a form generated from the input model: one input per leaf field, typed (`number`, `date`, `checkbox`, `select` for choices, `text`), the description as help, defaults filled, `reason` and `source_ref` fields on writes, a `directive` select on company writes, `--clear` as a per-field "clear" checkbox on update forms; submit runs the command and renders the output as a field view or the error document with its code and fields.
- `/c/{company}/audit`: `audit list` with its filters and paging by `next_before`; a row opens `audit show` with entries and diffs.
- `/hub/audit`: the same for hub admins.
- `/c/{company}/activity/{type}/{id}`: the record's audit events in time order (the row 6 feed will merge notes and attachments into this page).

Every page takes its data from the same routes a program would call, through the in-process runner, with interface `gui`.

## Demo

The seed gains nothing; the workbench shows what exists.

## Error matrix additions

`E_LOGIN_FAILED` (bad username or password, 401), `E_NO_PASSWORD` (login for a user without one, 401), `E_TOKEN_EXPIRED` (401), `E_HOST_UNREACHABLE` (a CLI call found a live host in `root.lock` but could not reach it; the message says to stop the stale host or remove the lock). `serve` refuses to start when another host holds the lock (`E_DB_BUSY`).

## Edges

- Does not touch: lists, ledger, attachments, agent tokens bound to principals, `user add`, `membership grant`, MCP.
- The host is the single writer while it runs; a CLI on the same machine forwards; a CLI on another machine talks HTTP with a bearer token.
- Cold start of the CLI is unaffected: `adapters/http` and `adapters/workbench` are imported only by `serve`.

## Verification the builder will perform

Tests: every registered command called through the library, the CLI, and HTTP with the same inputs and compared outputs and errors including status codes; a second process's CLI call forwarded to a running host and recorded with interface `cli`; concurrent reads not blocked by a long write; two writes to one company serialized and to two companies concurrent; login, session expiry, bearer tokens, 401s; the SSE stream delivering a new event and resuming from `Last-Event-ID`; the workbench pages rendered for every noun and verb with a form field per input leaf, an update form carrying `expected_version`, and a submit round trip through HTMX; `--allow-network` gating. By hand, and this is the human's taste gate: open the workbench in a browser, log in, pick the demo company, edit company information twice from two browsers to see the conflict, add a directive, watch presence, and read the audit page.
