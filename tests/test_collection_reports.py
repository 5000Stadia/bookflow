"""Who is overdue, read against the aging report the numbers come from.

The receivables month is the one `tests/test_receivable_reports.py` already posts,
so the expected columns here are that file's own hand-computed oracles rather than
a second set that could drift from them. What this file adds is the collections
question: which of those customers is actually late, which invoices made them late,
and whether the telephone numbers recorded against them reach the page.
"""
import pytest

from bookflow.core.errors import BookflowError
from tests.test_receivable_reports import (  # noqa: F401
    AS_OF, COMPANY, COLUMNS, EXPECTED_ROWS, EXPECTED_TOTALS, build,
)

REASON = "Collections fixture"
# Every customer in that month is overdue by something, so the bucket columns of
# the collections report are the aging report's columns exactly.
EXPECTED_OVERDUE = {
    "Aging Alpha": 150000,
    "Aging Alpha:North Job": 30000,
    "Aging Beta": 25000,
    "Aging Gamma": 40000,
}
# Customer first, then that customer's overdue invoices, oldest due date first.
EXPECTED_SHAPE = [
    ("customer", "Aging Alpha", None),
    ("invoice", "Aging Alpha", "AGE-91"),
    ("invoice", "Aging Alpha", "AGE-90"),
    ("invoice", "Aging Alpha", "AGE-30"),
    ("customer", "Aging Alpha:North Job", None),
    ("invoice", "Aging Alpha:North Job", "AGE-31"),
    ("customer", "Aging Beta", None),
    ("invoice", "Aging Beta", "AGE-PARTLY"),
    ("customer", "Aging Gamma", None),
    ("invoice", "Aging Gamma", "AGE-OLD"),
]
CONTACTS = [
    {"role": "primary", "display_name": "Robin Alpha", "job_title": "Accounts payable",
     "work_phone": "555-0200", "primary_email": "robin@alpha.example.test",
     "points": [{"kind": "mobile_phone", "value": "555-0201"},
                {"kind": "additional_email", "custom_label": "Site office",
                 "value": "site@alpha.example.test"}]},
    {"role": "alternate", "display_name": "Sam Alpha", "work_phone": "555-0202"},
]


def writes(command):
    from bookflow.core import registry
    registry.load_all()
    return registry.get(command).is_write


@pytest.fixture
def books(client):
    def run(command, body, *, reason=None):
        extra = {"reason": reason or REASON} if writes(command) else {}
        return client.run(command, body, company=COMPANY, **extra)
    made = build(run)
    # Somebody to telephone. The job under Alpha keeps the inherited contact mode it
    # was created with, so chasing the job has to reach the parent's contacts.
    alpha = run("customer show", {"customer": made["alpha"]})
    run("customer update", {"customer": made["alpha"], "expected_version": alpha["version"],
                            "contacts": CONTACTS})
    assert run("customer show", {"customer": made["job"]})["contact_mode"] == "inherit"
    return client, made


def report(client, **extra):
    return client.run("report collections", {"as_of": AS_OF, "limit": 200, **extra}, company=COMPANY)


def shape(result):
    return [(row["kind"], row["display_customer_label"], row["number"]) for row in result["rows"]]


def test_the_overdue_customers_their_invoices_and_the_aging_they_came_from(books):
    client, _ = books
    result = report(client)
    assert result["next_cursor"] is None and result["count"] == len(result["rows"])
    assert result["customer_count"] == 4
    assert shape(result) == EXPECTED_SHAPE

    # The columns are the aging report's columns, for the same customers on the
    # same date. These two must never disagree, so this is asserted directly.
    aging = client.run("report ar-aging", {"as_of": AS_OF, "limit": 200}, company=COMPANY)
    by_customer = {row["display_customer_label"]: row for row in aging["rows"]}
    for row in result["rows"]:
        if row["kind"] != "customer":
            continue
        expected = by_customer[row["display_customer_label"]]
        assert {name: row[name] for name in COLUMNS} == {name: expected[name] for name in COLUMNS}
        assert row["overdue"]["minor_units"] == EXPECTED_OVERDUE[row["display_customer_label"]]
    assert [(label, dict(zip(COLUMNS, (row[name]["minor_units"] for name in COLUMNS))))
            for label, row in ((row["display_customer_label"], row) for row in result["rows"]
                               if row["kind"] == "customer")] == EXPECTED_ROWS

    # Every customer that month is overdue, so the totals are the whole aging.
    assert {name: result["totals"][name]["minor_units"] for name in COLUMNS} == EXPECTED_TOTALS
    assert result["totals"]["overdue"]["minor_units"] == sum(EXPECTED_OVERDUE.values())
    assert result["totals"]["overdue"]["minor_units"] == sum(
        EXPECTED_TOTALS[name] for name in COLUMNS[1:-1])


def test_each_overdue_invoice_carries_what_is_needed_to_ask_for_it(books):
    client, _ = books
    rows = {row["number"]: row for row in report(client)["rows"] if row["kind"] == "invoice"}
    assert rows["AGE-91"]["due_date"] == "2026-03-31"
    assert rows["AGE-91"]["days_past_due"] == 91
    assert rows["AGE-91"]["aging_bucket"] == "over_90"
    assert rows["AGE-91"]["balance"]["minor_units"] == 70000
    # A partly paid invoice is chased for what is still open on it, not for its face.
    assert rows["AGE-PARTLY"]["balance"]["minor_units"] == 30000
    assert rows["AGE-PARTLY"]["aging_bucket"] == "days_61_90"
    assert rows["AGE-PARTLY"]["date"] == "2026-03-01"
    # A voided invoice is worth nothing, so nobody is chased for it.
    assert "AGE-VOID" not in rows
    # Nothing that is merely current is on a collections list.
    assert "AGE-TODAY" not in rows and "AGE-CURRENT" not in rows


def test_the_people_to_call_reach_the_row_including_through_an_inheriting_job(books):
    client, _ = books
    rows = {row["display_customer_label"]: row for row in report(client)["rows"]
            if row["kind"] == "customer"}
    alpha = rows["Aging Alpha"]["contacts"]
    assert [contact["display_name"] for contact in alpha] == ["Robin Alpha", "Sam Alpha"]
    assert [contact["role"] for contact in alpha] == ["primary", "alternate"]
    assert alpha[0]["job_title"] == "Accounts payable"
    assert alpha[0]["work_phone"] == "555-0200"
    assert alpha[0]["primary_email"] == "robin@alpha.example.test"
    assert [(point["kind"], point["custom_label"], point["value"]) for point in alpha[0]["points"]] == [
        ("mobile_phone", None, "555-0201"),
        ("additional_email", "Site office", "site@alpha.example.test")]
    assert not any(contact["inherited"] for contact in alpha)

    # The job records no contacts of its own, so chasing it reaches the parent's.
    job = rows["Aging Alpha:North Job"]["contacts"]
    assert [contact["contact_id"] for contact in job] == [contact["contact_id"] for contact in alpha]
    assert all(contact["inherited"] for contact in job)
    # A customer nobody recorded a contact for still appears, with nobody to call.
    assert rows["Aging Gamma"]["contacts"] == []


def test_the_chase_can_start_at_an_older_column(books):
    client, _ = books
    older = report(client, minimum_bucket="days_61_90")
    assert [row["display_customer_label"] for row in older["rows"] if row["kind"] == "customer"] == [
        "Aging Alpha", "Aging Beta", "Aging Gamma"]
    assert older["customer_count"] == 3
    assert older["totals"]["overdue"]["minor_units"] == 130000 + 30000 + 40000
    # A customer that reaches the chosen column is chased for everything overdue,
    # so the invoices under it are still every past-due invoice it holds.
    assert [row["number"] for row in older["rows"] if row["kind"] == "invoice"] == [
        "AGE-91", "AGE-90", "AGE-30", "AGE-PARTLY", "AGE-OLD"]

    oldest = report(client, minimum_bucket="over_90")
    assert [row["display_customer_label"] for row in oldest["rows"] if row["kind"] == "customer"] == [
        "Aging Alpha", "Aging Gamma"]
    assert oldest["totals"]["overdue"]["minor_units"] == 70000 + 40000


def test_rows_page_while_the_totals_cover_every_overdue_customer(books):
    client, _ = books
    whole = report(client)
    seen, cursor, counted = [], None, 0
    while True:
        page = report(client, limit=3, **({"cursor": cursor} if cursor else {}))
        assert page["totals"] == whole["totals"], "totals cover the filter, never the page"
        assert page["customer_count"] == whole["customer_count"]
        assert page["count"] == len(page["rows"]) <= 3
        counted += page["count"]
        seen.extend(page["rows"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert counted == len(whole["rows"])
    assert [(row["kind"], row["display_customer_label"], row["number"]) for row in seen] == EXPECTED_SHAPE


def test_a_bad_date_or_bucket_is_rejected_and_an_audited_change_stales_a_continuation(books):
    client, _ = books
    for body in ({"as_of": "not-a-date"}, {"as_of": AS_OF, "minimum_bucket": "current"},
                 {"as_of": AS_OF, "minimum_bucket": "days_2_40"}):
        with pytest.raises(BookflowError) as caught:
            client.run("report collections", body, company=COMPANY)
        assert caught.value.code == "E_VALIDATION"
    first = report(client, limit=1)
    assert first["next_cursor"]
    client.run("customer create", {"name": "Collections continuation witness"}, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        report(client, limit=1, cursor=first["next_cursor"])
    assert caught.value.code == "E_QUERY_STALE"
