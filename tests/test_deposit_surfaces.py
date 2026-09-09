"""One deposit contract, four places a person or an agent reaches it.

The MCP leg of this parity lives with its family in
``tests/test_mcp_registry_deposits.py``, which needs the optional ``mcp`` extra.
What is here runs on the plain checkout: the library, the packaged CLI, the HTTP
host, and the workbench page the home window sends a click to.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

import bookflow
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

COMPANY = "Demo Plumbing Co"


@pytest.fixture
def undeposited(root):
    """Two receipts of this test's own, waiting in Undeposited Funds."""
    client = bookflow.connect(data_root=str(root))
    accounts = {row["name"]: row for row in client.run("account list", {}, company=COMPANY)["items"]}
    uf = next(row["id"] for row in accounts.values() if row["system_role"] == "undeposited_funds")
    income = client.account.create(name="Surface parity income", type="income", company=COMPANY)["id"]
    bank = client.account.create(name="Surface parity bank", type="bank", company=COMPANY)["id"]
    customer = client.customer.create(name="Surface Parity Customer", company=COMPANY)["id"]
    code = next(row["id"] for row in client.run("sales-tax-code list", {}, company=COMPANY)["items"]
                if not row["taxable"])
    item = client.run("item create", dict(name="Surface parity service", type="service", sales_enabled=True,
        description="Service labor", income_account_id=income, price="100.00", sales_tax_code_id=code),
        company=COMPANY)["id"]
    method = next(row["id"] for row in client.run("payment-method list", {}, company=COMPANY)["items"]
                  if row["kind"] == "cash")
    invoice = client.run("invoice post", dict(customer=customer, date="2026-06-01",
        lines=[dict(item=item, quantity="1", net_amount="100")]), company=COMPANY, reason="bill the call")
    payment = client.run("payment receive", dict(customer=customer, date="2026-06-02", amount="100",
        payment_method=method, operation_key="surface-parity-cash", applications=dict(mode="inline",
            items=[dict(invoice=invoice["id"], expected_version=1, amount="100")])),
        company=COMPANY, reason="customer paid in cash")
    sale = client.run("sales-receipt post", dict(customer=customer, deposit_to=uf, payment_method=method,
        date="2026-06-02", lines=[dict(item=item, quantity="1", unit_price="60")]),
        company=COMPANY, reason="counter sale")
    return dict(client=client, bank=bank, uf=uf, payment=payment["id"], sale=sale["id"])


def test_the_same_deposit_reads_and_writes_on_every_surface(root, cli, undeposited):
    """The host takes the data-root lock for its whole run, so the local surfaces go first."""
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from tests.test_row3_host import Hosted

    request = dict(date="2026-06-03", limit=200)
    library = undeposited["client"].run("deposit sources", request, company=COMPANY)
    over_cli = cli.json("--company", COMPANY, "deposit", "sources", "--date", "2026-06-03", "--limit", "200")
    assert library == over_cli, "one command, one answer"

    mine = [row for row in library["items"] if row["source"] in (undeposited["payment"], undeposited["sale"])]
    assert {row["amount"]["minor_units"] for row in mine} == {10000, 6000}
    document = dict(mode="inline", deposit_to=undeposited["bank"], date="2026-06-03",
        memo="Saturday receipts", sources=[dict(source_type=row["source_type"], source=row["source"],
                                                expected_version=row["expected_version"]) for row in mine])

    issued = undeposited["client"].token.issue(label="surface parity")
    company_id = undeposited["client"].company.list()["items"][0]["company_id"]
    handle = start_serving(root, client_version(), bind="127.0.0.1:8765", secure_cookies=False)
    try:
        hosted = Hosted(handle, root, "", company_id, issued, "", {})
        assert hosted.ok("deposit.sources", request, company=company_id) == library

        # Preview and commit over HTTP, exactly as the workbench form does.
        preview = hosted.api.post(f"/companies/{company_id}/commands/deposit.post",
            json=dict(operation_key="surface-parity-deposit", document=document),
            params={"dry_run": "true"},
            headers={**hosted.bearer, "X-Bookflow-reason": "bank Saturday receipts"})
        assert preview.status_code == 200, preview.text
        assert preview.json()["dry_run"] and preview.json()["deposit"]["bank_total"]["minor_units"] == 16000

        posted = hosted.api.post(f"/companies/{company_id}/commands/deposit.post",
            json=dict(operation_key="surface-parity-deposit", document=document,
                      expected_facts_fingerprint=preview.json()["facts_fingerprint"]),
            headers={**hosted.bearer, "X-Bookflow-reason": "bank Saturday receipts"})
        assert posted.status_code == 200, posted.text
        banked = posted.json()
        assert banked["deposit"]["bank_total"]["minor_units"] == 16000
        assert sorted(banked["deposit"]["banked_receipt_ids"]) == sorted(
            [undeposited["payment"], undeposited["sale"]])
    finally:
        handle.stop()

    # What the CLI and the library see afterwards is what HTTP wrote.
    after = cli.json("--company", COMPANY, "deposit", "sources", "--date", "2026-06-03", "--limit", "200")
    assert not [row for row in after["items"] if row["source"] in (undeposited["payment"], undeposited["sale"])]
    assert after == undeposited["client"].run("deposit sources", request, company=COMPANY)

    # The CLI writes the same command too, through its own generated flags.
    if after["items"]:
        remaining = json.dumps([dict(source_type=row["source_type"], source=row["source"],
                                     expected_version=row["expected_version"]) for row in after["items"][:1]])
        made = cli.json("--company", COMPANY, "deposit", "post", "--operation-key", "surface-parity-cli",
                        "--document-mode", "inline", "--document-deposit-to", undeposited["bank"],
                        "--document-date", "2026-06-03", "--document-sources", remaining,
                        "--reason", "bank the rest")
        assert made["deposit"]["bank_total"]["minor_units"] == after["items"][0]["amount"]["minor_units"]


def test_the_workbench_offers_a_deposit_form_where_the_home_window_points(hosted):  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    page = browser.get(f"/c/{hosted.company_id}/deposit/post", follow_redirects=False)
    assert page.status_code == 200, page.text[:400]
    body = page.text.split("<main>", 1)[-1].split("</main>", 1)[0]
    assert 'class="error"' not in body and not re.search(r"\bE_[A-Z_]+\b", body)
    assert "<form" in body
    for field in ("document.deposit_to", "document.date", "document.sources", "operation_key"):
        assert field in body, field
