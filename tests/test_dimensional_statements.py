"""One month of jobbed and classified postings, read down and across.

Every figure below is computed by hand from the journals the fixture posts, and
the total column of each dimensional report is checked against ``report
profit-and-loss`` for the same dates -- account by account and total by total --
because a split of a statement that does not add back up to the statement is worse
than no split at all.
"""
import re

import pytest

from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted, PASSWORD, WB  # noqa: F401

COMPANY = "Demo Plumbing Co"
# A window of its own, so nothing the demo seeded is inside the report.
FROM, TO = "2027-03-01", "2027-03-31"
DATE = "2027-03-05"


def build(run):
    """Post the month through whichever surface ``run(command, input)`` speaks."""
    income = run("account create", {"name": "Dimension income", "type": "income"})["id"]
    swing = run("account create", {"name": "Dimension swing", "type": "income"})["id"]
    expense = run("account create", {"name": "Dimension expense", "type": "expense"})["id"]
    clearing = run("account create", {"name": "Dimension clearing", "type": "other_current_asset"})["id"]
    alpha = run("customer create", {"name": "Dimension Alpha"})["id"]
    phase = run("customer create", {"name": "Phase one", "parent_id": alpha,
                                    "job_status": "in_progress"})["id"]
    beta = run("customer create", {"name": "Dimension Beta"})["id"]
    vendor = run("vendor create", {"name": "Dimension Vendor"})["id"]
    north = run("class create", {"name": "Dimension North"})["id"]
    south = run("class create", {"name": "Dimension South"})["id"]

    def earn(number, amount, party, class_id, account=income):
        run("journal post", {"date": DATE, "number": number, "lines": [
            {"account": clearing, "side": "debit", "amount": amount},
            {"account": account, "side": "credit", "amount": amount,
             **({"name_type": "customer", "name_id": party} if party else {}),
             **({"class_id": class_id} if class_id else {})}]})

    def spend(number, amount, name_type, party, class_id):
        run("journal post", {"date": DATE, "number": number, "lines": [
            {"account": expense, "side": "debit", "amount": amount,
             **({"name_type": name_type, "name_id": party} if party else {}),
             **({"class_id": class_id} if class_id else {})},
            {"account": clearing, "side": "credit", "amount": amount}]})

    earn("DIM-1", "300.00", alpha, north)
    earn("DIM-2", "200.00", phase, south)
    earn("DIM-3", "100.00", beta, None)
    spend("DIM-4", "50.00", "customer", phase, north)
    # A vendor named on an expense line is a vendor, not a job: it belongs in the
    # job report's Unassigned column and in its own class column.
    spend("DIM-5", "40.00", "vendor", vendor, south)
    spend("DIM-6", "10.00", None, None, None)
    # An account whose whole-company net for the period is zero but which is real
    # money on two different jobs. The profit and loss does not print it; the split
    # must, or one job's column would be short by what the other's is long.
    earn("DIM-7", "25.00", alpha, None, account=swing)
    run("journal post", {"date": DATE, "number": "DIM-8", "lines": [
        {"account": swing, "side": "debit", "amount": "25.00", "name_type": "customer", "name_id": beta},
        {"account": clearing, "side": "credit", "amount": "25.00"}]})
    return dict(income=income, swing=swing, expense=expense, clearing=clearing,
                alpha=alpha, phase=phase, beta=beta, vendor=vendor, north=north, south=south)


@pytest.fixture
def books(client):
    def run(command, body):
        return client.run(command, body, company=COMPANY, reason="Dimensional report fixture")
    return client, build(run)


def report(client, verb, **extra):
    return client.run("report " + verb, {"date_from": FROM, "date_to": TO, "limit": 200, **extra},
                      company=COMPANY)


def cells(result):
    """The report as {account label: {column label: minor units}} plus its totals."""
    labels = [column["label"] for column in result["columns"]]
    return {row["display_account_label"]: dict(zip(labels, [a["minor_units"] for a in row["amounts"]]))
            for row in result["rows"]}


EXPECTED_JOB = {
    "Dimension income": {"Dimension Alpha": 30000, "Dimension Alpha:Phase one": 20000,
                         "Dimension Beta": 10000, "Unassigned": 0},
    "Dimension swing": {"Dimension Alpha": 2500, "Dimension Alpha:Phase one": 0,
                        "Dimension Beta": -2500, "Unassigned": 0},
    "Dimension expense": {"Dimension Alpha": 0, "Dimension Alpha:Phase one": 5000,
                          "Dimension Beta": 0, "Unassigned": 5000},
}
EXPECTED_CLASS = {
    "Dimension income": {"Dimension North": 30000, "Dimension South": 20000, "Unclassified": 10000},
    "Dimension swing": {"Dimension North": 0, "Dimension South": 0, "Unclassified": 0},
    "Dimension expense": {"Dimension North": 5000, "Dimension South": 4000, "Unclassified": 1000},
}


def test_the_job_columns_are_exact_and_the_total_column_is_the_profit_and_loss(books):
    client, _ = books
    result = report(client, "profit-and-loss-by-job")
    assert result["next_cursor"] is None and result["count"] == len(result["rows"])
    assert result["dimension"] == "job"
    assert [(column["kind"], column["label"]) for column in result["columns"]] == [
        ("value", "Dimension Alpha"), ("value", "Dimension Alpha:Phase one"),
        ("value", "Dimension Beta"), ("unassigned", "Unassigned")]
    assert cells(result) == EXPECTED_JOB
    assert {column["label"]: column["totals"]["net_income"]["minor_units"] for column in result["columns"]} == {
        "Dimension Alpha": 32500, "Dimension Alpha:Phase one": 15000,
        "Dimension Beta": 7500, "Unassigned": -5000}

    # The whole point: the report is the profit and loss, read sideways.
    statement = client.run("report profit-and-loss", {"date_from": FROM, "date_to": TO, "limit": 200}, company=COMPANY)
    assert result["totals"] == statement["totals"]
    by_account = {row["account_id"]: row["amount"]["minor_units"] for row in statement["rows"]}
    for row in result["rows"]:
        assert sum(amount["minor_units"] for amount in row["amounts"]) == row["total"]["minor_units"]
        if row["account_id"] in by_account:
            assert by_account[row["account_id"]] == row["total"]["minor_units"], row["display_account_label"]
    # The zero-net account the statement omits is here, because it is not zero on a job.
    assert "Dimension swing" not in {row["display_account_label"] for row in statement["rows"]}
    assert "Dimension swing" in cells(result)


def test_the_class_columns_are_exact_and_the_total_column_is_the_profit_and_loss(books):
    client, _ = books
    result = report(client, "profit-and-loss-by-class")
    assert result["dimension"] == "class"
    assert [(column["kind"], column["label"]) for column in result["columns"]] == [
        ("value", "Dimension North"), ("value", "Dimension South"), ("unassigned", "Unclassified")]
    assert cells(result) == EXPECTED_CLASS
    assert {column["label"]: column["totals"]["net_income"]["minor_units"] for column in result["columns"]} == {
        "Dimension North": 25000, "Dimension South": 16000, "Unclassified": 9000}
    statement = client.run("report profit-and-loss", {"date_from": FROM, "date_to": TO, "limit": 200}, company=COMPANY)
    assert result["totals"] == statement["totals"]
    for row in result["rows"]:
        assert sum(amount["minor_units"] for amount in row["amounts"]) == row["total"]["minor_units"]


@pytest.mark.parametrize("verb,expected", [("profit-and-loss-by-job", EXPECTED_JOB),
                                           ("profit-and-loss-by-class", EXPECTED_CLASS)])
def test_rows_page_while_columns_and_totals_cover_the_whole_statement(books, verb, expected):
    client, _ = books
    whole = report(client, verb)
    seen, cursor, pages = {}, None, 0
    while True:
        page = report(client, verb, limit=1, **({"cursor": cursor} if cursor else {}))
        pages += 1
        assert page["totals"] == whole["totals"]
        assert page["columns"] == whole["columns"]
        labels = [column["label"] for column in page["columns"]]
        for row in page["rows"]:
            seen[row["display_account_label"]] = dict(zip(labels, [a["minor_units"] for a in row["amounts"]]))
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == len(whole["rows"])
    assert seen == expected


def test_a_narrow_column_request_folds_the_rest_without_losing_any_money(books):
    client, _ = books
    whole = report(client, "profit-and-loss-by-job")
    narrow = report(client, "profit-and-loss-by-job", columns=1)
    assert [(column["kind"], column["label"], column["folded_count"]) for column in narrow["columns"]] == [
        ("value", "Dimension Alpha", None), ("other", "Other (2)", 2), ("unassigned", "Unassigned", None)]
    assert narrow["totals"] == whole["totals"]
    folded = cells(narrow)
    wide = cells(whole)
    for account, columns in wide.items():
        assert folded[account]["Dimension Alpha"] == columns["Dimension Alpha"]
        assert folded[account]["Unassigned"] == columns["Unassigned"]
        assert folded[account]["Other (2)"] == (columns["Dimension Alpha:Phase one"]
                                                + columns["Dimension Beta"])
    for row in narrow["rows"]:
        assert sum(amount["minor_units"] for amount in row["amounts"]) == row["total"]["minor_units"]


def test_zero_accounts_are_shown_only_when_asked_and_carry_no_money(books):
    client, _ = books
    plain = report(client, "profit-and-loss-by-job")
    everything = report(client, "profit-and-loss-by-job", include_zero=True)
    assert everything["totals"] == plain["totals"]
    assert len(everything["rows"]) > len(plain["rows"])
    added = {row["display_account_label"] for row in everything["rows"]} - set(cells(plain))
    values = cells(everything)
    for label in added:
        assert set(values[label].values()) == {0}, label


def test_a_bad_date_and_an_out_of_range_column_count_are_rejected(books):
    client, _ = books
    for body in ({"date_from": FROM, "date_to": "not-a-date"},
                 {"date_from": TO, "date_to": FROM},
                 {"date_from": FROM, "date_to": TO, "columns": 0},
                 {"date_from": FROM, "date_to": TO, "columns": 201}):
        with pytest.raises(BookflowError) as caught:
            client.run("report profit-and-loss-by-job", body, company=COMPANY)
        assert caught.value.code == "E_VALIDATION"


def test_an_audited_change_stales_a_continuation_rather_than_repaging(books):
    client, _ = books
    first = report(client, "profit-and-loss-by-job", limit=1)
    assert first["next_cursor"]
    client.run("customer create", {"name": "Dimensional continuation witness"}, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        report(client, "profit-and-loss-by-job", limit=1, cursor=first["next_cursor"])
    assert caught.value.code == "E_QUERY_STALE"


# ---------------------------------------------------------------------------
# The page itself, without a browser.
#
# The rendered report had a Chrome-gated test and nothing else, so on a machine
# with no Chrome the whole page was unwitnessed and a broken one shipped: the
# form picked the wrong template for a dimensional result and every row raised
# on an ``amount`` field these rows do not carry. With no rows the loop never
# ran, so an empty company rendered and hid it. These read the served HTML, so
# they fail whether or not a browser is installed.

_LABEL = re.compile(r'<span class="document-cell-label"[^>]*>.*?</span>', re.S)


def _text(fragment):
    """One cell as a reader sees it, without the phone-only column label."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _LABEL.sub("", fragment))).strip()


def _table(page, table_id):
    """The headings and the visible text of every body cell, in document order."""
    body = page[page.index(f'id="{table_id}"'):]
    body = body[:body.index("</table>")]
    head, rest = body.split("</thead>", 1)
    return ([_text(cell) for cell in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)],
            [[_text(cell) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
             for row in re.findall(r"<tr[^>]*>(.*?)</tr>", rest.split("</tbody>")[0], re.S)])


def _pairs(page, list_id):
    """A definition list on the page as the label and figure pairs it prints."""
    part = page[page.index(f'id="{list_id}"'):]
    part = part[:part.index("</dl>")]
    return [(_text(term), _text(value))
            for term, value in re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", part, re.S)]


@pytest.fixture
def served(hosted):  # noqa: F811
    """The month, posted over HTTP, and a signed-in workbench client for the company."""
    from fastapi.testclient import TestClient
    made = build(lambda command, body: hosted.ok(command.replace(" ", "."), body, company=hosted.company_id))
    api = TestClient(hosted.handle.app)
    assert api.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return hosted, api, made


def _run_report(api, company_id, verb, **fields):
    page = api.post(f"/c/{company_id}/report/{verb}",
                    data={"f:date_from": FROM, "f:date_to": TO, "f:limit": "200",
                          "f:columns": "50", **fields}, headers=WB)
    assert page.status_code == 200, page.text[:600]
    return page.text


@pytest.mark.parametrize("verb,headings,rows,summary", [
    ("profit-and-loss-by-job",
     ["Account", "Dimension Alpha", "Dimension Alpha:Phase one", "Dimension Beta", "Unassigned", "Total"],
     [["Dimension income Income", "300.00", "200.00", "100.00", "0.00", "600.00"],
      ["Dimension swing Income", "25.00", "0.00", "-25.00", "0.00", "0.00"],
      ["Dimension expense Expense", "0.00", "50.00", "0.00", "50.00", "100.00"]],
     [("Dimension Alpha", "325.00"), ("Dimension Alpha:Phase one", "150.00"),
      ("Dimension Beta", "75.00"), ("Unassigned", "-50.00"), ("Total — net income", "500.00")]),
    ("profit-and-loss-by-class",
     ["Account", "Dimension North", "Dimension South", "Unclassified", "Total"],
     [["Dimension income Income", "300.00", "200.00", "100.00", "600.00"],
      ["Dimension swing Income", "0.00", "0.00", "0.00", "0.00"],
      ["Dimension expense Expense", "50.00", "40.00", "10.00", "100.00"]],
     [("Dimension North", "250.00"), ("Dimension South", "160.00"),
      ("Unclassified", "90.00"), ("Total — net income", "500.00")]),
])
def test_the_workbench_page_reads_a_dimensional_row_it_actually_has(served, verb, headings, rows, summary):
    """Every figure off the served HTML, for a company that has posted money."""
    hosted, api, _ = served
    page = _run_report(api, hosted.company_id, verb)
    # The dimensional result is rendered by the dimensional page, not by the
    # single-column statement, whose row markup these rows cannot satisfy.
    assert 'id="dimensional-lines"' in page, page[page.find("<main>"):][:800]
    assert 'id="statement-accounts"' not in page
    assert _table(page, "dimensional-lines") == (headings, rows)
    assert _pairs(page, "dimensional-column-summary") == summary
    assert ("net income", "500.00 USD") in [(term.lower(), value)
                                            for term, value in _pairs(page, "dimensional-totals")]
    # Each row's Total opens that account's own current general ledger.
    assert page.count(f'/c/{hosted.company_id}/report/general-ledger?') >= len(rows)


def test_an_empty_period_still_renders_the_dimensional_page(served):
    """The zero-row case, which rendered even while every real row raised."""
    hosted, api, _ = served
    page = _run_report(api, hosted.company_id, "profit-and-loss-by-job",
                       **{"f:date_from": "2029-01-01", "f:date_to": "2029-01-31"})
    assert 'id="dimensional-lines"' in page and 'id="statement-accounts"' not in page
    assert "No income or expense was posted in this period." in page


def test_the_dimensional_page_folds_wide_columns_and_carries_its_own_next_page(served):
    """The two parts of the page the first witness does not reach."""
    hosted, api, _ = served
    narrow = _run_report(api, hosted.company_id, "profit-and-loss-by-job", **{"f:columns": "1"})
    assert _table(narrow, "dimensional-lines") == (
        ["Account", "Dimension Alpha", "Other (2)", "Unassigned", "Total"],
        [["Dimension income Income", "300.00", "300.00", "0.00", "600.00"],
         ["Dimension swing Income", "25.00", "-25.00", "0.00", "0.00"],
         ["Dimension expense Expense", "0.00", "50.00", "50.00", "100.00"]])
    count = narrow[narrow.index('id="report-page-count"'):]
    assert "added together in the Other column" in count[:count.index("</p>")]

    first = _run_report(api, hosted.company_id, "profit-and-loss-by-job", **{"f:limit": "1"})
    assert _table(first, "dimensional-lines")[1] == [
        ["Dimension income Income", "300.00", "200.00", "100.00", "0.00", "600.00"]]
    form = first[first.index('id="statement-next-page"'):]
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                             form[:form.index("</form>")]))
    assert fields["f:cursor"] and fields["f:date_from"] == FROM
    page = api.post(f"/c/{hosted.company_id}/report/profit-and-loss-by-job", data=fields, headers=WB)
    assert page.status_code == 200, page.text[:600]
    assert _table(page.text, "dimensional-lines")[1] == [
        ["Dimension swing Income", "25.00", "0.00", "-25.00", "0.00", "0.00"]]
    # Whole-statement totals on every page, never this page's own arithmetic.
    assert ("net income", "500.00 USD") in [(term.lower(), value)
                                            for term, value in _pairs(page.text, "dimensional-totals")]
