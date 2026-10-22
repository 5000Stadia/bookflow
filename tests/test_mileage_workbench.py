"""R118 in the browser: Enter Vehicle Mileage, with the Vehicle List and Mileage Rates on the same page,
reached from Customers. A vehicle is added, a rate set and a billable trip saved through the page's forms,
the trip is listed with its deduction and billing status, paid back by check, and another is voided."""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_enter_vehicle_mileage_records_lists_pays_back_and_voids_trips(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    base, page = f"/companies/{hosted.company_id}/commands", f"/c/{hosted.company_id}/mileage"

    def run(name, body):
        reason = {} if name.split()[-1] in ("show", "query", "list") else {"X-Bookflow-Reason": "mileage witness"}
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    income = run("account create", {"name": "MI Mileage Income", "type": "income"})["id"]
    run("account create", {"name": "MI Checking", "type": "bank"})
    run("account create", {"name": "MI Travel", "type": "expense"})
    exempt = next(row["id"] for row in browser.post(f"{base}/sales-tax-code.list", json={}, headers=HEADERS).json()["items"] if not row["taxable"])
    run("item create", {"name": "MI Mileage", "type": "other_charge", "sales_enabled": True, "description": "Mileage",
                        "sales_tax_code_id": exempt, "income_account_id": income, "price": "0.70"})
    run("customer create", {"name": "MI Smith"})
    run("employee create", {"name": "MI Driver"})

    assert page in browser.get(f"/c/{hosted.company_id}/_group/customers", headers=HEADERS).text
    empty = browser.get(page, headers=HEADERS)
    assert empty.status_code == 200 and "data-no-trips" in empty.text and "data-trip-form" in empty.text

    def post(**form):
        answered = browser.post(page, data={"ctx:reason": "mileage witness", **form}, headers=HEADERS)
        assert answered.status_code == 200, _text(answered.text)[:800]
        return answered

    post(action="vehicle", vehicle_name="MI Van", vehicle_description="Ford Transit")
    post(action="rate", effective_date="2026-01-01", rate="0.67")
    van = run("vehicle show", {"vehicle": "MI Van"})["id"]
    saved = post(action="trip", vehicle=van, trip_end_date="2026-10-20", odometer_start="1000", odometer_end="1042",
                 billable="yes", customer="MI Smith", item="MI Mileage", notes="Smith job")
    assert "Recorded a trip of 42 miles on 2026-10-20" in _text(saved.text)
    listed = _text(saved.text)
    assert "MI Van" in listed and "unbilled" in listed and "28.14 at 0.67" in listed  # 42 x 0.67
    assert "0.67 USD" in listed and "Ford Transit" in listed

    # A billable trip without a customer:job is refused on the page, with the field named.
    refused = browser.post(page, data={"action": "trip", "vehicle": van, "trip_end_date": "2026-10-21",
                                       "total_miles": "5", "billable": "yes"}, headers=HEADERS)
    assert refused.status_code == 400 and "customer" in _text(refused.text)

    trip = run("trip query", {})["trips"][0]
    paid = post(action="reimburse", **{"pick:" + trip["id"]: "yes"}, pay_type="employee", pay_to="MI Driver",
                bank="MI Checking", expense="MI Travel", pay_date="2026-10-31")
    assert "28.14 pays back 1 trip(s)" in _text(paid.text)
    assert run("trip show", {"trip": trip["id"]})["reimbursed_by"]

    spare = run("trip create", {"vehicle": van, "trip_end_date": "2026-10-22", "total_miles": "3"})
    voided = post(action=f"void:{spare['id']}:{spare['version']}")
    assert "Voided the trip of 3 miles" in _text(voided.text)
    assert run("trip show", {"trip": spare["id"]})["status"] == "voided"
