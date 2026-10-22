"""R110 in the browser: the Price rules page, reached from Customers. A rule is added through the
page's form, listed with its summary, edited, turned off and on; the conflict preference is set; the
price check names the rule; and an invoice form's preview says which line took its price from a rule."""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_price_rules_page_adds_edits_checks_and_previews(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    base, page = f"/companies/{hosted.company_id}/commands", f"/c/{hosted.company_id}/price-rules"

    def run(name, body):
        reason = {} if name.split()[-1] in ("show", "query", "list", "preferences") else {"X-Bookflow-Reason": "pricing witness"}
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    income = run("account create", {"name": "PW Income", "type": "income"})["id"]
    exempt = next(row["id"] for row in browser.post(f"{base}/sales-tax-code.list", json={}, headers=HEADERS).json()["items"] if not row["taxable"])
    item = run("item create", {"name": "PW Fitting", "type": "service", "sales_enabled": True, "description": "Fitting",
                               "sales_tax_code_id": exempt, "income_account_id": income, "price": "20.00"})["id"]
    customer = run("customer create", {"name": "PW Acme"})["id"]

    assert page in browser.get(f"/c/{hosted.company_id}/_group/customers", headers=HEADERS).text
    empty = browser.get(page, headers=HEADERS)
    assert empty.status_code == 200 and "data-no-price-rules" in empty.text and "data-price-rule-form" in empty.text

    def post(**form):
        answered = browser.post(page, data={"ctx:reason": "pricing witness", **form}, headers=HEADERS)
        assert answered.status_code == 200, _text(answered.text)[:800]
        return _text(answered.text)

    added = post(action="create", name="PW Acme fittings", customers="PW Acme", items="PW Fitting", value="10",
                 kind="percent", direction="down", basis="sales_price", rounding="cent", date_to="2026-12-31")
    assert "Added price rule PW Acme fittings: 10% off sales price for items PW Fitting, customers PW Acme" in added
    rule = run("price-rule show", {"rule": "PW Acme fittings"})
    # Editing: the form comes back filled in, and saving changes only what changed.
    form = browser.get(f"{page}?rule={rule['id']}", headers=HEADERS).text
    assert 'value="PW Acme"' in form and "Save rule" in form
    saved = post(action="update", rule=rule["id"], expected_version="1", name="PW Acme fittings", customers="PW Acme",
                 items="PW Fitting", breaks="10:5, 50:10", kind="percent", direction="down", basis="sales_price",
                 rounding="x.99", date_to="2026-12-31")
    assert "Saved price rule PW Acme fittings: 10+ at 5% off; 50+ at 10% off" in saved
    # A bad break is refused on the page with the field named.
    refused = browser.post(page, data={"action": "create", "name": "PW bad", "breaks": "10"}, headers=HEADERS)
    assert refused.status_code == 400 and "breaks" in _text(refused.text)

    checked = post(action="check", item="PW Fitting", customer="PW Acme", quantity="50", date="2026-11-02")
    assert "18.99 USD" in checked and "price from rule PW Acme fittings" in checked  # 18.00 up to .99
    assert "Use the lowest price" in checked
    post(action="policy", conflict_policy="priority")
    assert run("price-rule preferences", {})["conflict_policy"] == "priority"

    # The invoice form's own preview says which line a rule priced.
    preview = browser.post(f"/c/{hosted.company_id}/invoice/post", headers=HEADERS, data={
        "action": "preview", "f:customer": customer, "f:date": "2026-11-02", "collection:lines": "1",
        "c:lines:0:item": item, "c:lines:0:quantity": "50"})
    assert preview.status_code == 200, _text(preview.text)[:800]
    assert "Line 1: price from rule PW Acme fittings" in _text(preview.text)

    off = post(action=f"deactivate:{rule['id']}:2")
    assert "Turned off price rule PW Acme fittings" in off
    on = post(action=f"activate:{rule['id']}:3")
    assert "Turned on price rule PW Acme fittings" in on
    assert run("price-rule show", {"rule": rule["id"]})["active"] is True
