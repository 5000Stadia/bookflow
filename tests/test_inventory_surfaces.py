"""Registration is not callability: these navigate to each inventory page and use it.

Every assertion here is made against a page a person actually lands on. The home window's
tile is clicked rather than its URL typed, the report filter form is submitted the way the
browser submits it, and the figures compared are the ones rendered in the table, not the ones
the command returned.

The host runs in-process on an OS-chosen bind so this file never contends for a fixed port.
"""
import re

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.adapters.workbench import home
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version

COMPANY = "Demo Plumbing Co"
ITEM = "Brass Shutoff Valve"
PASSWORD = "correct-horse-battery"

PAGES = ("/inventory/adjust", "/report/inventory-valuation", "/report/stock-status")


@pytest.fixture
def workbench(root):
    """A signed-in workbench over the running host.

    Commands the tests need afterwards go over HTTP through the same client, because the host
    holds the data-root lock for its whole run and a library call beside it is E_DB_BUSY.
    """
    login = os_login()
    library = bookflow.connect(data_root=str(root))
    library.run("user set-password", {"username": login, "password": PASSWORD})
    item = library.run("item show", {"item": ITEM}, company=COMPANY)["id"]
    library.run("inventory adjust", dict(item=item, date="2026-01-10",
                                         adjustment_account="Opening Balance Equity",
                                         quantity_change="5", value_change="57.00"),
                company=COMPANY, reason="opening stock")
    company_id = library.company.list()["items"][0]["company_id"]
    handle = start_serving(root, client_version(), bind="127.0.0.1:0", secure_cookies=False,
                           publish_descriptor=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post("/login", json={"username": login, "password": PASSWORD}).status_code == 200

        def run(name, body=None):
            response = browser.post(f"/companies/{company_id}/commands/{name.replace(' ', '.')}",
                                    json=body or {}, headers={"X-Bookflow-Workbench": "1"})
            assert response.status_code == 200, response.text
            return response.json()

        yield browser, company_id, run
    finally:
        handle.stop()


def usable(browser, href, label):
    """The same witness the home window applies: a landing, a heading, and something to do."""
    page = browser.get(href, follow_redirects=False)
    assert page.status_code == 200, (label, href, page.status_code, page.headers.get("location"))
    assert 'name="password"' not in page.text, (label, href, "sent a signed-in reader to log in")
    body = page.text.split("<main>", 1)[-1].split("</main>", 1)[0]
    assert 'class="error"' not in body, (label, href, body[:400])
    assert not re.search(r"\bE_[A-Z_]+\b", body), (label, href, body[:400])
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", page.text, re.S)
    assert heading and heading.group(1).strip(), (label, href, "no heading")
    assert re.search(r"<(form|input|button|a )", body), (label, href, "nothing to do on this page")
    return page.text, heading.group(1).strip()


def test_each_inventory_page_is_reached_and_is_usable(workbench):
    browser, company_id, _ = workbench
    titles = {}
    for path in PAGES:
        _, heading = usable(browser, f"/c/{company_id}{path}", path)
        titles[path] = heading
    assert titles == {"/inventory/adjust": "Adjust inventory",
                      "/report/inventory-valuation": "Inventory valuation summary",
                      "/report/stock-status": "Inventory stock status by item"}


def test_the_home_window_tile_is_live_and_lands_on_the_adjustment_form(workbench):
    browser, company_id, _ = workbench
    board = home.resolve(company_id)
    tiles = {item.step.id: item for panel in board for item in panel.steps}
    tile = tiles["adjust-inventory"]
    assert tile.live, "the adjust-inventory tile did not resolve live"
    assert tile.step.action.kind == home.WRITE
    # Click the tile on the rendered page, not a URL nobody would type.
    board_page = browser.get(f"/c/{company_id}/")
    assert tile.href and f'href="{tile.href}"' in board_page.text
    _, heading = usable(browser, tile.href, "home/adjust-inventory")
    assert heading == "Adjust inventory"


def test_the_reports_group_offers_both_stock_reports(workbench):
    browser, company_id, _ = workbench
    page = browser.get(f"/c/{company_id}/_group/reports")
    assert page.status_code == 200
    for verb in ("inventory-valuation", "stock-status"):
        assert f'/report/{verb}"' in page.text, verb


def test_running_a_stock_report_in_the_workbench_renders_the_money(workbench):
    browser, company_id, run = workbench
    for verb, expected in (("inventory-valuation", None), ("stock-status", "Order")):
        page = browser.post(f"/c/{company_id}/report/{verb}",
                            data={"f:as_of": "2026-12-31", "f:limit": "50", "action": "submit"},
                            headers={"X-Bookflow-Workbench": "1"})
        assert page.status_code == 200, page.text[:400]
        body = page.text.split("<main>", 1)[-1].split("</main>", 1)[0]
        assert 'class="error"' not in body, body[:400]
        assert 'id="stock-rows"' in body
        # The figures on the page are the figures the command computed.
        result = run(f"report {verb}", {"as_of": "2026-12-31", "limit": 50})
        assert re.findall(r'data-total="asset_value">([^<]*)<', body) == \
            [result["totals"]["asset_value"]["amount"]]
        assert re.findall(r'data-value="asset_value">([^<]*)<', body) == \
            [row["asset_value"]["amount"] for row in result["rows"]]
        assert re.findall(r'data-value="quantity_on_hand">([^<]*)<', body) == \
            [row["quantity_on_hand"] for row in result["rows"]]
        assert ITEM in body
        if expected:
            assert expected in body


def test_posting_an_adjustment_through_the_workbench_form_moves_the_stock(workbench):
    browser, company_id, run = workbench
    item = run("item show", {"item": ITEM})["id"]
    page = browser.post(
        f"/c/{company_id}/inventory/adjust",
        data={"f:item": item, "f:date": "2026-04-01",
              "f:adjustment_account": "Cost of Goods Sold", "f:quantity_change": "-2",
              "_reason": "sold two", "action": "submit"},
        headers={"X-Bookflow-Workbench": "1"})
    assert page.status_code == 200, page.text[:600]
    body = page.text.split("<main>", 1)[-1].split("</main>", 1)[0]
    assert 'class="error"' not in body, body[:600]
    assert run("item show", {"item": ITEM})["quantity_on_hand"] == "3"


def test_every_registered_inventory_command_is_discoverable_through_mcp():
    """An agent finds the same named actions, with schemas, not merely a shared noun."""
    from bookflow.adapters.mcp import catalog
    for name in ("inventory adjust", "inventory void", "inventory show",
                 "report inventory-valuation", "report stock-status"):
        listed = catalog.list_commands(prefix=name, limit=5)["commands"]
        assert name in {row["name"] for row in listed}, name
        described = catalog.command_help(name)
        assert described["name"] == name
        assert described["local_only"] is False and described["standalone"] is False
        assert described["input_schema"]
    assert catalog.command_help("inventory adjust")["kind"] == "write"
