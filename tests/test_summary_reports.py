"""One month of real trading, and the four summaries that have to add up to it.

Every expected figure below was computed by hand from the documents the fixture posts,
and every one of the three income summaries is checked against the income total the
profit and loss independently reports for the same dates. That reconciliation is the
point of these reports: a breakdown that does not add up to the statement it breaks down
is worse than no breakdown, because a reader has no way to tell which figure is wrong.
"""
import pytest

from bookflow.core.errors import BookflowError

COMPANY = "Demo Plumbing Co"
FROM, TO = "2027-03-01", "2027-03-30"

# Hand-computed from the fixture, in minor units.
#
#   Invoice SUM-1   Ridge Builders        rep North (the customer's own default)
#                     Drain Service  3 @ 100.00 = 300.00
#                     Valve Fitting  2 @  25.00 =  50.00
#   Invoice SUM-2   Ridge Builders:Tower  rep South (named on the document)
#                     Drain Service  1 @ 100.00 = 100.00
#   Receipt SUM-3   Coastal Cafe          rep North (the customer's own default)
#                     Valve Fitting  4 @  25.00 = 100.00
#   Credit  SUM-4   Coastal Cafe          rep North (a credit memo takes the default)
#                     Valve Fitting  1 @  25.00 =  25.00 back off
#   Journal SUM-5   60.00 to income, no customer, no item, no rep
#   Journal SUM-6   40.00 to income, named to Coastal Cafe, no item, no rep
#   Invoice SUM-7   Coastal Cafe, 500.00, voided: worth nothing on its own date
#
#   Bill SUM-B1     Summary Supply Co     materials 200.00 + permits 50.00 = 250.00
#   Cheque 5001     Summary Fuel Depot    materials 80.00 + goods 20.00 = 100.00
#                     -- the payee is on the funding line, not on the expense lines
#   Card charge     Summary Fuel Depot    permits 30.00
#   Vendor credit   Summary Supply Co     materials 45.00 back off
#   Journal SUM-X   15.00 of other expense, no vendor at all
INCOME = 62500
EXPENSE = 35000

BY_CUSTOMER = [
    ("Summary Coastal Cafe", 11500, "18.4"),
    ("Summary Ridge Builders", 35000, "56"),
    ("Summary Ridge Builders:Tower Job", 10000, "16"),
    ("No name", 6000, "9.6"),
]
BY_ITEM = [
    # label, quantity microunits, income, average price minor units, percent
    ("Summary Drain Service", 4_000_000, 40000, 10000, "64"),
    ("Summary Valve Fitting", 5_000_000, 12500, 2500, "20"),
    ("No item", 0, 10000, None, "16"),
]
BY_REP = [
    ("Summary Rep North", 42500, "68"),
    ("Summary Rep South", 10000, "16"),
    ("Unassigned", 10000, "16"),
]
BY_VENDOR = [
    ("Summary Fuel Depot", 13000, "37.142857"),
    ("Summary Supply Co", 20500, "58.571429"),
    ("No name", 1500, "4.285714"),
]


def build(run):
    """Post the month through whichever surface `run(command, input, reason=)` speaks."""
    account = lambda name, kind: run("account create", {"name": name, "type": kind})["id"]
    service_income = account("Summary Service Income", "income")
    materials = account("Summary Job Materials", "expense")
    permits = account("Summary Permits", "expense")
    goods = account("Summary Cost of Goods", "cost_of_goods_sold")
    interest = account("Summary Interest Paid", "other_expense")

    def employee(name):
        return run("employee create", {"name": name})["id"]

    def rep(name, initials, person):
        return run("sales-rep create", {"name": name, "initials": initials,
                                        "name_type": "employee", "name_id": person})["id"]

    north = rep("Summary Rep North", "SNO", employee("Summary North Person"))
    south = rep("Summary Rep South", "SSO", employee("Summary South Person"))

    # Every sale here is nontaxable, so the figures are the line nets and nothing else.
    nontaxable = next(code["id"] for code in run("sales-tax-code list", {})["items"]
                      if code["code"] == "Non")

    def customer(name, **extra):
        return run("customer create", {"name": name, "sales_tax_code_id": nontaxable, **extra})["id"]

    ridge = customer("Summary Ridge Builders", sales_rep_id=north)
    tower = customer("Tower Job", parent_id=ridge, job_status="in_progress")
    cafe = customer("Summary Coastal Cafe", sales_rep_id=north)

    def item(name, price):
        return run("item create", {"name": name, "type": "service", "sales_enabled": True,
                                   "description": name, "price": price,
                                   "income_account_id": service_income,
                                   "sales_tax_code_id": nontaxable})["id"]

    drain = item("Summary Drain Service", "100.00")
    valve = item("Summary Valve Fitting", "25.00")

    def line(which, quantity, price):
        return {"item": which, "quantity": quantity, "unit_price": price}

    # The rep on SUM-1 is never named: it is the customer's own default, captured at
    # entry, which is what the reassignment test below moves out from under it.
    invoices = {}
    invoices["SUM-1"] = run("invoice post", {"number": "SUM-1", "date": "2027-03-03", "customer": ridge,
        "lines": [line(drain, "3", "100.00"), line(valve, "2", "25.00")]})
    invoices["SUM-2"] = run("invoice post", {"number": "SUM-2", "date": "2027-03-05", "customer": tower,
        "sales_rep": south, "lines": [line(drain, "1", "100.00")]})
    invoices["SUM-3"] = run("sales-receipt post", {"number": "SUM-3", "date": "2027-03-07", "customer": cafe,
        "deposit_to": "Checking", "payment_method": "Check", "lines": [line(valve, "4", "25.00")]})
    invoices["SUM-4"] = run("credit-memo post", {"date": "2027-03-09", "customer": cafe,
        "lines": [line(valve, "1", "25.00")]}, reason="Return one fitting")
    invoices["SUM-5"] = run("journal post", {"number": "SUM-5", "date": "2027-03-11", "lines": [
        {"account": "Checking", "side": "debit", "amount": "60.00"},
        {"account": service_income, "side": "credit", "amount": "60.00"}]})
    invoices["SUM-6"] = run("journal post", {"number": "SUM-6", "date": "2027-03-12", "lines": [
        {"account": "Checking", "side": "debit", "amount": "40.00"},
        {"account": service_income, "side": "credit", "amount": "40.00",
         "name_type": "customer", "name_id": cafe}]})
    voided = run("invoice post", {"number": "SUM-7", "date": "2027-03-13", "customer": cafe,
        "lines": [line(drain, "5", "100.00")]})
    run("invoice void", {"invoice": voided["id"], "expected_version": 1},
        reason="Raised against the wrong customer")

    supply = run("vendor create", {"name": "Summary Supply Co"})["id"]
    fuel = run("vendor create", {"name": "Summary Fuel Depot"})["id"]
    bill = run("bill post", {"number": "SUM-B1", "date": "2027-03-04", "due_date": "2027-04-04",
        "vendor": supply, "expenses": [{"account": materials, "amount": "200.00"},
                                       {"account": permits, "amount": "50.00"}]})
    # No party on either expense line: the payee is on the line that pays, which is the
    # only place a cheque ever records it.
    cheque = run("check post", {"account": "Checking", "date": "2027-03-06", "number": "5001",
        "amount": "100.00", "pay_to": {"name_type": "vendor", "name_id": fuel},
        "expenses": [{"account": materials, "amount": "80.00"},
                     {"account": goods, "amount": "20.00"}]}, reason="Pay the fuel depot")
    card = run("card-charge post", {"account": "Business Credit Card", "date": "2027-03-08",
        "amount": "30.00", "pay_to": {"name_type": "vendor", "name_id": fuel},
        "expenses": [{"account": permits, "amount": "30.00"}]}, reason="Permit on the card")
    credit = run("vendor-credit post", {"date": "2027-03-10", "vendor": supply,
        "expenses": [{"account": materials, "amount": "45.00"}]}, reason="Returned materials")
    run("journal post", {"number": "SUM-X", "date": "2027-03-14", "lines": [
        {"account": interest, "side": "debit", "amount": "15.00"},
        {"account": "Checking", "side": "credit", "amount": "15.00"}]})
    return dict(invoices=invoices, voided=voided, bill=bill, cheque=cheque, card=card,
                vendor_credit=credit, ridge=ridge, tower=tower, cafe=cafe, north=north,
                south=south, drain=drain, valve=valve, supply=supply, fuel=fuel,
                service_income=service_income, materials=materials, permits=permits,
                goods=goods, interest=interest)


@pytest.fixture
def books(client):
    def run(command, body, *, reason=None):
        return client.run(command, body, company=COMPANY, **({"reason": reason} if reason else {}))
    return client, build(run)


def report(client, verb, **extra):
    result = client.run(f"report {verb}", {"date_from": FROM, "date_to": TO, "limit": 200, **extra},
                        company=COMPANY)
    assert result["next_cursor"] is None and result["count"] == len(result["rows"])
    assert result["metadata"]["period"] == {"date_from": FROM, "date_to": TO}
    assert result["metadata"]["basis"] == "accrual"
    return result


def statement_income(client):
    """The income total the profit and loss reports for exactly these dates."""
    result = client.run("report profit-and-loss", {"date_from": FROM, "date_to": TO, "limit": 200},
                        company=COMPANY)
    assert result["next_cursor"] is None
    return result["totals"]["income"]["minor_units"], result


def test_income_by_customer_rolls_jobs_under_their_parent_and_ties_to_the_statement(books):
    client, _ = books
    result = report(client, "sales-by-customer")
    assert [(row["display_customer_label"], row["income"]["minor_units"], row["percent_of_total"])
            for row in result["rows"]] == BY_CUSTOMER
    assert result["totals"]["income"]["minor_units"] == INCOME
    assert statement_income(client)[0] == INCOME

    # A job is its own row, named under the parent it belongs to and never rolled into
    # the parent's figure, which is what lets a reader add the two deliberately.
    job = next(row for row in result["rows"] if row["display_customer_label"].endswith("Tower Job"))
    parent = next(row for row in result["rows"] if row["display_customer_label"] == "Summary Ridge Builders")
    assert job["parent_id"] == parent["customer_id"]
    assert job["parent_label"] == "Summary Ridge Builders"
    assert job["is_job"] and job["job_status"] == "in_progress"
    assert not parent["is_job"] and parent["parent_id"] is None and parent["parent_label"] is None
    # Rows read in hierarchy-name order, so the job follows its parent immediately.
    assert result["rows"].index(job) == result["rows"].index(parent) + 1

    unnamed = result["rows"][-1]
    assert unnamed["customer_id"] is None and unnamed["active"] is None
    assert sum(row["percent_of_total_millionths"] for row in result["rows"]) == 100 * 10 ** 6


def test_income_by_item_reconciles_with_the_statement_once_non_item_income_is_counted(books):
    client, _ = books
    result = report(client, "sales-by-item")
    assert [(row["display_item_label"], row["quantity_microunits"], row["income"]["minor_units"],
             None if row["average_price"] is None else row["average_price"]["minor_units"],
             row["percent_of_total"]) for row in result["rows"]] == BY_ITEM

    totals = result["totals"]
    # The reconciliation this report exists for, stated as the assertion the brief asks
    # for: item income plus the income that reached no item is the statement's income.
    statement, _ = statement_income(client)
    assert totals["income"]["minor_units"] == statement == INCOME
    assert totals["item_income"]["minor_units"] + totals["no_item_income"]["minor_units"] == statement
    assert totals["no_item_income"]["minor_units"] == 10000

    # And the report says so in its own rows rather than dropping it silently.
    no_item = next(row for row in result["rows"] if row["item_id"] is None)
    assert no_item["display_item_label"] == "No item"
    assert no_item["income"]["minor_units"] == totals["no_item_income"]["minor_units"]
    assert no_item["average_price"] is None

    drain = result["rows"][0]
    assert drain["quantity"] == "4" and drain["quantity_complete"]
    assert drain["item_type"] == "service" and drain["active"] is True
    # Average price is income over quantity and nothing else.
    assert drain["average_price"]["minor_units"] * drain["quantity_microunits"] // 10 ** 6 \
        == drain["income"]["minor_units"]


def test_income_by_rep_reads_the_sale_and_not_the_customers_current_rep(books):
    client, fixture = books
    result = report(client, "sales-by-rep")
    assert [(row["display_sales_rep_label"], row["income"]["minor_units"], row["percent_of_total"])
            for row in result["rows"]] == BY_REP
    assert result["totals"]["income"]["minor_units"] == INCOME == statement_income(client)[0]
    assert result["rows"][0]["current_sales_rep_initials"] == "SNO"
    assert result["rows"][-1]["sales_rep_id"] is None

    # The whole point: move the customer to the other representative and last month's
    # sales stay exactly where they were made. A report that read the customer's list
    # record would move 350.00 from North to South here.
    customer = client.run("customer show", {"customer": fixture["ridge"]}, company=COMPANY)
    client.run("customer update", {"customer": fixture["ridge"],
                                   "expected_version": customer["version"],
                                   "sales_rep_id": fixture["south"]}, company=COMPANY)
    after = report(client, "sales-by-rep")
    assert [(row["display_sales_rep_label"], row["income"]["minor_units"])
            for row in after["rows"]] == [(label, amount) for label, amount, _ in BY_REP]


def test_correcting_a_sale_moves_its_income_to_the_rep_it_was_corrected_to(books):
    """The other half of reading the sale rather than the customer: a real correction.

    A reversal batch names the revision it reverses and a replacement names the new one,
    so the old representative's income comes off at the old capture and the new one's goes
    on at the new. Neither the period's total nor any other cut of it moves.
    """
    client, fixture = books
    before_customers = report(client, "sales-by-customer")["rows"]
    before_items = report(client, "sales-by-item")["rows"]

    # SUM-2 is the Tower job's 100.00, entered under South. Re-issue it under North.
    client.run("invoice update", {"invoice": fixture["invoices"]["SUM-2"]["id"],
                                  "expected_version": 1, "sales_rep": fixture["north"]},
               company=COMPANY, reason="The job was sold by the north representative")

    after = report(client, "sales-by-rep")
    assert [(row["display_sales_rep_label"], row["income"]["minor_units"])
            for row in after["rows"]] == [("Summary Rep North", 52500), ("Unassigned", 10000)]
    # South leaves because the correction took its income to nothing, not because a
    # status was filtered, and the period's income has not moved at all.
    assert after["totals"]["income"]["minor_units"] == INCOME == statement_income(client)[0]
    # The same money, cut two other ways, is exactly where it was.
    assert report(client, "sales-by-customer")["rows"] == before_customers
    assert report(client, "sales-by-item")["rows"] == before_items


def test_expense_by_vendor_covers_every_document_that_reaches_a_cost_account(books):
    client, _ = books
    result = report(client, "expenses-by-vendor")
    assert [(row["display_vendor_label"], row["expense"]["minor_units"], row["percent_of_total"])
            for row in result["rows"]] == BY_VENDOR
    assert result["totals"]["expense"]["minor_units"] == EXPENSE

    # The cheque and the card charge are in it at all only because the payee on the
    # funding line reaches the expense lines behind it.
    fuel = next(row for row in result["rows"] if row["display_vendor_label"] == "Summary Fuel Depot")
    assert fuel["expense"]["minor_units"] == 13000
    # The vendor credit is negative and takes the bill back down.
    supply = next(row for row in result["rows"] if row["display_vendor_label"] == "Summary Supply Co")
    assert supply["expense"]["minor_units"] == 25000 - 4500
    assert result["rows"][-1]["vendor_id"] is None

    # Cost of goods sold, ordinary expense and other expense together, which is what the
    # profit and loss adds up over the same three sections.
    _, statement = statement_income(client)
    sections = {"cost_of_goods_sold", "expense", "other_expense"}
    assert sum(statement["totals"][name]["minor_units"] for name in sections) == EXPENSE


def test_a_voided_sale_and_a_fully_credited_one_leave_because_they_are_worth_nothing(books):
    client, fixture = books
    labels = [row["display_customer_label"] for row in report(client, "sales-by-customer")["rows"]]
    # SUM-7 was raised against Coastal Cafe and voided; the customer is still on the
    # report for its other trading, but the 500.00 is nowhere in any figure.
    assert "Summary Coastal Cafe" in labels
    assert report(client, "sales-by-customer")["totals"]["income"]["minor_units"] == INCOME

    # Credit the whole of the Tower job's invoice back and its row goes, because it is
    # worth nothing -- not because anything filtered a status.
    run = lambda command, body, reason=None: client.run(
        command, body, company=COMPANY, **({"reason": reason} if reason else {}))
    run("credit-memo post", {"date": "2027-03-20", "customer": fixture["tower"],
        "lines": [{"item": fixture["drain"], "quantity": "1", "unit_price": "100.00"}]},
        reason="Job cancelled")
    after = report(client, "sales-by-customer")
    assert not [row for row in after["rows"] if row["display_customer_label"].endswith("Tower Job")]
    assert after["totals"]["income"]["minor_units"] == INCOME - 10000
    assert statement_income(client)[0] == INCOME - 10000


def test_a_period_outside_the_trading_has_no_rows_and_no_percentages(books):
    client, _ = books
    for verb in ("sales-by-customer", "sales-by-item", "sales-by-rep", "expenses-by-vendor"):
        result = client.run(f"report {verb}", {"date_from": "2020-01-01", "date_to": "2020-01-31"},
                            company=COMPANY)
        assert result["rows"] == [] and result["count"] == 0
        amount = result["totals"].get("income") or result["totals"]["expense"]
        assert amount["minor_units"] == 0


def test_every_summary_pages_without_moving_its_totals(books):
    client, _ = books
    for verb, key in (("sales-by-customer", "income"), ("sales-by-item", "income"),
                      ("sales-by-rep", "income"), ("expenses-by-vendor", "expense")):
        whole = report(client, verb)
        seen, cursor = [], None
        while True:
            page = client.run(f"report {verb}", {"date_from": FROM, "date_to": TO, "limit": 1,
                                                 **({"cursor": cursor} if cursor else {})},
                              company=COMPANY)
            # Totals cover the whole filter on every page, never the page's own rows.
            assert page["totals"][key] == whole["totals"][key]
            seen.extend(page["rows"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == whole["rows"]


def test_a_company_change_stales_a_continuation_rather_than_paging_into_another_order(books):
    client, _ = books
    first = client.run("report sales-by-customer", {"date_from": FROM, "date_to": TO, "limit": 1},
                       company=COMPANY)
    client.run("customer create", {"name": "Summary continuation witness"}, company=COMPANY)
    with pytest.raises(BookflowError) as raised:
        client.run("report sales-by-customer", {"date_from": FROM, "date_to": TO, "limit": 1,
                                                "cursor": first["next_cursor"]}, company=COMPANY)
    assert raised.value.code == "E_QUERY_STALE"


def test_a_backwards_period_is_refused_by_name(books):
    client, _ = books
    for verb in ("sales-by-customer", "sales-by-item", "sales-by-rep", "expenses-by-vendor"):
        with pytest.raises(BookflowError) as raised:
            client.run(f"report {verb}", {"date_from": TO, "date_to": FROM}, company=COMPANY)
        assert raised.value.code == "E_VALIDATION"
