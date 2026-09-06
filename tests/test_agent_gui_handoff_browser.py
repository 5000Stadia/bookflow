"""Actual bearer-agent / human-browser handoffs on disposable company books."""
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from bookflow.core import clock
from bookflow.core.config import Config
from bookflow.hub import schema as h
from tests.conftest import make_actor
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row7_credentials import writer
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _click, _contained, _fill, _preview, _saved

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome unavailable")


@pytest.mark.parametrize("width", [1280, 390])
def test_agent_invoice_human_correction_agent_continuation(register_browser, width):
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
    prefix = f"/companies/{env.site.company_id}/commands/"
    with httpx.Client(base_url=env.site.base_url, trust_env=False, timeout=15,
                      headers={"Authorization": f"Bearer {issued['secret']}",
                               "X-Bookflow-Client-Name": "handoff-agent"}) as api:
        def call(name, data, **kwargs):
            if name.partition("?")[0] in {"invoice.post", "invoice.update"}:
                kwargs["headers"] = {
                    "X-Bookflow-Reason": "Continue the shared invoice",
                    **kwargs.get("headers", {}),
                }
            response = api.post(prefix + name, json=data, **kwargs)
            assert response.status_code == 200, response.text
            return response.json()

        payload = dict(date="2026-01-12", customer=customer, memo="Prepared by agent",
                       lines=[dict(item=item, quantity=".5")])
        preview = call("invoice.post?dry_run=1", payload)
        assert preview["total_minor_units"] == 617
        assert human("customer.show", dict(customer=customer))["current_balance"]["minor_units"] == 0
        payload["expected_facts_fingerprint"] = preview["facts_fingerprint"]
        key = str(uuid4())
        posted = call("invoice.post", payload, headers={"Idempotency-Key": key})
        invoice = posted["id"]
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
        _preview(b)
        _click(b, "submit")
        assert _saved(b, "invoice") == invoice
        corrected = call("invoice.show", dict(invoice=invoice))
        assert corrected["version"] == 2 and corrected["total_minor_units"] == 1851
        assert corrected["revision"]["lines"][0]["line_id"] == posted["revision"]["lines"][0]["line_id"]
        rejected = api.post(prefix + "invoice.update", json=dict(
            invoice=invoice, expected_version=1, memo="Stale agent draft"),
            headers={"X-Bookflow-Reason": "Continue the shared invoice"})
        assert rejected.status_code == 409 and rejected.json()["code"] == "E_VERSION_CONFLICT"
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
        assert [e["interface"] for e in events] == ["http", "http", "http"]
        assert [e["client_name"] for e in events] == ["handoff-agent", "bookflow-workbench", "handoff-agent"]
        assert [e["on_behalf_of"] for e in events] == [principal, None, principal]
        assert [e["actor_kind"] for e in events] == ["agent", "human", "agent"]
        assert all(e["reason"] == "Continue the shared invoice" for e in (events[0], events[2]))
        history = call("invoice.history", dict(invoice=invoice))["items"]
        assert [r["revision_number"] for r in history] == [1, 2, 3]
        assert human("customer.show", dict(customer=customer))["current_balance"]["minor_units"] == 1851
