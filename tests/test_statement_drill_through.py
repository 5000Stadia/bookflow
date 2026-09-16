"""A figure on a statement reaches the transactions behind it, and they reach their documents.

An accounting report is not a printed page. A reader who does not believe a figure follows it
down to the entries that made it and then to the document that posted them, and the chain is
only real if every link opens. So the journey here is walked rather than described: each link
is taken out of the rendered page and fetched, because a rendered href that nothing follows is
exactly how the dimensional Total column came to be shipped untested.

Two things travel the whole way down. The audit position the statement was read at, so the
document answers the question the statement asked rather than a fresh one. And retained
history, because a report sums immutable effects: deleting a document hides it from ordinary
lists without changing a single figure, so the rows it posted still name it and it must still
open behind them.
"""
import json
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from bookflow.adapters.workbench import missing_checks, statements, transaction_detail
from bookflow.company import ledger_reports, ledger_schema, money_out_schema

from tests.test_financial_statements_browser import fill
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401

PERIOD = {"f:date_from": "2000-01-01", "f:date_to": "2100-12-31"}


class _Anchors(HTMLParser):
    """Every link in a fragment, as the href and the words it is written on."""

    def __init__(self):
        super().__init__()
        self.found: list[list[str]] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self._open = [href, ""]
            self.found.append(self._open)

    def handle_endtag(self, tag):
        if tag == "a":
            self._open = None

    def handle_data(self, data):
        if self._open is not None:
            self._open[1] += data


def _anchors(fragment):
    parser = _Anchors()
    parser.feed(fragment)
    return [(href, " ".join(text.split())) for href, text in parser.found]


class _Form(HTMLParser):
    """The report's own filter form, read back exactly as a browser would submit it."""

    def __init__(self):
        super().__init__()
        self.action = None
        self.fields: dict[str, str] = {}
        self._inside = False
        self._select = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and "data-generated-form" in attrs:
            self._inside, self.action = True, attrs.get("action")
        elif not self._inside:
            return
        elif tag == "input" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")
        elif tag == "select" and attrs.get("name"):
            self._select = attrs["name"]
        elif tag == "option" and self._select:
            if "selected" in attrs or self._select not in self.fields:
                self.fields[self._select] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._inside = False
        elif tag == "select":
            self._select = None


def _readable(url, answer):
    assert answer.status_code == 200, f"{url} answered {answer.status_code}: {answer.text[:600]}"
    body = answer.text.split("<main>", 1)[-1].split("</main>", 1)[0]
    assert 'class="error"' not in body, f"{url} rendered an error: {body[:600]}"
    return answer.text


def _page(browser, url):
    """One page of the journey, refused rather than followed if it did not come out readable."""
    return _readable(url, browser.get(url))


def _run_report(browser, url):
    """Open a report link the way a person does: follow it, then run what it filled in."""
    page = _page(browser, url)
    form = _Form()
    form.feed(page)
    assert form.action, f"{url} offered no report to run"
    return _readable(form.action, browser.post(form.action, data={**form.fields, "action": "submit"},
                                               headers={"X-Bookflow-Workbench": "1"}))


def _report_rows(page, table="report-lines"):
    """Only the report's own table, so the surrounding navigation is never mistaken for a row."""
    start = page.index(f'id="{table}"')
    return page[start:page.index("</table>", start)]


def _ledger_link(page, company_id):
    """The one link a figure on a statement offers: that account's general ledger."""
    found = [href for href, _ in _anchors(page)
             if href.startswith(f"/c/{company_id}/report/general-ledger?")]
    assert found, "no figure on this statement opened a general ledger"
    return found[0]


def _document_links(page, company_id, table="report-lines"):
    """Every report row's own document link, which is the step this file exists for."""
    return [(href, text) for href, text in _anchors(_report_rows(page, table))
            if href.startswith(f"/c/{company_id}/") and "/report/" not in href]


# --------------------------------------------------------------- the presentation itself


def _ledger_result(rows, watermark=7):
    return {"rows": rows, "columns": [], "next_cursor": None,
            "metadata": {"audit_watermark": watermark, "period": {"date_from": "2017-01-01", "date_to": "2017-12-31"}}}


def test_a_ledger_row_links_its_document_and_keeps_the_position_it_was_read_at():
    """The check test for the journey below: without the link there is nothing to follow.

    This is the presentation on its own, so it names what the journey can only demonstrate --
    that the document link exists at all, that a family which can be deleted asks for its
    retained history, and that the audit position handed in from the source statement is the
    one carried on, not the ledger's own.
    """
    rows = [{"kind": "opening", "account_id": "A", "transaction_id": None, "transaction_type": None},
            {"kind": "posting", "account_id": "A", "transaction_id": "T1", "transaction_type": "invoice"},
            {"kind": "posting", "account_id": "A", "transaction_id": "T2", "transaction_type": "journal_entry"}]
    shown = statements.view(_ledger_result(rows), {}, "CO", "report general-ledger", "312")
    opening, invoice, journal = shown["rows"]

    assert opening["document_url"] is None, "a summary row names no document and must not link"
    assert invoice["document_url"] == "/c/CO/invoice/T1?include_deleted=1&source_report_watermark=312"
    # A journal entry now has retained-deletion storage of its own, so it asks for it too --
    # a report sums immutable effects, and a deleted entry's rows must still open behind them.
    assert journal["document_url"] == "/c/CO/journal/T2?include_deleted=1&source_report_watermark=312"

    # A ledger opened directly, with no statement above it, anchors on its own watermark.
    alone = statements.view(_ledger_result(rows), {}, "CO", "report general-ledger")
    assert alone["rows"][1]["document_url"].endswith("source_report_watermark=7")

    # The account drill-down the statement already had is untouched by any of this.
    assert invoice["ledger_url"] == ("/c/CO/report/general-ledger?f%3Aaccount=A"
                                     "&f%3Adate_from=2017-01-01&f%3Adate_to=2017-12-31"
                                     "&source_report_watermark=7")


def _row_a_ledger_publishes(**names):
    """One general-ledger row, built through the report's own model rather than by hand.

    A hand-written dict is how the assertion below came to be green about a row no report
    can produce. ``{"transaction_type": "check"}`` reads perfectly plausibly and is what the
    test used to assert on; a general ledger has never emitted it, because ``check`` is a
    deletion family and never a transaction type. Built here, the model refuses it, so a row
    shape that cannot exist fails the test instead of passing it.
    """
    zero = ledger_reports.money(0, "USD")
    return ledger_reports.GeneralLedgerRow(
        kind="posting", account_id="A", current_account_label="A", current_account_name="A",
        current_account_number=None, display_account_label="A",
        signed_balance=zero, debit=zero, credit=zero, transaction_id="X", **names).model_dump()


def _how_a_report_names(family):
    """What a report row calls a document of this deletion family: its type, and its kind.

    Two vocabularies meet here and they are not the same one. A deletion family is a
    business kind of document; ``transaction_type`` is how the ledger stores it. They
    coincide for the four families that post as themselves and diverge for exactly the two
    that do not: a check and a card charge post *as* journal entries, and only the money-out
    marker says which was entered. Both halves are read from the declarations that own them,
    and a family neither can name fails rather than being quietly skipped -- a family whose
    documents no report row can name is a family whose deletions nobody can reach.
    """
    if family in ledger_schema.TRANSACTION_TYPES:
        return {"transaction_type": family}
    if family in money_out_schema.KINDS:
        return {"transaction_type": money_out_schema.DOCUMENT_TYPE, "money_out_kind": family}
    raise AssertionError(f"no report row can name a {family}, so its deletions cannot be reached")


def test_every_deletable_family_can_be_reached_from_a_report():
    """The families whose documents can be hidden from lists all ask for their history.

    Named from the deletion registry rather than from a list kept here, so a family that
    ships a deletion tomorrow is covered on the day it ships. What the registry hands over
    is a family name, which is not what a report row carries, so it is translated through
    the two declarations that own the two spellings and then put through the report's own
    row model -- because the whole failure this guards against is an assertion that passes
    against a row the product never builds.
    """
    from bookflow.core.deletion_families import TOMBSTONE_TABLE
    for family in TOMBSTONE_TABLE:
        row = _row_a_ledger_publishes(**_how_a_report_names(family))
        link = transaction_detail.document_link("CO", row)
        assert link is not None, f"a ledger row naming a {family} offered no way to open it"
        assert "include_deleted=1" in link, f"{family} could not be opened as retained history"


# ------------------------------------------------------------------------- the journey


@pytest.mark.timeout(600)
def test_a_statement_figure_is_followed_to_the_document_behind_it(hosted):  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id

    # 1. A profit and loss, read at some audit position, and one of its figures.
    statement = _run_report(browser, f"/c/{company}/report/profit-and-loss?"
                            + "&".join(f"{key}={value}" for key, value in PERIOD.items()))
    ledger_url = _ledger_link(statement, company)
    assert "source_report_watermark=" in ledger_url
    watermark = ledger_url.split("source_report_watermark=")[1].split("&")[0]

    # 2. The general ledger behind that figure, which is where the chain used to stop.
    ledger = _run_report(browser, ledger_url)
    documents = _document_links(ledger, company)
    assert documents, "a general ledger row named a document but offered no way to open it"
    assert all(f"source_report_watermark={watermark}" in href for href, _ in documents), \
        "a drill-down link lost the audit position the statement was read at"

    # 3. The documents themselves, opened by following the links a person would click.
    #    Every distinct one on the page, not the first: a ledger names several document
    #    families, and a family whose page does not open is exactly the dead end this row
    #    is about.
    for href in dict.fromkeys(href for href, _ in documents):
        transaction_id = href.split("?")[0].rsplit("/", 1)[-1]
        document = _page(browser, href)
        assert transaction_id in document, f"following {href} did not open what the row named"
        assert f"audit watermark {watermark}" in document, \
            "the document did not say which reading of the books sent the reader here"

    # 4. A document deleted out of ordinary lists is still summed by the report, so the row
    #    that names it still opens it. This is the same chain, one deleted document later.
    deleted = _delete_a_sale(hosted)
    ledger = _run_report(browser, f"/c/{company}/report/general-ledger?f:account={deleted['account']}"
                         + f"&f:date_from={deleted['date']}&f:date_to={deleted['date']}"
                         + f"&source_report_watermark={watermark}")
    hidden = [href for href, _ in _document_links(ledger, company) if deleted["id"] in href]
    assert hidden, "the deleted sale's ledger rows no longer offered the document behind them"
    retained = _page(browser, hidden[0])
    assert deleted["number"] in retained
    assert deleted["reason"] in retained, \
        "the retained document did not present itself as deleted history"

    # 5. The by-job page's Total column joins the same chain, which nothing had followed.
    dimensional = _run_report(browser, f"/c/{company}/report/profit-and-loss-by-job?"
                              + "&".join(f"{key}={value}" for key, value in PERIOD.items()))
    total_url = _ledger_link(dimensional, company)
    from_total = _document_links(_run_report(browser, total_url), company)
    assert from_total, "the by-job Total column reached a ledger with no way on to a document"
    _page(browser, from_total[0][0])


def _delete_a_sale(hosted):  # noqa: F811
    """One sale of this test's own, posted and then deleted, with the grant that requires.

    Every step goes through the running host, which is the one process that may write this
    data root, so the deletion is the product's own rather than a fixture reaching around it.
    """
    company = hosted.company_id
    reason = "The sale a report must still reach after it is deleted"
    why = {"X-Bookflow-Reason": reason}
    # The demo company's own commercial example, dated past anything else it holds.
    posted = hosted.ok("invoice.post", {"customer": "Commercial Example Customer",
                                        "date": "2031-04-07", "number": "DRILL-DELETED-1",
                                        "sales_tax_item": "Commercial Example Tax 8%",
                                        "customer_tax_code": "Tax", "terms": "Net 30",
                                        "lines": [{"item": "Commercial Example Service", "quantity": "1"}]},
                       company=company, headers=why)
    _grant(hosted, "transaction.invoice.delete", why)
    hosted.ok("invoice.delete", {"invoice": posted["id"], "expected_version": posted["version"]},
              company=company, headers=why)
    ledger = hosted.ok("report.general-ledger", {"date_from": "2031-04-07", "date_to": "2031-04-07",
                                                 "limit": 50}, company=company)
    account = next(row["account_id"] for row in ledger["rows"] if row.get("transaction_id") == posted["id"])
    return {"id": posted["id"], "number": "DRILL-DELETED-1", "date": "2031-04-07",
            "account": account, "reason": reason}


# ----------------------------------------------------------------- in a real browser


@pytest.mark.timeout(600)
@pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")
def test_a_person_clicks_a_statement_figure_through_to_the_document(browser_site, tmp_path):  # noqa: F811
    """The same chain in an actual engine, clicked rather than fetched.

    Kept to one walk: a profit-and-loss figure, the ledger under it, and the document
    under that. What it adds over the journey above is that each step is a real anchor a
    person can press, on a page a browser actually laid out.
    """
    site = browser_site
    browser = _Cdp(tmp_path / "drill-chrome")
    transaction = ("[...document.querySelectorAll('#report-lines a')].find(a =>"
                   " a.getAttribute('href').startsWith('/c/')"
                   " && !a.getAttribute('href').includes('/report/'))")
    try:
        browser.viewport(1280, 900)
        browser.navigate(site.base_url + "/login")
        browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
            % (json.dumps(site.login), json.dumps(PASSWORD)))
        browser.wait_for("!!document.querySelector('.nav-group, a.flow-tile')")

        browser.navigate(f"{site.base_url}/c/{site.company_id}/report/profit-and-loss")
        fill(browser, {"date_from": "2000-01-01", "date_to": "2100-12-31", "limit": "50"})
        browser.wait_for("!!document.querySelector('#statement-accounts tbody a')")

        # The figure opens the ledger under it, carrying where it was read from.
        browser.evaluate("document.querySelector('#statement-accounts tbody a').click()")
        browser.wait_for("!!document.querySelector('#report-source-state')")
        watermark = browser.evaluate(
            "new URLSearchParams(location.search).get('source_report_watermark')")
        fill(browser, {"limit": "50"})
        browser.wait_for("!!" + transaction)

        # The ledger line opens the document that posted it -- the step this row adds.
        wording = browser.evaluate(transaction + ".textContent.trim()")
        browser.evaluate(transaction + ".click()")
        browser.wait_for("!!document.querySelector('.record-heading')")
        assert watermark in browser.evaluate(
            "document.querySelector('#report-source-state').textContent")
        printed = browser.evaluate("document.body.innerText")
        assert wording.split()[-1] in printed, f"{wording} opened a page that does not name it"
    finally:
        browser.close()


def _grant(hosted, capability, why):  # noqa: F811
    """One deletion capability, granted through the running host as the product grants it."""
    state = hosted.ok("permission.show")
    hosted.ok("permission.activate", {"expected_generation": state["generation"],
                                      "expected_catalog_sha256": state["catalog_sha256"]}, headers=why)
    member = next(row for row in hosted.ok("membership.list", {"company": hosted.company_id})["items"]
                  if row["scope_type"] == "company" and row["scope_id"] == hosted.company_id)
    hosted.ok("membership.grant", {"user": member["user_id"], "company": hosted.company_id,
                                   "expected_version": member["version"], "grants": [capability]},
              headers=why)


# ------------------------------------- the chequebook, which kept a third spelling of its own


def _missing_checks_result(before, after, watermark=23):
    return {"rows": [{"kind": "gap", "account_id": "A1", "first_missing": 5002,
                      "last_missing": 5002, "missing_count": 1, "legacy_uncertain": False,
                      "before": before, "after": after, "checks": []}],
            "next_cursor": None, "totals": {}, "disclosure": None,
            "metadata": {"audit_watermark": watermark, "period": {"date_to": "2026-12-31"}}}


def test_a_missing_check_row_opens_each_cheque_as_the_document_it_was_entered_as():
    """The check test for the journey below: the cheques around a hole are two documents.

    A check posts *as* a journal entry, so its stored transaction type cannot tell it from
    one; only the money-out marker can. A cheque written from Pay Bills is a bill payment in
    its own right and does not open as a check at all. A hand-built `/check/<id>` is wrong
    about the second and, because it carries no retained-history flag, dead-ends on the
    first as soon as somebody deletes it -- which is exactly when a hole appears in a
    chequebook and somebody comes here to look.
    """
    written = {"transaction_id": "T1", "transaction_type": "journal_entry",
               "money_out_kind": "check", "number": "5001", "status": "posted"}
    from_pay_bills = {"transaction_id": "T2", "transaction_type": "bill_payment",
                      "money_out_kind": None, "number": "5003", "status": "posted"}
    shown = missing_checks.view(_missing_checks_result(written, from_pay_bills), {}, "CO")
    row = shown["rows"][0]

    assert row["before"]["document_url"] == "/c/CO/check/T1?include_deleted=1&source_report_watermark=23"
    assert row["after"]["document_url"] == "/c/CO/bill-payment/T2?source_report_watermark=23"


def test_a_hole_with_nothing_below_it_opens_nothing():
    """The first number in a chequebook has no cheque under it, and must not link to one."""
    shown = missing_checks.view(_missing_checks_result(None, None), {}, "CO")
    assert shown["rows"][0]["before"] is None and shown["rows"][0]["after"] is None


def _occupants(page, company):
    """Every cheque the missing-checks page offered to open, by the number written on it."""
    return {text.split(" ")[0]: href for href, text in _document_links(page, company)}


@pytest.mark.timeout(900)
def test_a_cheque_beside_a_hole_is_followed_to_the_cheque_even_once_it_is_deleted(hosted):  # noqa: F811
    """The checks around a hole are clicked, and each opens the document it actually is.

    Three cheques on one new chequebook, with a hole either side of the middle one: two
    checks and one written from Pay Bills, which is a bill payment and has never opened as a
    check. Then the first is deleted, because a deleted cheque is precisely the case this
    report exists for -- the paper is still gone, so the number is still occupied, and the
    row that names it has to still open it. Every link is fetched rather than asserted to
    exist, for the reason the whole file exists.
    """
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id
    why = {"X-Bookflow-Reason": "Write the cheques this report is read with"}
    write = dict(company=company, headers=why)

    book = hosted.ok("account.create", {"name": "Drill Chequebook", "type": "bank"}, **write)
    spend = {"account": book["id"], "date": "2031-06-01", "amount": "40.00",
             "pay_to": {"name_type": "vendor", "name_id": "Regional Parts"},
             "expenses": [{"account": "Professional Fees", "amount": "40.00"}]}
    first = hosted.ok("check.post", {**spend, "number": "900001"}, **write)
    hosted.ok("check.post", {**spend, "number": "900003"}, **write)
    bill = hosted.ok("bill.post", {"vendor": "Regional Parts", "date": "2031-06-02",
                                   "expenses": [{"account": "Professional Fees", "amount": "15.00"}]},
                     **write)
    hosted.ok("bill.pay", {"date": "2031-06-03", "bills": [{"bill": bill["id"]}],
                           "funding_account": book["id"], "method": "check",
                           "check_number": "900005"}, **write)

    report = f"/c/{company}/report/missing-checks?f:as_of=2100-12-31&f:account={book['id']}"
    occupants = _occupants(_run_report(browser, report), company)
    assert {"900001", "900003", "900005"} <= set(occupants), \
        f"the holes at 900002 and 900004 did not name the cheques around them: {occupants}"

    # A check posts as a journal entry and a bill payment is its own document; the hand-built
    # path this replaces called all three a check, and one of them is not.
    assert "/check/" in occupants["900001"] and "/check/" in occupants["900003"]
    assert "/bill-payment/" in occupants["900005"], \
        f"the cheque written from Pay Bills was opened as {occupants['900005']}"
    for number, href in occupants.items():
        opened = _page(browser, href)
        assert number in opened, f"following {href} did not open the cheque numbered {number}"

    # The same chain once the first cheque is deleted out of the ordinary lists. Its number
    # is still gone from the chequebook, so the report still names it and must still open it.
    reason = "The cheque a chequebook must still reach after it is deleted"
    _grant(hosted, "transaction.check.delete", why)
    hosted.ok("check.delete", {"check": first["id"], "expected_version": first["version"]},
              company=company, headers={"X-Bookflow-Reason": reason})

    occupants = _occupants(_run_report(browser, report), company)
    assert "900001" in occupants, "the deleted cheque stopped occupying the number it took"
    retained = _page(browser, occupants["900001"])
    assert "900001" in retained, "following the deleted cheque did not open it"
    assert reason in retained, "the retained cheque did not present itself as deleted history"

    # And the same deleted cheque from the general ledger, which is the other page a person
    # lands on holding a chequebook. A cheque posts as a journal entry, so a ledger row that
    # knew only its transaction type opened `/journal/<id>`, which cannot be asked for
    # retained history and refuses a deleted cheque outright.
    ledger = _run_report(browser, f"/c/{company}/report/general-ledger?f:account={book['id']}"
                         + "&f:date_from=2031-06-01&f:date_to=2031-06-03")
    from_ledger = [href for href, _ in _document_links(ledger, company) if first["id"] in href]
    assert from_ledger, "the deleted cheque's ledger rows no longer offered the document behind them"
    assert all("/check/" in href for href in from_ledger), \
        f"the general ledger opened the deleted cheque as {from_ledger[0]}"
    from_ledger_page = _page(browser, from_ledger[0])
    assert "900001" in from_ledger_page
    assert reason in from_ledger_page, \
        "the cheque opened from the general ledger did not present itself as deleted history"

    # Transaction detail reads the same lines through a query of its own, so it is walked
    # rather than assumed to follow: two queries carrying the same fact is two places it can
    # be missing from, and the shared link cannot tell which one forgot.
    detail = _run_report(browser, f"/c/{company}/report/transaction-detail"
                         + "?f:date_from=2031-06-01&f:date_to=2031-06-03")
    from_detail = [href for href, _ in _document_links(detail, company) if first["id"] in href]
    assert from_detail, "the deleted cheque's transaction-detail rows offered no document behind them"
    assert all("/check/" in href for href in from_detail), \
        f"transaction detail opened the deleted cheque as {from_detail[0]}"
    assert reason in _page(browser, from_detail[0]), \
        "the cheque opened from transaction detail did not present itself as deleted history"


@pytest.mark.timeout(900)
def test_a_customer_statement_row_hands_on_what_every_other_report_row_hands_on(hosted):  # noqa: F811
    """A statement row is followed to its document, carrying the two things a link carries.

    A statement had been building its own path once it knew the noun, so it lost both: the
    audit position the statement was read at, and the request for retained history that a
    report summing immutable effects has to make. Neither can be seen by looking at the page;
    the link is taken out of it and fetched, and the document has to say which reading of the
    books sent the reader here.
    """
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id

    credit = hosted.ok("credit-memo.post", {
        "customer": "Commercial Example Customer", "date": "2031-05-09",
        "number": "DRILL-CREDIT-1", "sales_tax_item": "Commercial Example Tax 8%",
        "customer_tax_code": "Tax",
        "lines": [{"item": "Commercial Example Service", "quantity": "1"}]},
        company=company, headers={"X-Bookflow-Reason": "Credit a service that was not delivered"})

    statement = _run_report(browser, f"/c/{company}/report/statement?"
                            + "&".join(f"{key}={value}" for key, value in PERIOD.items()))
    links = _document_links(statement, company, "statement-lines")
    assert links, "no row on this statement offered the document behind it"

    found = [href for href, _ in links if credit["id"] in href]
    assert found, f"the credit memo row offered no way to open it; the row's links were {links}"
    assert "source_report_watermark=" in found[0], \
        "a statement row lost the audit position the statement was read at"
    watermark = found[0].split("source_report_watermark=")[1].split("&")[0]

    opened = _page(browser, found[0])
    assert credit["id"] in opened, f"following {found[0]} did not open what the row named"
    assert "DRILL-CREDIT-1" in opened, "the page the credit memo row opened does not name it"
    assert f"audit watermark {watermark}" in opened, \
        "the document did not say which reading of the books sent the reader here"

    # A family that can be deleted asks for its retained history, the way every other
    # report's rows do, and every row still opens what it names.
    deletable = [href for href, _ in links if "/invoice/" in href or "/sales-receipt/" in href]
    assert deletable, "this statement printed no document of a family that can be deleted"
    assert all("include_deleted=1" in href for href in deletable), \
        f"a statement row could not ask for its retained history: {deletable}"
    for href in dict.fromkeys(href for href, _ in links):
        document = _page(browser, href)
        assert href.split("?")[0].rsplit("/", 1)[-1] in document, \
            f"following {href} did not open what the row named"
