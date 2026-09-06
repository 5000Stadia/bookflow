"""Root-independent MCP launch contract; SDK imports happen only on invocation."""

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.registry import command


class McpInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: str | None = Field(None, description="Existing Bookflow host origin; otherwise BOOKFLOW_URL")
    token_env: str = Field("BOOKFLOW_TOKEN", min_length=1, description="Environment variable containing the bearer; never the bearer itself")
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
