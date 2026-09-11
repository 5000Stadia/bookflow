"""What is waiting to be billed, against the billing window that will bill it.

Every amount below was computed by hand from the work the fixture records, and the
report's remaining figures are checked against ``estimate billing`` and
``work-order billing`` for the same sources, because these are meant to be the same
numbers read two ways rather than two answers to the same question.
"""
import pytest

from bookflow.core.errors import BookflowError

COMPANY = "Demo Plumbing Co"
AS_OF = "2027-04-30"
DATE = "2027-04-02"


def build(run):
    """Record the month's work through whichever surface ``run(command, input)`` speaks."""
    income = run("account create", {"name": "Unbilled income", "type": "income"})["id"]
    code = next(row["id"] for row in run("sales-tax-code list", {})["items"] if not row["taxable"])
    item = run("item create", {"name": "Unbilled service", "type": "service", "sales_enabled": True,
                               "description": "Site labor", "income_account_id": income,
                               "price": "100.00", "sales_tax_code_id": code})["id"]
    parent = run("customer create", {"name": "Unbilled Customer"})["id"]
    job = run("customer create", {"name": "Site A", "parent_id": parent, "job_status": "in_progress"})["id"]
    other = run("customer create", {"name": "Unbilled Other"})["id"]

    def source(noun, number, customer, lines, **extra):
        return run(f"{noun} create", {"date": DATE, "title": "Recorded work " + number,
                                      "number": number, "customer": customer, "lines": lines, **extra})

    def accept(estimate):
        return run("estimate update", {"estimate": estimate["id"], "expected_version": estimate["version"],
                                       "status": "accepted", "decision_note": "Customer accepted the scope"})

    # An accepted estimate against a job, with one line nobody is charged for.
    open_estimate = accept(source("estimate", "UB-EST-OPEN", job, [
        {"item": item, "quantity": "2", "description": "Two days on site"},
        {"item": item, "quantity": "1", "billable": False, "description": "Goodwill visit"}]))
    # A work order of its own, against the parent customer.
    work_order = source("work-order", "UB-WO-OPEN", parent, [
        {"item": item, "quantity": "3", "description": "Replacement run"}])
    # An accepted estimate whose work order has taken the work over: the estimate is
    # not listed and the work order is.
    converted = accept(source("estimate", "UB-EST-CONVERTED", other, [
        {"item": item, "quantity": "4", "description": "Converted scope"}]))
    converted_order = run("estimate work-order", {"estimate": converted["id"],
        "expected_version": converted["version"], "conversion_key": "ub-convert",
        "date": DATE, "number": "UB-WO-CONVERTED"})
    # Nobody has accepted this one, so no cost has been recorded against the job.
    source("estimate", "UB-EST-DRAFT", job, [{"item": item, "quantity": "5", "description": "Quoted only"}])
    # Cancelled work is not work to bill.
    cancelled = source("work-order", "UB-WO-CANCELLED", job, [
        {"item": item, "quantity": "6", "description": "Called off"}])
    run("work-order update", {"work_order": cancelled["id"], "expected_version": cancelled["version"],
                              "status": "cancelled"})
    # Billed in full, so there is nothing left on it.
    billed = accept(source("estimate", "UB-EST-BILLED", other, [
        {"item": item, "quantity": "7", "description": "Finished and invoiced"}]))
    run("estimate invoice", {"estimate": billed["id"], "expected_version": billed["version"],
                             "conversion_key": "ub-billed", "date": DATE})
    # Half billed, so half is still to bill.
    partial = accept(source("estimate", "UB-EST-PARTIAL", job, [
        {"item": item, "quantity": "8", "description": "Phase one of two"}]))
    run("estimate invoice", {"estimate": partial["id"], "expected_version": partial["version"],
                             "conversion_key": "ub-partial", "date": DATE, "percent": "50"})
    # Work dated after the as-of date has not been recorded yet.
    accept(source("estimate", "UB-EST-LATER", job, [
        {"item": item, "quantity": "9", "description": "Next month"}], date="2027-05-04"))
    return dict(item=item, parent=parent, job=job, other=other, income=income,
                open_estimate=open_estimate, work_order=work_order,
                converted=converted, converted_order=converted_order, partial=partial, billed=billed)


REASON = "Unbilled cost fixture"


def writes(command):
    """Whether this command needs the reason a lifecycle write requires."""
    from bookflow.core import registry
    registry.load_all()
    return registry.get(command).is_write


@pytest.fixture
def books(client):
    def run(command, body):
        extra = {"reason": REASON} if writes(command) else {}
        return client.run(command, body, company=COMPANY, **extra)
    # The demo company already has work waiting to be billed, which is the point of
    # the demo. This month's work is measured against what was there before it.
    baseline = client.run("report unbilled-costs", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    return client, build(run), baseline


def report(client, **extra):
    return client.run("report unbilled-costs", {"as_of": AS_OF, "limit": 200, **extra}, company=COMPANY)


def shape(result):
    return rows_shape(result["rows"])


def rows_shape(rows):
    return [(row["kind"], row["display_customer_label"], row["source_number"], row["description"],
             row["state"], row["remaining"]["minor_units"]) for row in rows]


def mine(rows):
    """Only this month's own customers, in the order the report printed them."""
    return [row for row in rows if row["display_customer_label"].startswith("Unbilled ")]


# Customers read in hierarchy order, each with its own subtotal. 'Unbilled Customer'
# is the parent's own work order; 'Unbilled Customer:Site A' is the job's.
EXPECTED = [
    ("line", "Unbilled Customer", "UB-WO-OPEN", "Replacement run", "unbilled", 30000),
    ("subtotal", "Unbilled Customer", None, None, None, 30000),
    ("line", "Unbilled Customer:Site A", "UB-EST-OPEN", "Two days on site", "unbilled", 20000),
    ("line", "Unbilled Customer:Site A", "UB-EST-PARTIAL", "Phase one of two", "partially_billed", 40000),
    ("subtotal", "Unbilled Customer:Site A", None, None, None, 60000),
    ("line", "Unbilled Other", "UB-WO-CONVERTED", "Converted scope", "unbilled", 40000),
    ("subtotal", "Unbilled Other", None, None, None, 40000),
]


def test_every_billable_line_still_to_bill_with_a_subtotal_for_each_job(books):
    client, made, baseline = books
    result = report(client)
    assert result["next_cursor"] is None and result["count"] == len(result["rows"])
    assert result["metadata"]["period"] == {"date_from": None, "date_to": AS_OF}
    assert rows_shape(mine(result["rows"])) == EXPECTED
    assert (result["totals"]["remaining"]["minor_units"]
            - baseline["totals"]["remaining"]["minor_units"]) == 130000
    assert (result["totals"]["billed"]["minor_units"]
            - baseline["totals"]["billed"]["minor_units"]) == 40000

    # Nothing that is finished, unchargeable, unaccepted, cancelled or superseded.
    printed = {row["source_number"] for row in mine(result["rows"]) if row["kind"] == "line"}
    assert printed == {"UB-WO-OPEN", "UB-EST-OPEN", "UB-EST-PARTIAL", "UB-WO-CONVERTED"}
    assert "Goodwill visit" not in {row["description"] for row in result["rows"]}
    # The converted work is listed under the work order that now owns it.
    converted = next(row for row in result["rows"] if row["source_number"] == "UB-WO-CONVERTED")
    assert converted["source_id"] == made["converted_order"]["id"]
    assert converted["source_kind"] == "work_order"
    # Each line says what it sells and where that sale posts.
    assert converted["item_id"] == made["item"]
    assert converted["item_label"] == "Unbilled service"
    assert converted["account_id"] == made["income"]
    assert converted["account_label"] == "Unbilled income"
    assert converted["remaining_quantity"] == "4"


def test_the_remaining_amounts_are_the_billing_window_s_own_numbers(books):
    client, made, _baseline = books
    rows = [row for row in report(client)["rows"] if row["kind"] == "line"]
    for noun, source in (("estimate", made["open_estimate"]), ("estimate", made["partial"]),
                         ("work-order", made["work_order"]), ("work-order", made["converted_order"])):
        window = client.run(f"{noun} billing", {noun.replace("-", "_"): source["id"], "limit": 200},
                            company=COMPANY)
        listed = [row for row in rows if row["source_id"] == window["owner_id"]]
        assert sum(row["remaining"]["minor_units"] for row in listed) == window["remaining_net_minor_units"]
        states = {line["line_id"]: line["state"] for line in window["lines"]}
        for row in listed:
            assert row["state"] == states[row["line_id"]]
            assert row["remaining_quantity"] == next(
                line["remaining_quantity"] for line in window["lines"] if line["line_id"] == row["line_id"])


def test_one_job_can_be_read_alone_and_by_its_canonical_name(books):
    client, made, _baseline = books
    by_id = report(client, customer=made["job"])
    assert shape(by_id) == [row for row in EXPECTED if row[1] == "Unbilled Customer:Site A"]
    assert by_id["totals"]["remaining"]["minor_units"] == 60000
    assert shape(report(client, customer="Unbilled Customer:Site A")) == shape(by_id)
    # A job is its own customer: the parent's own work is not swept in with it.
    assert shape(report(client, customer=made["parent"])) == [
        row for row in EXPECTED if row[1] == "Unbilled Customer"]
    with pytest.raises(BookflowError) as caught:
        report(client, customer="Nobody at all")
    assert caught.value.code == "E_RECORD_NOT_FOUND"


def test_rows_page_while_subtotals_and_totals_cover_the_whole_filter(books):
    client, _made, _baseline = books
    whole = report(client)
    seen, cursor, counted = [], None, 0
    while True:
        page = report(client, limit=4, **({"cursor": cursor} if cursor else {}))
        assert page["totals"] == whole["totals"], "totals cover the filter, never the page"
        assert page["count"] == len(page["rows"]) <= 4
        counted += page["count"]
        seen.extend(page["rows"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert counted == len(whole["rows"])
    assert rows_shape(seen) == rows_shape(whole["rows"])
    assert rows_shape(mine(seen)) == EXPECTED


def test_an_earlier_as_of_date_leaves_out_work_not_yet_recorded(books):
    client, _made, baseline = books
    assert mine(report(client, as_of="2027-04-01")["rows"]) == []
    later = report(client, as_of="2027-05-31")
    assert "UB-EST-LATER" in {row["source_number"] for row in mine(later["rows"])}
    assert (later["totals"]["remaining"]["minor_units"]
            - baseline["totals"]["remaining"]["minor_units"]) == 130000 + 90000


def test_a_bad_as_of_date_is_rejected_and_an_audited_change_stales_a_continuation(books):
    client, _made, _baseline = books
    with pytest.raises(BookflowError) as caught:
        client.run("report unbilled-costs", {"as_of": "not-a-date"}, company=COMPANY)
    assert caught.value.code == "E_VALIDATION"
    first = report(client, limit=1)
    assert first["next_cursor"]
    client.run("customer create", {"name": "Unbilled continuation witness"}, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        report(client, limit=1, cursor=first["next_cursor"])
    assert caught.value.code == "E_QUERY_STALE"
