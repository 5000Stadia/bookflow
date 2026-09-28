"""R84: an authorized standard agent banks a customer payment through its own bearer token.

The blind week (notes, 2026-09-27) found `deposit sources` and `deposit post` refusing the
agent with a bare E_PERMISSION while its human could deposit. Here the agent lists the
receipts in Undeposited Funds and deposits one, over HTTP and over the real MCP adapter,
and a read-only agent is refused with the requirement that refused it named.
"""
import asyncio

from bookflow.core.config import Config
from tests import provenance
from tests.conftest import hosted_call, make_agent
from tests.test_row3_host import hosted, live  # noqa: F401


def _agent(hosted, username, role):
    principal = Config.load(hosted.root / "config.toml").user_table(hosted.login)["user_id"]
    agent = make_agent(hosted_call(hosted), username, principals=principal, company=hosted.company_id, role=role)
    issued = hosted.ok("token.issue", {"user": agent, "principal": principal, "label": username})
    return agent, issued["secret"]


def _as(hosted, secret, name, body, reason=None, **query):
    path = f"/companies/{hosted.company_id}/commands/{name}"
    if query:
        path += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    headers = {"Authorization": f"Bearer {secret}", **({"X-Bookflow-Reason": reason} if reason else {})}
    return hosted.api.post(path, json=body, headers=headers)


def _undeposited(hosted, secret):
    """A receipt the agent itself takes, so the test does not lean on the demo's own."""
    open_invoice = next(x for x in _as(hosted, secret, "invoice.query", {"limit": 200}).json()["items"]
                        if x["status"] == "posted" and x.get("settlement_current")
                        and x["settlement_current"]["due_minor_units"] >= 100)
    method = _as(hosted, secret, "payment-method.list", {}).json()["items"][0]["id"]
    paid = _as(hosted, secret, "payment.receive", {
        "customer": open_invoice["customer_id"], "date": "2026-09-25", "amount": "1.00",
        "payment_method": method, "reference": "R84-1", "operation_key": "r84-agent-payment",
        "applications": {"mode": "inline", "items": [
            {"invoice": open_invoice["id"], "expected_version": open_invoice["version"], "amount": "1.00"}]}},
        reason="Customer check for the R84 witness")
    assert paid.status_code == 200, paid.text
    return paid.json()


def test_standard_agent_lists_sources_and_posts_a_deposit_over_http_and_mcp(hosted, live, tmp_path):
    agent, secret = _agent(hosted, "deposit-agent", "standard")
    payment = _undeposited(hosted, secret)

    listed = _as(hosted, secret, "deposit.sources", {"date": "2026-09-25", "limit": 200})
    assert listed.status_code == 200, listed.text
    source = next(x for x in listed.json()["items"] if x["source"] == payment["id"])

    body = {"operation_key": "r84-agent-deposit", "document": {
        "mode": "inline", "deposit_to": "Checking", "date": "2026-09-25",
        "sources": [{"source_type": "payment", "source": payment["id"],
                     "expected_version": source["expected_version"]}]}}
    preview = _as(hosted, secret, "deposit.post", body, reason="Bank the check", dry_run="true")
    assert preview.status_code == 200, preview.text
    posted = _as(hosted, secret, "deposit.post", body, reason="Bank the check")
    assert posted.status_code == 200, posted.text
    deposit = posted.json()
    assert deposit["changed"] is True
    # The human sees what the agent banked, attributed to the agent acting for them.
    events = hosted.ok("audit.list", {"command": "deposit post"}, company=hosted.company_id)["items"]
    assert events[0]["actor_id"] == agent

    async def over_mcp():
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        params = StdioServerParameters(command=provenance.launcher(), args=["mcp", "--url", live],
            env=provenance.child_env(BOOKFLOW_TOKEN=secret, BOOKFLOW_COMPANY=hosted.company_id,
                                     BOOKFLOW_DATA_ROOT=str(tmp_path / "absent")), cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                reply = await session.call_tool("bookflow_run", {"command": "deposit sources",
                                                                 "input": {"date": "2026-09-25"}})
                assert not reply.is_error, reply.structured_content
                return reply.structured_content

    via_mcp = asyncio.run(over_mcp())
    # The receipt just banked is no longer waiting.
    assert payment["id"] not in {x["source"] for x in via_mcp["items"]}


def test_read_only_agent_refusal_names_the_requirement(hosted):
    _, secret = _agent(hosted, "deposit-reader", "readonly")
    refused = _as(hosted, secret, "deposit.post", {"operation_key": "r84-refused", "document": {
        "mode": "inline", "deposit_to": "Checking", "date": "2026-09-25", "sources": []}},
                  reason="Bank the check", dry_run="true")
    assert refused.status_code == 403, refused.text
    error = refused.json()
    assert error["code"] == "E_PERMISSION"
    assert error["details"]["capability"] == "ledger.post", error
    assert error["details"]["required_role"] == "standard", error
