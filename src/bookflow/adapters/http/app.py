"""The HTTP host: routes generated from the registry, one core entry point, the same error documents as the CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket as _socket
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import sqlalchemy as sa
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from bookflow.adapters.http import auth
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import ALL_CODES, INFRASTRUCTURE_CODES, BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.session import Session
from bookflow.hub import schema as h
from bookflow.hub import users

log = logging.getLogger("bookflow.http")

STATUS = {"E_UNAUTHENTICATED": 401, "E_LOGIN_FAILED": 401, "E_PERMISSION": 403, "E_WORKBENCH_HEADER": 403,
          "E_COMPANY_NOT_FOUND": 404, "E_ORGANIZATION_NOT_FOUND": 404, "E_EVENT_NOT_FOUND": 404, "E_DIRECTIVE_NOT_FOUND": 404, "E_RECORD_NOT_FOUND": 404, "E_USER_NOT_FOUND": 404, "E_TOKEN_NOT_FOUND": 404,
          "E_WORK_DEPENDENCY": 409, "E_CONVERSION_KEY_REUSED": 409, "E_VERSION_CONFLICT": 409, "E_PREVIEW_STALE": 409, "E_QUERY_STALE": 409, "E_IDEMPOTENCY_MISMATCH": 409, "E_NAME_TAKEN": 409, "E_DB_BUSY": 409,
          "E_VALIDATION": 422, "E_VALUE_RANGE": 422, "E_LIST_FILTER": 422, "E_INTERNAL": 500}
CONTEXT_HEADERS = {"reason": "X-Bookflow-Reason", "directive_id": "X-Bookflow-Directive", "source_ref": "X-Bookflow-Source-Ref",
                   "idempotency_key": "Idempotency-Key", "client_name": "X-Bookflow-Client-Name", "client_version": "X-Bookflow-Client-Version"}
COOKIE = "bookflow_session"
WORKBENCH_HEADER = "x-bookflow-workbench"


def decode_context_headers(headers) -> dict[str, str | None]:
    """Decode explicitly encoded textual context once, before constructing Context."""
    values = {field: headers.get(name) for field, name in CONTEXT_HEADERS.items()}
    encoding = headers.get("X-Bookflow-Context-Encoding")
    if encoding is None:
        return values
    try:
        if encoding != "percent-utf8":
            raise ValueError
        for field, value in values.items():
            if value is not None:
                if not value.isascii() or re.search(r"%(?![0-9a-fA-F]{2})", value):
                    raise ValueError
                values[field] = unquote(value, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise BookflowError("E_VALIDATION", message="Invalid percent-utf8 context headers.") from None
    return values


def safe_workbench_destination(value: Any) -> str:
    """Return an application-local redirect target, or the workbench root."""
    destination = str(value or "")
    parsed = urlsplit(destination)
    if (not destination.startswith("/") or destination.startswith("//") or "\\" in destination
            or parsed.scheme or parsed.netloc or any(ord(char) < 32 or ord(char) == 127 for char in destination)):
        return "/"
    return destination


class CookieRenewalMiddleware:
    """Append a sliding-expiry cookie without BaseHTTPMiddleware buffering streams."""

    def __init__(self, app, *, secure_cookies: bool):
        self.app = app
        self.secure_cookies = secure_cookies

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                secret = scope.get("state", {}).get("renew_session_cookie")
                if secret:
                    holder = Response()
                    holder.set_cookie(COOKIE, secret, httponly=True, samesite="lax", secure=self.secure_cookies,
                                      max_age=auth.SESSION_HOURS * 3600, path="/")
                    cookie_headers = [header for header in holder.raw_headers if header[0].lower() == b"set-cookie"]
                    message["headers"] = [*message.get("headers", []), *cookie_headers]
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def route_name(command_name: str) -> str:
    return command_name.replace(" ", ".")


def command_name(route: str) -> str:
    return route.replace(".", " ")


def error_response(err: BookflowError) -> JSONResponse:
    return JSONResponse(status_code=STATUS.get(err.code, 400), content=err.to_dict())


class Credential:
    def __init__(self, user_id: str, token_id: str, kind: str, label: str | None, login: str = "",
                 on_behalf_of: str | None = None, actor_kind: str = "human", hub_admin: bool = False,
                 *, secret: str):
        self.user_id, self.token_id, self.kind, self.label = user_id, token_id, kind, label
        self.login, self.on_behalf_of = login, on_behalf_of
        self.actor_kind, self.hub_admin = actor_kind, hub_admin
        self._secret = secret

    def revalidate(self, db) -> None:
        """Check the admitted credential in the session that will execute it."""
        row = auth.resolve_token(db, self._secret)
        if (row["id"], row["user_id"], row["kind"], row.get("on_behalf_of")) != (
            self.token_id, self.user_id, self.kind, self.on_behalf_of,
        ):
            raise BookflowError("E_UNAUTHENTICATED", details={"reason": "credential changed"})


def create_app(host, *, secure_cookies: bool) -> FastAPI:
    registry.load_all()
    app = FastAPI(title="Bookflow", version=host.version, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CookieRenewalMiddleware, secure_cookies=secure_cookies)
    from bookflow.adapters.http.publication import PublicationMiddleware
    app.add_middleware(PublicationMiddleware)

    # ------------------------------------------------------------ credentials
    def credential(request: Request, *, renew_cookie: bool = True, publication: bool = True) -> Credential:
        header = request.headers.get("authorization", "")
        secret = None
        via_cookie = False
        if header.lower().startswith("bearer "):
            secret = header[7:].strip()
        elif COOKIE in request.cookies:
            secret = request.cookies[COOKIE]
            via_cookie = True
        if not secret:
            raise BookflowError("E_UNAUTHENTICATED", details={"reason": "no credential"})
        if via_cookie and request.method != "GET" and request.headers.get(WORKBENCH_HEADER) != "1":
            raise BookflowError("E_WORKBENCH_HEADER")
        with _reader_hub(host) as db:
            row = auth.resolve_token(db, secret)
            user = db.conn.execute(sa.select(h.users.c.kind, h.users.c.hub_admin).where(
                h.users.c.id == row["user_id"], h.users.c.active.is_(True),
            )).mappings().first()
            if user is None:
                raise BookflowError("E_UNAUTHENTICATED", details={"reason": "user"})
            if auth.needs_refresh(row):
                queued = host.enqueue_token_refresh(row["id"], row["kind"])
                if queued and via_cookie and renew_cookie:
                    request.state.renew_session_cookie = secret
        result = Credential(row["user_id"], row["id"], row["kind"], row["label"], on_behalf_of=row.get("on_behalf_of"),
                            actor_kind=user["kind"], hub_admin=bool(user["hub_admin"]), secret=secret)
        from bookflow.adapters.http.publication import protect_credentials
        if publication:
            protect_credentials(host, result)
        return result

    def secret_of(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return request.cookies.get(COOKIE, "")

    def make_context(request: Request, cred: Credential, interface: Interface = Interface.http) -> Context:
        hdr = request.headers
        values = decode_context_headers(hdr)
        client_name = values["client_name"] or (cred.label if cred.kind == "bearer" else "bookflow-workbench" if hdr.get(WORKBENCH_HEADER) == "1" else "http")
        return Context(interface=interface, client_name=client_name or "http", client_version=values["client_version"] or host.version,
                       client_host=_socket.gethostname(), session_id=cred.token_id, request_id=new_id(), on_behalf_of=cred.on_behalf_of,
                       reason=values["reason"], directive_id=values["directive_id"],
                       source_ref=values["source_ref"], idempotency_key=values["idempotency_key"])

    # ------------------------------------------------------------ running commands
    def run_command(cmd, raw: dict[str, Any], ctx: Context, cred: Credential, selector: str | None, source: str, dry_run: bool) -> dict[str, Any]:
        from bookflow.core.performance import span
        with span("command", command=cmd.name, mode="hosted"):
            return run_command_body(cmd, raw, ctx, cred, selector, source, dry_run)

    def run_command_body(cmd, raw: dict[str, Any], ctx: Context, cred: Credential, selector: str | None, source: str, dry_run: bool) -> dict[str, Any]:
        from bookflow.adapters.http.execution import run_hosted
        bad = [k for k in raw if k in Context.model_fields]
        if bad:
            raise BookflowError("E_CONTEXT_IN_INPUT", message="Context values go in headers, not the body: " + ", ".join(f"{k} -> {CONTEXT_HEADERS.get(k, 'not accepted')}" for k in bad), details={"fields": bad})

        return run_hosted(host, cmd, raw, ctx, cred, selector, source, dry_run)

    def lookup(route: str):
        name = command_name(route)
        cmd = registry.get(name)
        if cmd is None:
            raise BookflowError("E_USAGE", message=f"unknown command {name!r}")
        if cmd.local_only:
            raise BookflowError("E_USAGE", message=f"`{name}` runs only on the host's own machine; it is not served over HTTP.")
        return cmd

    async def body_of(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "body", "problem": "not a JSON object"}]})
        if not isinstance(data, dict):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "body", "problem": "not a JSON object"}]})
        return data

    @app.exception_handler(BookflowError)
    async def _handle(request: Request, err: BookflowError):
        result = error_response(err)
        if request.url.path.startswith("/adapters/mcp"):
            from bookflow.adapters.mcp.catalog import BRIDGE_VERSION
            result.headers["X-Bookflow-MCP-Version"] = str(BRIDGE_VERSION)
        return result

    @app.exception_handler(Exception)
    async def _handle_any(request: Request, exc: Exception):
        rid = new_id()
        log.exception("internal failure %s", rid)
        return error_response(BookflowError("E_INTERNAL", message="Internal failure; see the host log.", details={"request_id": rid}))

    def selector_of(request: Request, company_id: str | None) -> tuple[str | None, str]:
        header = request.headers.get("x-bookflow-company")
        if company_id is not None:
            path_value = company_id.strip()
            if not is_ulid(path_value):
                raise BookflowError("E_VALIDATION", details={"fields": [{
                    "field": "company_id", "problem": "must be a ULID company id",
                }]})
            path_literal = normalize_ulid(path_value)
            if header:
                header_value = header.strip()
                header_literal = normalize_ulid(header_value) if is_ulid(header_value) else header_value
                if header_literal != path_literal:
                    raise BookflowError("E_VALIDATION", details={"fields": [{
                        "field": "X-Bookflow-Company",
                        "problem": "must equal the company id in the request path",
                    }]})
            return path_literal, "option"
        if header:
            return header, "option"
        return None, "none"

    def serve_command(route: str, request: Request, raw: dict[str, Any], company_id: str | None):
        # Authenticate before route and selector diagnostics so an anonymous
        # caller cannot use error differences to probe the command surface.
        cred = credential(request)
        cmd = lookup(route)
        selector, source = selector_of(request, company_id)
        dry = request.query_params.get("dry_run") in ("1", "true")
        return run_command(cmd, raw, make_context(request, cred), cred, selector, source, dry)

    @app.post("/commands/{route}")
    async def hub_command(route: str, request: Request):
        raw = await body_of(request)
        return await run_in_threadpool(serve_command, route, request, raw, None)

    @app.post("/companies/{company_id}/commands/{route}")
    async def company_command(company_id: str, route: str, request: Request):
        raw = await body_of(request)
        return await run_in_threadpool(serve_command, route, request, raw, company_id)

    from bookflow.adapters.http.transfers import install as install_transfers
    install_transfers(app, host, credential=credential, lookup=lookup, selector_of=selector_of,
                      make_context=make_context, secret_of=secret_of)

    # ------------------------------------------------------------ login / logout
    @app.post("/login")
    async def login(request: Request):
        data = await body_of(request) if request.headers.get("content-type", "").startswith("application/json") else dict(await request.form())
        return await run_in_threadpool(do_login, request, data)

    def do_login(request: Request, data: dict[str, Any]) -> JSONResponse:
        username, password = str(data.get("username", "")), str(data.get("password", ""))
        source = request.client.host if request.client else "?"
        auth.throttle_login(source)
        try:
            with _reader_hub(host) as db:
                row = users.find_by_username(db, username)
                if row and (not row["active"] or row["kind"] != "human"):
                    row = None
            ok = auth.verify_password(row["password_hash"] if row else None, password)
            if not ok or row is None:
                time.sleep(0.5)
                raise BookflowError("E_LOGIN_FAILED")
            secret = _issue_session(host, row["id"], username=username, expected_password_hash=row["password_hash"])
        finally:
            auth.release_login(source)
        resp = JSONResponse({"ok": True, "user_id": row["id"], "username": row["username"]})
        resp.set_cookie(COOKIE, secret, httponly=True, samesite="lax", secure=secure_cookies, max_age=auth.SESSION_HOURS * 3600, path="/")
        if request.headers.get("hx-request", "").lower() == "true":
            resp.headers["HX-Redirect"] = safe_workbench_destination(data.get("next"))
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        return await run_in_threadpool(do_logout, request)

    def do_logout(request: Request) -> Response:
        is_cookie = not request.headers.get("authorization", "").lower().startswith("bearer ") and COOKIE in request.cookies
        try:
            # Logout publishes only the constant logout acknowledgement and
            # cookie deletion, never an authority-dependent business result.
            cred = credential(request, renew_cookie=False, publication=False)
        except BookflowError as e:
            if not is_cookie or e.code != "E_UNAUTHENTICATED":
                raise
        else:
            def logout(s):
                cred.revalidate(s.hub)
                return _revoke(s, cred.token_id, "logout")
            host.run_write(cred.user_id, "", logout)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------------------ events
    def stream(request: Request, company_id: str | None):
        from bookflow.core.dispatch import _close, execute, guard, validate_input
        name = "audit tail" if company_id else "hub audit tail"
        cmd = registry.get(name)
        assert cmd is not None
        cred = credential(request, renew_cookie=False)
        selector, _ = selector_of(request, company_id)
        after = request.headers.get("last-event-id")
        if after is None:
            after = request.query_params.get("after")
        raw = {k: v for k, v in request.query_params.items() if k != "after"}
        if after is not None:
            raw["after"] = after
        validated = validate_input(cmd, raw)
        values = validated.model_dump(mode="json", exclude_none=True)
        cursor = values.pop("after", None)
        values.pop("limit", None)
        secret = secret_of(request)
        ctx = make_context(request, cred)
        def resolve_again() -> None:
            with _reader_hub(host) as hub_db:
                row = auth.resolve_token(hub_db, secret)
                if auth.needs_refresh(row):
                    host.enqueue_token_refresh(row["id"], row["kind"])

        def drain(start: int | None) -> tuple[list[str], int, str, bool]:
            frames: list[str] = []
            next_cursor = start
            s = host.reader_session(cred.user_id, cred.login)
            try:
                command_input = {**values, **({"after": next_cursor} if next_cursor is not None else {}), "limit": 100, "scan_limit": 100}
                out = execute(cmd, command_input, ctx, s, company_selector=selector, company_source="option")
                for item in out["items"]:
                    frames.append(f"id: {item['seq']}\nevent: audit\ndata: {json.dumps(item, default=str)}\n\n")
                if out["next_after"] is not None:
                    next_cursor = out["next_after"]
                elif next_cursor is None:
                    next_cursor = out.get("high_water") or 0
            finally:
                try:
                    guard(lambda: _close(s), cred.hub_admin)
                finally:
                    host.reader_done()
            canonical_key = s.company_row["id"] if s.company_row is not None else "hub"
            return frames, next_cursor or 0, canonical_key, out["scan_more"]

        async def gen():
            nonlocal cursor
            event = asyncio.Event()
            subscription = None
            ready_announced = False
            try:
                try:
                    frames, cursor, key, more = await run_in_threadpool(drain, cursor)
                except BookflowError as e:
                    yield f"event: error\ndata: {json.dumps(e.to_dict())}\n\n"
                    return
                subscription, _ = host.subscribe(key, asyncio.get_running_loop(), event)
                for frame in frames:
                    yield frame
                # Drain once after registration. This closes the only unobservable
                # window: a commit after the first drain but before subscription.
                while True:
                    if host._stopping:
                        yield "event: error\ndata: {\"code\": \"E_DB_BUSY\", \"message\": \"the host is stopping\", \"details\": {}}\n\n"
                        return
                    event.clear()
                    before = host.stream_sequence(key)
                    try:
                        await run_in_threadpool(resolve_again)
                        frames, cursor, canonical_key, more = await run_in_threadpool(drain, cursor)
                    except BookflowError as e:
                        yield f"event: error\ndata: {json.dumps(e.to_dict())}\n\n"
                        return
                    if canonical_key != key:  # pragma: no cover - ids are immutable while a route is open
                        raise RuntimeError("the event stream's company identity changed")
                    for frame in frames:
                        yield frame
                    if host._stopping:
                        yield "event: error\ndata: {\"code\": \"E_DB_BUSY\", \"message\": \"the host is stopping\", \"details\": {}}\n\n"
                        return
                    latest = host.stream_sequence(key)
                    if more or latest != before or event.is_set():
                        continue
                    if not ready_announced:
                        # An SSE comment is invisible to clients but makes the
                        # host's first idle boundary observable to shutdown
                        # probes. Once this reaches the peer, the next iterator
                        # step is the event wait that begin_shutdown wakes.
                        yield ": ready\n\n"
                        ready_announced = True
                    keepalive_at = asyncio.get_running_loop().time() + 15.0
                    while not event.is_set():
                        remaining = keepalive_at - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            yield ": keep-alive\n\n"
                            break
                        try:
                            await asyncio.wait_for(event.wait(), timeout=min(0.5, remaining))
                        except TimeoutError:
                            if await request.is_disconnected():
                                return
            finally:
                if subscription is not None:
                    host.unsubscribe(subscription)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/companies/{company_id}/events")
    async def events(company_id: str, request: Request):
        return await run_in_threadpool(stream, request, company_id)

    @app.get("/hub-events")
    async def hub_events(request: Request):
        return await run_in_threadpool(stream, request, None)

    # ------------------------------------------------------------ openapi / health
    @app.get("/openapi.json")
    def openapi():
        return build_openapi(host.version)

    @app.get("/health")
    async def health():
        return {"ok": True}

    from bookflow.adapters.mcp.bridge import mount_mcp
    mount_mcp(app, host, credential, make_context)
    from bookflow.adapters.workbench.pages import mount_workbench
    mount_workbench(app, host, credential, make_context, run_command, secure_cookies)
    return app


def _reader_hub(host):
    from contextlib import contextmanager
    from bookflow.storage.engine import open_database

    @contextmanager
    def reader():
        host.reader_started()
        try:
            with open_database(host.data_root / "hub.db", writable=False) as db:
                yield db
        finally:
            host.reader_done()
    return reader()


def _issue_session(host, user_id: str, *, username: str, expected_password_hash: str) -> str:
    def job(s: Session):
        from bookflow.core.audit import write_event_to
        from bookflow.core.registry import Touched
        s.hub.raw.execute("BEGIN IMMEDIATE")
        user = users.find_by_username(s.hub, username)
        if (user is None or user["id"] != user_id or not user["active"] or user["kind"] != "human"
                or user["password_hash"] != expected_password_hash):
            raise BookflowError("E_LOGIN_FAILED")
        row, secret = auth.issue_token(s.hub, user_id=user_id, kind="session", label="browser session", days=None, via="http", actor_id=user_id)
        ctx = Context(interface=Interface.http, client_name="bookflow-workbench", client_version=host.version, client_host=_socket.gethostname(), session_id=row["id"], request_id=new_id())
        write_event_to(s.hub, ctx, "login", "logged in", [Touched("api_token", row["id"], "create", None, 1, {k: v for k, v in row.items() if k != "token_hash"})], actor_id=user_id, actor_kind="human")
        s.hub.raw.execute("COMMIT")
        return secret
    return host.run_write(user_id, "", job)


def _revoke(s: Session, token_id: str, why: str) -> dict[str, Any]:
    from bookflow.core import clock
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    s.hub.raw.execute("BEGIN IMMEDIATE")
    s.hub.conn.execute(h.api_tokens.update().where(h.api_tokens.c.id == token_id).values(revoked_at=clock.now_iso()))
    ctx = Context(interface=Interface.http, client_name="bookflow-workbench", client_version="", client_host=_socket.gethostname(), session_id=token_id, request_id=new_id())
    write_event_to(s.hub, ctx, why, "logged out" if why == "logout" else "revoked a token", [Touched("api_token", token_id, "update", None, None, {"revoked": True})], actor_id=s.actor.id if s.actor else None, actor_kind=s.actor.kind if s.actor else None)
    s.hub.raw.execute("COMMIT")
    return {"ok": True}


def build_openapi(version: str) -> dict[str, Any]:
    """One operation per routed command, generated from the registry (row 3 plan, Routes)."""
    registry.load_all()
    paths: dict[str, Any] = {}
    error_schema = {"type": "object", "properties": {"code": {"type": "string", "enum": sorted(ALL_CODES)}, "message": {"type": "string"}, "details": {"type": "object"}}, "required": ["code", "message", "details"]}
    params = [{"name": "X-Bookflow-Context-Encoding", "in": "header", "required": False, "schema": {"type": "string", "enum": ["percent-utf8"]}}] + [{"name": hdr, "in": "header", "required": False, "schema": {"type": "string"}} for hdr in CONTEXT_HEADERS.values()]
    for cmd in registry.routed_commands():
        path = f"/commands/{route_name(cmd.name)}" if cmd.scope == "hub" else f"/companies/{{company_id}}/commands/{route_name(cmd.name)}"
        errors = list(dict.fromkeys([*cmd.error_codes, *INFRASTRUCTURE_CODES]))
        op = {
            "summary": cmd.description,
            "parameters": params + ([{"name": "company_id", "in": "path", "required": True, "schema": {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}$"}}, {"name": "X-Bookflow-Company", "in": "header", "required": False, "schema": {"type": "string"}, "description": "If supplied, this must be the same company id as the path (ULID case is normalized)."}] if cmd.scope == "company" else [])
                          + ([{"name": "dry_run", "in": "query", "required": False, "schema": {"type": "boolean"}}] if cmd.is_write else []),
            "requestBody": {"required": True, "content": {"application/json": {"schema": cmd.input_model.model_json_schema()}}},
            "responses": {"200": {"content": {"application/json": {"schema": cmd.output_model.model_json_schema()}}},
                          "4XX": {"description": "Error document; codes: " + ", ".join(errors), "content": {"application/json": {"schema": error_schema}}}},
            "security": [{"bearer": []}, {"cookie": []}],
            "x-bookflow-error-codes": errors,
        }
        if cmd.transfer is not None:
            path = f"/companies/{{company_id}}/transfers/{route_name(cmd.name)}"
            op["parameters"].append({"name": "X-Bookflow-Input", "in": "header", "required": True,
                "schema": {"type": "string", "maxLength": 8192},
                "description": "Unpadded base64url UTF-8 JSON object, at most 6144 decoded bytes.",
                "x-bookflow-input-schema": cmd.input_model.model_json_schema()})
            op["x-bookflow-transfer"] = cmd.transfer.direction
            if cmd.transfer.direction == "input":
                op["requestBody"] = {"required": True, "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
            else:
                op.pop("requestBody")
                op["responses"]["200"] = {"description": "Verified binary attachment; empty request body required.",
                    "headers": {name: {"schema": {"type": "string"}} for name in
                                ("Content-Disposition", "Content-Length", "X-Bookflow-SHA256", "X-Bookflow-Output")},
                    "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
        paths[path] = {"post": op}
    paths["/login"] = {"post": {"summary": "Log in with username and password; sets the session cookie.", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"username": {"type": "string"}, "password": {"type": "string", "format": "password"}}}}}}}}
    return {"openapi": "3.1.0", "info": {"title": "Bookflow", "version": version}, "paths": paths,
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}, "cookie": {"type": "apiKey", "in": "cookie", "name": COOKIE}}, "schemas": {"Error": error_schema}}}
