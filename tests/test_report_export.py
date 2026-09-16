"""A report the user is looking at can be saved as a file, and the file is the whole report.

There was no way to get a report out of this workbench at all: an accountant asking for the
profit and loss as a file, or a bank asking for a balance sheet, could only be read to off a
screen fifty rows at a time. So the witness here is not that an endpoint answers 200. It is the
journey: run a report that is longer than one page, take the file the page offers, and find in
that file the rows that were never on the page.

Nothing here names the twenty-three reports. The registry is asked which commands are reports,
and each report's own output model is asked what its columns are, so a report declared next week
is exported and covered on the day it is declared.
"""
import csv
import html
import io
import json
import re

import pytest
from fastapi.testclient import TestClient

from bookflow.adapters.workbench import report_export as Export
from bookflow.core import registry
from bookflow.core.errors import BookflowError

from tests.test_row3_host import OUTSIDER_PASSWORD, PASSWORD, hosted  # noqa: F401

# The one report the demo company is long enough to page: 391 rows over eight pages of fifty.
LONG_REPORT = "general-ledger"
PERIOD = {"f:date_from": "2000-01-01", "f:date_to": "2099-12-31"}


def _browser(hosted, login=None, password=PASSWORD) -> TestClient:  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": login or hosted.login,
                                        "password": password}).status_code == 200
    return browser


def _run_report(browser, company_id, verb, fields):
    """Run a report the way a person does: fill the filters in and press the button."""
    page = browser.post(f"/c/{company_id}/report/{verb}",
                        data={**fields, "action": "submit"},
                        headers={"X-Bookflow-Workbench": "1"})
    assert page.status_code == 200, page.text[:400]
    return page


def _shown(page):
    """The exact result the page just rendered, as the page itself published it."""
    found = re.search(r"Structured report data</summary><pre>(.*?)</pre>", page.text, re.S)
    assert found, "the report page no longer publishes the result it rendered"
    return json.loads(html.unescape(found.group(1)))


def _export_link(page):
    found = re.search(r'href="([^"]*' + re.escape(Export.SEGMENT) + r'[^"]*)"', page.text)
    return html.unescape(found.group(1)) if found else None


def _table(response):
    """The downloaded file as a spreadsheet would read it: preamble, header row, data rows."""
    body = response.content.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(body)))
    blank = lines.index([])
    preamble = {line[0]: (line[1] if len(line) > 1 else "") for line in lines[:blank] if line}
    header = lines[blank + 1]
    rows, totals = [], []
    collecting = True
    for line in lines[blank + 2:]:
        if line and line[0].startswith("Whole-report totals"):
            collecting = False
            continue
        if not line:
            continue
        (rows if collecting else totals).append(line)
    return preamble, header, rows, totals


# --- what the file is made of, asked of the registry rather than of a list -----------------

def test_the_registry_declares_the_reports_this_export_covers():
    """If this ever finds nothing, everything below is passing by covering nothing."""
    found = Export.reports()
    assert len(found) >= 20, (len(found), "far fewer reports than this product has")
    registered = sorted(cmd.verb for cmd in registry.all_commands()
                        if cmd.noun == "report" and not cmd.is_write)
    assert found == registered, "a registered report the export does not admit"


@pytest.mark.parametrize("verb", Export.reports())
def test_every_report_names_its_own_columns(verb):
    """A report's columns are its row model's own fields, so no report is written out here.

    Two columns headed the same is a column a person cannot identify in a spreadsheet, so
    that is checked too rather than left to whoever reads the file.
    """
    plan = Export.columns(registry.get(f"report {verb}"), {})
    assert plan, f"report {verb} exports no columns"
    headings = [heading for _, _, heading in plan]
    assert len(set(headings)) == len(headings), (verb, headings)
    assert not any(heading.endswith("_id") or "_" in heading for heading in headings), headings


def _sample_filters(cmd):
    """One plausible value for every filter this report declares, from its own types."""
    sample = {}
    for name, field in cmd.input_model.model_fields.items():
        annotation = str(field.annotation)
        if name in ("cursor", "limit"):
            continue
        if name.startswith("date") or name == "as_of":
            sample[name] = "2026-12-31"
        elif "bool" in annotation:
            sample[name] = True
        elif "list" in annotation:
            sample[name] = ["X1", "X2"]
        elif "int" in annotation:
            sample[name] = 5
        elif "Literal['accrual']" in annotation:
            sample[name] = "accrual"
        elif "Literal" in annotation:
            continue
        else:
            sample[name] = "X1"
    return sample


@pytest.mark.parametrize("verb", Export.reports())
def test_every_report_carries_its_own_filters_into_the_file(verb):
    """The download link is the report on the screen, so every filter has to survive it.

    A flag, a number and a filter that names a set of accounts are all written in the report
    form's own encoding and read back by the form's own translator, so what the file is a
    report of is what the page was showing -- and a report that grows a filter keeps working
    without anything here being edited.
    """
    from starlette.datastructures import QueryParams

    cmd = registry.get(f"report {verb}")
    sample = _sample_filters(cmd)
    link = Export.export_url("CO", verb, {**sample, "limit": 50, "cursor": "a-continuation"})
    read = Export.inputs_from(cmd, QueryParams(link.split("?", 1)[1] if "?" in link else ""))
    assert read == sample, (verb, read, sample)
    assert "cursor" not in read and "limit" not in read, "the file would start mid-report"
    assert cmd.input_model.model_validate(read).limit == 50


def test_a_report_read_across_columns_gets_one_file_column_per_report_column():
    """The profit and loss by job has a money value per job on every row. In a spreadsheet
    each job has to be its own column, or the figures cannot be added up."""
    plan = Export.columns(registry.get("report profit-and-loss-by-job"),
                          {"columns": [{"label": "Acme"}, {"label": "Beta"}, {"label": "Unassigned"}]})
    spread = [heading for name, kind, heading in plan if name == "amounts"]
    assert spread == ["Acme", "Beta", "Unassigned"], plan


def test_a_cell_a_spreadsheet_would_run_as_a_formula_is_neutralised():
    """A memo is typed by a person and a spreadsheet treats a leading = as a program.
    A number is left exactly as the report wrote it, negatives included."""
    assert Export._guard("=SUM(A1:A9)").startswith("'")
    assert Export._guard("@import").startswith("'")
    assert Export._guard("+44 20 7000 0000").startswith("'")
    assert Export._guard("-100.00") == "-100.00"
    assert Export._guard("1,234.50") == "1,234.50"


def test_a_report_longer_than_the_ceiling_says_so_rather_than_ending_silently():
    """The traversal is bounded, so the one thing it must never do is stop without saying."""
    reads = {"n": 0}

    def read(name, raw, company):
        reads["n"] += 1
        return {"rows": [{"kind": "posting"}], "next_cursor": "more",
                "metadata": {"period": {"date_from": "2026-01-01", "date_to": "2026-12-31"},
                             "basis": "accrual", "currency": "USD",
                             "generation_time": "now", "audit_watermark": 1},
                "totals": {}}

    cmd = registry.get("report general-ledger")
    rows, first, truncated = Export.read_all(read, "company", cmd, {"date_to": "2026-12-31"})
    assert truncated and reads["n"] == Export.PAGES + 1, reads
    said = Export.preamble("General ledger", "Demo", first, len(rows), truncated)
    assert any(line[0] == "Truncated" for line in said), said


def test_a_write_during_the_traversal_restarts_it_rather_than_mixing_two_states():
    """A report refuses its own continuation when the books move, and that refusal is right.

    What must never happen is a file whose first half is one state and second half another,
    so the rows read so far are thrown away and the whole report is taken again.
    """
    attempts = {"n": 0}

    def read(name, raw, company):
        if raw.get("cursor") is None:
            attempts["n"] += 1
        if attempts["n"] == 1 and raw.get("cursor") is not None:
            raise BookflowError("E_QUERY_STALE")
        return {"rows": [{"kind": "posting", "attempt": attempts["n"]}],
                "next_cursor": "more" if raw.get("cursor") is None else None,
                "metadata": {"period": {"date_from": None, "date_to": "2026-12-31"},
                             "basis": "accrual", "currency": "USD",
                             "generation_time": "now", "audit_watermark": attempts["n"]},
                "totals": {}}

    rows, first, truncated = Export.read_all(
        read, "company", registry.get("report general-ledger"), {"date_to": "2026-12-31"})
    assert attempts["n"] == 2, attempts
    assert {row["attempt"] for row in rows} == {2}, "the file mixed a discarded reading in"
    assert first["metadata"]["audit_watermark"] == 2


# --- the journey ---------------------------------------------------------------------------

def test_a_long_report_exports_every_page_and_not_just_the_first(hosted):  # noqa: F811
    """Run a report longer than one page, page it by hand, then take the file it offers.

    The file has to carry the rows that were never on the first page, so the evidence is the
    second page's own rows -- the ones a person only sees after pressing Next -- found inside
    the downloaded file, and the first page's rows found there too.
    """
    browser = _browser(hosted)
    company = hosted.company_id

    first = _run_report(browser, company, LONG_REPORT, PERIOD)
    page_one = _shown(first)
    assert page_one["next_cursor"], "this report is not longer than one page; the journey proves nothing"
    assert len(page_one["rows"]) == 50, len(page_one["rows"])

    # The second page, reached the way the page offers it: its own Next form.
    form = re.search(r'<form[^>]*id="statement-next-page".*?</form>', first.text, re.S)
    assert form, "the report no longer offers a next page"
    carried = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(0)))
    second = browser.post(f"/c/{company}/report/{LONG_REPORT}",
                          data={**carried, "action": "submit"},
                          headers={"X-Bookflow-Workbench": "1"})
    assert second.status_code == 200, second.text[:400]
    page_two = _shown(second)
    assert page_two["rows"], "the second page came back empty"

    link = _export_link(first)
    assert link, "the report page offers no way to save the report as a file"
    saved = browser.get(link)
    assert saved.status_code == 200, saved.text[:400]
    assert saved.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="general-ledger-' in saved.headers["content-disposition"]
    assert saved.headers["cache-control"] == "no-store"

    preamble, header, rows, totals = _table(saved)
    assert header == [heading for _, _, heading in
                      Export.columns(registry.get(f"report {LONG_REPORT}"), page_one)]

    line_id = header.index("Posting line")
    in_file = {row[line_id] for row in rows if row[line_id]}
    on_page_one = {row["posting_line_id"] for row in page_one["rows"] if row.get("posting_line_id")}
    on_page_two = {row["posting_line_id"] for row in page_two["rows"] if row.get("posting_line_id")}

    assert on_page_one and on_page_two and not (on_page_one & on_page_two)
    assert on_page_one <= in_file, "the file is missing rows the first page showed"
    assert on_page_two <= in_file, "the file is only the first page"
    assert len(in_file - on_page_one - on_page_two) > 100, \
        (len(in_file), "the file stopped at the pages a person had already read")
    assert len(rows) > len(page_one["rows"]) * 5, (len(rows), "the file is barely longer than one page")

    # What the file says it is: the company, the report, the period and the state of the books.
    assert preamble["Company"] and preamble["Report"] == "General ledger"
    assert preamble["Period"] == "2000-01-01 to 2099-12-31"
    assert preamble["Currency"] == page_one["metadata"]["currency"]
    assert "Truncated" not in preamble, preamble
    assert int(preamble["Rows"]) == len(rows)
    # And the whole-report totals the page prints above the table, so the file reconciles
    # without anyone adding a column up by hand.
    figures = dict(row[:2] for row in totals if len(row) > 1)
    assert figures["Period debits"] == page_one["totals"]["period_debits"]["amount"], figures


def test_the_report_page_offers_the_file_and_carries_its_own_filters(hosted):  # noqa: F811
    """Without the download link on the page there is no export, whatever the route answers."""
    browser = _browser(hosted)
    page = _run_report(browser, hosted.company_id, "trial-balance", {"f:date_to": "2099-12-31"})
    link = _export_link(page)
    assert link, "a report that has been run offers no file"
    assert link.startswith(f"/c/{hosted.company_id}/report/trial-balance/{Export.SEGMENT}")
    assert "f%3Adate_to=2099-12-31" in link or "f:date_to=2099-12-31" in link, link
    # The continuation is deliberately not carried: the file starts at the first row.
    assert "cursor" not in link, link
    preamble, header, rows, _ = _table(browser.get(link))
    assert preamble["As of"] == "2099-12-31"
    assert "Signed net" in header and rows


def test_a_name_that_is_not_a_report_is_refused_rather_than_downloaded(hosted):  # noqa: F811
    """The route is opened by what the registry calls a report, so it must close on anything else."""
    browser = _browser(hosted)
    for verb in ("not-a-real-report", "statement/../../company"):
        answer = browser.get(f"/c/{hosted.company_id}/report/{verb}/{Export.SEGMENT}",
                             follow_redirects=False)
        assert answer.status_code != 200 or "text/csv" not in answer.headers.get("content-type", ""), verb


def test_a_member_of_another_organization_cannot_export_this_company(hosted):  # noqa: F811
    """The file is produced through the same `run` the page uses, so isolation is not re-decided."""
    outsider = _browser(hosted, login="outsider", password=OUTSIDER_PASSWORD)
    answer = outsider.get(f"/c/{hosted.company_id}/report/trial-balance/{Export.SEGMENT}"
                          "?f:date_to=2099-12-31", follow_redirects=False)
    assert "text/csv" not in answer.headers.get("content-type", ""), answer.headers
    assert answer.status_code >= 400 or answer.status_code == 303, answer.status_code
