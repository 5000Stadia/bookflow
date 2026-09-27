"""The fourth surface for the identity commands: a real MCP client, over the host.

This file needs the `mcp` extra, exactly like the other MCP witnesses in this suite.
"""

import pytest

import bookflow

from tests.test_identity_commands import live, office  # noqa: F401 - fixtures
from tests import provenance


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
    binary = provenance.launcher()
    source = str(Path(__file__).resolve().parents[1] / "src")
    result = {}

    async def witness():
        params = StdioServerParameters(
            command=binary, args=["mcp", "--url", live, "--client-name", "identity-witness"],
            env=provenance.child_env(source, BOOKFLOW_TOKEN=issued["secret"], BOOKFLOW_DATA_ROOT=str(tmp_path / "absent"), PATH=os.environ.get("PATH", "")),
            cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                names = set()
                for prefix in ("user", "membership"):
                    listed = await session.call_tool("bookflow_list_commands", {"prefix": prefix})
                    assert not listed.is_error, listed
                    names |= {row["name"] for row in listed.structured_content["commands"]}
                assert {"user add", "user list", "membership grant", "membership list",
                        "membership revoke"} <= names, names

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

                # And the agent can read back what it just set up, both ways round.
                people = await run("user list", {"company": office.first})
                assert "morgan" in {row["username"] for row in people["items"]}
                assert all(row["kind"] and row["added_at"] for row in people["items"])
                held = await run("membership list", {"user": "morgan"})
                assert [(row["scope_id"], row["role"]) for row in held["items"]] == [(office.first, "standard")]
                withdrawn = await run("membership list", {"user": "morgan", "include_inactive": True})
                assert {row["scope_id"] for row in withdrawn["items"]} == {office.first, office.second}
                assert [row["active"] for row in withdrawn["items"] if row["scope_id"] == office.second] == [False]

    anyio.run(witness)

    assert result["password"], "the agent must be handed something the person can log in with"
    morgan = office.login_as("morgan", result["password"])
    assert [row["company_id"] for row in office.ok(morgan, "company.list")["items"]] == [office.first]
    events = office.admin("hub.audit.list", {"command": "user add", "limit": 5})["items"]
    assert events[0]["interface"] == "mcp" and events[0]["reason"] == "Set up the front desk workstation"


@pytest.mark.timeout(240)
def test_a_member_who_cannot_administer_learns_nothing_about_usernames_on_any_surface(office, live, tmp_path):
    """Membership administration resolves the scope and the caller's authority over it
    before it looks the target person up, so a readonly member gets one refusal for a
    real username and an invented one: over HTTP and over a real MCP client, whose shared
    preparation re-checks the target before the command runs."""
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from tests.test_identity_commands import add_jordan

    add_jordan(office, role="readonly")
    office.admin("user.add", {"username": "sam", "password": "a-long-enough-password",
                              "company": office.first, "role": "standard"})
    secret = office.admin("token.issue", {"user": "jordan", "label": "probe"})["secret"]
    bodies = {"grant": {"company": office.first, "role": "standard"},
              "grant-full": {"company": office.first, "role": "standard", "expected_version": 1,
                             "grants": ["ledger.post"], "denies": ["ledger.read"]},
              "revoke": {"company": office.first}}

    def pairs():
        for key, body in bodies.items():
            command = "membership revoke" if key == "revoke" else "membership grant"
            for name in ("sam", "nobody-at-all"):
                yield key, name, command, {"user": name, **body}

    answers = {"http": {}, "mcp": {}}
    for key, name, command, body in pairs():
        r = office.call(office.browser(), command.replace(" ", "."), body,
                        headers={"Authorization": "Bearer " + secret, "X-Bookflow-Reason": "probe"})
        answers["http"][key, name] = (r.status_code, r.json())

    async def over_mcp():
        params = StdioServerParameters(command=provenance.launcher(), args=["mcp", "--url", live],
                                       env=provenance.child_env(BOOKFLOW_TOKEN=secret,
                                                                BOOKFLOW_DATA_ROOT=str(tmp_path / "absent")),
                                       cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for key, name, command, body in pairs():
                    reply = await session.call_tool("bookflow_run", {"command": command, "input": body,
                                                                     "reason": "probe"})
                    answers["mcp"][key, name] = (reply.is_error, reply.structured_content)
    anyio.run(over_mcp)

    for surface, seen in answers.items():
        for key in bodies:
            real, invented = seen[key, "sam"], seen[key, "nobody-at-all"]
            assert real == invented, (surface, key, real, invented)
            assert real[1]["code"] == "E_PERMISSION", (surface, key, real)


def test_the_local_surface_answers_a_readonly_member_the_same_for_any_username(root):
    """The CLI and Python dispatch the same preparation locally; no host is running."""
    from tests.conftest import as_user, make_actor
    company = bookflow.connect(data_root=str(root)).company.list()["items"][0]["company_id"]
    make_actor(root, "ro-desk", company_role=(company, "readonly"))
    make_actor(root, "sam-desk", company_role=(company, "standard"))
    local = as_user(root, "ro-desk")
    for command, body in (("membership grant", {"company": company, "role": "standard"}),
                          ("membership revoke", {"company": company})):
        seen = []
        for name in ("sam-desk", "nobody-at-all"):
            with pytest.raises(bookflow.BookflowError) as refused:
                local.run(command, {"user": name, **body})
            seen.append(refused.value.to_dict())
        assert seen[0] == seen[1] and seen[0]["code"] == "E_PERMISSION", (command, seen)
