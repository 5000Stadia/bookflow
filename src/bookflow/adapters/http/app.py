"""The HTTP host: routes generated from the registry, one core entry point, the same error documents as the CLI."""

from __future__ import annotations

import json
import logging
import socket as _socket
import time
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from bookflow.adapters.http import auth
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import ALL_CODES, INFRASTRUCTURE_CODES, BookflowError
from bookflow.core.ids import new_id
from bookflow.core.session import Session
from bookflow.hub import schema as h

log = logging.getLogger("bookflow.http")

STATUS = {"E_UNAUTHENTICATED": 401, "E_LOGIN_FAILED": 401, "E_PERMISSION": 403, "E_WORKBENCH_HEADER": 403,
          "E_COMPANY_NOT_FOUND": 404, "E_ORGANIZATION_NOT_FOUND": 404, "E_EVENT_NOT_FOUND": 404, "E_DIRECTIVE_NOT_FOUND": 404, "E_RECORD_NOT_FOUND": 404, "E_USER_NOT_FOUND": 404, "E_TOKEN_NOT_FOUND": 404,
          "E_VERSION_CONFLICT": 409, "E_IDEMPOTENCY_MISMATCH": 409, "E_NAME_TAKEN": 409, "E_DB_BUSY": 409, "E_VALIDATION": 422, "E_INTERNAL": 500}
CONTEXT_HEADERS = {"reason": "X-Bookflow-Reason", "directive_id": "X-Bookflow-Directive", "source_ref": "X-Bookflow-Source-Ref",
                   "idempotency_key": "Idempotency-Key", "client_name": "X-Bookflow-Client-Name", "client_version": "X-Bookflow-Client-Version"}
COOKIE = "bookflow_session"
WORKBENCH_HEADER = "x-bookflow-workbench"


def route_name(command_name: str) -> str:
    return command_name.replace(" ", ".")


def command_name(route: str) -> str:
    return route.replace(".", " ")


def error_response(err: BookflowError) -> JSONResponse:
    return JSONResponse(status_code=STATUS.get(err.code, 400), content=err.to_dict())


class Credential:
    def __init__(self, user_id: str, token_id: str, kind: str, label: str | None, login: str = "", on_behalf_of: str | None = None):
        self.user_id, self.token_id, self.kind, self.label, self.login, self.on_behalf_of = user_id, token_id, kind, label, login, on_behalf_of


def create_app(host, *, secure_cookies: bool) -> FastAPI:
    registry.load_all()
    app = FastAPI(title="Bookflow", version=host.version, docs_url=None, redoc_url=None, openapi_url=None)

    # ------------------------------------------------------------ credentials
    def credential(request: Request) -> Credential:
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
            if auth.needs_refresh(row):
                host.submit(lambda: auth.refresh_token(host._hub, row["id"], row["kind"]))
        return Credential(row["user_id"], row["id"], row["kind"], row["label"], on_behalf_of=row.get("on_behalf_of"))

    def secret_of(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return request.cookies.get(COOKIE, "")

    def make_context(request: Request, cred: Credential, interface: Interface = Interface.http) -> Context:
        hdr = request.headers
        client_name = hdr.get(CONTEXT_HEADERS["client_name"]) or (cred.label if cred.kind == "bearer" else "bookflow-workbench" if hdr.get(WORKBENCH_HEADER) == "1" else "http")
        return Context(interface=interface, client_name=client_name or "http", client_version=hdr.get(CONTEXT_HEADERS["client_version"]) or host.version,
                       client_host=_socket.gethostname(), session_id=cred.token_id, request_id=new_id(), on_behalf_of=cred.on_behalf_of,
                       reason=hdr.get(CONTEXT_HEADERS["reason"]), directive_id=hdr.get(CONTEXT_HEADERS["directive_id"]),
                       source_ref=hdr.get(CONTEXT_HEADERS["source_ref"]), idempotency_key=hdr.get(CONTEXT_HEADERS["idempotency_key"]))

    # ------------------------------------------------------------ running commands
    def run_command(cmd, raw: dict[str, Any], ctx: Context, cred: Credential, selector: str | None, source: str, dry_run: bool) -> dict[str, Any]:
        from bookflow.core.dispatch import _close, execute
        bad = [k for k in raw if k in Context.model_fields]
        if bad:
            raise BookflowError("E_CONTEXT_IN_INPUT", message="Context values go in headers, not the body: " + ", ".join(f"{k} -> {CONTEXT_HEADERS.get(k, 'not accepted')}" for k in bad), details={"fields": bad})
        if cmd.is_write and not dry_run or cmd.kind == "advisory":
            return host.run_write(cred.user_id, cred.login, lambda s: execute(cmd, raw, ctx, s, company_selector=selector, company_source=source, dry_run=dry_run))
        s = host.reader_session(cred.user_id, cred.login)
        try:
            return execute(cmd, raw, ctx, s, company_selector=selector, company_source=source, dry_run=dry_run)
        finally:
            _close(s)
            host.reader_done()

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
        return error_response(err)

    @app.exception_handler(Exception)
    async def _handle_any(request: Request, exc: Exception):
        rid = new_id()
        log.exception("internal failure %s", rid)
        return error_response(BookflowError("E_INTERNAL", message="Internal failure; see the host log.", details={"request_id": rid}))

    def selector_of(request: Request, company_id: str | None) -> tuple[str | None, str]:
        header = request.headers.get("x-bookflow-company")
        if header:
            return header, "option"
        if company_id:
            return company_id, "option"
        return None, "none"

    def serve_command(route: str, request: Request, raw: dict[str, Any], company_id: str | None):
        cmd = lookup(route)
        cred = credential(request)
        dry = request.query_params.get("dry_run") in ("1", "true")
        if company_id is None:
            selector, source = request.headers.get("x-bookflow-company"), "option"
        else:
            selector, source = selector_of(request, company_id)
        return run_command(cmd, raw, make_context(request, cred), cred, selector, source, dry)

    @app.post("/commands/{route}")
    async def hub_command(route: str, request: Request):
        raw = await body_of(request)
        return await run_in_threadpool(serve_command, route, request, raw, None)

    @app.post("/companies/{company_id}/commands/{route}")
    async def company_command(company_id: str, route: str, request: Request):
        raw = await body_of(request)
        return await run_in_threadpool(serve_command, route, request, raw, company_id)

    # ------------------------------------------------------------ login / logout
    @app.post("/login")
    async def login(request: Request, response: Response):
        data = await body_of(request) if request.headers.get("content-type", "").startswith("application/json") else dict(await request.form())
        return await run_in_threadpool(do_login, request, data)

    def do_login(request: Request, data: dict[str, Any]) -> Response:
        username, password = str(data.get("username", "")), str(data.get("password", ""))
        source = request.client.host if request.client else "?"
        auth.throttle_login(source)
        try:
            with _reader_hub(host) as db:
                row = db.conn.execute(sa.select(h.users).where(h.users.c.username == username, h.users.c.active.is_(True), h.users.c.kind == "human")).mappings().first()
                ok = auth.verify_password(row["password_hash"] if row else None, password)
            if not ok or row is None:
                time.sleep(0.5)
                raise BookflowError("E_LOGIN_FAILED")
            secret = _issue_session(host, row["id"])
        finally:
            auth.release_login(source)
        resp = JSONResponse({"ok": True, "user_id": row["id"], "username": row["username"]})
        resp.set_cookie(COOKIE, secret, httponly=True, samesite="lax", secure=secure_cookies, max_age=auth.SESSION_HOURS * 3600, path="/")
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        return await run_in_threadpool(do_logout, request)

    def do_logout(request: Request) -> Response:
        cred = credential(request)
        host.run_write(cred.user_id, "", lambda s: _revoke(s, cred.token_id, "logout"))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------------------ events
    def stream(request: Request, company_id: str | None):
        cred = credential(request)
        secret = secret_of(request)
        from bookflow.core.dispatch import _close, execute
        name = "audit tail" if company_id else "hub audit tail"
        cmd = registry.get(name)
        after = request.headers.get("last-event-id") or request.query_params.get("after")
        filters = {k: v for k, v in request.query_params.items() if k not in ("after",)}
        key = company_id or "hub"

        def gen():
            nonlocal after
            cursor = int(after) if after is not None else None
            checked = True  # the credential was resolved when the request arrived
            while True:
                try:
                    if not checked:  # a revoked or expired credential ends the stream with an error event
                        with _reader_hub(host) as hub_db:
                            auth.resolve_token(hub_db, secret)
                    checked = False
                    s = host.reader_session(cred.user_id, cred.login)
                    try:
                        while True:
                            raw = {**filters, **({"after": cursor} if cursor is not None else {}), "limit": 100}
                            out = execute(cmd, raw, make_context(request, cred), s, company_selector=company_id, company_source="option")
                            for item in out["items"]:
                                yield f"id: {item['seq']}\nevent: audit\ndata: {json.dumps(item, default=str)}\n\n"
                            if out["next_after"] is not None:
                                cursor = out["next_after"]
                            elif cursor is None:
                                cursor = out.get("high_water") or 0
                            if out["count"] < 100:
                                break
                    finally:
                        _close(s)
                        host.reader_done()
                except BookflowError as e:
                    yield f"event: error\ndata: {json.dumps(e.to_dict())}\n\n"
                    return
                if host._stopping:
                    yield "event: error\ndata: {\"code\": \"E_DB_BUSY\", \"message\": \"the host is stopping\"}\n\n"
                    return
                host.wait_for_commit(key, 15.0)
                yield ": keep-alive\n\n"

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

    from bookflow.adapters.workbench.pages import mount_workbench
    mount_workbench(app, host, credential, make_context, run_command, secure_cookies)
    return app


def _reader_hub(host):
    from bookflow.storage.engine import open_database
    return open_database(host.data_root / "hub.db", writable=False)


def _issue_session(host, user_id: str) -> str:
    def job(s: Session):
        from bookflow.core.audit import write_event_to
        from bookflow.core.registry import Touched
        s.hub.raw.execute("BEGIN IMMEDIATE")
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
    params = [{"name": hdr, "in": "header", "required": False, "schema": {"type": "string"}} for hdr in CONTEXT_HEADERS.values()]
    for cmd in registry.routed_commands():
        path = f"/commands/{route_name(cmd.name)}" if cmd.scope == "hub" else f"/companies/{{company_id}}/commands/{route_name(cmd.name)}"
        op = {
            "summary": cmd.description,
            "parameters": params + ([{"name": "company_id", "in": "path", "required": True, "schema": {"type": "string"}}, {"name": "X-Bookflow-Company", "in": "header", "required": False, "schema": {"type": "string"}, "description": "A company id, display name, or Organization/Company; overrides the path id"}] if cmd.scope == "company" else [])
                          + ([{"name": "dry_run", "in": "query", "required": False, "schema": {"type": "boolean"}}] if cmd.is_write else []),
            "requestBody": {"content": {"application/json": {"schema": cmd.input_model.model_json_schema()}}},
            "responses": {"200": {"content": {"application/json": {"schema": cmd.output_model.model_json_schema()}}},
                          "4XX": {"description": "Error document; codes: " + ", ".join(cmd.error_codes + list(INFRASTRUCTURE_CODES)), "content": {"application/json": {"schema": error_schema}}}},
            "x-bookflow-error-codes": cmd.error_codes,
        }
        paths[path] = {"post": op}
    paths["/login"] = {"post": {"summary": "Log in with username and password; sets the session cookie.", "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"username": {"type": "string"}, "password": {"type": "string", "format": "password"}}}}}}}}
    return {"openapi": "3.1.0", "info": {"title": "Bookflow", "version": version}, "paths": paths,
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}, "cookie": {"type": "apiKey", "in": "cookie", "name": COOKIE}}, "schemas": {"Error": error_schema}}}
