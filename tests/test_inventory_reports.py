"""The two stock reports, and the property this whole increment exists to hold.

**Inventory asset on the balance sheet equals the total on these reports, for the same date.**
That is the correctness test; everything else here is the columns a bookkeeper reads beside it.
It is asserted on several dates, not only today, because a valuation that ties at the end and
not in the middle is a valuation that cannot be used to close a month.
"""
import pytest

from bookflow import BookflowError

COMPANY = "Demo Plumbing Co"
ITEM = "Brass Shutoff Valve"
KIT = "Two-Valve Kit"


@pytest.fixture
def stocked(client):
    """One item bought twice and issued once, so every column has something to show."""
    valve = client.run("item show", {"item": ITEM}, company=COMPANY)["id"]
    kit = client.run("item show", {"item": KIT}, company=COMPANY)["id"]
    run = lambda body: client.run("inventory adjust", body, company=COMPANY, reason="stock")
    run(dict(item=valve, date="2026-01-10", adjustment_account="Opening Balance Equity",
             quantity_change="20", value_change="228.00"))
    run(dict(item=kit, date="2026-02-01", adjustment_account="Opening Balance Equity",
             quantity_change="4", value_change="91.20"))
    run(dict(item=valve, date="2026-03-05", adjustment_account="Cost of Goods Sold",
             quantity_change="-16"))
    return valve, kit


def inventory_asset(client, date):
    """What the balance sheet says the inventory asset is on this date."""
    sheet = client.run("report balance-sheet", {"date_to": date, "limit": 200,
                                                "include_zero": True}, company=COMPANY)
    rows = [row for row in sheet["rows"] if row["display_account_label"].endswith("Inventory Asset")]
    assert len(rows) == 1, [row["display_account_label"] for row in sheet["rows"]]
    return rows[0]["amount"]["minor_units"]


def test_the_inventory_asset_on_the_balance_sheet_is_the_total_on_both_reports(client, stocked):
    for date in ("2026-01-09", "2026-01-31", "2026-02-15", "2026-03-05", "2026-12-31"):
        valuation = client.run("report inventory-valuation", {"as_of": date, "limit": 200},
                               company=COMPANY)
        status = client.run("report stock-status", {"as_of": date, "limit": 200}, company=COMPANY)
        total = valuation["totals"]["asset_value"]["minor_units"]
        assert status["totals"]["asset_value"]["minor_units"] == total
        assert sum(row["asset_value"]["minor_units"] for row in valuation["rows"]) == total
        assert inventory_asset(client, date) == total, date


def test_the_tie_survives_a_backdated_purchase_that_recosts_an_earlier_issue(client, stocked):
    valve, _ = stocked
    client.run("inventory adjust", dict(item=valve, date="2026-02-20",
                                        adjustment_account="Opening Balance Equity",
                                        quantity_change="10", value_change="150.00"),
               company=COMPANY, reason="backdated")
    for date in ("2026-02-19", "2026-02-20", "2026-03-05", "2026-12-31"):
        total = client.run("report inventory-valuation", {"as_of": date, "limit": 200},
                           company=COMPANY)["totals"]["asset_value"]["minor_units"]
        assert inventory_asset(client, date) == total, date


def test_the_valuation_lists_each_item_with_its_quantity_average_cost_and_value(client, stocked):
    report = client.run("report inventory-valuation", {"as_of": "2026-12-31", "limit": 200},
                        company=COMPANY)
    rows = {row["item_name"]: row for row in report["rows"]}
    assert rows[ITEM]["quantity_on_hand"] == "4"
    assert rows[ITEM]["average_cost"]["amount"] == "11.40"
    assert rows[ITEM]["asset_value"]["amount"] == "45.60"
    assert rows[KIT]["quantity_on_hand"] == "4" and rows[KIT]["asset_value"]["amount"] == "91.20"
    assert report["totals"]["asset_value"]["amount"] == "136.80"
    assert report["metadata"]["period"]["date_to"] == "2026-12-31"
    # Only stock-carrying items; a service or a non-inventory part has nothing to value.
    assert set(rows) == {ITEM, KIT}


def test_the_valuation_is_dated_not_merely_current(client, stocked):
    before = client.run("report inventory-valuation", {"as_of": "2026-02-28", "limit": 200},
                        company=COMPANY)
    rows = {row["item_name"]: row for row in before["rows"]}
    assert rows[ITEM]["quantity_on_hand"] == "20"
    assert before["totals"]["asset_value"]["amount"] == "319.20"
    empty = client.run("report inventory-valuation", {"as_of": "2026-01-01", "limit": 200},
                       company=COMPANY)
    assert empty["totals"]["asset_value"]["amount"] == "0.00"
    assert all(row["quantity_on_hand"] == "0" for row in empty["rows"])


def test_stock_status_carries_the_reorder_point_and_the_flag(client, stocked):
    report = client.run("report stock-status", {"as_of": "2026-12-31", "limit": 200}, company=COMPANY)
    rows = {row["item_name"]: row for row in report["rows"]}
    # The demo valve reorders at six and only four are left.
    assert rows[ITEM]["reorder_point_min"] == "6" and rows[ITEM]["reorder_point_max"] == "24"
    assert rows[ITEM]["below_reorder_point"] is True
    assert rows[ITEM]["quantity_available"] == rows[ITEM]["quantity_on_hand"]
    assert rows[ITEM]["quantity_on_order"] == "0"
    assert rows[KIT]["reorder_point_min"] is None and rows[KIT]["below_reorder_point"] is False
    assert report["totals"]["below_reorder_point"] == 1
    assert rows[ITEM]["preferred_vendor_id"]


def test_both_reports_page_without_moving_their_totals(client, stocked):
    whole = client.run("report inventory-valuation", {"as_of": "2026-12-31", "limit": 200},
                       company=COMPANY)
    first = client.run("report inventory-valuation", {"as_of": "2026-12-31", "limit": 1},
                       company=COMPANY)
    assert first["count"] == 1 and first["next_cursor"]
    assert first["totals"] == whole["totals"]
    second = client.run("report inventory-valuation",
                        {"as_of": "2026-12-31", "limit": 1, "cursor": first["next_cursor"]},
                        company=COMPANY)
    assert second["totals"] == whole["totals"]
    assert [row["item_id"] for row in first["rows"] + second["rows"]] == \
        [row["item_id"] for row in whole["rows"]]


def test_a_company_change_stales_a_continuation_rather_than_paging_into_other_rows(client, stocked):
    valve, _ = stocked
    first = client.run("report stock-status", {"as_of": "2026-12-31", "limit": 1}, company=COMPANY)
    client.run("inventory adjust", dict(item=valve, date="2026-04-01",
                                        adjustment_account="Cost of Goods Sold",
                                        quantity_change="-1"), company=COMPANY, reason="shrink")
    with pytest.raises(BookflowError) as caught:
        client.run("report stock-status",
                   {"as_of": "2026-12-31", "limit": 1, "cursor": first["next_cursor"]},
                   company=COMPANY)
    assert caught.value.code == "E_QUERY_STALE"


def test_a_valuation_the_balance_sheet_would_contradict_is_refused_not_printed(client, stocked):
    """An asset posting no item owns cannot happen through a command, so it is forced here.

    A company upgraded from before this ledger existed can carry exactly such an entry, and
    the honest answer is the refusal with the difference named, not a valuation the balance
    sheet disagrees with.
    """
    from pathlib import Path

    import sqlalchemy as sa

    from bookflow.company import schema as c
    from bookflow.core.ids import new_id
    from bookflow.storage.engine import open_database

    path = Path(client.run("company show", {}, company=COMPANY)["path"]) / "company.db"
    with open_database(path, writable=True) as db:
        db.raw.execute("BEGIN IMMEDIATE")
        line = db.conn.execute(sa.select(c.posting_lines).limit(1)).mappings().one()
        asset = db.conn.execute(sa.select(c.accounts.c.id).where(
            c.accounts.c.system_role == "inventory_asset")).scalar_one()
        db.conn.execute(c.posting_lines.insert().values(
            **{**dict(line), "id": new_id(), "account_id": asset, "line_no": 900,
               "debit_minor_units": 1234, "credit_minor_units": 0, "reversed_line_id": None}))
        db.raw.execute("COMMIT")
    with pytest.raises(BookflowError) as caught:
        client.run("report inventory-valuation", {"as_of": "2026-12-31"}, company=COMPANY)
    assert caught.value.code == "E_INTERNAL"
    assert caught.value.details["difference_minor_units"] == 1234
