"""The four summaries narrowed, and what a narrowed report has to keep true.

The same month of trading the unfiltered tests use, with classed documents added on top so
that a filter has something to admit and something real to leave out. Every expectation
below was computed by hand from those documents, and three properties are checked on every
filtered report because they are what makes a filter safe to trust:

* the totals cover the whole filtered set and never the page in front of the reader;
* the report still reconciles -- its own total plus what the filter kept out is the period,
  and the period is the figure ``report profit-and-loss`` reports for the same two dates;
* a continuation carries the filter, so page two cannot drift onto other rows, and a filter
  that has moved under the reader is refused rather than quietly re-read.
"""
import pytest

from bookflow.core.errors import BookflowError

from tests.test_summary_reports import COMPANY, EXPENSE, FROM, INCOME, build

# Hand-computed from the classed documents `classed` posts, in minor units.
#
#   Invoice FIL-1   Coastal Cafe          class Residential   rep North
#                     Drain Service  1 @ 100.00 = 100.00
#   Invoice FIL-2   Ridge Builders:Tower  class Commercial    rep South
#                     Valve Fitting  2 @  25.00 =  50.00
#   Invoice FIL-3   Coastal Cafe          class Residential:Emergency, rep North
#                     Drain Service  1 @ 100.00 = 100.00
#   Bill FIL-B1     Summary Supply Co     class Residential   materials  60.00
#   Cheque 5002     Summary Fuel Depot    class Commercial    materials  40.00
#                     -- the payee is on the funding line, which carries no class at all
RESIDENTIAL_INCOME = 10000
COMMERCIAL_INCOME = 5000
EMERGENCY_INCOME = 10000
RESIDENTIAL_EXPENSE = 6000
COMMERCIAL_EXPENSE = 4000

PERIOD_INCOME = INCOME + RESIDENTIAL_INCOME + COMMERCIAL_INCOME + EMERGENCY_INCOME
PERIOD_EXPENSE = EXPENSE + RESIDENTIAL_EXPENSE + COMMERCIAL_EXPENSE

AMOUNT = {"sales-by-customer": "income", "sales-by-item": "income",
          "sales-by-rep": "income", "expenses-by-vendor": "expense"}
SALES = ("sales-by-customer", "sales-by-item", "sales-by-rep")
# Which filters each report offers, which is the whole of the deliberate choice: the class
# on all four, the customer only where a row carries no customer of its own.
FILTERS = {"sales-by-customer": ("class_id",), "sales-by-item": ("class_id", "customer"),
           "sales-by-rep": ("class_id", "customer"), "expenses-by-vendor": ("class_id",)}


def classed(run, fixture):
    """Post the classed half of the month on top of the unfiltered fixture."""
    # A sales document refuses a class while the company has classes switched off -- a bill line
    # accepts one, which is a recorded inconsistency between the two grids and somebody else's row.
    run("company update", {"use_classes": True}, reason="Track work by class")
    residential = run("class create", {"name": "Filter Residential"})["id"]
    emergency = run("class create", {"name": "Emergency", "parent_id": residential})["id"]
    commercial = run("class create", {"name": "Filter Commercial"})["id"]

    def sale(number, date, customer, klass, item, quantity, price, **extra):
        return run("invoice post", {"number": number, "date": date, "customer": customer,
            "lines": [{"item": item, "quantity": quantity, "unit_price": price,
                       "class_id": klass}], **extra})

    sale("FIL-1", "2027-03-16", fixture["cafe"], residential, fixture["drain"], "1", "100.00")
    sale("FIL-2", "2027-03-17", fixture["tower"], commercial, fixture["valve"], "2", "25.00",
         sales_rep=fixture["south"])
    sale("FIL-3", "2027-03-18", fixture["cafe"], emergency, fixture["drain"], "1", "100.00")
    run("bill post", {"number": "FIL-B1", "date": "2027-03-16", "due_date": "2027-04-16",
        "vendor": fixture["supply"], "expenses": [
            {"account": fixture["materials"], "amount": "60.00", "class_id": residential}]})
    run("check post", {"account": "Checking", "date": "2027-03-17", "number": "5002",
        "amount": "40.00", "pay_to": {"name_type": "vendor", "name_id": fixture["fuel"]},
        "expenses": [{"account": fixture["materials"], "amount": "40.00",
                      "class_id": commercial}]}, reason="Pay the fuel depot for the shop")
    return dict(residential=residential, emergency=emergency, commercial=commercial)


@pytest.fixture
def books(client):
    def run(command, body, *, reason=None):
        return client.run(command, body, company=COMPANY, **({"reason": reason} if reason else {}))
    fixture = build(run)
    return client, {**fixture, **classed(run, fixture)}


def report(client, verb, **filters):
    return client.run(f"report {verb}", {"date_from": FROM, "date_to": TO_DATE, "limit": 200,
                                         **filters}, company=COMPANY)


TO_DATE = "2027-03-30"


def statement_totals(client):
    """The income and the three cost sections the profit and loss reports for this period."""
    result = client.run("report profit-and-loss",
                        {"date_from": FROM, "date_to": TO_DATE, "limit": 200}, company=COMPANY)
    assert result["next_cursor"] is None
    costs = ("cost_of_goods_sold", "expense", "other_expense")
    return (result["totals"]["income"]["minor_units"],
            sum(result["totals"][name]["minor_units"] for name in costs))


def reconciles(client, result, verb):
    """The invariant every filtered report keeps: own total, plus excluded, is the period.

    The period is checked against the profit and loss independently, so this is a report
    agreeing with the statement rather than a report agreeing with itself.
    """
    scope, own = result["scope"], result["totals"][AMOUNT[verb]]["minor_units"]
    assert own + scope["excluded"]["minor_units"] == scope["period"]["minor_units"]
    income, expense = statement_totals(client)
    assert scope["period"]["minor_units"] == (income if verb in SALES else expense)
    return scope


def test_an_unfiltered_summary_says_it_excluded_nothing(books):
    client, _ = books
    for verb in AMOUNT:
        result = report(client, verb)
        scope = reconciles(client, result, verb)
        assert scope["filtered"] is False and scope["selected"] == []
        assert scope["excluded"]["minor_units"] == 0
        assert scope["period"]["minor_units"] == result["totals"][AMOUNT[verb]]["minor_units"]
    assert report(client, "sales-by-customer")["totals"]["income"]["minor_units"] == PERIOD_INCOME
    assert report(client, "expenses-by-vendor")["totals"]["expense"]["minor_units"] == PERIOD_EXPENSE


def test_a_class_narrows_every_summary_and_each_says_what_it_left_out(books):
    client, fixture = books
    residential = {"class_id": fixture["residential"]}

    by_customer = report(client, "sales-by-customer", **residential)
    assert [(row["display_customer_label"], row["income"]["minor_units"],
             row["percent_of_total"]) for row in by_customer["rows"]] \
        == [("Summary Coastal Cafe", RESIDENTIAL_INCOME, "100")]
    scope = reconciles(client, by_customer, "sales-by-customer")
    assert scope["filtered"] is True
    assert scope["selected"] == [{"filter": "class_id", "id": fixture["residential"],
                                  "label": "Filter Residential", "active": True}]
    assert scope["excluded"]["minor_units"] == PERIOD_INCOME - RESIDENTIAL_INCOME

    by_item = report(client, "sales-by-item", **residential)
    assert [(row["display_item_label"], row["quantity_microunits"], row["income"]["minor_units"])
            for row in by_item["rows"]] == [("Summary Drain Service", 1_000_000, RESIDENTIAL_INCOME)]
    # The item report's own reconciliation still holds inside the filter.
    assert by_item["totals"]["item_income"]["minor_units"] \
        + by_item["totals"]["no_item_income"]["minor_units"] == RESIDENTIAL_INCOME
    reconciles(client, by_item, "sales-by-item")

    by_rep = report(client, "sales-by-rep", **residential)
    assert [(row["display_sales_rep_label"], row["income"]["minor_units"])
            for row in by_rep["rows"]] == [("Summary Rep North", RESIDENTIAL_INCOME)]
    reconciles(client, by_rep, "sales-by-rep")

    by_vendor = report(client, "expenses-by-vendor", **residential)
    assert [(row["display_vendor_label"], row["expense"]["minor_units"])
            for row in by_vendor["rows"]] == [("Summary Supply Co", RESIDENTIAL_EXPENSE)]
    scope = reconciles(client, by_vendor, "expenses-by-vendor")
    assert scope["excluded"]["minor_units"] == PERIOD_EXPENSE - RESIDENTIAL_EXPENSE


def test_a_subclass_is_its_own_class_and_is_not_read_as_its_parent(books):
    client, fixture = books
    parent = report(client, "sales-by-customer", class_id=fixture["residential"])
    child = report(client, "sales-by-customer", class_id=fixture["emergency"])
    assert parent["totals"]["income"]["minor_units"] == RESIDENTIAL_INCOME
    assert child["totals"]["income"]["minor_units"] == EMERGENCY_INCOME
    # Both name the same customer, so a parent that silently swallowed its subclass would
    # show one row of 200.00 here rather than two reports of 100.00 each.
    assert [row["display_customer_label"] for row in parent["rows"]] \
        == [row["display_customer_label"] for row in child["rows"]] == ["Summary Coastal Cafe"]
    # The canonical full name resolves the subclass exactly as its ID does.
    assert report(client, "sales-by-customer", class_id="Filter Residential:Emergency")["rows"] \
        == child["rows"]


def test_a_classed_cheque_still_reaches_the_payee_the_funding_line_names(books):
    """The expense filter narrows the cost lines and not the vendor behind them.

    A cheque records its payee on the line that pays, which carries no class at all, so a
    class filter that narrowed the whole posting would file every classed cheque under no
    vendor. The fallback deliberately reads the unfiltered posting for that one fact.
    """
    client, fixture = books
    result = report(client, "expenses-by-vendor", class_id=fixture["commercial"])
    assert [(row["display_vendor_label"], row["expense"]["minor_units"])
            for row in result["rows"]] == [("Summary Fuel Depot", COMMERCIAL_EXPENSE)]
    assert result["rows"][0]["vendor_id"] == fixture["fuel"]


def test_a_customer_narrows_the_two_reports_that_carry_no_customer_of_their_own(books):
    client, fixture = books
    by_item = report(client, "sales-by-item", customer=fixture["tower"])
    # The job's own trading, item by item: the unclassed 100.00 of drain from the
    # unfiltered month and the 50.00 of valve the classed half added.
    assert [(row["display_item_label"], row["income"]["minor_units"])
            for row in by_item["rows"]] == [("Summary Drain Service", 10000),
                                            ("Summary Valve Fitting", COMMERCIAL_INCOME)]
    scope = reconciles(client, by_item, "sales-by-item")
    assert [choice["filter"] for choice in scope["selected"]] == ["customer"]
    assert scope["selected"][0]["label"] == "Summary Ridge Builders:Tower Job"

    # A job is its own customer: filtering to the parent does not collect the job.
    parent = report(client, "sales-by-item", customer=fixture["ridge"])
    assert [(row["display_item_label"], row["income"]["minor_units"])
            for row in parent["rows"]] == [("Summary Drain Service", 30000),
                                           ("Summary Valve Fitting", 5000)]

    by_rep = report(client, "sales-by-rep", customer=fixture["tower"])
    assert [(row["display_sales_rep_label"], row["income"]["minor_units"])
            for row in by_rep["rows"]] == [("Summary Rep South", 15000)]
    reconciles(client, by_rep, "sales-by-rep")


def test_two_filters_narrow_together_and_both_are_named_on_the_report(books):
    client, fixture = books
    result = report(client, "sales-by-item", class_id=fixture["commercial"],
                    customer=fixture["tower"])
    assert [(row["display_item_label"], row["income"]["minor_units"])
            for row in result["rows"]] == [("Summary Valve Fitting", COMMERCIAL_INCOME)]
    scope = reconciles(client, result, "sales-by-item")
    assert [(choice["filter"], choice["label"]) for choice in scope["selected"]] \
        == [("class_id", "Filter Commercial"),
            ("customer", "Summary Ridge Builders:Tower Job")]
    assert scope["excluded"]["minor_units"] == PERIOD_INCOME - COMMERCIAL_INCOME
    # Two filters that admit nothing together still report the period they excluded.
    empty = report(client, "sales-by-item", class_id=fixture["residential"],
                   customer=fixture["tower"])
    assert empty["rows"] == [] and empty["totals"]["income"]["minor_units"] == 0
    assert reconciles(client, empty, "sales-by-item")["excluded"]["minor_units"] == PERIOD_INCOME


def test_only_the_reports_that_mean_it_take_each_filter(books):
    """The deliberate half of this work: a filter a report does not offer is refused."""
    client, fixture = books
    for verb, offered in FILTERS.items():
        for field, value in (("class_id", fixture["residential"]), ("customer", fixture["cafe"])):
            if field in offered:
                assert report(client, verb, **{field: value})["scope"]["filtered"]
                continue
            with pytest.raises(BookflowError) as raised:
                report(client, verb, **{field: value})
            assert raised.value.code == "E_VALIDATION"


def test_a_filtered_summary_pages_without_moving_its_totals_or_its_scope(books):
    client, fixture = books
    for verb in AMOUNT:
        filters = {"class_id": fixture["commercial"]}
        whole = report(client, verb, **filters)
        seen, cursor = [], None
        while True:
            page = client.run(f"report {verb}", {"date_from": FROM, "date_to": TO_DATE,
                "limit": 1, **filters, **({"cursor": cursor} if cursor else {})}, company=COMPANY)
            # Totals and scope cover the whole filter on every page, never the page's rows.
            assert page["totals"] == whole["totals"], verb
            assert page["scope"] == whole["scope"], verb
            seen.extend(page["rows"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == whole["rows"], verb


def _second_filter_row(client, fixture, *, commercial=False):
    """Two groups are needed to exercise a one-row page continuation."""
    client.run("invoice post", {
        "number": "FIL-PAGE", "date": "2027-03-19", "customer": fixture["cafe"],
        "lines": [{"item": fixture["valve"], "quantity": "1", "unit_price": "25.00",
                   "class_id": fixture["commercial" if commercial else "residential"]}],
    }, company=COMPANY)


def test_renaming_the_filtered_class_refuses_the_next_page_rather_than_relabelling_it(books):
    client, fixture = books
    _second_filter_row(client, fixture)
    filters = {"class_id": fixture["residential"]}
    first = client.run("report sales-by-item", {"date_from": FROM, "date_to": TO_DATE,
                                                "limit": 1, **filters}, company=COMPANY)
    assert first["next_cursor"] is not None
    assert first["scope"]["selected"][0]["label"] == "Filter Residential"
    shown = client.run("class show", {"class": fixture["residential"]}, company=COMPANY)
    client.run("class update", {"class": fixture["residential"],
                                "expected_version": shown["version"],
                                "name": "Filter Residential Renamed"}, company=COMPANY)
    with pytest.raises(BookflowError) as raised:
        client.run("report sales-by-item", {"date_from": FROM, "date_to": TO_DATE, "limit": 1,
                                            **filters, "cursor": first["next_cursor"]},
                   company=COMPANY)
    assert raised.value.code == "E_QUERY_STALE"
    # Restarting reads the new name; nothing about the money moved.
    restarted = report(client, "sales-by-item", **filters)
    assert restarted["scope"]["selected"][0]["label"] == "Filter Residential Renamed"
    assert restarted["totals"]["income"]["minor_units"] == RESIDENTIAL_INCOME + 2500


def test_a_continuation_carries_its_filter_and_refuses_a_different_one(books):
    client, fixture = books
    _second_filter_row(client, fixture)
    first = client.run("report sales-by-item", {"date_from": FROM, "date_to": TO_DATE,
        "limit": 1, "class_id": fixture["residential"]}, company=COMPANY)
    assert first["next_cursor"] is not None
    second = client.run("report sales-by-item", {"date_from": FROM, "date_to": TO_DATE,
        "limit": 1, "class_id": fixture["residential"], "cursor": first["next_cursor"]},
        company=COMPANY)
    whole = report(client, "sales-by-item", class_id=fixture["residential"])
    assert first["rows"] + second["rows"] == whole["rows"]
    assert second["totals"] == first["totals"] == whole["totals"]
    assert second["scope"] == first["scope"] == whole["scope"]
    assert second["next_cursor"] is None
    for changed in ({"class_id": fixture["commercial"]}, {}, {"class_id": fixture["residential"],
                                                              "customer": fixture["cafe"]}):
        with pytest.raises(BookflowError) as raised:
            client.run("report sales-by-item", {"date_from": FROM, "date_to": TO_DATE,
                "limit": 1, **changed, "cursor": first["next_cursor"]}, company=COMPANY)
        assert raised.value.code == "E_VALIDATION"
        assert raised.value.details["fields"][0]["field"] == "cursor"


def test_page_two_reads_the_renamed_class_off_the_cursor_and_never_the_typed_name(books):
    """A canonical name on page one becomes a stable ID on the continuation.

    A report filtered by name that re-resolved that name on page two would answer
    record-not-found the moment somebody renamed the class. The ID rides the signed
    cursor, so the next page is refused as stale -- a restart -- and never as a
    missing record.
    """
    client, fixture = books
    _second_filter_row(client, fixture, commercial=True)
    first = client.run("report sales-by-customer", {"date_from": FROM, "date_to": TO_DATE,
        "limit": 1, "class_id": "Filter Commercial"}, company=COMPANY)
    assert first["next_cursor"] is not None
    shown = client.run("class show", {"class": fixture["commercial"]}, company=COMPANY)
    client.run("class update", {"class": fixture["commercial"],
                                "expected_version": shown["version"],
                                "name": "Filter Commercial Renamed"}, company=COMPANY)
    with pytest.raises(BookflowError) as raised:
        client.run("report sales-by-customer", {"date_from": FROM, "date_to": TO_DATE,
            "limit": 1, "class_id": "Filter Commercial", "cursor": first["next_cursor"]},
            company=COMPANY)
    assert raised.value.code == "E_QUERY_STALE"


def test_a_filter_naming_nothing_is_refused_by_name(books):
    client, _ = books
    for verb, field in (("sales-by-customer", "class_id"), ("sales-by-item", "customer"),
                        ("sales-by-rep", "class_id"), ("expenses-by-vendor", "class_id")):
        with pytest.raises(BookflowError) as raised:
            report(client, verb, **{field: "No such record at all"})
        assert raised.value.code == "E_RECORD_NOT_FOUND"


def test_an_inactive_class_is_still_reportable_and_says_so(books):
    """Last year's trading happened under classes a company has since retired."""
    client, fixture = books
    shown = client.run("class show", {"class": fixture["residential"]}, company=COMPANY)
    client.run("class deactivate", {"class": fixture["residential"],
                                    "expected_version": shown["version"], "cascade": True}, company=COMPANY)
    result = report(client, "sales-by-customer", class_id=fixture["residential"])
    assert result["totals"]["income"]["minor_units"] == RESIDENTIAL_INCOME
    assert result["scope"]["selected"][0]["active"] is False
    reconciles(client, result, "sales-by-customer")
