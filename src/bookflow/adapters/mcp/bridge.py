"""Dedicated bearer-only host entry. Mounting is owned by the HTTP application."""

import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core import registry
from bookflow.core.context import Interface
from bookflow.core.context_options import normalize_options
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid

from .catalog import BRIDGE_VERSION, command_help, list_commands
from .envelopes import HelpArguments, ListArguments, RunArguments, validate


def mount_mcp(app, host, credential, make_context, executor):
    """Executor shares ordinary hosted execution/publication; never an authority path here."""
    from bookflow.adapters.http.app import STATUS

    def authenticate(request):
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            raise BookflowError("E_UNAUTHENTICATED")
        return credential(request, renew_cookie=False)

    def response(document, status=200):
        return JSONResponse(document, status_code=status, headers={"X-Bookflow-MCP-Version": str(BRIDGE_VERSION), "Cache-Control": "no-store"})

    @app.get("/adapters/mcp", include_in_schema=False)
    async def preflight(request: Request):
        try:
            await run_in_threadpool(authenticate, request)
            return response({"bridge_version": BRIDGE_VERSION, "host_version": host.version})
        except BookflowError as exc:
            return response(exc.to_dict(), STATUS.get(exc.code, 400))

    @app.post("/adapters/mcp/{tool}", include_in_schema=False)
    async def invoke(tool: str, request: Request):
        try:
            cred = await run_in_threadpool(authenticate, request)
            try:
                envelope = await request.json()
            except (ValueError, UnicodeError):
                raise BookflowError("E_VALIDATION", details={"reason": "malformed_transport"}) from None
            if not isinstance(envelope, dict) or set(envelope) - {"arguments", "company_selection"}:
                raise BookflowError("E_VALIDATION", details={"reason": "malformed_transport"})
            arguments = validate(tool, envelope.get("arguments"))
            if isinstance(arguments, ListArguments):
                result = await run_in_threadpool(list_commands, **arguments.model_dump())
                await run_in_threadpool(authenticate, request)
                return response(result)
            if isinstance(arguments, HelpArguments):
                result = await run_in_threadpool(command_help, arguments.command)
                await run_in_threadpool(authenticate, request)
                return response(result)
            if isinstance(arguments, RunArguments):
                cmd = registry.get(arguments.command)
                if cmd is None or cmd.local_only:
                    raise BookflowError("E_USAGE", details={"command": arguments.command, "boundary": "local_only" if cmd else "unknown_command"})
                options = normalize_options(cmd, **arguments.model_dump(include={"company", "reason", "source_ref", "directive", "idempotency_key", "dry_run"}))
                selection = envelope.get("company_selection", {})
                if not isinstance(selection, dict) or set(selection) != {"value", "source"} or selection["source"] not in {"option", "env", "default", "none"} or (selection["value"] is not None and not isinstance(selection["value"], str)):
                    raise BookflowError("E_VALIDATION", details={"reason": "malformed_selection"})
                if cmd.scope != "company" and selection["value"] is not None:
                    raise BookflowError("E_USAGE", details={"argument": "company"})
                ctx = make_context(request, cred, Interface.mcp)
                session = request.headers.get("X-Bookflow-Session-Id", "")
                if not is_ulid(session):
                    raise BookflowError("E_VALIDATION", details={"fields": [{"field": "session_id", "problem": "process ULID required"}]})
                ctx = ctx.model_copy(update={"session_id": session, "client_host": request.headers.get("X-Bookflow-Client-Host", ""),
                    "reason": options["reason"], "source_ref": options["source_ref"], "directive_id": options["directive"], "idempotency_key": options["idempotency_key"]})
                return await executor(request, cmd, arguments, ctx, cred, selection)
            return await executor(request, None, arguments, None, cred, None)
        except BookflowError as exc:
            return response(exc.to_dict(), STATUS.get(exc.code, 400))
