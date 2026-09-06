"""Installed stdio launcher. No bookkeeping host, root lock or ambient authentication."""

import ipaddress
import json
import os
import socket
from contextlib import ExitStack
from urllib.parse import urlsplit, quote

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id

from .envelopes import TOOLS, validate, tool_schema
from .catalog import BRIDGE_VERSION


def host_origin(value):
    try:
        if not isinstance(value, str) or any(ord(character) < 32 for character in value):
            raise ValueError
        parsed = urlsplit(value or "")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError
        _ = parsed.port
        if parsed.scheme == "http" and not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError
    except ValueError:
        raise BookflowError("E_USAGE", message="Configure an HTTPS host origin or numeric HTTP loopback origin without credentials, path, query or fragment.") from None
    return value.rstrip("/")


async def serve(inp, origin, secret):
    import httpx2
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types

    headers = {
        "Authorization": "Bearer " + secret,
        "X-Bookflow-Client-Name": quote(inp.label, safe=""),
        "X-Bookflow-Context-Encoding": "percent-utf8",
        "X-Bookflow-Client-Host": socket.gethostname(),
        "X-Bookflow-Client-Version": "0.0.1",
        "X-Bookflow-Session-Id": new_id(),
    }
    async with httpx2.AsyncClient(base_url=origin, headers=headers, trust_env=False,
                                  follow_redirects=False, timeout=httpx2.Timeout(30, connect=5)) as client:
        async def list_tools(_ctx, _params):
            tools = []
            for name, (model, description) in TOOLS.items():
                schema = tool_schema(name)
                tools.append(types.Tool(name=name, description=description, input_schema=schema))
            return types.ListToolsResult(tools=tools)

        async def call_tool(_ctx, params):
            submitted = False
            try:
                arguments = validate(params.name, params.arguments or {})
                preflight = await client.get("/adapters/mcp")
                if preflight.status_code == 404 or preflight.headers.get("x-bookflow-mcp-version") != str(BRIDGE_VERSION):
                    raise BookflowError("E_VERSION_MISMATCH", details={"supported_bridge_versions": [BRIDGE_VERSION], "received_bridge_version": preflight.headers.get("x-bookflow-mcp-version"), "stage": "preflight", "outcome": "not_submitted"})
                if preflight.status_code >= 400:
                    raise BookflowError("E_UNAUTHENTICATED")
                payload = {"arguments": arguments.model_dump(exclude_unset=True)}
                from .envelopes import RunArguments
                if isinstance(arguments, RunArguments):
                    help_response = await client.post("/adapters/mcp/bookflow_help", json={"arguments": {"command": arguments.command}})
                    metadata = help_response.json()
                    if help_response.status_code >= 400:
                        raise BookflowError(metadata["code"], message=metadata["message"], details=metadata["details"])
                    from .selection import company_selection
                    company, source = company_selection(metadata["scope"], arguments.company, selection_root=inp.selection_root)
                    payload["company_selection"] = {"value": company, "source": source}
                submitted = params.name == "bookflow_run"
                response = await client.post("/adapters/mcp/" + params.name,
                                             json=payload)
                if response.headers.get("x-bookflow-mcp-version") != str(BRIDGE_VERSION):
                    raise BookflowError("E_IO", details={"operation": "mcp_result", "reason": "incompatible_bridge", "stage": "post_submission", "outcome": "unknown"})
                try:
                    document = response.json()
                    if not isinstance(document, dict):
                        raise ValueError
                except ValueError:
                    raise BookflowError("E_IO", details={"operation": "mcp_result", "reason": "invalid_json", "stage": "post_submission", "outcome": "unknown"}) from None
                is_error = response.status_code >= 400
            except BookflowError as exc:
                document, is_error = exc.to_dict(), True
            except httpx2.HTTPError:
                document, is_error = BookflowError("E_IO", details={"operation": "mcp_result", "reason": "connection", "stage": "post_submission", "outcome": "unknown"}).to_dict(), True
            except Exception:
                document, is_error = BookflowError("E_IO", details={"operation": "mcp_result", "reason": "invalid_response", "outcome": "unknown" if submitted else "not_submitted"}).to_dict(), True
            try:
                rendered = json.dumps(document, ensure_ascii=False, allow_nan=False)
                return types.CallToolResult(content=[types.TextContent(text=rendered)], structured_content=document, is_error=is_error)
            except Exception:
                document = BookflowError("E_IO", details={"operation": "mcp_result", "reason": "serialization", "outcome": "unknown" if submitted else "not_submitted"}).to_dict()
                return types.CallToolResult(content=[types.TextContent(text=json.dumps(document))], structured_content=document, is_error=True)

        server = Server("bookflow", version="0.0.1", on_list_tools=list_tools, on_call_tool=call_tool)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


def launch(inp):
    origin = host_origin(inp.url or os.environ.get("BOOKFLOW_URL"))
    secret = os.environ.get(inp.token_env)
    if not secret or not secret.strip() or any(ch in secret for ch in "\r\n"):
        raise BookflowError("E_UNAUTHENTICATED", message="Configure the launcher's bearer environment variable.")
    try:
        import anyio
        import mcp  # noqa: F401
        import httpx2  # noqa: F401
    except ImportError:
        raise BookflowError("E_USAGE", message="Install bookflow-core[mcp] in the launcher's environment.") from None
    from .files import Directories
    with ExitStack() as resources:
        inputs = Directories(inp.input_dir)
        resources.callback(inputs.close)
        outputs = Directories(inp.output_dir)
        resources.callback(outputs.close)
        anyio.run(serve, inp, origin, secret)
