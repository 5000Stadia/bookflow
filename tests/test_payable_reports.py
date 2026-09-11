"""Aging and unpaid-bill oracles from one bookkeeper's real payables month.

Every expected figure below was computed by hand from the documents the fixture
posts, and the aging is checked against the Accounts Payable balance that the
balance sheet and the trial balance independently report for the same date.
"""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.company.aging import bucket_of, days_past_due
from bookflow.company import receivable_reports
from tests.test_row3_host import hosted, PASSWORD, WB  # noqa: F401

COMPANY = "Demo Plumbing Co"
AS_OF = "2026-06-30"
COLUMNS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90", "total")

# Hand-computed from the fixture, in minor units. One expense line per bill, so a
# bill is exactly the amount entered on it.
#
#   Supply   AP-CURRENT   125.00 due 2026-07-15  15 days early  -> current
#            AP-TODAY     800.00 due 2026-06-30   0 days past   -> current
#            AP-30        240.50 due 2026-05-31  30 days past   -> 1-30
#            AP-90        615.00 due 2026-04-01  90 days past   -> 61-90
#   Freight  AP-31        310.00 due 2026-05-30  31 days past   -> 31-60
#            AP-FIXED     posted at 500.00 and corrected to 275.00,
#                         due 2026-06-10, 20 days past         -> 1-30
#   Parts    AP-91        700.00 due 2026-03-31  91 days past   -> over 90
#            AP-OLD       400.00 due 2026-03-01 121 days past   -> over 90
#            AP-VOID      750.00 voided, so nothing is payable on it at all
EXPECTED_ROWS = [
    ("Aging Freight LLC", dict(current=0, days_1_30=27500, days_31_60=31000, days_61_90=0, over_90=0, total=58500)),
    ("Aging Parts Inc", dict(current=0, days_1_30=0, days_31_60=0, days_61_90=0, over_90=110000, total=110000)),
    ("Aging Supply Co", dict(current=92500, days_1_30=24050, days_31_60=0, days_61_90=61500, over_90=0, total=178050)),
]
EXPECTED_TOTALS = dict(current=92500, days_1_30=51550, days_31_60=31000,
                       days_61_90=61500, over_90=110000, total=346550)
# due date, number, days past due, column, amount, applied, balance, vendor, reference
EXPECTED_UNPAID = [
    ("2026-03-01", "AP-OLD", 121, "over_90", 40000, 0, 40000, "Aging Parts Inc", "PI-9"),
    ("2026-03-31", "AP-91", 91, "over_90", 70000, 0, 70000, "Aging Parts Inc", None),
    ("2026-04-01", "AP-90", 90, "days_61_90", 61500, 0, 61500, "Aging Supply Co", None),
    ("2026-05-30", "AP-31", 31, "days_31_60", 31000, 0, 31000, "Aging Freight LLC", None),
    ("2026-05-31", "AP-30", 30, "days_1_30", 24050, 0, 24050, "Aging Supply Co", "SUP-51"),
    ("2026-06-10", "AP-FIXED", 20, "days_1_30", 27500, 0, 27500, "Aging Freight LLC", None),
    ("2026-06-30", "AP-TODAY", 0, "current", 80000, 0, 80000, "Aging Supply Co", None),
    ("2026-07-15", "AP-CURRENT", -15, "current", 12500, 0, 12500, "Aging Supply Co", "SUP-77"),
]


def build(run):
    """Post the month through whichever surface `run(command, input, reason=)` speaks."""
    expense = run("account create", {"name": "Aging purchases", "type": "expense"})["id"]
    supply = run("vendor create", {"name": "Aging Supply Co"})["id"]
    freight = run("vendor create", {"name": "Aging Freight LLC"})["id"]
    parts = run("vendor create", {"name": "Aging Parts Inc"})["id"]

    def bill(number, vendor, date, due, amount, reference):
        return run("bill post", {"number": number, "date": date, "due_date": due,
            "vendor": vendor, **({"supplier_reference": reference} if reference else {}),
            "expenses": [{"account": expense, "amount": amount}]})

    bills = {number: bill(number, vendor, date, due, amount, reference)
             for number, vendor, date, due, amount, reference in (
        ("AP-CURRENT", supply, "2026-06-01", "2026-07-15", "125.00", "SUP-77"),
        ("AP-TODAY", supply, "2026-06-02", "2026-06-30", "800.00", None),
        ("AP-30", supply, "2026-05-01", "2026-05-31", "240.50", "SUP-51"),
        ("AP-31", freight, "2026-04-01", "2026-05-30", "310.00", None),
        ("AP-90", supply, "2026-03-15", "2026-04-01", "615.00", None),
        ("AP-91", parts, "2026-03-14", "2026-03-31", "700.00", None),
        ("AP-OLD", parts, "2026-01-05", "2026-03-01", "400.00", "PI-9"),
        ("AP-VOID", parts, "2026-02-01", "2026-03-03", "750.00", None),
        ("AP-FIXED", freight, "2026-05-10", "2026-06-10", "500.00", None),
    )}
    run("bill void", {"bill": bills["AP-VOID"]["id"], "expected_version": 1},
        reason="Vendor billed us twice")
    run("bill update", {"bill": bills["AP-FIXED"]["id"], "expected_version": 1,
        "expenses": [{"account": expense, "amount": "275.00"}]},
        reason="Vendor issued a corrected invoice")
    return dict(bills=bills, expense=expense, supply=supply, freight=freight, parts=parts)


@pytest.fixture
def books(client):
    def run(command, body, *, reason=None):
        return client.run(command, body, company=COMPANY, **({"reason": reason} if reason else {}))
    return client, build(run)


def units(row):
    return {column: row[column]["minor_units"] for column in COLUMNS}


def payable_on_the_balance_sheet(client, as_of=AS_OF):
    sheet = client.run("report balance-sheet", {"date_to": as_of, "limit": 200}, company=COMPANY)
    assert sheet["next_cursor"] is None
    return sum(row["amount"]["minor_units"] for row in sheet["rows"]
               if row["account_type"] == "accounts_payable")


def test_aging_columns_are_exact_and_the_total_is_accounts_payable(books):
    client, _ = books
    aging = client.run("report ap-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    assert aging["next_cursor"] is None and aging["count"] == len(aging["rows"])
    assert aging["metadata"]["period"] == {"date_from": None, "date_to": AS_OF}
    assert aging["metadata"]["basis"] == "accrual"
    assert [(row["display_vendor_label"], units(row)) for row in aging["rows"]] == EXPECTED_ROWS
    assert units(aging["totals"]) == EXPECTED_TOTALS
    assert sum(EXPECTED_TOTALS[column] for column in COLUMNS[:-1]) == EXPECTED_TOTALS["total"]

    # The whole point of the report: it ties, and it ties to both statements.
    assert aging["totals"]["total"]["minor_units"] == payable_on_the_balance_sheet(client)
    trial = client.run("report trial-balance", {"date_to": AS_OF, "limit": 200}, company=COMPANY)
    payable = [row for row in trial["rows"] if row["current_account_name"] == "Accounts Payable"]
    # A payable is credit-normal, so the trial balance's signed net is the aging
    # total with the other sign; the magnitude is the same figure.
    assert [row["signed_net"]["minor_units"] for row in payable] == [-EXPECTED_TOTALS["total"]]
    assert all(row["active"] for row in aging["rows"])
    assert all(row["vendor_id"] and row["current_vendor_name"] for row in aging["rows"])


def test_a_voided_bill_is_absent_and_a_correction_carries_the_corrected_amount(books):
    client, made = books
    unpaid = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    numbers = {row["number"]: row for row in unpaid["rows"]}
    assert "AP-VOID" not in numbers
    assert client.run("bill show", {"bill": made["bills"]["AP-VOID"]["id"]},
                      company=COMPANY)["status"] == "voided"
    aging = client.run("report ap-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    parts = next(row for row in aging["rows"] if row["display_vendor_label"] == "Aging Parts Inc")
    # 700.00 + 400.00 only: the voided 750.00 is in no column at all.
    assert parts["over_90"]["minor_units"] == 110000

    # It is absent because it is worth nothing, not because a status filter hid
    # it: on a date inside the month it was entered it is still worth nothing.
    earlier = client.run("report unpaid-bills", {"as_of": "2026-02-15", "limit": 200}, company=COMPANY)
    assert [row["number"] for row in earlier["rows"]] == ["AP-OLD"]
    assert earlier["totals"]["balance"]["minor_units"] == 40000
    assert earlier["totals"]["balance"]["minor_units"] == payable_on_the_balance_sheet(client, "2026-02-15")

    # A correction is not a second payable: the bill is owed at what it now says.
    assert numbers["AP-FIXED"]["amount"]["minor_units"] == 27500
    assert numbers["AP-FIXED"]["balance"]["minor_units"] == 27500


def test_unpaid_bills_carry_due_dates_references_open_balances_and_filters(books):
    client, _ = books
    page = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    assert page["next_cursor"] is None
    assert [(row["due_date"], row["number"], row["days_past_due"], row["aging_bucket"],
             row["amount"]["minor_units"], row["applied"]["minor_units"],
             row["balance"]["minor_units"], row["display_vendor_label"],
             row["supplier_reference"]) for row in page["rows"]] == EXPECTED_UNPAID
    # Nothing can settle a bill yet, so every bill is unpaid for its whole amount.
    assert {row["settlement_status"] for row in page["rows"]} == {"unpaid"}
    assert [page["totals"][key]["minor_units"] for key in ("amount", "applied", "balance")] == [346550, 0, 346550]
    assert page["totals"]["balance"]["minor_units"] == payable_on_the_balance_sheet(client)
    assert {row["date"] for row in page["rows"]} == {
        "2026-01-05", "2026-03-14", "2026-03-15", "2026-04-01", "2026-05-01",
        "2026-05-10", "2026-06-02", "2026-06-01"}

    past_due = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 200, "past_due_only": True}, company=COMPANY)
    assert [row["number"] for row in past_due["rows"]] == [row[1] for row in EXPECTED_UNPAID if row[2] > 0]
    supply = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 200, "vendor": "Aging Supply Co"}, company=COMPANY)
    assert [row["number"] for row in supply["rows"]] == ["AP-90", "AP-30", "AP-TODAY", "AP-CURRENT"]
    assert supply["totals"]["balance"]["minor_units"] == 178050
    with pytest.raises(BookflowError) as caught:
        client.run("report unpaid-bills", {"as_of": AS_OF, "vendor": "No Such Vendor"}, company=COMPANY)
    assert caught.value.code == "E_RECORD_NOT_FOUND"


def test_a_payable_journal_entry_ages_on_its_own_date_and_the_aging_still_ties(books):
    client, made = books
    accounts = client.run("account query", {"limit": 200}, company=COMPANY)["items"]
    payable_account = next(row["id"] for row in accounts if row["type"] == "accounts_payable")
    # A vendor credit entered as a journal: it reduces Accounts Payable without
    # belonging to any bill, so it is the payables twin of unapplied customer credit.
    client.run("journal post", {"date": "2026-06-20", "lines": [
        {"account": payable_account, "side": "debit", "amount": "100.00",
         "name_type": "vendor", "name_id": made["supply"]},
        {"account": made["expense"], "side": "credit", "amount": "100.00"}]}, company=COMPANY)
    aging = client.run("report ap-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    supply = next(row for row in aging["rows"] if row["display_vendor_label"] == "Aging Supply Co")
    # 10 days old on the as-of date, so it lands in 1-30 on its own date rather
    # than being netted against a bill it did not pay.
    assert supply["days_1_30"]["minor_units"] == 24050 - 10000
    assert units(aging["totals"]) == {**EXPECTED_TOTALS, "days_1_30": 41550, "total": 336550}
    assert aging["totals"]["total"]["minor_units"] == payable_on_the_balance_sheet(client)

    # Unpaid bills lists bills, so it is payables before the credit and says so.
    unpaid = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    assert unpaid["totals"]["balance"]["minor_units"] == 346550
    assert unpaid["totals"]["balance"]["minor_units"] - 10000 == payable_on_the_balance_sheet(client)


def test_payables_age_by_the_same_rule_receivables_do():
    # One rule, one home: A/P 1-30 and A/R 1-30 are the same number of days.
    assert (bucket_of, days_past_due) == (receivable_reports.bucket_of, receivable_reports.days_past_due)
    for aging_date, column in (("2026-07-15", "current"), ("2026-06-30", "current"),
                               ("2026-06-29", "days_1_30"), ("2026-05-31", "days_1_30"),
                               ("2026-05-30", "days_31_60"), ("2026-05-01", "days_31_60"),
                               ("2026-04-30", "days_61_90"), ("2026-04-01", "days_61_90"),
                               ("2026-03-31", "over_90")):
        assert bucket_of(AS_OF, aging_date) == column, aging_date
    assert [days_past_due(AS_OF, date) for date in
            ("2026-05-31", "2026-05-30", "2026-04-01", "2026-03-31", "2026-07-15")] == [30, 31, 90, 91, -15]


def test_an_as_of_date_before_the_first_possible_due_date_does_not_underflow(client):
    aging = client.run("report ap-aging", {"as_of": "0001-01-05"}, company=COMPANY)
    assert aging["rows"] == [] and aging["totals"]["total"]["minor_units"] == 0
    bills = client.run("report unpaid-bills", {"as_of": "0001-01-05"}, company=COMPANY)
    assert bills["rows"] == [] and bills["totals"]["balance"]["minor_units"] == 0
    with pytest.raises(BookflowError) as caught:
        client.run("report ap-aging", {"as_of": "2026-13-01"}, company=COMPANY)
    assert caught.value.code == "E_VALIDATION"


def test_pages_join_up_and_a_continuation_stales_on_a_company_write(books):
    client, _ = books
    whole = client.run("report ap-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    walked, cursor = [], None
    while True:
        page = client.run("report ap-aging", {"as_of": AS_OF, "limit": 1,
            **({"cursor": cursor} if cursor else {})}, company=COMPANY)
        assert page["count"] == len(page["rows"]) <= 1
        assert units(page["totals"]) == EXPECTED_TOTALS
        walked += page["rows"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert walked == whole["rows"]

    first = client.run("report ap-aging", {"as_of": AS_OF, "limit": 1}, company=COMPANY)
    # A cursor belongs to one report and one filter set, never to its neighbour.
    for other, body in (("report unpaid-bills", {"as_of": AS_OF, "limit": 1}),
                        ("report ar-aging", {"as_of": AS_OF, "limit": 1}),
                        ("report ap-aging", {"as_of": "2026-06-29", "limit": 1})):
        with pytest.raises(BookflowError) as caught:
            client.run(other, {**body, "cursor": first["next_cursor"]}, company=COMPANY)
        assert caught.value.code == "E_VALIDATION", other
    # Renaming a vendor relabels and reorders the rows this cursor counted past.
    vendor = client.vendor.create(name="Aging cursor witness", company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run("report ap-aging", {"as_of": AS_OF, "limit": 1,
                                       "cursor": first["next_cursor"]}, company=COMPANY)
    assert caught.value.code == "E_QUERY_STALE"
    assert vendor["id"]


def test_a_renamed_vendor_stales_the_unpaid_bills_continuation_it_was_filtered_to(books):
    client, made = books
    first = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 1,
        "vendor": "Aging Supply Co"}, company=COMPANY)
    assert [row["number"] for row in first["rows"]] == ["AP-90"] and first["next_cursor"]
    second = client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 1,
        "vendor": "Aging Supply Co", "cursor": first["next_cursor"]}, company=COMPANY)
    assert [row["number"] for row in second["rows"]] == ["AP-30"]
    supply = client.run("vendor show", {"vendor": made["supply"]}, company=COMPANY)
    client.run("vendor update", {"vendor": made["supply"], "expected_version": supply["version"],
                                 "name": "Aging Supply Company"}, company=COMPANY)
    # The cursor carries the resolved id, so the rename stales it rather than
    # turning page two into a record-not-found for a name nobody asked for.
    with pytest.raises(BookflowError) as caught:
        client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 1,
            "vendor": "Aging Supply Co", "cursor": first["next_cursor"]}, company=COMPANY)
    assert caught.value.code == "E_QUERY_STALE"


def settled(document):
    """One report reading, with the two fields no two readings ever share."""
    document = dict(document, metadata=dict(document["metadata"]))
    document["metadata"].pop("generation_time")
    document.pop("next_cursor")
    return document


def test_the_cli_prints_the_same_payables_it_returns_as_json(books, cli):
    client, _ = books
    assert settled(cli.json("report", "ap-aging", "--as-of", AS_OF, "--limit", "200", "--company", COMPANY)) == \
        settled(client.run("report ap-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY))
    assert settled(cli.json("report", "unpaid-bills", "--as-of", AS_OF, "--limit", "200",
                            "--past-due-only", "--company", COMPANY)) == \
        settled(client.run("report unpaid-bills", {"as_of": AS_OF, "limit": 200, "past_due_only": True}, company=COMPANY))
    # The default renderer has to show the money, not swallow it.
    printed = cli.run("report", "ap-aging", "--as-of", AS_OF, "--limit", "200", "--company", COMPANY).stdout
    assert "Aging Freight LLC" in printed and "3465.50" in printed and "585.00" in printed


def test_the_host_and_the_workbench_serve_the_payables(hosted):  # noqa: F811
    from fastapi.testclient import TestClient
    cid = hosted.company_id

    def run(command, body, *, reason=None):
        headers = {"X-Bookflow-Reason": reason} if reason else None
        return hosted.ok(command.replace(" ", "."), body, company=cid, headers=headers)

    build(run)
    assert units(run("report ap-aging", {"as_of": AS_OF, "limit": 200})["totals"]) == EXPECTED_TOTALS
    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    for name, table, total in (("ap-aging", "payables-aging", "3465.50"),
                               ("unpaid-bills", "payables-unpaid", "3465.50")):
        page = api.get(f"/c/{cid}/report/{name}", headers=WB)
        assert page.status_code == 200 and 'name="f:as_of"' in page.text
        # The filter form restarts the report; the continuation lives on the result.
        assert 'name="f:cursor"' not in page.text
        result = api.post(f"/c/{cid}/report/{name}", data={"f:as_of": AS_OF, "f:limit": "200"}, headers=WB)
        assert result.status_code == 200, result.text[:400]
        # A designed table, not a JSON blob: the columns, the rows and the tie.
        assert f'id="{table}"' in result.text and 'id="payables-totals"' in result.text
        assert "Aging Freight LLC" in result.text, result.text[:2000]
        key = "total" if name == "ap-aging" else "balance"
        assert f'<td data-total="{key}">{total}</td>' in result.text, result.text[:2000]
    aging = api.post(f"/c/{cid}/report/ap-aging", data={"f:as_of": AS_OF, "f:limit": "200"}, headers=WB).text
    for heading in ("Current", "1-30", "31-60", "61-90", "Over 90"):
        assert f'<th scope="col">{heading}</th>' in aging
    # Each vendor opens their own open bills; each bill opens itself.
    assert f'/c/{cid}/report/unpaid-bills?f%3Aas_of={AS_OF}&amp;f%3Avendor=' in aging, aging[:2000]
    listed = api.post(f"/c/{cid}/report/unpaid-bills", data={"f:as_of": AS_OF, "f:limit": "200"}, headers=WB).text
    assert f'href="/c/{cid}/bill/' in listed and ">AP-FIXED</a>" in listed
    assert "AP-VOID" not in listed
    assert "SUP-51" in listed
    # The report picker offers both alongside the seven that were there before.
    picker = api.get(f"/c/{cid}/_group/reports", headers=WB).text
    for href in ("/report/ap-aging", "/report/unpaid-bills", "/report/ar-aging",
                 "/report/open-invoices", "/report/statement", "/report/trial-balance",
                 "/report/general-ledger", "/report/profit-and-loss", "/report/balance-sheet"):
        assert f'href="/c/{cid}{href}"' in picker, href
    # Each page is titled with the report a bookkeeper asks for, not the command.
    for verb, title in (("ap-aging", "A/P aging summary"), ("unpaid-bills", "Unpaid bills")):
        opened = api.get(f"/c/{cid}/report/{verb}", headers=WB).text
        assert f"<h1>{title}</h1>" in opened and f"report {verb}" not in opened
    hosted.handle.stop()
