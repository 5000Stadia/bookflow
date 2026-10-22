"""R148 in the browser: the generic form for `customer-credit transfer`, reached from Customers.

The form is the command's own (one field per input), so what it submits is exactly what the other
surfaces take. Both jobs' balances are read back through the commands afterwards.
"""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def test_the_credit_transfer_form_moves_the_credit_and_the_void_form_takes_it_back(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    base = f"/companies/{hosted.company_id}/commands"

    def run(name, body):
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, "X-Bookflow-Reason": "transfer witness"})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    def read(name, body):
        return browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers=HEADERS).json()

    bank = run("account create", {"name": "Transfer Checking", "type": "bank"})["id"]
    income = run("account create", {"name": "Transfer Sales", "type": "income"})["id"]
    exempt = next(row["id"] for row in browser.post(f"{base}/sales-tax-code.list", json={}, headers=HEADERS).json()["items"] if not row["taxable"])
    item = run("item create", {"name": "Transfer Service", "type": "service", "sales_enabled": True, "description": "Service",
                               "sales_tax_code_id": exempt, "income_account_id": income, "price": "50"})["id"]
    parent = run("customer create", {"name": "Transfer Customer"})["id"]
    kitchen = run("customer create", {"name": "Kitchen", "parent_id": parent})["id"]
    bath = run("customer create", {"name": "Bath", "parent_id": parent})["id"]
    sale = run("invoice post", {"customer": kitchen, "date": "2026-07-01", "lines": [
        {"item": item, "quantity": "1", "unit_price": "75.00", "tax_code": exempt}]})
    run("payment receive", {"customer": kitchen, "date": "2026-07-02", "amount": "100.00", "payment_method": "Check",
                            "deposit_to": bank, "operation_key": "transfer-wb-receive",
                            "applications": {"mode": "inline", "items": [{"invoice": sale["id"], "expected_version": 1, "amount": "75.00"}]}})
    form = browser.get(f"/c/{hosted.company_id}/customer-credit/transfer", headers=HEADERS)
    assert form.status_code == 200
    for name in ("from_customer", "to_customer", "amount", "date", "operation_key", "credit_memo", "payment", "memo"):
        assert re.search(rf'name="f:{re.escape(name)}"', form.text), (name, form.text[:1500])
    # The Customers page offers it as a task.
    assert f"/c/{hosted.company_id}/customer-credit/transfer" in browser.get(f"/c/{hosted.company_id}/_group/customers", headers=HEADERS).text
    submitted = browser.post(f"/c/{hosted.company_id}/customer-credit/transfer", headers=HEADERS, follow_redirects=False, data={
        "f:from_customer": "Transfer Customer:Kitchen", "f:to_customer": "Transfer Customer:Bath", "f:amount": "25.00",
        "f:date": "2026-07-10", "f:operation_key": "transfer-wb", "ctx:reason": "Use the Kitchen overpayment on Bath"})
    assert submitted.status_code in (200, 303), submitted.text[:800]
    aging = {row["customer_id"]: row["total"]["minor_units"] for row in read("report ar-aging", {"as_of": "2026-12-31"})["rows"]}
    # Kitchen paid 100.00 on 75.00: its 25.00 of credit is now Bath's.
    assert aging.get(kitchen, 0) == 0 and aging[bath] == -2500
    transfer = browser.post(f"{base}/customer-credit.transfer", json={
        "from_customer": kitchen, "to_customer": bath, "amount": "25.00", "date": "2026-07-10", "operation_key": "transfer-wb"},
        headers={**HEADERS, "X-Bookflow-Reason": "replay"}).json()
    assert transfer["idempotent_replay"] is True
    voided = browser.post(f"/c/{hosted.company_id}/customer-credit/transfer-void", headers=HEADERS, follow_redirects=False, data={
        "f:transfer": transfer["transfer_id"], "ctx:reason": "Customer changed their mind"})
    assert voided.status_code in (200, 303), voided.text[:800]
    aging = {row["customer_id"]: row["total"]["minor_units"] for row in read("report ar-aging", {"as_of": "2026-12-31"})["rows"]}
    assert aging[kitchen] == -2500 and aging.get(bath, 0) == 0
