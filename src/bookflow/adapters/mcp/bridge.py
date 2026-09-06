"""Dedicated bearer-only host entry. Mounting is owned by the HTTP application."""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from bookflow.core.errors import BookflowError

from .catalog import BRIDGE_VERSION, command_help, list_commands
from .envelopes import HelpArguments, ListArguments, validate


def mount_mcp(app, host, credential, make_context):
    """Static discovery plus the host-owned intent transport used by the launcher."""
    from bookflow.adapters.http.app import STATUS

    def authenticate(request):
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            raise BookflowError("E_UNAUTHENTICATED")
        return credential(request, renew_cookie=False)

    def response(document, status=200):
        return JSONResponse(document, status_code=status, headers={"X-Bookflow-MCP-Version": str(BRIDGE_VERSION), "Cache-Control": "no-store"})

    from .transport import mount_transport
    mount_transport(app, host, authenticate, make_context, response)

    @app.get("/adapters/mcp", include_in_schema=False)
    async def preflight(request: Request):
        try:
            await run_in_threadpool(authenticate, request)
            from .runtime import require_parser
            require_parser()
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
                result = await run_in_threadpool(command_help, arguments.command, arguments.view)
                await run_in_threadpool(authenticate, request)
                return response(result)
            raise BookflowError("E_USAGE", message="Bridge v2 command execution uses the installed launcher's admitted intent transport.")
        except BookflowError as exc:
            return response(exc.to_dict(), STATUS.get(exc.code, 400))
