"""R119 in the workbench: the fixed asset list and a schedule with posted and pending periods, the
loan list, and a loan's schedule with a Pay form that writes the check."""
from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def test_fixed_asset_and_loan_pages(hosted):  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id
    base = f"/companies/{company}/commands"

    def run(name, body):
        from bookflow.core import registry
        reason = {"X-Bookflow-Reason": "asset witness"} if registry.get(name).is_write else {}
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    acc = {name: run("account create", {"name": name, "type": kind})["id"] for name, kind in (
        ("FW Vehicles", "fixed_asset"), ("FW Accumulated", "fixed_asset"), ("FW Depreciation", "expense"),
        ("FW Gain", "other_income"), ("FW Loan", "long_term_liability"), ("FW Interest", "expense"), ("FW Bank", "bank"))}
    van = run("item create", {"name": "FW Van", "type": "fixed_asset", "asset_number": "FW-1", "description": "Van",
                              "purchase_date": "2026-01-10", "original_cost": "12000.00", "asset_account_id": acc["FW Vehicles"],
                              "disposal_status": "in_service", "depreciation_method": "straight_line"})["id"]
    run("fixed-asset setup", {"item": van, "method": "straight_line", "salvage": "1200.00", "life_months": 48,
                              "accumulated_depreciation_account": acc["FW Accumulated"],
                              "depreciation_expense_account": acc["FW Depreciation"], "gain_loss_account": acc["FW Gain"]})
    run("fixed-asset depreciate", {"through": "2026-02-28"})

    listing = browser.get(f"/c/{company}/fixed-asset", headers=HEADERS)
    assert listing.status_code == 200 and f'data-fixed-asset="{van}"' in listing.text and "450.00" in listing.text
    assert f"/c/{company}/fixed-asset/depreciate" in listing.text
    schedule = browser.get(f"/c/{company}/fixed-asset/{van}", headers=HEADERS)
    assert schedule.status_code == 200 and 'data-period="1" data-status="posted"' in schedule.text
    assert 'data-period="3" data-status="pending"' in schedule.text and f"fixed-asset/dispose?item={van}" in schedule.text
    form = browser.get(f"/c/{company}/fixed-asset/dispose?item={van}", headers=HEADERS)
    assert form.status_code == 200 and van in form.text
    depreciate = browser.get(f"/c/{company}/fixed-asset/depreciate", headers=HEADERS)
    assert depreciate.status_code == 200
    accounting = browser.get(f"/c/{company}/_group/accounting", headers=HEADERS).text
    assert f"/c/{company}/fixed-asset" in accounting and f"/c/{company}/loan" in accounting

    run("journal post", {"date": "2026-01-02", "lines": [{"account": acc["FW Bank"], "side": "debit", "amount": "25000.00"},
                                                         {"account": acc["FW Loan"], "side": "credit", "amount": "25000.00"}]})
    loan = run("loan create", {"name": "FW Truck loan", "liability_account": acc["FW Loan"], "interest_account": acc["FW Interest"],
                               "principal": "25000.00", "annual_rate": "6.5", "term_payments": 60,
                               "first_payment_date": "2026-02-01", "payment_account": acc["FW Bank"]})["loan"]["loan"]
    loans = browser.get(f"/c/{company}/loan", headers=HEADERS)
    assert loans.status_code == 200 and f'data-loan="{loan["id"]}"' in loans.text and "489.15" in loans.text
    page = browser.get(f"/c/{company}/loan/{loan['id']}", headers=HEADERS)
    assert page.status_code == 200 and "data-loan-pay" in page.text and 'data-ties="yes"' in page.text
    assert 'data-payment="60"' in page.text and "135.42" in page.text
    paid = browser.post(f"/c/{company}/loan/{loan['id']}", headers=HEADERS,
                        data={"extra_principal": "1,000.00", "ctx:reason": "first payment"})
    assert paid.status_code == 200 and "data-notice" in paid.text and "1489.15" in paid.text
    assert 'data-status="paid" data-payment="1"' in paid.text and 'data-ties="yes"' in paid.text
    shown = run("loan show", {"loan": loan["id"]})["loan"]
    assert shown["balance"]["amount"] == "23646.27" and shown["ties"]
    bad = browser.post(f"/c/{company}/loan/{loan['id']}", headers=HEADERS, data={"extra_principal": "99999.00"})
    assert bad.status_code == 400 and "E_VALIDATION" in bad.text
