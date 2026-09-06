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


async def serve(inp, origin, secret, inputs, outputs):
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
        from .client import Client
        transport = Client(client, inputs, outputs)
        async def list_tools(_ctx, _params):
            tools = []
            for name, (model, description) in TOOLS.items():
                schema = tool_schema(name)
                tools.append(types.Tool(name=name, description=description, input_schema=schema))
            return types.ListToolsResult(tools=tools)

        async def call_tool(_ctx, params):
            submitted = False
            delivery = None
            reference = None
            stage = "preflight"

            def error_document(exc, *, adapter_failure=False):
                # A failed observation cannot establish that an earlier intent
                # never executed. Keep that identity through every local failure.
                known = reference or (delivery or {}).get("operation_ref")
                if known is not None or adapter_failure:
                    exc.details.setdefault("stage", stage)
                if known is not None:
                    from .responses import annotate
                    exc.details["outcome"] = "unknown"
                    annotate(exc, known, submitted=True)
                return exc.to_dict()

            try:
                raw_arguments = params.arguments or {}
                if params.name == "bookflow_run" and isinstance(raw_arguments, dict):
                    from .envelopes import intent_reference
                    supplied = {raw_arguments[key] for key in ("operation_ref", "input_ref")
                                if isinstance(raw_arguments.get(key), str)}
                    if len(supplied) == 1:
                        try:
                            reference = intent_reference(next(iter(supplied)))
                        except BookflowError:
                            pass  # Never echo an invalid reference-shaped value.
                arguments = validate(params.name, raw_arguments)
                from .envelopes import RecoveryArguments
                if isinstance(arguments, RecoveryArguments):
                    reference = arguments.operation_ref or arguments.input_ref

                preflight = await client.get("/adapters/mcp")
                if preflight.status_code == 404 or preflight.headers.get("x-bookflow-mcp-version") != str(BRIDGE_VERSION):
                    raise BookflowError("E_VERSION_MISMATCH", details={"supported_bridge_versions": [BRIDGE_VERSION], "received_bridge_version": preflight.headers.get("x-bookflow-mcp-version"), "stage": "preflight", "outcome": "not_submitted"})
                if preflight.status_code >= 400:
                    transport.document(preflight)
                payload = {"arguments": arguments.model_dump(exclude_unset=True)}
                from .envelopes import RunArguments
                metadata = selection = None
                if isinstance(arguments, RunArguments):
                    help_response = await client.post("/adapters/mcp/bookflow_help", json={"arguments": {"command": arguments.command}})
                    metadata = help_response.json()
                    if help_response.status_code >= 400:
                        raise BookflowError(metadata["code"], message=metadata["message"], details=metadata["details"])
                    from .selection import company_selection
                    company, source = company_selection(metadata["scope"], arguments.company, selection_root=inp.selection_root, command_name=arguments.command)
                    payload["company_selection"] = {"value": company, "source": source}
                    selection = payload["company_selection"]
                submitted = params.name == "bookflow_run"
                stage = "post_submission"
                if submitted:
                    document, is_error, delivery = await transport.run(arguments, metadata=metadata, selection=selection)
                else:
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
                document, is_error = error_document(exc), True
            except httpx2.HTTPError:
                document, is_error = error_document(BookflowError("E_IO", details={"operation": "mcp_result", "reason": "connection", "outcome": "unknown" if submitted else "not_submitted"}), adapter_failure=True), True
            except Exception:
                document, is_error = error_document(BookflowError("E_IO", details={"operation": "mcp_result", "reason": "invalid_response", "outcome": "unknown" if submitted else "not_submitted"}), adapter_failure=True), True
            try:
                rendered = json.dumps(document, ensure_ascii=False, allow_nan=False)
                content = [types.TextContent(text=rendered)]
                if delivery and delivery.get("output_file"):
                    content.append(types.TextContent(text="Verified downloaded file: " + delivery["output_file"]))
                return types.CallToolResult(content=content, structured_content=document, is_error=is_error,
                                            meta={"bookflow_transport": delivery} if delivery else None)
            except Exception:
                document = error_document(BookflowError("E_IO", details={"operation": "mcp_result", "reason": "serialization", "outcome": "unknown" if submitted else "not_submitted"}), adapter_failure=True)
                return types.CallToolResult(content=[types.TextContent(text=json.dumps(document))], structured_content=document, is_error=True)

        server = Server("bookflow", version="0.0.1", on_list_tools=list_tools, on_call_tool=call_tool)
        from .stdio import eof_fenced_streams
        async with stdio_server() as (read, write):
            read, write = eof_fenced_streams(read, write)
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
        anyio.run(serve, inp, origin, secret, inputs, outputs)
