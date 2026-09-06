import os
import sys
from pathlib import Path

import anyio
import pytest

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


@pytest.mark.timeout(120)
def test_installed_agent_invoice_and_directive_journal_workflow(hosted, live, tmp_path):
    """Real MCP business calls; deterministic integration witness, not blind J8."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.core import clock
    from bookflow.core.config import Config
    from bookflow.hub import schema as h
    from tests.conftest import make_actor
    from tests.test_row7_credentials import writer

    principal = Config.load(hosted.root / "config.toml").user_table(hosted.login)["user_id"]
    agent = make_actor(hosted.root, "mcp-business-agent", kind="agent", owner_user_id=principal,
                       company_role=(hosted.company_id, "owner"))
    with writer(hosted.root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent, epoch=1))
        db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent,
            principal_user_id=principal, assigned_by=principal, assigned_at=clock.now_iso()))
    issued = hosted.ok("token.issue", {"user": agent, "principal": principal, "label": "MCP business witness"})

    async def witness():
        binary = os.environ.get("BOOKFLOW_MCP_TEST_BINARY", str(Path(sys.executable).with_name("bookflow")))
        params = StdioServerParameters(command=binary,
            args=["mcp", "--url", live, "--client-name", "mcp-business-witness"],
            env={"BOOKFLOW_TOKEN": issued["secret"], "BOOKFLOW_COMPANY": hosted.company_id,
                 "BOOKFLOW_DATA_ROOT": str(tmp_path / "absent-launcher-root")}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def tool(name, arguments, error=None):
                    reply = await session.call_tool(name, arguments)
                    result = reply.structured_content
                    assert bool(reply.is_error) == bool(error), result
                    if error:
                        assert result["code"] == error, result
                    print("MCP", name, arguments.get("command", ""), error or "OK")
                    return result

                async def run(command, data, error=None, **context):
                    verb = command.split(" ")[-1]
                    defaults = {} if verb in {"list", "show", "history", "query"} else {"reason": "Record today's plumbing work"}
                    return await tool("bookflow_run", {"command": command, "input": data,
                        **defaults, **context}, error=error)

                discovered = await tool("bookflow_list_commands", {"prefix": "invoice"})
                assert "invoice post" in {x["name"] for x in discovered["commands"]}
                for command in ("invoice post", "invoice update", "journal post", "directive add"):
                    help_ = await tool("bookflow_help", {"command": command})
                    assert help_["input_schema"]["type"] == "object"
                income = await run("account create", {"name": "MCP labor income", "type": "income"})
                bank = await run("account create", {"name": "MCP operating bank", "type": "bank"})
                customer = await run("customer create", {"name": "MCP plumbing customer"})
                codes = await run("sales-tax-code list", {})
                exempt = next(x["id"] for x in codes["items"] if not x["taxable"])
                item = await run("item create", {"name": "MCP plumbing labor", "type": "service",
                    "description": "Plumbing labor",
                    "sales_enabled": True, "income_account_id": income["id"], "price": "12.34",
                    "sales_tax_code_id": exempt})
                payload = {"date": "2026-01-12", "customer": customer["id"], "memo": "Agent invoice",
                           "lines": [{"item": item["id"], "quantity": ".5"}]}
                preview = await run("invoice post", payload, dry_run=True)
                assert preview["total_minor_units"] == 617
                assert (await run("customer show", {"customer": customer["id"]}))["current_balance"]["minor_units"] == 0
                payload["expected_facts_fingerprint"] = preview["facts_fingerprint"]
                invoice = await run("invoice post", payload, idempotency_key="mcp-invoice-witness")
                assert invoice["version"] == 1 and invoice["total_minor_units"] == 617
                assert (await run("invoice post", payload, idempotency_key="mcp-invoice-witness"))["id"] == invoice["id"]
                change = {"invoice": invoice["id"], "expected_version": 1, "memo": "Agent continued"}
                change["expected_facts_fingerprint"] = (await run("invoice update", change, dry_run=True))["facts_fingerprint"]
                updated = await run("invoice update", change)
                assert updated["version"] == 2
                await run("invoice update", {"invoice": invoice["id"], "expected_version": 1,
                    "memo": "stale"}, error="E_VERSION_CONFLICT")
                current = await run("invoice show", {"invoice": invoice["id"]})
                assert current["version"] == 2 and current["revision"]["memo"] == "Agent continued"

                directive = await run("directive add", {"text": "Record today's plumbing receipts and balanced bank entry."})
                code = directive["directive"]["code"]
                journal_input = {"date": "2026-01-12", "lines": [
                    {"account": bank["id"], "side": "debit", "amount": "12.34"},
                    {"account": income["id"], "side": "credit", "amount": "12.34"}]}
                journal = await run("journal post", journal_input, directive=code, reason=None,
                                    idempotency_key="mcp-journal-witness")
                assert journal["total_minor_units"] == 1234
                assert (await run("journal show", {"journal": journal["id"]}))["version"] == 1
                bad = {**journal_input, "lines": [journal_input["lines"][0],
                    {**journal_input["lines"][1], "amount": "10.00"}]}
                await run("journal post", bad, directive=code, reason=None, error="E_UNBALANCED_ENTRY")
                events = await run("audit list", {"record_type": "transaction", "record_id": journal["id"]})
                assert len(events["items"]) == 1
                event = events["items"][0]
                assert event["interface"] == "mcp" and event["actor_id"] == agent
                assert event["on_behalf_of"] == principal and event["directive_code"] == code
                assert event["client_name"] == "mcp-business-witness"
                print("VERIFIED invoice", invoice["id"], "version", current["version"], "total", current["total_minor_units"])
                print("VERIFIED journal", journal["id"], "total", journal["total_minor_units"], "directive", code)
                return journal["id"]

    journal = anyio.run(witness)
    observed = hosted.ok("journal.show", {"journal": journal}, company=hosted.company_id)
    assert observed["total_minor_units"] == 1234
    assert not (tmp_path / "absent-launcher-root").exists()
