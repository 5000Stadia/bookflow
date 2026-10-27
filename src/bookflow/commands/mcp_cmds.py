"""Root-independent MCP launch contract; SDK imports happen only on invocation."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.registry import command


class McpInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: str | None = Field(None, description="Existing Bookflow host origin; otherwise BOOKFLOW_URL")
    token_env: str = Field("BOOKFLOW_TOKEN", min_length=1, description="Environment variable containing the bearer; never the bearer itself")
    token_file: str | None = Field(None, description="Owner-only file (mode 0600) holding the bearer, as `bookflow setup` writes it; used instead of the token_env variable when both are present")
    label: str = Field("bookflow-agent", min_length=1, max_length=120, description="Informational client label recorded with MCP activity", json_schema_extra={"cli_flag": "client-name"})
    selection_root: str | None = Field(None, description="Read-only calling-machine configuration root for company fallback")
    input_dir: list[str] = Field(default_factory=list, description="Allowed calling-machine business input directory; repeatable", json_schema_extra={"cli_repeatable": True})
    output_dir: list[str] = Field(default_factory=list, description="Allowed calling-machine business output directory; repeatable", json_schema_extra={"cli_repeatable": True})


class McpExit(BaseModel):
    """No document is emitted when the protocol runner closes."""


def run_mcp(_cmd, inp, _ctx):
    from bookflow.adapters.mcp.launcher import launch
    launch(inp)
    return {}


def _unused(*_args):
    raise NotImplementedError("standalone MCP runner")


mcp = command(
    "mcp", scope="hub", description="Expose Bookflow commands through three MCP tools over stdio.",
    input_model=McpInput, output_model=McpExit, bootstrap=True, local_only=True,
    standalone_runner=run_mcp, protocol_stdout=True,
)(_unused)


# ---------------------------------------------------------------- `agent run` (R183)

class AgentRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command: str = Field(min_length=1, max_length=200, description="The Bookflow command to run as the agent, e.g. `company show` or `invoice post`")
    input: dict[str, Any] = Field(default_factory=dict, description="The command's input as one JSON object, exactly as `bookflow_run` takes it")
    company: str | None = Field(None, description="Company id or name; defaults to the company the MCP configuration names (BOOKFLOW_COMPANY)")
    because: str | None = Field(None, max_length=140, description="Short audit reason; every write needs one", json_schema_extra={"cli_flag": "reason"})
    preview: bool = Field(False, description="Preview the write and save nothing (a dry run)", json_schema_extra={"cli_flag": "dry-run"})
    mcp_config: str = Field(".mcp.json", min_length=1, description="The MCP client configuration `bookflow setup` wrote; its `bookflow` server names the host, the token file and the company")
    url: str | None = Field(None, description="Host origin; overrides the configuration's --url")
    token_file: str | None = Field(None, description="Owner-only token file; overrides the configuration's --token-file")


class AgentRunOutput(BaseModel):
    """The command's own result document, as `bookflow_run` returns it."""
    model_config = ConfigDict(extra="allow")


def _configured(path: str) -> tuple[dict[str, str], str | None]:
    """The `bookflow` server's --url, --token-file and --client-name, and its BOOKFLOW_COMPANY."""
    import json
    from pathlib import Path
    from bookflow.core.errors import BookflowError
    file = Path(path).expanduser()
    try:
        server = json.loads(file.read_text(encoding="utf-8"))["mcpServers"]["bookflow"]
        args, env = list(server.get("args") or []), dict(server.get("env") or {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BookflowError("E_USAGE", message=(
            f"No `bookflow` server in {file}. Run this in the folder `bookflow setup` wrote .mcp.json into, "
            "or pass --mcp-config, or --url and --token-file.")) from None
    found = {}
    for flag in ("--url", "--token-file", "--client-name"):
        if flag in args and args.index(flag) + 1 < len(args):
            found[flag] = args[args.index(flag) + 1]
    return found, env.get("BOOKFLOW_COMPANY")


def run_agent(_cmd, inp, ctx):
    """One `bookflow_run` call through the same launcher an MCP client starts: attributed as the agent."""
    import json
    import os
    from types import SimpleNamespace
    from bookflow.core.errors import BookflowError
    from bookflow.adapters.mcp.launcher import host_origin, read_token_file
    configured, company = ({}, None) if (inp.url and inp.token_file) else _configured(inp.mcp_config)
    origin = host_origin(inp.url or configured.get("--url"))
    token_file = inp.token_file or configured.get("--token-file")
    if not token_file:
        raise BookflowError("E_UNAUTHENTICATED", message="No token file: pass --token-file, or --mcp-config naming the one setup wrote.")
    secret = read_token_file(os.path.expanduser(token_file))
    if not secret or any(ch in secret for ch in "\r\n"):
        raise BookflowError("E_UNAUTHENTICATED", message="The token file holds no usable token.")
    arguments = {"command": inp.command, "input": inp.input}
    if inp.company or company:
        arguments["company"] = inp.company or company
    for key, value in (("reason", inp.because or ctx.reason), ("source_ref", ctx.source_ref),
                       ("directive", ctx.directive_id), ("idempotency_key", ctx.idempotency_key)):
        if value:
            arguments[key] = value
    if inp.preview:
        arguments["dry_run"] = True
    import anyio
    from contextlib import ExitStack
    from bookflow.adapters.mcp.launcher import serve
    from bookflow.adapters.mcp.files import Directories
    launcher = SimpleNamespace(label=configured.get("--client-name") or "bookflow-agent", selection_root=None)
    with ExitStack() as resources:
        inputs = Directories([], flag="--input-dir")
        resources.callback(inputs.close)
        outputs = Directories([], flag="--output-dir")
        resources.callback(outputs.close)
        result = anyio.run(serve, launcher, origin, secret, inputs, outputs, ("bookflow_run", arguments))
    document = result.structured_content
    if document is None:
        document = json.loads(result.content[0].text)
    if result.is_error:
        raise BookflowError(document.get("code", "E_IO"), message=document.get("message"), details=document.get("details") or {})
    return document


agent_run = command(
    "agent run", scope="hub",
    description=("Run one Bookflow command as the AI agent `bookflow setup` made, without an MCP client: it reads the "
                 "MCP configuration setup wrote (host, token file, company) and makes the same `bookflow_run` call a "
                 "connected client makes, so the books record it exactly as an MCP call: the agent, working for the "
                 "owner. For an agent whose MCP client has not reloaded .mcp.json yet. Writes need --reason."),
    input_model=AgentRunInput, output_model=AgentRunOutput, positional=["command"], bootstrap=True, local_only=True,
    standalone_runner=run_agent,
)(_unused)
