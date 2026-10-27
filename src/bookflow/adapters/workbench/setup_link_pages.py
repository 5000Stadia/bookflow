"""The one-time "set your password" page a `bookflow setup` link opens (R182).

GET shows the form; POST sets the password once and signs the person in. The link store and its rules
are `bookflow.hub.setup_links`. The page answers only a loopback client: the link is meant for the
person at this computer, and a host opened to the network does not open it to the network too.
"""
from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError
from bookflow.hub import setup_links

GONE = "This link has expired or was already used. Ask whoever set up Bookflow to run `bookflow setup` again for a new one."


def _loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host in ("testclient", "localhost"):  # the in-process test client has no address
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install(app: FastAPI, *, host, render, secure_cookies: bool) -> None:
    from bookflow.adapters.http.app import COOKIE, _issue_session, _reader_hub
    from bookflow.adapters.http import auth
    import sqlalchemy as sa
    from bookflow.hub import schema as h

    def person(user_id: str) -> dict[str, Any] | None:
        """The person the link is for, while they are an active human with no password yet."""
        with _reader_hub(host) as db:
            found = db.conn.execute(sa.select(h.users).where(h.users.c.id == user_id)).mappings().first()
        row = dict(found) if found else None
        if row is None or row["kind"] != "human" or not row["active"] or row["password_hash"]:
            return None
        return row

    def page(request: Request, *, status: int = 200, **ctx: Any) -> HTMLResponse:
        response = render("setup_password.html", request, status_code=status, **ctx)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/setup/{secret}", response_class=HTMLResponse)
    def setup_page(secret: str, request: Request):
        if not _loopback(request):
            return page(request, status=404, error="Open this link on the computer Bookflow runs on.", username=None, secret=None)
        link = setup_links.peek(host.data_root, secret)
        row = person(link["user_id"]) if link else None
        if row is None:
            return page(request, status=410, error=GONE, username=None, secret=None)
        return page(request, error=None, username=row["username"], display_name=row["display_name"],
                    secret=secret, expires_at=link["expires_at"], minimum=setup_links.MIN_PASSWORD)

    @app.post("/setup/{secret}")
    async def setup_submit(secret: str, request: Request):
        data = dict(await request.form())
        return await run_in_threadpool(_submit, request, secret, data)

    def _submit(request: Request, secret: str, data: dict[str, Any]) -> JSONResponse:
        if not _loopback(request):
            return JSONResponse({"ok": False, "message": "Open this link on the computer Bookflow runs on."}, status_code=404)
        password, again = str(data.get("password", "")), str(data.get("again", ""))
        if len(password) < setup_links.MIN_PASSWORD or len(password) > 1024:
            return JSONResponse({"ok": False, "message": f"Use at least {setup_links.MIN_PASSWORD} characters."}, status_code=400)
        if password != again:
            return JSONResponse({"ok": False, "message": "The two passwords do not match."}, status_code=400)
        claimed = setup_links.claim(host.data_root, secret)
        if claimed is None:
            return JSONResponse({"ok": False, "message": GONE}, status_code=410)
        link, path = claimed
        row = person(link["user_id"])
        if row is None:
            setup_links.spend(path)
            return JSONResponse({"ok": False, "message": GONE}, status_code=410)
        try:
            new_hash = _set_first_password(host, row, password)
        except BookflowError:
            setup_links.release(path, secret)
            return JSONResponse({"ok": False, "message": "The password could not be saved. Try again."}, status_code=409)
        setup_links.spend(path)
        session = _issue_session(host, row["id"], username=row["username"], expected_password_hash=new_hash)
        response = JSONResponse({"ok": True, "username": row["username"]})
        response.set_cookie(COOKIE, session, httponly=True, samesite="lax", secure=secure_cookies,
                            max_age=auth.SESSION_HOURS * 3600, path="/")
        response.headers["HX-Redirect"] = "/"
        return response


def _set_first_password(host, row: dict[str, Any], password: str) -> str:
    """`user set-password` as the person themself, only while they still have no password."""
    from bookflow.core import registry
    from bookflow.core.context import ActorKind, Context, Interface
    from bookflow.core.dispatch import run_in_session

    def job(s):
        import sqlalchemy as sa
        from bookflow.hub import schema as h
        current = s.hub.conn.execute(sa.select(h.users.c.password_hash).where(h.users.c.id == row["id"])).scalar_one_or_none()
        if current:
            raise BookflowError("E_VERSION_CONFLICT", message="A password was set already.")
        cmd = registry.get("user set-password")
        ctx = Context.new(Interface.http, "bookflow-setup-link", reason="Set first password from the setup link")
        ctx = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": ActorKind(s.actor.kind)})
        run_in_session(cmd, cmd.input_model.model_validate({"username": row["username"], "password": password}), ctx, s)
        return s.hub.conn.execute(sa.select(h.users.c.password_hash).where(h.users.c.id == row["id"])).scalar_one()

    return host.run_write(row["id"], "", job)
