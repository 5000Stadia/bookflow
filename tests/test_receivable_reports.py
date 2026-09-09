"""Aging and open-invoice oracles from one bookkeeper's real receivables month.

Every expected figure below was computed by hand from the documents the fixture
posts, and the aging is checked against the Accounts Receivable balance that the
balance sheet and the trial balance independently report for the same date.
"""
import pytest

from bookflow.core.errors import BookflowError
from bookflow.company.receivable_reports import bucket_of, days_past_due
from tests.test_row3_host import hosted, PASSWORD, WB  # noqa: F401

COMPANY = "Demo Plumbing Co"
AS_OF = "2026-06-30"
COLUMNS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90", "total")

# Hand-computed from the fixture, in minor units. The service item is 100.00 and
# carries a non-taxable code, so an invoice is exactly quantity x 100.00.
#
#   Alpha    AGE-CURRENT   100.00 due 2026-07-15  15 days early  -> current
#            AGE-TODAY     800.00 due 2026-06-30   0 days past   -> current
#            AGE-30        200.00 due 2026-05-31  30 days past   -> 1-30
#            AGE-90        600.00 due 2026-04-01  90 days past   -> 61-90
#            AGE-91        700.00 due 2026-03-31  91 days past   -> over 90
#   Job      AGE-31        300.00 due 2026-05-30  31 days past   -> 31-60
#   Beta     AGE-PARTLY    500.00 due 2026-04-30  61 days past   -> 61-90,
#                          less the 200.00 applied to it          = 300.00
#            AGE-PAY       250.00 received 2026-06-20 with 200.00 applied, so
#                          50.00 of credit stands, 10 days old   -> 1-30
#   Gamma    AGE-OLD       400.00 due 2026-03-01 121 days past   -> over 90
#            AGE-VOID      700.00 voided, so it is not receivable at all
EXPECTED_ROWS = [
    ("Aging Alpha", dict(current=90000, days_1_30=20000, days_31_60=0, days_61_90=60000, over_90=70000, total=240000)),
    ("Aging Alpha:North Job", dict(current=0, days_1_30=0, days_31_60=30000, days_61_90=0, over_90=0, total=30000)),
    ("Aging Beta", dict(current=0, days_1_30=-5000, days_31_60=0, days_61_90=30000, over_90=0, total=25000)),
    ("Aging Gamma", dict(current=0, days_1_30=0, days_31_60=0, days_61_90=0, over_90=40000, total=40000)),
]
EXPECTED_TOTALS = dict(current=90000, days_1_30=15000, days_31_60=30000,
                       days_61_90=90000, over_90=110000, total=335000)
# due date, number, days past due, column, amount, applied, balance, customer
EXPECTED_OPEN = [
    ("2026-03-01", "AGE-OLD", 121, "over_90", 40000, 0, 40000, "Aging Gamma"),
    ("2026-03-31", "AGE-91", 91, "over_90", 70000, 0, 70000, "Aging Alpha"),
    ("2026-04-01", "AGE-90", 90, "days_61_90", 60000, 0, 60000, "Aging Alpha"),
    ("2026-04-30", "AGE-PARTLY", 61, "days_61_90", 50000, 20000, 30000, "Aging Beta"),
    ("2026-05-30", "AGE-31", 31, "days_31_60", 30000, 0, 30000, "Aging Alpha:North Job"),
    ("2026-05-31", "AGE-30", 30, "days_1_30", 20000, 0, 20000, "Aging Alpha"),
    ("2026-06-30", "AGE-TODAY", 0, "current", 80000, 0, 80000, "Aging Alpha"),
    ("2026-07-15", "AGE-CURRENT", -15, "current", 10000, 0, 10000, "Aging Alpha"),
]


def build(run):
    """Post the month through whichever surface `run(command, input, reason=)` speaks."""
    income = run("account create", {"name": "Aging income", "type": "income"})["id"]
    code = next(row["id"] for row in run("sales-tax-code list", {})["items"] if not row["taxable"])
    item = run("item create", {"name": "Aging service", "type": "service", "sales_enabled": True,
        "description": "Labor", "income_account_id": income, "price": "100.00",
        "sales_tax_code_id": code})["id"]
    alpha = run("customer create", {"name": "Aging Alpha"})["id"]
    job = run("customer create", {"name": "North Job", "parent_id": alpha})["id"]
    beta = run("customer create", {"name": "Aging Beta"})["id"]
    gamma = run("customer create", {"name": "Aging Gamma"})["id"]

    def invoice(number, customer, date, due, quantity):
        return run("invoice post", {"number": number, "date": date, "due_date": due,
            "customer": customer, "lines": [{"item": item, "quantity": str(quantity)}]})

    invoices = {number: invoice(number, customer, date, due, quantity) for number, customer, date, due, quantity in (
        ("AGE-CURRENT", alpha, "2026-06-01", "2026-07-15", 1),
        ("AGE-TODAY", alpha, "2026-06-02", "2026-06-30", 8),
        ("AGE-30", alpha, "2026-05-01", "2026-05-31", 2),
        ("AGE-90", alpha, "2026-03-15", "2026-04-01", 6),
        ("AGE-91", alpha, "2026-03-14", "2026-03-31", 7),
        ("AGE-31", job, "2026-04-01", "2026-05-30", 3),
        ("AGE-PARTLY", beta, "2026-03-01", "2026-04-30", 5),
        ("AGE-OLD", gamma, "2026-01-05", "2026-03-01", 4),
        ("AGE-VOID", gamma, "2026-02-01", "2026-03-03", 7),
    )}
    run("invoice void", {"invoice": invoices["AGE-VOID"]["id"], "expected_version": 1},
        reason="Duplicate invoice")
    method = run("payment-method list", {})["items"][0]["id"]
    payment = run("payment receive", {"customer": beta, "date": "2026-06-20", "amount": "250.00",
        "number": "AGE-PAY", "operation_key": "aging-receipt", "payment_method": method,
        "applications": {"mode": "inline", "items": [
            {"invoice": invoices["AGE-PARTLY"]["id"], "expected_version": 1, "amount": "200.00"}]}})
    return dict(invoices=invoices, payment=payment, alpha=alpha, job=job, beta=beta, gamma=gamma)


@pytest.fixture
def books(client):
    def run(command, body, *, reason=None):
        return client.run(command, body, company=COMPANY, **({"reason": reason} if reason else {}))
    return client, build(run)


def units(row):
    return {column: row[column]["minor_units"] for column in COLUMNS}


def receivable_on_the_balance_sheet(client, as_of=AS_OF):
    sheet = client.run("report balance-sheet", {"date_to": as_of, "limit": 200}, company=COMPANY)
    assert sheet["next_cursor"] is None
    return sum(row["amount"]["minor_units"] for row in sheet["rows"]
               if row["account_type"] == "accounts_receivable")


def test_aging_columns_are_exact_and_the_total_is_accounts_receivable(books):
    client, _ = books
    aging = client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    assert aging["next_cursor"] is None and aging["count"] == len(aging["rows"])
    assert aging["metadata"]["period"] == {"date_from": None, "date_to": AS_OF}
    assert aging["metadata"]["basis"] == "accrual"
    assert [(row["display_customer_label"], units(row)) for row in aging["rows"]] == EXPECTED_ROWS
    assert units(aging["totals"]) == EXPECTED_TOTALS
    assert sum(EXPECTED_TOTALS[column] for column in COLUMNS[:-1]) == EXPECTED_TOTALS["total"]

    # The whole point of the report: it ties, and it ties to both statements.
    assert aging["totals"]["total"]["minor_units"] == receivable_on_the_balance_sheet(client)
    trial = client.run("report trial-balance", {"date_to": AS_OF, "limit": 200}, company=COMPANY)
    receivable = [row for row in trial["rows"] if row["current_account_name"] == "Accounts Receivable"]
    assert [row["signed_net"]["minor_units"] for row in receivable] == [EXPECTED_TOTALS["total"]]

    # A job is its own row under its parent's name, not folded into the parent.
    job = next(row for row in aging["rows"] if row["display_customer_label"].endswith("North Job"))
    parent = next(row for row in aging["rows"] if row["display_customer_label"] == "Aging Alpha")
    assert job["parent_id"] == parent["customer_id"] and job["current_customer_name"] == "North Job"
    assert parent["parent_id"] is None and all(row["active"] for row in aging["rows"])


def test_a_voided_invoice_is_absent_and_a_credit_carries_its_own_column(books):
    client, made = books
    aging = client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    beta = next(row for row in aging["rows"] if row["display_customer_label"] == "Aging Beta")
    # Unapplied credit is not netted into an invoice it did not pay: it ages by
    # the date the cash arrived, which is what keeps the aging tied to AR.
    assert (beta["days_1_30"]["minor_units"], beta["days_61_90"]["minor_units"]) == (-5000, 30000)

    numbers = [row["number"] for row in client.run("report open-invoices",
        {"as_of": AS_OF, "limit": 200}, company=COMPANY)["rows"]]
    assert "AGE-VOID" not in numbers
    assert client.run("invoice show", {"invoice": made["invoices"]["AGE-VOID"]["id"]},
                      company=COMPANY)["status"] == "voided"
    # It is absent because it is worth nothing, not because a status filter hid
    # it: on a date inside the month it was entered it is still worth nothing.
    earlier = client.run("report open-invoices", {"as_of": "2026-02-15", "limit": 200}, company=COMPANY)
    assert [row["number"] for row in earlier["rows"]] == ["AGE-OLD"]
    assert earlier["totals"]["balance"]["minor_units"] == 40000


def test_open_invoices_carry_due_dates_remaining_balances_and_filters(books):
    client, _ = books
    page = client.run("report open-invoices", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    assert page["next_cursor"] is None
    assert [(row["due_date"], row["number"], row["days_past_due"], row["aging_bucket"],
             row["amount"]["minor_units"], row["applied"]["minor_units"],
             row["balance"]["minor_units"], row["display_customer_label"])
            for row in page["rows"]] == EXPECTED_OPEN
    statuses = {row["number"]: row["settlement_status"] for row in page["rows"]}
    assert statuses.pop("AGE-PARTLY") == "partly_paid"
    assert set(statuses.values()) == {"unpaid"}
    assert [page["totals"][key]["minor_units"] for key in ("amount", "applied", "balance")] == [360000, 20000, 340000]
    # Open invoices exclude credit, so they exceed AR by the unapplied 50.00.
    assert page["totals"]["balance"]["minor_units"] - 5000 == receivable_on_the_balance_sheet(client)

    past_due = client.run("report open-invoices", {"as_of": AS_OF, "limit": 200, "past_due_only": True}, company=COMPANY)
    assert [row["number"] for row in past_due["rows"]] == [row[1] for row in EXPECTED_OPEN if row[2] > 0]
    alpha = client.run("report open-invoices", {"as_of": AS_OF, "limit": 200, "customer": "Aging Alpha"}, company=COMPANY)
    assert [row["number"] for row in alpha["rows"]] == ["AGE-91", "AGE-90", "AGE-30", "AGE-TODAY", "AGE-CURRENT"]
    assert alpha["totals"]["balance"]["minor_units"] == 240000
    job = client.run("report open-invoices", {"as_of": AS_OF, "limit": 200, "customer": "Aging Alpha:North Job"}, company=COMPANY)
    assert [row["number"] for row in job["rows"]] == ["AGE-31"]
    with pytest.raises(BookflowError) as caught:
        client.run("report open-invoices", {"as_of": AS_OF, "customer": "No Such Customer"}, company=COMPANY)
    assert caught.value.code == "E_RECORD_NOT_FOUND"


def test_settlement_moves_the_aging_and_the_total_still_ties(books):
    client, made = books
    applied = client.run("payment settlement", {"payment": made["payment"]["id"], "kind": "applications"},
                         company=COMPANY)["items"][0]
    before = client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    client.run("payment unapply", {"payment": made["payment"]["id"],
        "expected_version": made["payment"]["version"], "operation_key": "aging-unapply",
        "applications": [{"application_id": applied["application_id"],
                          "invoice_expected_version": applied["invoice_version"]}]},
        company=COMPANY, reason="Credit applied to the wrong invoice")
    after = client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    beta = next(row for row in after["rows"] if row["display_customer_label"] == "Aging Beta")
    # The 200.00 goes back onto the invoice and back into the credit. Neither
    # the ledger nor the aging total moves, because unapplying posts nothing.
    assert (beta["days_61_90"]["minor_units"], beta["days_1_30"]["minor_units"]) == (50000, -25000)
    assert units(after["totals"]) == {**EXPECTED_TOTALS, "days_1_30": -5000, "days_61_90": 110000}
    assert after["totals"]["total"] == before["totals"]["total"]
    assert after["totals"]["total"]["minor_units"] == receivable_on_the_balance_sheet(client)
    partly = next(row for row in client.run("report open-invoices", {"as_of": AS_OF, "limit": 200},
                                            company=COMPANY)["rows"] if row["number"] == "AGE-PARTLY")
    assert (partly["applied"]["minor_units"], partly["balance"]["minor_units"],
            partly["settlement_status"]) == (0, 50000, "unpaid")


def test_bucket_boundaries_are_exact_on_both_evaluators():
    for aging_date, column in (("2026-07-15", "current"), ("2026-06-30", "current"),
                               ("2026-06-29", "days_1_30"), ("2026-05-31", "days_1_30"),
                               ("2026-05-30", "days_31_60"), ("2026-05-01", "days_31_60"),
                               ("2026-04-30", "days_61_90"), ("2026-04-01", "days_61_90"),
                               ("2026-03-31", "over_90")):
        assert bucket_of(AS_OF, aging_date) == column, aging_date
    assert [days_past_due(AS_OF, date) for date in
            ("2026-05-31", "2026-05-30", "2026-04-01", "2026-03-31", "2026-07-15")] == [30, 31, 90, 91, -15]


def test_an_as_of_date_before_the_first_possible_due_date_does_not_underflow(client):
    aging = client.run("report ar-aging", {"as_of": "0001-01-05"}, company=COMPANY)
    assert aging["rows"] == [] and aging["totals"]["total"]["minor_units"] == 0
    with pytest.raises(BookflowError) as caught:
        client.run("report ar-aging", {"as_of": "2026-13-01"}, company=COMPANY)
    assert caught.value.code == "E_VALIDATION"


def test_pages_join_up_and_a_continuation_stales_on_a_company_write(books):
    client, _ = books
    whole = client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    assert whole["metadata"]["report_version"] == "2"
    walked, cursor = [], None
    while True:
        page = client.run("report ar-aging", {"as_of": AS_OF, "limit": 1,
            **({"cursor": cursor} if cursor else {})}, company=COMPANY)
        assert page["count"] == len(page["rows"]) <= 1
        assert units(page["totals"]) == EXPECTED_TOTALS
        walked += page["rows"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert walked == whole["rows"]

    first = client.run("report ar-aging", {"as_of": AS_OF, "limit": 1}, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run("report open-invoices", {"as_of": AS_OF, "limit": 1,
                                            "cursor": first["next_cursor"]}, company=COMPANY)
    assert caught.value.code == "E_VALIDATION"
    client.customer.create(name="Aging cursor witness", company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run("report ar-aging", {"as_of": AS_OF, "limit": 1,
                                       "cursor": first["next_cursor"]}, company=COMPANY)
    assert caught.value.code == "E_QUERY_STALE"


def settled(document):
    """One report reading, with the two fields no two readings ever share."""
    document = dict(document, metadata=dict(document["metadata"]))
    document["metadata"].pop("generation_time")
    document.pop("next_cursor")
    return document


def test_the_cli_prints_the_same_aging_it_returns_as_json(books, cli):
    client, _ = books
    assert settled(cli.json("report", "ar-aging", "--as-of", AS_OF, "--limit", "200", "--company", COMPANY)) == \
        settled(client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY))
    assert settled(cli.json("report", "open-invoices", "--as-of", AS_OF, "--limit", "200",
                            "--past-due-only", "--company", COMPANY)) == \
        settled(client.run("report open-invoices", {"as_of": AS_OF, "limit": 200, "past_due_only": True}, company=COMPANY))
    # The default renderer has to show the money, not swallow it.
    printed = cli.run("report", "ar-aging", "--as-of", AS_OF, "--limit", "200", "--company", COMPANY).stdout
    assert "Aging Alpha:North Job" in printed and "3350.00" in printed and "-50.00" in printed


def test_the_host_and_the_workbench_serve_the_aging(hosted):  # noqa: F811
    from fastapi.testclient import TestClient
    cid = hosted.company_id

    def run(command, body, *, reason=None):
        headers = {"X-Bookflow-Reason": reason} if reason else None
        return hosted.ok(command.replace(" ", "."), body, company=cid, headers=headers)

    build(run)
    assert units(run("report ar-aging", {"as_of": AS_OF, "limit": 200})["totals"]) == EXPECTED_TOTALS
    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    for name, table, total in (("ar-aging", "receivables-aging", "3350.00"),
                               ("open-invoices", "receivables-open", "3400.00")):
        page = api.get(f"/c/{cid}/report/{name}", headers=WB)
        assert page.status_code == 200 and 'name="f:as_of"' in page.text
        result = api.post(f"/c/{cid}/report/{name}", data={"f:as_of": AS_OF, "f:limit": "200"}, headers=WB)
        assert result.status_code == 200, result.text[:400]
        # A designed table, not a JSON blob: the columns, the rows and the tie.
        assert f'id="{table}"' in result.text and 'id="receivables-totals"' in result.text
        assert "Aging Alpha:North Job" in result.text, result.text[:2000]
        key = "total" if name == "ar-aging" else "balance"
        assert f'<td data-total="{key}">{total}</td>' in result.text, result.text[:2000]
    aging = api.post(f"/c/{cid}/report/ar-aging", data={"f:as_of": AS_OF, "f:limit": "200"}, headers=WB).text
    for heading in ("Current", "1-30", "31-60", "61-90", "Over 90"):
        assert f'<th scope="col">{heading}</th>' in aging
    # Each customer opens their own open invoices; each invoice opens itself.
    assert f'/c/{cid}/report/open-invoices?f%3Aas_of={AS_OF}&amp;f%3Acustomer=' in aging, aging[:2000]
    listed = api.post(f"/c/{cid}/report/open-invoices", data={"f:as_of": AS_OF, "f:limit": "200"}, headers=WB).text
    assert f'href="/c/{cid}/invoice/' in listed and ">AGE-PARTLY</a>" in listed
    assert "AGE-VOID" not in listed
    hosted.handle.stop()
