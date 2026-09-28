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
    ("Payment Example Customer:Job A", 2000),
    ("Payment Example Customer:Job B", -1000),
    ("Tax Rounding Example Customer", 33),
    ("Tax Work Example Customer", 6),
]
CUSTOMER_DETAIL = [
    # customer, kind, number, amount, running balance
    ("Commercial Example Customer", "activity", "DEMO-SALE-INV-ACTIVE", 12800, 12800),
    ("Commercial Example Customer", "total", None, 12800, 12800),
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
#                    DEMO-KIT-BILL and bill 1 bill received goods: they
#                    move the receipts' payable onto themselves and
#                    change nothing the vendor is owed.               =  78.10
VENDOR_DETAIL = [
    ("activity", "DEMO-BUY-BILL-1", 30080, 30080),
    ("activity", "DEMO-BUY-PAY-1", -30080, 0),
    ("activity", "DEMO-BUY-CREDIT-1", -2190, -2190),
    ("activity", "6", 6000, 3810),
    ("activity", "7", 400, 4210),
    ("activity", "8", 3600, 7810),
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
    assert _minor(result["totals"]["balance"]) == 13839
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


# --- the report pages -------------------------------------------------------------------
#
# Each page opens already run on the filters its link carries, shows the report's own
# headline figure, and offers the whole report as print and CSV.
PAGES = {
    "customer-balance-summary": ({"f:as_of": DEMO_AS_OF}, "138.39"),
    "customer-balance-detail": ({"f:as_of": DEMO_AS_OF}, "138.39"),
    "vendor-balance-summary": ({"f:as_of": DEMO_AS_OF}, "78.10"),
    "vendor-balance-detail": ({"f:as_of": DEMO_AS_OF}, "78.10"),
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
    assert exported.status_code == 200 and figure in exported.text
    printed = browser.get(f"/c/{hosted.company_id}/report/{verb}/print-all?" + urlencode(query))
    assert printed.status_code == 200 and figure in printed.text
