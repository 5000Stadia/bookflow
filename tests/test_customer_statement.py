"""One customer's account, read the way the customer reads it, from real documents.

Every figure below was computed by hand from the documents the fixture posts and
then checked against three independent readings of the same books: the aging
summary, the open-invoice list, and Accounts Receivable on the balance sheet. A
statement that does not tie is worse than no statement, because the customer
does the arithmetic that was skipped here.
"""
import re

import pytest

from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted, PASSWORD, WB  # noqa: F401

COMPANY = "Demo Plumbing Co"
FROM, TO = "2026-06-01", "2026-06-30"
BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90", "total")

# The service item is 10.00 and carries a non-taxable code, so an invoice is
# exactly quantity x 10.00.
#
#   Quill Signage     STM-050   250.00  2026-03-02, due 2026-04-01, never paid.
#                               Nothing at all happens to it in June.
#   Marlow Tooling    STM-060   120.00  2026-02-01, paid in full 2026-02-15.
#                               Owes nothing and did nothing in June.
#   Rowan Fabrication STM-100   300.00  2026-04-10, due 2026-05-10
#                     STM-101   200.00  2026-05-20, due 2026-06-19
#                     STM-VOID  700.00  2026-06-05, voided
#                     STM-200   400.00  2026-06-08, due 2026-07-08
#                     PMT-300   300.00  2026-06-12, all of it onto STM-100
#                     PMT-310   150.00  2026-06-18, onto STM-101, which leaves 50.00
#                     PMT-320   250.00  2026-06-22, 180.00 onto the job's STM-400,
#                                       so 70.00 of it stays as unapplied credit
#   ...:Shop Floor    STM-400   180.00  2026-06-10, due 2026-06-25
#
# Rowan Fabrication, by hand:
#   opening   300.00 + 200.00                              =   500.00
#   06-08     STM-200   + 400.00                           =   900.00
#   06-12     PMT-300   - 300.00                           =   600.00
#   06-18     PMT-310   - 150.00                           =   450.00
#   06-22     PMT-320   -  70.00 (the part nobody's invoice claimed)
#   closing                                                =   380.00
# Shop Floor, by hand: 0.00 + 180.00 - 180.00              =     0.00
# Quill Signage, by hand: 250.00 brought forward, nothing  =   250.00
#   whole report                                           =   630.00
#
# customer, kind, entry, date, number, amount, running balance
EXPECTED_ROWS = [
    ("Quill Signage", "opening", "balance_forward", None, None, 25000, 25000),
    ("Quill Signage", "closing", "balance_due", None, None, 0, 25000),
    ("Rowan Fabrication", "opening", "balance_forward", None, None, 50000, 50000),
    ("Rowan Fabrication", "activity", "invoice", "2026-06-08", "STM-200", 40000, 90000),
    ("Rowan Fabrication", "activity", "payment", "2026-06-12", "PMT-300", -30000, 60000),
    ("Rowan Fabrication", "activity", "payment", "2026-06-18", "PMT-310", -15000, 45000),
    ("Rowan Fabrication", "activity", "payment", "2026-06-22", "PMT-320", -7000, 38000),
    ("Rowan Fabrication", "closing", "balance_due", None, None, 0, 38000),
    ("Rowan Fabrication:Shop Floor", "opening", "balance_forward", None, None, 0, 0),
    ("Rowan Fabrication:Shop Floor", "activity", "invoice", "2026-06-10", "STM-400", 18000, 18000),
    ("Rowan Fabrication:Shop Floor", "activity", "payment", "2026-06-22", "PMT-320", -18000, 0),
    ("Rowan Fabrication:Shop Floor", "closing", "balance_due", None, None, 0, 0),
]
EXPECTED_TOTALS = dict(opening=75000, charges=58000, credits=-70000, closing=63000)
# STM-200 is not due until 2026-07-08; STM-101's 50.00 is 11 days past due and the
# 70.00 of credit is 8 days old, so they share a column and partly cancel there;
# STM-050 is due exactly 90 days before the as-of date, which is the 61-90 edge.
EXPECTED_AGING = dict(current=40000, days_1_30=-2000, days_31_60=0,
                      days_61_90=25000, over_90=0, total=63000)


def build(run):
    """Post the month through whichever surface `run(command, input, reason=)` speaks."""
    income = run("account create", {"name": "Statement income", "type": "income"})["id"]
    code = next(row["id"] for row in run("sales-tax-code list", {})["items"] if not row["taxable"])
    item = run("item create", {"name": "Statement service", "type": "service", "sales_enabled": True,
        "description": "Labor", "income_account_id": income, "price": "10.00",
        "sales_tax_code_id": code})["id"]
    rowan = run("customer create", {"name": "Rowan Fabrication"})["id"]
    shop = run("customer create", {"name": "Shop Floor", "parent_id": rowan})["id"]
    quill = run("customer create", {"name": "Quill Signage"})["id"]
    marlow = run("customer create", {"name": "Marlow Tooling"})["id"]
    method = run("payment-method list", {})["items"][0]["id"]

    invoices = {number: run("invoice post", {"number": number, "date": date, "due_date": due,
            "customer": customer, "lines": [{"item": item, "quantity": str(tens)}]})
        for number, customer, date, due, tens in (
            ("STM-050", quill, "2026-03-02", "2026-04-01", 25),
            ("STM-060", marlow, "2026-02-01", "2026-03-01", 12),
            ("STM-100", rowan, "2026-04-10", "2026-05-10", 30),
            ("STM-101", rowan, "2026-05-20", "2026-06-19", 20),
            ("STM-VOID", rowan, "2026-06-05", "2026-07-05", 70),
            ("STM-200", rowan, "2026-06-08", "2026-07-08", 40),
            ("STM-400", shop, "2026-06-10", "2026-06-25", 18))}
    run("invoice void", {"invoice": invoices["STM-VOID"]["id"], "expected_version": 1},
        reason="Billed the wrong job")

    def receive(number, customer, date, amount, invoice, applied):
        run("payment receive", {"customer": customer, "date": date, "amount": amount,
            "number": number, "operation_key": "statement-" + number, "payment_method": method,
            "applications": {"mode": "inline", "items": [
                {"invoice": invoices[invoice]["id"], "expected_version": 1, "amount": applied}]}})

    receive("PMT-290", marlow, "2026-02-15", "120.00", "STM-060", "120.00")
    receive("PMT-300", rowan, "2026-06-12", "300.00", "STM-100", "300.00")
    receive("PMT-310", rowan, "2026-06-18", "150.00", "STM-101", "150.00")
    # New cash is owned by the party whose invoice it settles, so 180.00 of this
    # belongs to the job and the remaining 70.00 stays credit on the parent.
    receive("PMT-320", rowan, "2026-06-22", "250.00", "STM-400", "180.00")
    return dict(invoices=invoices, rowan=rowan, shop=shop, quill=quill, marlow=marlow)


@pytest.fixture
def books(client):
    def run(command, body, *, reason=None):
        return client.run(command, body, company=COMPANY, **({"reason": reason} if reason else {}))
    return client, build(run)


def read(client, **overrides):
    return client.run("report statement", {"date_from": FROM, "date_to": TO, "limit": 200,
                                           **overrides}, company=COMPANY)


def shape(page):
    return [(row["display_customer_label"], row["kind"], row["entry"], row["date"], row["number"],
             row["amount"]["minor_units"], row["balance"]["minor_units"]) for row in page["rows"]]


def units(group, keys):
    return {key: group[key]["minor_units"] for key in keys}


def receivable_on_the_balance_sheet(client, as_of=TO):
    sheet = client.run("report balance-sheet", {"date_to": as_of, "limit": 200}, company=COMPANY)
    assert sheet["next_cursor"] is None
    return sum(row["amount"]["minor_units"] for row in sheet["rows"]
               if row["account_type"] == "accounts_receivable")


def test_the_running_balance_is_exact_and_the_closing_total_is_accounts_receivable(books):
    client, _ = books
    page = read(client)
    assert page["next_cursor"] is None and page["count"] == len(page["rows"])
    assert page["metadata"]["period"] == {"date_from": FROM, "date_to": TO}
    assert page["metadata"]["basis"] == "accrual"
    assert shape(page) == EXPECTED_ROWS
    assert units(page["totals"], EXPECTED_TOTALS) == EXPECTED_TOTALS
    # Every row between the two balances, and nothing else, moves one into the other.
    assert (EXPECTED_TOTALS["opening"] + EXPECTED_TOTALS["charges"] + EXPECTED_TOTALS["credits"]
            == EXPECTED_TOTALS["closing"])
    assert sum(row[5] for row in EXPECTED_ROWS if row[1] == "activity") == \
        EXPECTED_TOTALS["charges"] + EXPECTED_TOTALS["credits"]

    # The whole point of the document: it ties, and it ties to the other readings.
    assert page["totals"]["closing"]["minor_units"] == receivable_on_the_balance_sheet(client)
    assert units(page["aging"], EXPECTED_AGING) == EXPECTED_AGING
    assert sum(EXPECTED_AGING[column] for column in BUCKETS[:-1]) == EXPECTED_AGING["total"]
    assert EXPECTED_AGING["total"] == EXPECTED_TOTALS["closing"]

    aging = client.run("report ar-aging", {"as_of": TO, "limit": 200}, company=COMPANY)
    assert units(aging["totals"], BUCKETS) == units(page["aging"], BUCKETS)
    # Every customer's closing balance is the balance the aging shows for them.
    closing = {row["display_customer_label"]: row["balance"]["minor_units"]
               for row in page["rows"] if row["kind"] == "closing"}
    aged = {row["display_customer_label"]: row["total"]["minor_units"] for row in aging["rows"]}
    assert closing == {"Quill Signage": 25000, "Rowan Fabrication": 38000,
                       "Rowan Fabrication:Shop Floor": 0}
    assert aged == {name: amount for name, amount in closing.items() if amount}
    assert sum(closing.values()) == receivable_on_the_balance_sheet(client)


def test_one_receipt_lands_on_the_two_statements_that_share_it(books):
    client, _ = books
    rows = [row for row in read(client)["rows"] if row["number"] == "PMT-320"]
    # 250.00 arrived from the parent; the part that settled the job's invoice is
    # the job's, and only the rest is credit standing on the parent.
    assert [(row["display_customer_label"], row["amount"]["minor_units"]) for row in rows] == [
        ("Rowan Fabrication", -7000), ("Rowan Fabrication:Shop Floor", -18000)]
    assert sum(row["amount"]["minor_units"] for row in rows) == -25000

    # That unapplied 70.00 is the whole difference between what the invoices say
    # is owed and what the customer's account actually stands at.
    invoices = client.run("report open-invoices", {"as_of": TO, "limit": 200}, company=COMPANY)
    assert [(row["number"], row["balance"]["minor_units"]) for row in invoices["rows"]] == [
        ("STM-050", 25000), ("STM-101", 5000), ("STM-200", 40000)]
    assert invoices["totals"]["balance"]["minor_units"] - 7000 == EXPECTED_TOTALS["closing"]


def test_a_voided_invoice_has_no_row_because_it_is_worth_nothing(books):
    client, made = books
    assert "STM-VOID" not in {row["number"] for row in read(client)["rows"]}
    assert client.run("invoice show", {"invoice": made["invoices"]["STM-VOID"]["id"]},
                      company=COMPANY)["status"] == "voided"
    # No status was consulted. Read a period that contains the day the invoice was
    # written and nothing else: on that day it was already worth nothing, because
    # the void reverses it at its own date, so there is no amount to print.
    inside = read(client, date_from="2026-06-05", date_to="2026-06-06")
    assert shape(inside) == [
        ("Quill Signage", "opening", "balance_forward", None, None, 25000, 25000),
        ("Quill Signage", "closing", "balance_due", None, None, 0, 25000),
        ("Rowan Fabrication", "opening", "balance_forward", None, None, 50000, 50000),
        ("Rowan Fabrication", "closing", "balance_due", None, None, 0, 50000)]
    assert units(inside["totals"], EXPECTED_TOTALS) == dict(opening=75000, charges=0, credits=0, closing=75000)
    assert inside["aging"]["total"]["minor_units"] == 75000
    assert inside["totals"]["closing"]["minor_units"] == receivable_on_the_balance_sheet(client, "2026-06-06")


def test_a_customer_who_did_nothing_still_gets_their_brought_forward_balance(books):
    client, _ = books
    quill = read(client, customer="Quill Signage")
    assert shape(quill) == EXPECTED_ROWS[:2]
    assert units(quill["totals"], EXPECTED_TOTALS) == dict(opening=25000, charges=0, credits=0, closing=25000)
    assert units(quill["aging"], BUCKETS) == dict(current=0, days_1_30=0, days_31_60=0,
                                                  days_61_90=25000, over_90=0, total=25000)

    # A customer with no balance and no activity is not printed at all, which
    # cannot move a total; asking for that customer by name still answers.
    assert "Marlow Tooling" not in {row["display_customer_label"] for row in read(client)["rows"]}
    marlow = read(client, customer="Marlow Tooling")
    assert shape(marlow) == [
        ("Marlow Tooling", "opening", "balance_forward", None, None, 0, 0),
        ("Marlow Tooling", "closing", "balance_due", None, None, 0, 0)]
    assert units(marlow["totals"], EXPECTED_TOTALS) == dict(opening=0, charges=0, credits=0, closing=0)
    assert units(marlow["aging"], BUCKETS) == dict.fromkeys(BUCKETS, 0)

    # A customer who has never been invoiced at all has no receivable effect of
    # any kind to group, which is a different empty from Marlow's netted-out one.
    client.customer.create(name="Never Invoiced", company=COMPANY)
    never = read(client, customer="Never Invoiced")
    assert shape(never) == [
        ("Never Invoiced", "opening", "balance_forward", None, None, 0, 0),
        ("Never Invoiced", "closing", "balance_due", None, None, 0, 0)]
    assert units(never["totals"], EXPECTED_TOTALS) == dict(opening=0, charges=0, credits=0, closing=0)
    assert units(never["aging"], BUCKETS) == dict.fromkeys(BUCKETS, 0)


def test_one_customer_is_one_statement_and_a_job_is_its_own_customer(books):
    client, made = books
    parent = read(client, customer="Rowan Fabrication")
    assert shape(parent) == EXPECTED_ROWS[2:8]
    assert units(parent["totals"], EXPECTED_TOTALS) == dict(opening=50000, charges=40000, credits=-52000, closing=38000)
    assert units(parent["aging"], BUCKETS) == dict(current=40000, days_1_30=-2000, days_31_60=0,
                                                   days_61_90=0, over_90=0, total=38000)
    assert parent["aging"]["total"] == parent["totals"]["closing"]

    job = read(client, customer="Rowan Fabrication:Shop Floor")
    assert shape(job) == EXPECTED_ROWS[8:]
    assert units(job["totals"], EXPECTED_TOTALS) == dict(opening=0, charges=18000, credits=-18000, closing=0)
    assert units(job["aging"], BUCKETS) == dict.fromkeys(BUCKETS, 0)
    assert [row["parent_id"] for row in job["rows"]] == [made["rowan"]] * 4
    assert [row["customer_id"] for row in job["rows"]] == [made["shop"]] * 4

    # Resolving by stable ID and by canonical full name reach the same statement.
    by_id = read(client, customer=made["rowan"])
    assert shape(by_id) == shape(parent) and by_id["totals"] == parent["totals"]
    with pytest.raises(BookflowError) as caught:
        read(client, customer="No Such Customer")
    assert caught.value.code == "E_RECORD_NOT_FOUND"


def test_an_invoice_row_carries_its_due_date_and_a_receipt_does_not(books):
    client, _ = books
    rows = {(row["display_customer_label"], row["number"]): row for row in read(client)["rows"]}
    invoice = rows[("Rowan Fabrication", "STM-200")]
    assert (invoice["due_date"], invoice["document_date"], invoice["date"]) == \
        ("2026-07-08", "2026-06-08", "2026-06-08")
    assert invoice["transaction_id"] and invoice["memo"] is None
    payment = rows[("Rowan Fabrication", "PMT-300")]
    assert payment["due_date"] is None and payment["document_date"] == "2026-06-12"
    forward = rows[("Quill Signage", None)]
    assert (forward["date"], forward["transaction_id"], forward["due_date"]) == (None, None, None)
    assert all(row["active"] for row in read(client)["rows"])


def test_a_period_must_be_a_period_and_an_early_one_does_not_underflow(client):
    with pytest.raises(BookflowError) as caught:
        read(client, date_from="2026-07-01", date_to="2026-06-30")
    assert caught.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as caught:
        read(client, date_to="2026-13-01")
    assert caught.value.code == "E_VALIDATION"
    early = read(client, date_from="0001-01-01", date_to="0001-01-05")
    assert early["rows"] == [] and early["totals"]["closing"]["minor_units"] == 0
    assert early["aging"]["total"]["minor_units"] == 0


def test_pages_join_up_and_a_continuation_stales_on_a_company_write(books):
    client, _ = books
    whole = read(client)
    walked, cursor, pages = [], None, 0
    while True:
        page = read(client, limit=1, **({"cursor": cursor} if cursor else {}))
        assert page["count"] == len(page["rows"]) <= 1
        # A running balance computed per page would be wrong on every page but
        # the first, so the totals and the aging cover the whole match either way.
        assert units(page["totals"], EXPECTED_TOTALS) == EXPECTED_TOTALS
        assert units(page["aging"], BUCKETS) == EXPECTED_AGING
        walked += page["rows"]
        cursor, pages = page["next_cursor"], pages + 1
        if cursor is None:
            break
    assert walked == whole["rows"] and pages == len(EXPECTED_ROWS)

    first = read(client, limit=1)
    with pytest.raises(BookflowError) as caught:
        client.run("report ar-aging", {"as_of": TO, "limit": 1, "cursor": first["next_cursor"]},
                   company=COMPANY)
    assert caught.value.code == "E_VALIDATION"
    with pytest.raises(BookflowError) as caught:
        read(client, limit=1, customer="Rowan Fabrication", cursor=first["next_cursor"])
    assert caught.value.code == "E_VALIDATION"
    client.customer.create(name="Statement cursor witness", company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        read(client, limit=1, cursor=first["next_cursor"])
    assert caught.value.code == "E_QUERY_STALE"


def test_a_settlement_moves_two_statements_and_the_total_still_ties(books):
    client, made = books
    payment = next(row for row in client.run("payment query", {"limit": 200}, company=COMPANY)["items"]
                   if row["number"] == "PMT-320")
    applied = client.run("payment settlement", {"payment": payment["id"], "kind": "applications"},
                         company=COMPANY)["items"][0]
    client.run("payment unapply", {"payment": payment["id"], "expected_version": payment["version"],
        "operation_key": "statement-unapply", "applications": [
            {"application_id": applied["application_id"],
             "invoice_expected_version": applied["invoice_version"]}]},
        company=COMPANY, reason="Applied to the wrong job")
    after = read(client)
    # Unapplying posts nothing, so the ledger has not moved; what moved is which
    # of the two customers the credit is standing against.
    assert after["totals"]["closing"]["minor_units"] == EXPECTED_TOTALS["closing"]
    assert after["totals"]["closing"]["minor_units"] == receivable_on_the_balance_sheet(client)
    balances = {row["display_customer_label"]: row["balance"]["minor_units"]
                for row in after["rows"] if row["kind"] == "closing"}
    assert balances == {"Quill Signage": 25000, "Rowan Fabrication": 38000,
                        "Rowan Fabrication:Shop Floor": 0}
    aging = client.run("report ar-aging", {"as_of": TO, "limit": 200}, company=COMPANY)
    assert units(after["aging"], BUCKETS) == units(aging["totals"], BUCKETS)
    assert after["aging"]["total"]["minor_units"] == after["totals"]["closing"]["minor_units"]


def settled(document):
    """One report reading, with the two fields no two readings ever share."""
    document = dict(document, metadata=dict(document["metadata"]))
    document["metadata"].pop("generation_time")
    document.pop("next_cursor")
    return document


def test_the_cli_prints_the_same_statement_it_returns_as_json(books, cli):
    client, _ = books
    assert settled(cli.json("report", "statement", "--date-from", FROM, "--date-to", TO,
                            "--limit", "200", "--company", COMPANY)) == settled(read(client))
    assert settled(cli.json("report", "statement", "--date-from", FROM, "--date-to", TO,
                            "--limit", "200", "--customer", "Rowan Fabrication",
                            "--company", COMPANY)) == settled(read(client, customer="Rowan Fabrication"))
    # The default renderer has to show the money, not swallow it.
    printed = cli.run("report", "statement", "--date-from", FROM, "--date-to", TO,
                      "--limit", "200", "--company", COMPANY).stdout
    assert "Rowan Fabrication:Shop Floor" in printed
    for amount in ("500.00", "-300.00", "380.00", "630.00", "-20.00"):
        assert amount in printed, amount


def test_the_host_and_the_workbench_serve_the_statement(hosted):  # noqa: F811
    from fastapi.testclient import TestClient
    cid = hosted.company_id

    def run(command, body, *, reason=None):
        headers = {"X-Bookflow-Reason": reason} if reason else None
        return hosted.ok(command.replace(" ", "."), body, company=cid, headers=headers)

    build(run)
    served = run("report statement", {"date_from": FROM, "date_to": TO, "limit": 200})
    assert shape(served) == EXPECTED_ROWS
    assert units(served["totals"], EXPECTED_TOTALS) == EXPECTED_TOTALS

    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    form = api.get(f"/c/{cid}/report/statement", headers=WB)
    assert form.status_code == 200 and 'name="f:date_from"' in form.text
    assert "Customer statement" in form.text
    result = api.post(f"/c/{cid}/report/statement",
                      data={"f:date_from": FROM, "f:date_to": TO, "f:limit": "200"}, headers=WB)
    assert result.status_code == 200, result.text[:400]
    # A designed document, not a JSON blob: the balances, the rows and the foot.
    page = result.text[result.text.index('id="customer-statement"'):]
    assert 'id="statement-lines"' in page and 'id="statement-aging"' in page
    assert "Rowan Fabrication:Shop Floor" in page
    assert '<div data-total="closing"><dt>Balance due</dt><dd>630.00 USD</dd></div>' in page, page[:2000]
    assert '<div data-aging="days_1_30"><dt>1-30</dt><dd>-20.00 USD</dd></div>' in page, page[:2000]
    for heading in ("Current", "1-30", "31-60", "61-90", "Over 90", "Total"):
        assert f"<dt>{heading}</dt>" in page, heading
    assert "Balance forward" in page and "Balance due" in page
    assert "STM-VOID" not in page
    # Nothing on the page offers to hand it to anyone: the product cannot, and a
    # control that says otherwise is the defect this check exists to catch.
    offers = [text for text in re.findall(r"<(?:button|a)\b[^>]*>(.*?)</(?:button|a)>", page, re.S)
              if re.search(r"send|e-?mail|print|deliver|post it|mail", text, re.I)]
    assert offers == [] and "mailto:" not in page, offers

    # Each document opens where it was written; each customer opens their own statement.
    assert f'href="/c/{cid}/invoice/' in page and ">STM-200</a>" in page
    assert f'href="/c/{cid}/payment/' in page and ">PMT-320</a>" in page
    assert f'/c/{cid}/report/statement?f%3Adate_from={FROM}' in page, page[:2000]

    # A page boundary does not restart the running balance.
    paged = api.post(f"/c/{cid}/report/statement",
                     data={"f:date_from": FROM, "f:date_to": TO, "f:limit": "4"}, headers=WB)
    assert 'id="customer-statement-next-page"' in paged.text
    assert '<div data-total="closing"><dt>Balance due</dt><dd>630.00 USD</dd></div>' in paged.text
    hosted.handle.stop()
