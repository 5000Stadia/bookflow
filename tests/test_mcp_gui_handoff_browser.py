"""Actual SDK MCP agent / human Chrome / MCP continuation on shared books."""
import json
import os
from pathlib import Path
from uuid import uuid4

import sys
from contextlib import ExitStack
from anyio.from_thread import start_blocking_portal
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest

from bookflow.core import clock
from bookflow.core.config import Config
from bookflow.hub import schema as h
from tests.conftest import make_actor
from tests.test_mcp_registry_work import company_snapshot
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row7_credentials import writer
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _click, _contained, _fill, _preview, _saved

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome unavailable")


@pytest.mark.parametrize("width", [1280, 390])
def test_mcp_invoice_human_correction_mcp_continuation(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900 if width == 1280 else 844)
    root = Path(os.environ["BOOKFLOW_DATA_ROOT"])
    principal = Config.load(root / "config.toml").user_table(env.site.login)["user_id"]
    # Fixture setup stands in for the still-pending user/assignment administration.
    # Every business operation below uses the real authenticated host or browser.
    agent = make_actor(root, "handoff-agent", kind="agent", owner_user_id=principal,
                       company_role=(env.site.company_id, "owner"))
    with writer(root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent, epoch=1))
        db.conn.execute(h.agent_principals.insert().values(
            agent_user_id=agent, principal_user_id=principal, assigned_by=principal,
            assigned_at=clock.now_iso()))
    issuance = b.evaluate(f'''fetch('/commands/token.issue', {{method:'POST',
        credentials:'same-origin', headers:{{'Content-Type':'application/json',
        'X-Bookflow-Workbench':'1'}}, body:JSON.stringify({json.dumps(dict(
            user=agent, principal=principal, label="Disposable handoff witness"))})
        }}).then(async r=>({{status:r.status, body:await r.json()}}))''', await_promise=True)
    assert issuance["status"] == 200, issuance["body"]
    issued = issuance["body"]
    human = lambda name, data: _command(b, env.site, name, data)
    income = human("account.create", dict(name="Handoff income", type="income"))["id"]
    customer = human("customer.create", dict(name="Handoff customer"))["id"]
    exempt = next(x["id"] for x in human("sales-tax-code.list", {})["items"] if not x["taxable"])
    item = human("item.create", dict(name="Handoff service", type="service", sales_enabled=True,
        description="Service labor", income_account_id=income, price="12.34", sales_tax_code_id=exempt))["id"]
    binary = os.environ.get("BOOKFLOW_MCP_TEST_BINARY", str(Path(sys.executable).with_name("bookflow")))
    params = StdioServerParameters(command=binary,
        args=["mcp", "--url", env.site.base_url, "--client-name", "handoff-mcp-agent"],
        env={"BOOKFLOW_TOKEN": issued["secret"], "BOOKFLOW_COMPANY": env.site.company_id,
             "BOOKFLOW_DATA_ROOT": str(tmp_path / "absent-caller-root")}, cwd=str(tmp_path))
    with ExitStack() as stack:
        portal = stack.enter_context(start_blocking_portal())
        read, write = stack.enter_context(portal.wrap_async_context_manager(stdio_client(params)))
        session = stack.enter_context(portal.wrap_async_context_manager(ClientSession(read, write)))
        portal.call(session.discover)

        def call(name, data, **kwargs):
            command, _, query = name.partition("?")
            arguments = {"command": command.replace(".", " "), "input": data}
            if command in {"invoice.post", "invoice.update"}:
                arguments["reason"] = "Continue the shared invoice"
            if query:
                assert query == "dry_run=1"
                arguments["dry_run"] = True
            headers = kwargs.get("headers", {})
            if "Idempotency-Key" in headers:
                arguments["idempotency_key"] = headers["Idempotency-Key"]
            reply = portal.call(session.call_tool, "bookflow_run", arguments)
            assert not reply.is_error, reply
            return reply.structured_content

        payload = dict(date="2026-01-12", customer=customer, memo="Prepared by agent",
                       lines=[dict(item=item, quantity=".5")])
        preview = call("invoice.post?dry_run=1", payload)
        assert preview["total_minor_units"] == 617
        assert human("customer.show", dict(customer=customer))["current_balance"]["minor_units"] == 0
        payload["expected_facts_fingerprint"] = preview["facts_fingerprint"]
        key = str(uuid4())
        posted = call("invoice.post", payload, headers={"Idempotency-Key": key})
        invoice = posted["id"]
        # Posting consumed the captured automatic number. A fresh intent with
        # that old fingerprint must not silently recompute facts or post again.
        before_stale = company_snapshot(root)
        stale_facts = portal.call(session.call_tool, "bookflow_run", {
            "command": "invoice post", "input": payload,
            "reason": "Continue the shared invoice"})
        assert stale_facts.is_error and stale_facts.structured_content["code"] == "E_PREVIEW_STALE"
        assert payload["expected_facts_fingerprint"] == preview["facts_fingerprint"]
        assert company_snapshot(root) == before_stale
        base = f"{env.site.base_url}/c/{env.site.company_id}/invoice/{invoice}"
        b.navigate(base)
        b.wait_for('!!document.querySelector(".sales-document")')
        assert "Prepared by agent" in b.evaluate('document.querySelector(".sales-document").innerText')
        assert "6.17" in b.evaluate('document.querySelector(".sales-document").innerText')
        _contained(b, width)
        b.navigate(base + "/update")
        b.wait_for('!!document.querySelector("[data-sales-form]")')
        _fill(b, "c:lines:0:quantity", "1.5")
        _fill(b, "f:memo", "Corrected by human")
        _fill(b, "ctx:reason", "Correct invoice quantity after agent preparation")
        _preview(b)
        _click(b, "submit")
        assert _saved(b, "invoice") == invoice
        corrected = call("invoice.show", dict(invoice=invoice))
        assert corrected["version"] == 2 and corrected["total_minor_units"] == 1851
        assert corrected["revision"]["lines"][0]["line_id"] == posted["revision"]["lines"][0]["line_id"]
        rejected = portal.call(session.call_tool, "bookflow_run", {
            "command": "invoice update", "input": dict(invoice=invoice, expected_version=1, memo="Stale agent draft"),
            "reason": "Continue the shared invoice"})
        assert rejected.is_error and rejected.structured_content["code"] == "E_VERSION_CONFLICT"
        assert call("invoice.show", dict(invoice=invoice)) == corrected
        # A retry of creation must not recreate or overwrite the human's correction.
        assert call("invoice.post", payload, headers={"Idempotency-Key": key})["id"] == invoice
        assert call("invoice.show", dict(invoice=invoice)) == corrected
        update = dict(invoice=invoice, expected_version=2, memo="Continued by agent")
        update["expected_facts_fingerprint"] = call("invoice.update?dry_run=1", update)["facts_fingerprint"]
        continued = call("invoice.update", update)
        assert continued["version"] == 3 and continued["total_minor_units"] == 1851
        b.navigate(base)
        b.wait_for('document.querySelector(".sales-document")?.innerText.includes("Continued by agent")')
        _contained(b, width)
        events = human("audit.list", dict(record_type="transaction", record_id=invoice))["items"]
        assert len(events) == 3
        assert [e["actor_id"] for e in events] == [agent, principal, agent]
        # The workbench uses HTTP; client_name distinguishes its browser actions.
        assert [e["interface"] for e in events] == ["mcp", "http", "mcp"]
        assert [e["client_name"] for e in events] == ["handoff-mcp-agent", "bookflow-workbench", "handoff-mcp-agent"]
        assert [e["on_behalf_of"] for e in events] == [principal, None, principal]
        assert [e["actor_kind"] for e in events] == ["agent", "human", "agent"]
        assert all(e["reason"] == "Continue the shared invoice" for e in (events[0], events[2]))
        history = call("invoice.history", dict(invoice=invoice))["items"]
        assert [r["revision_number"] for r in history] == [1, 2, 3]
        assert human("customer.show", dict(customer=customer))["current_balance"]["minor_units"] == 1851
