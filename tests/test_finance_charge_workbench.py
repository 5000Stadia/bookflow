"""R120 in the browser: the finance charge preferences on the company settings page, and the Assess
Finance Charges screen (a checkbox and an amount per customer) reached from Customers."""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_the_assess_finance_charges_screen_charges_the_marked_customers(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    base, page = f"/companies/{hosted.company_id}/commands", f"/c/{hosted.company_id}/finance-charge/assess"

    def run(name, body):
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, "X-Bookflow-Reason": "finance charge witness"})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    income = run("account create", {"name": "FC Sales", "type": "income"})["id"]
    run("account create", {"name": "FC Finance Charge Income", "type": "other_income"})
    exempt = next(row["id"] for row in browser.post(f"{base}/sales-tax-code.list", json={}, headers=HEADERS).json()["items"] if not row["taxable"])
    item = run("item create", {"name": "FC Service", "type": "service", "sales_enabled": True, "description": "Service",
                               "sales_tax_code_id": exempt, "income_account_id": income, "price": "50"})["id"]
    late = run("customer create", {"name": "FC Late Customer"})["id"]
    later = run("customer create", {"name": "FC Later Customer"})["id"]
    for customer in (late, later):
        run("invoice post", {"customer": customer, "date": "2026-01-01", "due_date": "2026-01-31", "lines": [
            {"item": item, "quantity": "1", "unit_price": "1000.00", "tax_code": exempt}]})

    # Off until set: the screen says so and points at the settings, where the seven preferences are grouped.
    off = browser.get(page, headers=HEADERS)
    assert off.status_code == 200 and "data-finance-charge-off" in off.text
    assert f"/c/{hosted.company_id}/company/self/update" in off.text
    settings = browser.get(f"/c/{hosted.company_id}/company/self/update", headers=HEADERS)
    assert settings.status_code == 200 and "Finance charges" in settings.text
    for name in ("finance_charge_annual_rate", "finance_charge_minimum", "finance_charge_grace_days", "finance_charge_account_id",
                 "finance_charge_on_overdue_charges", "finance_charge_from", "finance_charge_mark_to_print"):
        assert re.search(rf'name="f:{name}"', settings.text), name
    run("company update", {"finance_charge_annual_rate": "18", "finance_charge_minimum": "2.00",
                           "finance_charge_account_id": "FC Finance Charge Income"})
    assert page in browser.get(f"/c/{hosted.company_id}/_group/customers", headers=HEADERS).text

    shown = browser.get(page + "?date=2026-03-02", headers=HEADERS)
    assert shown.status_code == 200, shown.text[:800]
    for customer in (late, later):
        assert f'name="sel:{customer}"' in shown.text and f'name="amount:{customer}" ' in shown.text
    assert f'name="amount:{late}" inputmode="decimal" size="10" aria-label="Finance charge for FC Late Customer" value="14.79"' in shown.text
    policy = _text(re.search(r"<p data-finance-charge-policy>.*?</p>", shown.text, re.S).group(0))
    assert "18% a year" in policy, policy
    row = _text(re.search(rf'<tr data-finance-charge-row="{late}">.*?</tr>', shown.text, re.S).group(0))
    assert "1000.00" in row.replace(",", ""), row
    fingerprint = re.search(r'name="fingerprint" value="([0-9a-f]{64})"', shown.text).group(1)

    done = browser.post(page, headers=HEADERS, data={
        "date": "2026-03-02", "fingerprint": fingerprint, "action": "assess", f"sel:{late}": "yes",
        f"amount:{late}": "12.00", f"computed:{late}": "14.79", f"amount:{later}": "14.79", f"computed:{later}": "14.79",
        "ctx:reason": "Month-end finance charges"})
    assert done.status_code == 200, done.text[:800]
    assert "data-finance-charge-result" in done.text and "Assessed 1 finance charge totalling 12.00" in _text(done.text)
    charges = browser.post(f"{base}/statement-charge.query", json={}, headers=HEADERS).json()["items"]
    assert [(row["customer_id"], row["total"]["amount"]) for row in charges] == [(late, "12.00")]
    # The charged customer reads as charged; the other can still be assessed.
    again = browser.get(page + "?date=2026-03-02", headers=HEADERS)
    assert f'name="sel:{late}"' not in again.text and f'name="sel:{later}"' in again.text
