"""R103 in the browser: the Build Assemblies window (components, on hand, needed, maximum buildable),
Mark as pending when short, and the Pending Builds list with Finalize, reached from Items."""
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_build_assemblies_shows_the_components_builds_pends_and_finalizes(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    base, page = f"/companies/{hosted.company_id}/commands", f"/c/{hosted.company_id}/assembly/build"

    def run(name, body):
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body,
                                headers={**HEADERS, "X-Bookflow-Reason": "assembly witness"})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    accounts = browser.post(f"{base}/account.list", json={}, headers=HEADERS).json()["items"]
    income = run("account create", {"name": "AB Sales", "type": "income"})["id"]
    cogs = next(row["id"] for row in accounts if row["type"] == "cost_of_goods_sold")
    equity = next(row["id"] for row in accounts if row["type"] == "equity")
    tank = run("item create", {"name": "AB Tank", "type": "inventory_part", "description": "Tank", "price": "200",
                               "purchase_description": "Tank", "cost": "100", "cogs_account_id": cogs,
                               "income_account_id": income})["id"]
    kit = run("item create", {"name": "AB Heater Kit", "type": "inventory_assembly", "description": "Kit", "price": "500",
                              "purchase_description": "Kit", "cost": "0", "cogs_account_id": cogs, "income_account_id": income,
                              "members": [{"component_item_id": tank, "quantity": "1"}]})["id"]
    run("inventory adjust", {"item": tank, "date": "2026-02-01", "quantity_change": "3", "value_change": "300.00",
                             "adjustment_account": equity})

    items = browser.get(f"/c/{hosted.company_id}/_group/items", headers=HEADERS).text
    assert "/assembly/build" in items and "/assembly/builds?status=pending" in items
    shown = browser.get(f"{page}?item={kit}&date=2026-02-10&quantity=2", headers=HEADERS)
    assert shown.status_code == 200, shown.text[:800]
    row = _text(re.search(rf'<tr data-assembly-component="{tank}".*?</tr>', shown.text, re.S).group(0))
    assert "AB Tank" in row and " 3 " in row and " 2 " in row, row
    assert "Maximum number you can build from the quantity on hand: 3" in _text(shown.text)
    assert "data-assembly-submit" in shown.text and "data-assembly-pending" not in shown.text

    done = browser.post(page, headers=HEADERS, data={"item": kit, "date": "2026-02-10", "quantity": "2",
                                                     "action": "build", "ctx:reason": "Built two kits"})
    assert done.status_code == 200 and "data-assembly-result" in done.text, done.text[:800]
    assert "Built 2 AB Heater Kit at 200.00" in _text(done.text)

    short = browser.get(f"{page}?item={kit}&date=2026-02-11&quantity=4", headers=HEADERS)
    assert "data-assembly-short" in short.text and "data-assembly-pending" in short.text
    assert "Maximum number you can build from the quantity on hand: 1" in _text(short.text)
    pended = browser.post(page, headers=HEADERS, data={"item": kit, "date": "2026-02-11", "quantity": "4",
                                                       "action": "pending", "ctx:reason": "Waiting on tanks"})
    assert "Marked pending" in _text(pended.text)

    pending = browser.get(f"/c/{hosted.company_id}/assembly/builds?status=pending", headers=HEADERS)
    assert pending.status_code == 200 and "Pending builds" in pending.text
    build = re.search(r'data-assembly-build="([0-9A-Z]{26})"', pending.text).group(1)
    run("inventory adjust", {"item": tank, "date": "2026-02-11", "quantity_change": "3", "value_change": "330.00",
                             "adjustment_account": equity})
    final = browser.post(f"/c/{hosted.company_id}/assembly/builds", headers=HEADERS, data={
        "build": build, "status": "pending", "action": "finalize", "ctx:reason": "Tanks arrived"})
    assert final.status_code == 200 and "data-assembly-result" in final.text, final.text[:800]
    assert "No builds are pending." in final.text
    listed = browser.post(f"{base}/assembly.list", json={}, headers=HEADERS).json()
    assert sorted(row["status"] for row in listed["rows"]) == ["built", "built"]
