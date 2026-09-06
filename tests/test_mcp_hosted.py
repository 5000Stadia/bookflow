import os
import sys
from pathlib import Path

import anyio

from tests.test_row3_host import hosted, live


def test_real_stdio_discovery_help_and_attributed_host_write(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def witness():
        binary = os.environ.get("BOOKFLOW_MCP_TEST_BINARY", str(Path(sys.executable).with_name("bookflow")))
        params = StdioServerParameters(command=binary,
            args=["mcp", "--url", live, "--client-name", "mcp-installed-witness"],
            env={"BOOKFLOW_TOKEN": hosted.secret, "BOOKFLOW_COMPANY": hosted.company_id,
                 "BOOKFLOW_DATA_ROOT": str(tmp_path / "never-created")}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.is_error, result
                    return result.structured_content

                catalog = await call("bookflow_list_commands", {"prefix": "account"})
                assert "account list" in {item["name"] for item in catalog["commands"]}
                help_ = await call("bookflow_help", {"command": "account list"})
                assert help_["input_schema"]["type"] == "object"
                assert "account list" in help_["documentation"]
                accounts = await call("bookflow_run", {"command": "account list", "input": {}, "dry_run": False})
                assert accounts["count"] == len(accounts["items"]) > 0
                changed = await call("bookflow_run", {"command": "company update", "input": {"fax": "MCP-witness"}, "reason": "Test installed MCP handoff"})
                assert changed["version"] > 1
        assert not (tmp_path / "never-created").exists()

    anyio.run(witness)
    assert hosted.info()["info"]["fax"] == "MCP-witness"
    events = hosted.ok("audit.list", {"command": "company update"}, company=hosted.company_id)["items"]
    event = next(item for item in events if item["client_name"] == "mcp-installed-witness")
    assert event["interface"] == "mcp"
    assert event["session_id"] != hosted.token
