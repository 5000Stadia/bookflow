"""The everyday reports, read against the demo company as the seed writes it.

Every expected figure below is computed by hand from the documents `bookflow/demo/seed.toml`
posts, as of the day it is written as of, and is never read back from another report. Where a
report ties to a figure the books already state -- Accounts Receivable, Accounts Payable -- the
tie is checked as well, but always after the literal.
"""
import pytest

from bookflow.core.errors import BookflowError
from tests.demo_oracle import DEMO_AS_OF, DEMO_POSITION
from tests.test_row3_host import hosted  # noqa: F401

COMPANY = "Demo Plumbing Co"


def _run(client, command, body):
    return client.run(command, {**body, "limit": 200}, company=COMPANY)


def _minor(money):
    return money["minor_units"]


# --- customer balances -------------------------------------------------------------------
#
#   Commercial Example Customer   DEMO-SALE-INV-ACTIVE, 2026-09-05              128.00
#   Payment Example Customer:Job A DEMO-PAY-INV-A 100.00, 2026-10-03
#                                  less DEMO-PAY-P1 re-applied 80.00, 2026-10-05  20.00
#   Payment Example Customer:Job B DEMO-PAY-INV-B 60.00, 2026-10-03
#                                  less DEMO-PAY-P1 applied 30.00, 2026-10-05
#                                  less DEMO-PAY-P2 40.00 (30.00 applied, 10.00
#                                  left as the job's own credit), 2026-10-06     -10.00
#   Tax Rounding Example Customer  DEMO-TAX-LEGACY 0.10, -LINE 0.12, -TOTAL 0.11    0.33
#   Tax Work Example Customer      DEMO-TAX-WORK-INV, 2026-11-12                   0.06
#
# Every other customer's invoices are paid, credited or voided to nothing.
CUSTOMER_BALANCES = [
    ("Commercial Example Customer", 12800),
    ("Line Kinds Example Customer", 47782),
    ("Payment Example Customer:Job A", 2000),
    ("Payment Example Customer:Job B", -1000),
    ("Tax Rounding Example Customer", 33),
    ("Tax Work Example Customer", 6),
]
CUSTOMER_DETAIL = [
    # customer, kind, number, amount, running balance
    ("Commercial Example Customer", "activity", "DEMO-SALE-INV-ACTIVE", 12800, 12800),
    ("Commercial Example Customer", "total", None, 12800, 12800),
    ("Line Kinds Example Customer", "activity", "DEMO-LINE-KINDS", 47782, 47782),
    ("Line Kinds Example Customer", "total", None, 47782, 47782),
    ("Payment Example Customer:Job A", "activity", "DEMO-PAY-INV-A", 10000, 10000),
    ("Payment Example Customer:Job A", "activity", "DEMO-PAY-P1", -8000, 2000),
    ("Payment Example Customer:Job A", "total", None, 2000, 2000),
    ("Payment Example Customer:Job B", "activity", "DEMO-PAY-INV-B", 6000, 6000),
    ("Payment Example Customer:Job B", "activity", "DEMO-PAY-P1", -3000, 3000),
    ("Payment Example Customer:Job B", "activity", "DEMO-PAY-P2", -4000, -1000),
    ("Payment Example Customer:Job B", "total", None, -1000, -1000),
    ("Tax Rounding Example Customer", "activity", "DEMO-TAX-LEGACY", 10, 10),
    ("Tax Rounding Example Customer", "activity", "DEMO-TAX-LINE", 12, 22),
    ("Tax Rounding Example Customer", "activity", "DEMO-TAX-TOTAL", 11, 33),
    ("Tax Rounding Example Customer", "total", None, 33, 33),
    ("Tax Work Example Customer", "activity", "DEMO-TAX-WORK-INV", 6, 6),
    ("Tax Work Example Customer", "total", None, 6, 6),
]

# --- vendor balances ---------------------------------------------------------------------
#
#   Central Supply   DEMO-BUY-BILL-1 2026-11-12                     +300.80
#                    DEMO-BUY-PAY-1  2026-11-20 pays it in full      -300.80
#                    DEMO-BUY-CREDIT-1 2026-11-26 two valves back     -21.90
#                    item receipt (journal 6) 2026-12-05, 6 kits      +60.00
#                    receipt price correction (journal 7) 2026-12-05  +4.00
#                    item receipt (journal 8) 2026-12-11, 3 kits
#                      at 8.00 plus 12.00 shipping                    +36.00
#                    DEMO-KIT-BILL 2026-12-06 bills 4 of journal 6's kits
#                      (40.00) and journal 7's correction (4.00): that
#                      payable moves off the receipt onto the bill     -40.00 -4.00 +44.00
#                    bill 1 2026-12-12 bills 2 of journal 8's kits with
#                      8.00 of its shipping                           -24.00 +24.00
#                    Moving a payable onto the bill that took it over changes
#                    nothing the vendor is owed.                        =  78.10
VENDOR_DETAIL = [
    ("activity", "DEMO-BUY-BILL-1", 30080, 30080),
    ("activity", "DEMO-BUY-PAY-1", -30080, 0),
    ("activity", "DEMO-BUY-CREDIT-1", -2190, -2190),
    ("activity", "6", 6000, 3810),
    ("activity", "7", 400, 4210),
    ("activity", "6", -4000, 210),
    ("activity", "DEMO-KIT-BILL", 4400, 4610),
    ("activity", "7", -400, 4210),
    ("activity", "8", 3600, 7810),
    ("activity", "8", -2400, 5410),
    ("activity", "1", 2400, 7810),
    ("total", None, 7810, 7810),
]


def test_customer_balance_summary_is_each_customers_balance(client):
    result = _run(client, "report customer-balance-summary", {"as_of": DEMO_AS_OF})
    assert [(row["display_customer_label"], _minor(row["balance"])) for row in result["rows"]] == CUSTOMER_BALANCES
    assert _minor(result["totals"]["balance"]) == sum(amount for _, amount in CUSTOMER_BALANCES)
    # The total is Accounts Receivable on the books.
    assert _minor(result["totals"]["balance"]) == DEMO_POSITION["balances"]["Accounts Receivable"]
    job = next(row for row in result["rows"] if row["display_customer_label"].endswith(":Job A"))
    assert job["parent_id"] is not None


def test_customer_balance_detail_lists_every_effect_with_a_running_balance(client):
    result = _run(client, "report customer-balance-detail", {"as_of": DEMO_AS_OF})
    shown = [(row["display_customer_label"], row["kind"], row["number"], _minor(row["amount"]),
              _minor(row["balance"])) for row in result["rows"]]
    assert shown == CUSTOMER_DETAIL
    assert _minor(result["totals"]["balance"]) == DEMO_POSITION["balances"]["Accounts Receivable"]
    # One customer, named: its own rows and nothing else.
    one = _run(client, "report customer-balance-detail",
               {"as_of": DEMO_AS_OF, "customer": "Payment Example Customer:Job B"})
    assert [(row["number"], _minor(row["balance"])) for row in one["rows"]] == [
        ("DEMO-PAY-INV-B", 6000), ("DEMO-PAY-P1", 3000), ("DEMO-PAY-P2", -1000), (None, -1000)]
    # A customer named with nothing owed is still printed: the parent's own invoice
    # DEMO-PAY-INV-CUSTOMER (20.00) and the 20.00 of DEMO-PAY-P1 that stays with the parent,
    # the rest of that receipt having settled its jobs' invoices.
    none = _run(client, "report customer-balance-detail",
                {"as_of": DEMO_AS_OF, "customer": "Payment Example Customer"})
    assert [(row["kind"], row["number"], _minor(row["amount"]), _minor(row["balance"]))
            for row in none["rows"]] == [("activity", "DEMO-PAY-INV-CUSTOMER", 2000, 2000),
                                         ("activity", "DEMO-PAY-P1", -2000, 0),
                                         ("total", None, 0, 0)]


def test_vendor_balance_summary_and_detail_are_what_central_supply_is_owed(client):
    summary = _run(client, "report vendor-balance-summary", {"as_of": DEMO_AS_OF})
    assert [(row["display_vendor_label"], _minor(row["balance"])) for row in summary["rows"]] == [
        ("Central Supply", 7810)]
    assert -_minor(summary["totals"]["balance"]) == DEMO_POSITION["balances"]["Accounts Payable"]
    detail = _run(client, "report vendor-balance-detail", {"as_of": DEMO_AS_OF})
    assert {row["display_vendor_label"] for row in detail["rows"]} == {"Central Supply"}
    assert [(row["kind"], row["number"], _minor(row["amount"]), _minor(row["balance"]))
            for row in detail["rows"]] == VENDOR_DETAIL
    assert detail["rows"][0]["due_date"] == "2026-12-12"
    # The day before the bill was paid, the bill is the whole balance.
    before = _run(client, "report vendor-balance-summary", {"as_of": "2026-11-19"})
    assert [_minor(row["balance"]) for row in before["rows"]] == [30080]
    # The day it was paid, nothing is owed and the vendor leaves the summary.
    paid = _run(client, "report vendor-balance-summary", {"as_of": "2026-11-20"})
    assert paid["rows"] == [] and _minor(paid["totals"]["balance"]) == 0


def test_balance_reports_page_without_losing_the_totals(client):
    first = client.run("report customer-balance-detail", {"as_of": DEMO_AS_OF, "limit": 4}, company=COMPANY)
    assert first["count"] == 4 and first["next_cursor"]
    rows = list(first["rows"])
    cursor = first["next_cursor"]
    while cursor:
        page = client.run("report customer-balance-detail",
                          {"as_of": DEMO_AS_OF, "limit": 4, "cursor": cursor}, company=COMPANY)
        assert page["totals"] == first["totals"]
        rows += page["rows"]
        cursor = page["next_cursor"]
    assert [(row["number"], _minor(row["balance"])) for row in rows] == [
        (number, balance) for _, _, number, _, balance in CUSTOMER_DETAIL]


def test_a_balance_detail_filter_that_names_nothing_is_refused(client):
    with pytest.raises(BookflowError) as refused:
        _run(client, "report vendor-balance-detail", {"as_of": DEMO_AS_OF, "vendor": "Nobody At All"})
    assert refused.value.code == "E_RECORD_NOT_FOUND"



# --- purchases ---------------------------------------------------------------------------
#
# Every item the demo buys is a stock item, so every purchase is a receipt of stock:
#
#   Brass Shutoff Valve   DEMO-BUY-BILL-1 (Central Supply) 24 @ 10.95          262.80
#   Service Call Kit      check DEMO-KIT-CHECK (Regional Parts) 0.5 @ 12.34      6.17
#                         card charge (Regional Parts) 2 @ 10.00                20.00
#                         item receipt (Central Supply) 6 @ 10.00               60.00
#                         DEMO-KIT-BILL confirms 4 of them at 11.00: a price
#                           correction to the receipt, no new stock              4.00
#                                                          8.5 units            90.17
#   Received Shipping Kit item receipt (Central Supply) 3 @ 8.00 + 12.00 ship   36.00
#
# The check's 4.83 delivery expense and every expense-account bill line name no item and
# are not purchases; the count adjustment and the free kit given away are not purchases.
YEAR = {"date_from": "2026-01-01", "date_to": DEMO_AS_OF}
PURCHASES_BY_ITEM = [
    # label, quantity microunits, amount, average cost
    ("Brass Shutoff Valve", 24_000_000, 26280, 1095),
    ("Received Shipping Kit", 3_000_000, 3600, 1200),
    ("Service Call Kit", 8_500_000, 9017, 1061),   # 90.17 / 8.5 = 10.608...
]
PURCHASES_BY_VENDOR = [("Central Supply", 26280 + 6000 + 400 + 3600), ("Regional Parts", 617 + 2000)]


def test_purchases_by_item_and_by_vendor_are_the_demos_stock_purchases(client):
    by_item = _run(client, "report purchases-by-item", YEAR)
    assert [(row["display_item_label"], row["quantity_microunits"], _minor(row["amount"]),
             _minor(row["average_cost"])) for row in by_item["rows"]] == PURCHASES_BY_ITEM
    assert _minor(by_item["totals"]["amount"]) == 38897
    by_vendor = _run(client, "report purchases-by-vendor", YEAR)
    assert [(row["display_vendor_label"], _minor(row["amount"])) for row in by_vendor["rows"]] == PURCHASES_BY_VENDOR
    assert by_vendor["totals"] == by_item["totals"]
    assert [row["percent_of_total"] for row in by_vendor["rows"]] == ["93.271975", "6.728025"]
    # Before the valves arrived nothing had been bought.
    early = _run(client, "report purchases-by-item", {"date_from": "2026-01-01", "date_to": "2026-11-11"})
    assert early["rows"] == [] and _minor(early["totals"]["amount"]) == 0


def test_a_nonstock_item_is_bought_at_its_posted_cost_and_a_void_takes_it_back(client):
    from bookflow.core import registry

    def run(command, body):
        writes = registry.get(command).is_write
        return client.run(command, body, company=COMPANY, reason="Purchases scenario" if writes else None)
    fees = run("account show", {"account": "Professional Fees"})["id"]
    bank = run("account show", {"account": "Checking"})["id"]
    supply = run("vendor create", {"name": "Scenario Supply"})["id"]
    hardware = run("vendor create", {"name": "Scenario Hardware"})["id"]
    gasket = run("item create", {"name": "Scenario Gasket", "type": "non_inventory_part",
                                 "sales_enabled": False, "purchase_enabled": True,
                                 "purchase_description": "Gasket pack", "cost": "3.00",
                                 "expense_account_id": fees})["id"]
    # 10 gaskets at 3.00 and 5.00 of freight on an expense line, which is no item.
    run("bill post", {"vendor": supply, "date": "2027-02-03", "number": "SCN-1",
                      "expenses": [{"account": fees, "amount": "5.00"}],
                      "items": [{"item": gasket, "quantity": "10"}]})
    # 2 at 3.50 by check: the payee is on the bank line, not on the item line.
    run("check post", {"account": bank, "date": "2027-02-04", "amount": "7.00",
                       "pay_to": {"name_type": "vendor", "name_id": hardware},
                       "items": [{"item": gasket, "quantity": "2", "unit_cost": "3.50"}]})
    # 4 more, then voided: worth nothing, quantity and cost both taken back.
    voided = run("bill post", {"vendor": supply, "date": "2027-02-05", "number": "SCN-2",
                               "items": [{"item": gasket, "quantity": "4"}]})
    run("bill void", {"bill": voided["id"], "expected_version": voided["version"]})
    february = {"date_from": "2027-02-01", "date_to": "2027-02-28"}
    by_item = _run(client, "report purchases-by-item", february)
    assert [(row["display_item_label"], row["item_type"], row["quantity"], _minor(row["amount"]),
             _minor(row["average_cost"])) for row in by_item["rows"]] == [
        ("Scenario Gasket", "non_inventory_part", "12", 3700, 308)]   # 37.00 / 12 = 3.083...
    by_vendor = _run(client, "report purchases-by-vendor", february)
    assert [(row["display_vendor_label"], _minor(row["amount"])) for row in by_vendor["rows"]] == [
        ("Scenario Hardware", 700), ("Scenario Supply", 3000)]


# --- open purchase orders -----------------------------------------------------------------
#
#   PO-1105      ordered 24 valves + 38.00 of fittings, billed whole: nothing to receive
#   DEMO-KIT-PO  10 kits @ 10.00 = 100.00; 6 received                open  40.00
#   1            5 shipping kits @ 8.00 = 40.00; 3 received           open  16.00
OPEN_ORDERS = [("DEMO-KIT-PO", "partly_received", 10000, 6000, 4000),
               ("1", "partly_received", 4000, 2400, 1600)]


def test_open_purchase_orders_are_what_is_still_to_arrive(client):
    result = _run(client, "report open-purchase-orders", {"date_to": DEMO_AS_OF})
    assert [(row["number"], row["status"], _minor(row["amount"]), _minor(row["received"]),
             _minor(row["open_balance"])) for row in result["rows"]] == OPEN_ORDERS
    assert {key: _minor(value) for key, value in result["totals"].items()} == {
        "amount": 14000, "received": 8400, "open_balance": 5600}
    assert {row["display_vendor_label"] for row in result["rows"]} == {"Central Supply"}
    # Orders dated after the date are left out: the shipping-kit order is dated 2026-12-10.
    earlier = _run(client, "report open-purchase-orders", {"date_to": "2026-12-09"})
    assert [row["number"] for row in earlier["rows"]] == ["DEMO-KIT-PO"]
    other = _run(client, "report open-purchase-orders", {"date_to": DEMO_AS_OF, "vendor": "Regional Parts"})
    assert other["rows"] == [] and _minor(other["totals"]["open_balance"]) == 0



# --- deposit detail ----------------------------------------------------------------------
#
#   Deposit 1, 2026-10-10, to Checking, "Friday takings"                        324.00
#     DEMO-BANK-SR-1  2026-10-09  one service call at 100.00 + 8% tax, from
#                     Undeposited Funds                                       -108.00
#     DEMO-BANK-SR-2  2026-10-09  two service calls, 200.00 + 16.00 tax       -216.00
DEPOSITS = [
    ("deposit", "1", "1", "Checking", 32400),
    ("line", "1", "DEMO-BANK-SR-1", "Undeposited Funds", -10800),
    ("line", "1", "DEMO-BANK-SR-2", "Undeposited Funds", -21600),
]


def test_deposit_detail_is_each_deposit_and_what_it_gathered(client):
    result = _run(client, "report deposit-detail", YEAR)
    assert [(row["kind"], row["deposit_number"], row["number"], row["current_account_label"],
             _minor(row["amount"])) for row in result["rows"]] == DEPOSITS
    assert result["totals"]["deposits"] == 1 and _minor(result["totals"]["deposited"]) == 32400
    head, first, second = result["rows"]
    assert head["date"] == "2026-10-10" and head["memo"] == "Friday takings"
    assert first["transaction_type"] == second["transaction_type"] == "sales_receipt"
    assert first["date"] == "2026-10-09" and first["party_name"] == "Commercial Example Customer"
    outside = _run(client, "report deposit-detail", {"date_from": "2026-10-11", "date_to": DEMO_AS_OF})
    assert outside["rows"] == [] and outside["totals"]["deposits"] == 0


# --- transaction list by date -------------------------------------------------------------
#
# The buying fortnight, 2026-11-20 to 2026-12-02, one row per posting:
#   DEMO-BUY-PAY-1  bill payment from Payment Example Bank, pays A/P            300.80
#   check 1         Payment Example Bank to Regional Parts, fittings expense     184.00
#   card charge 2   Business Credit Card, Regional Parts, fuel expense            96.40
#   DEMO-BUY-CREDIT-1  vendor credit, A/P against the valves expense              21.90
#   transfer 3      card paid down from Payment Example Bank                      96.40
#   DEMO-COUNT      inventory adjustment: two valves written off to expense       21.90
#   check 4         half a kit to stock and a delivery expense: two splits        11.00
#   card charge 5   two kits to stock on the card                                 20.00
FORTNIGHT = [
    ("2026-11-20", "bill_payment", None, "DEMO-BUY-PAY-1", "Central Supply", "Payment Example Bank", "2000 · Accounts Payable", 30080),
    ("2026-11-22", "journal_entry", "check", "1", "Regional Parts", "Payment Example Bank", "6400 · Professional Fees", 18400),
    ("2026-11-24", "journal_entry", "card_charge", "2", "Regional Parts", "Business Credit Card", "6400 · Professional Fees", 9640),
    ("2026-11-26", "vendor_credit", None, "DEMO-BUY-CREDIT-1", "Central Supply", "2000 · Accounts Payable", "6400 · Professional Fees", 2190),
    ("2026-11-28", "journal_entry", "transfer", "3", None, "Payment Example Bank", "Business Credit Card", 9640),
    ("2026-11-30", "journal_entry", None, "DEMO-COUNT", None, "1300 · Inventory Asset", "6400 · Professional Fees", 2190),
    ("2026-12-01", "journal_entry", "check", "4", "Regional Parts", "Payment Example Bank", "-SPLIT-", 1100),
    ("2026-12-02", "journal_entry", "card_charge", "5", "Regional Parts", "Business Credit Card", "1300 · Inventory Asset", 2000),
]


def test_transaction_list_by_date_is_every_posting_in_order(client):
    result = _run(client, "report transaction-list-by-date", {"date_from": "2026-11-20", "date_to": "2026-12-02"})
    assert [(row["date"], row["transaction_type"], row["money_out_kind"], row["number"], row["party_name"],
             row["display_account_label"], row["split_account_label"], _minor(row["amount"]))
            for row in result["rows"]] == FORTNIGHT
    assert result["totals"]["transactions"] == len(FORTNIGHT)
    # A void is the original and its reversal on the original date, netting to nothing.
    voided = _run(client, "report transaction-list-by-date", {"date_from": "2026-10-08", "date_to": "2026-10-08"})
    assert [(row["number"], row["batch_kind"], _minor(row["amount"])) for row in voided["rows"]] == [
        ("DEMO-PAY-P-VOID", "original", 1000), ("DEMO-PAY-P-VOID", "reversal", -1000)]



# --- 1099 summary (R134) -----------------------------------------------------------------
#
#   Summit Pipe Contracting, marked 1099-eligible:
#     DEMO-1099-PAY-1  2026-12-08  check from Payment Example Bank           2,400.00
#     DEMO-1099-PAY-2  2026-12-09  paid on Business Credit Card: the card
#                      company reports it, so shown and not counted          150.00
#   2026 threshold 2,000.00, met. Regional Parts is paid by check too, but is not marked
#   eligible, and Central Supply is not either.
def test_1099_summary_counts_bank_payments_to_eligible_vendors(client):
    result = _run(client, "report vendor-1099-summary", {"date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert result["metadata"]["basis"] == "cash"
    assert [(row["display_vendor_label"], row["box"], _minor(row["payments"]),
             _minor(row["card_payments_excluded"]), row["meets_threshold"]) for row in result["rows"]] == [
        ("Summit Pipe Contracting", "nonemployee_compensation", 240000, 15000, True)]
    assert {key: (_minor(value) if isinstance(value, dict) else value)
            for key, value in result["totals"].items()} == {
        "threshold": 200000, "reportable": 240000, "vendors_meeting_threshold": 1,
        "payments": 240000, "opening_payments": 0, "card_payments_excluded": 15000}
    # Before the check was written nothing was paid, so nobody meets the threshold, and with
    # the filter off the card payment is still not counted.
    early = _run(client, "report vendor-1099-summary", {"date_from": "2026-01-01", "date_to": "2026-12-08",
                                                         "above_threshold_only": False})
    assert [(_minor(row["payments"]), _minor(row["card_payments_excluded"])) for row in early["rows"]] == [(240000, 0)]
    none = _run(client, "report vendor-1099-summary", {"date_from": "2026-01-01", "date_to": "2026-12-07",
                                                        "above_threshold_only": False})
    assert none["rows"] == [] and _minor(none["totals"]["reportable"]) == 0
    # The threshold is the year's: 600.00 for 2025 payments.
    last_year = _run(client, "report vendor-1099-summary", {"date_from": "2025-01-01", "date_to": "2025-12-31"})
    assert _minor(last_year["totals"]["threshold"]) == 60000 and last_year["rows"] == []


def test_1099_summary_opens_on_the_last_calendar_year():
    from bookflow.adapters.workbench import date_defaults
    attempted = {}
    date_defaults.seed("report vendor-1099-summary", "2027-01-15", initial_get=True, query={},
                       originals={}, attempted=attempted)
    assert attempted == {"f:date_from": "2026-01-01", "f:date_to": "2026-12-31"}
    kept = {}
    date_defaults.seed("report vendor-1099-summary", "2027-01-15", initial_get=True,
                       query={"f:date_from": "2026-01-01", "f:date_to": "2026-12-31"}, originals={}, attempted=kept)
    assert kept == {"f:date_from": "2026-01-01", "f:date_to": "2026-12-31"}


# --- the report pages -------------------------------------------------------------------
#
# Each page opens already run on the filters its link carries, shows the report's own
# headline figure, and offers the whole report as print and CSV.
PAGES = {
    "customer-balance-summary": ({"f:as_of": DEMO_AS_OF}, "616.21"),
    "customer-balance-detail": ({"f:as_of": DEMO_AS_OF}, "616.21"),
    "vendor-balance-summary": ({"f:as_of": DEMO_AS_OF}, "78.10"),
    "vendor-balance-detail": ({"f:as_of": DEMO_AS_OF}, "78.10"),
    "open-purchase-orders": ({"f:date_to": DEMO_AS_OF}, "56.00"),
    "purchases-by-vendor": ({"f:date_from": "2026-01-01", "f:date_to": DEMO_AS_OF}, "388.97"),
    "purchases-by-item": ({"f:date_from": "2026-01-01", "f:date_to": DEMO_AS_OF}, "388.97"),
    "deposit-detail": ({"f:date_from": "2026-01-01", "f:date_to": DEMO_AS_OF}, "324.00"),
    "transaction-list-by-date": ({"f:date_from": "2026-11-20", "f:date_to": "2026-12-02"}, "300.80"),
    "vendor-1099-summary": ({"f:date_from": "2026-01-01", "f:date_to": "2026-12-31"}, "2,400.00"),
}


@pytest.mark.parametrize("verb", sorted(PAGES))
def test_each_report_page_opens_with_its_figures(hosted, verb):  # noqa: F811
    from urllib.parse import urlencode
    from fastapi.testclient import TestClient
    from tests.test_row3_host import PASSWORD
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    query, figure = PAGES[verb]
    page = browser.get(f"/c/{hosted.company_id}/report/{verb}?" + urlencode(query))
    assert page.status_code == 200, page.text[:400]
    assert 'id="everyday-rows"' in page.text, page.text[:2000]
    assert figure in page.text
    assert "/print-all" in page.text and "/export.csv" in page.text
    exported = browser.get(f"/c/{hosted.company_id}/report/{verb}/export.csv?" + urlencode(query))
    assert exported.status_code == 200 and figure.replace(",", "") in exported.text
    printed = browser.get(f"/c/{hosted.company_id}/report/{verb}/print-all?" + urlencode(query))
    assert printed.status_code == 200 and figure in printed.text
