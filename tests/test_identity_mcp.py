"""The fourth surface for the identity commands: a real MCP client, over the host.

This file needs the `mcp` extra, exactly like the other MCP witnesses in this suite.
"""

import pytest

from tests.test_identity_commands import live, office  # noqa: F401 - fixtures


@pytest.mark.timeout(180)
def test_an_agent_over_mcp_can_set_a_workstation_up_end_to_end(office, live, tmp_path):
    """The fourth surface, doing the real thing: an agent adds the person, and that
    person logs in to the browser with the password the agent was handed."""
    import os
    import sys
    from pathlib import Path

    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    issued = office.admin("token.issue", {"label": "setup agent"})
    binary = str(Path(sys.executable).with_name("bookflow"))
    source = str(Path(__file__).resolve().parents[1] / "src")
    result = {}

    async def witness():
        params = StdioServerParameters(
            command=binary, args=["mcp", "--url", live, "--client-name", "identity-witness"],
            env={"BOOKFLOW_TOKEN": issued["secret"], "PYTHONPATH": source,
                 "BOOKFLOW_DATA_ROOT": str(tmp_path / "absent"), "PATH": os.environ.get("PATH", "")},
            cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                names = set()
                for prefix in ("user", "membership"):
                    listed = await session.call_tool("bookflow_list_commands", {"prefix": prefix})
                    assert not listed.is_error, listed
                    names |= {row["name"] for row in listed.structured_content["commands"]}
                assert {"user add", "membership grant", "membership revoke"} <= names, names

                helped = await session.call_tool("bookflow_help", {"command": "membership grant"})
                assert not helped.is_error and "role" in str(helped.structured_content)

                async def run(command, data, **options):
                    reply = await session.call_tool("bookflow_run",
                                                    {"command": command, "input": data, **options})
                    assert not reply.is_error, reply
                    return reply.structured_content

                preview = await run("user add", {"username": "morgan", "company": office.first},
                                    dry_run=True, reason="Preview the new workstation")
                assert preview["dry_run"] and preview["password"] is None
                added = await run("user add", {"username": "morgan", "display_name": "Morgan Ellis",
                                               "company": office.first, "role": "standard"},
                                  reason="Set up the front desk workstation")
                result.update(added)
                granted = await run("membership grant", {"user": "morgan", "company": office.second,
                                                         "role": "readonly"},
                                    reason="Let the front desk read the other book")
                assert granted["role"] == "readonly" and granted["changed"]
                revoked = await run("membership revoke", {"user": "morgan", "company": office.second},
                                    reason="Front desk no longer needs the other book")
                assert revoked["changed"] and revoked["revoked_at"]

    anyio.run(witness)

    assert result["password"], "the agent must be handed something the person can log in with"
    morgan = office.login_as("morgan", result["password"])
    assert [row["company_id"] for row in office.ok(morgan, "company.list")["items"]] == [office.first]
    events = office.admin("hub.audit.list", {"command": "user add", "limit": 5})["items"]
    assert events[0]["interface"] == "mcp" and events[0]["reason"] == "Set up the front desk workstation"
