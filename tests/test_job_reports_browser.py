"""The four job-and-class reports driven in a real browser, at desktop and phone widths.

The journey a bookkeeper takes: the home window, the report picker, and each of the
four pages, with every figure asserted read off the rendered page rather than off
the command's JSON. A wide column-per-job table gets its own checks, because the one
thing that can go wrong on a page nobody can fit on a screen is that the reader
cannot get at the numbers: the page itself must never scroll sideways, the table
must, and on a phone every cell must say which column it belongs to.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command
from tests.test_collection_reports import CONTACTS
from tests.test_dimensional_statements import FROM, TO
from tests.test_dimensional_statements import build as build_dimensions
from tests.test_receivable_reports import AS_OF as RECEIVABLE_AS_OF
from tests.test_receivable_reports import build as build_receivables
from tests.test_unbilled_costs import AS_OF as WORK_AS_OF
from tests.test_unbilled_costs import build as build_work

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")

# A report cell also carries the phone-only column label and any state marker, so
# a reader of the rendered table strips the label and reads what is left as one line.
_CELLS = """[...document.querySelectorAll('%s tbody tr')].map(row => [...row.cells].map(cell => {
    const copy = cell.cloneNode(true);
    copy.querySelectorAll('.document-cell-label').forEach(node => node.remove());
    return copy.textContent.trim().replace(/\\s+/g, ' ');}))"""


def _fill(browser, fields):
    browser.evaluate("""(() => { const form=document.querySelector('form[data-generated-form]');
        for(const [key,value] of Object.entries(%s)) form.elements.namedItem('f:'+key).value=value;
        form.querySelector('button[value=submit]').click(); })()""" % json.dumps(fields))


def _no_sideways_scroll(browser):
    assert browser.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), \
        browser.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")


def _open_from_the_reports_tile(browser, site, verb, heading):
    browser.navigate(f"{site.base_url}/c/{site.company_id}/")
    browser.wait_for("!!document.querySelector('a[href$=\"/_group/reports\"]')")
    browser.evaluate("document.querySelector('a[href$=\"/_group/reports\"]').click()")
    browser.wait_for(f"!!document.querySelector('a[href$=\"/report/{verb}\"]')")
    browser.evaluate(f"document.querySelector('a[href$=\"/report/{verb}\"]').click()")
    browser.wait_for("!!document.querySelector('form[data-generated-form]')")
    assert browser.evaluate("document.querySelector('h1').textContent") == heading
    # The filter form restarts the report; it never carries a continuation.
    assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")


@pytest.fixture
def site_with_a_month(browser_site, tmp_path):  # noqa: F811
    """One browser, signed in, with the month every report below reads."""
    browser = _Cdp(tmp_path / "job-reports-chrome")
    try:
        browser.navigate(browser_site.base_url + "/login")
        browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
            % (json.dumps(browser_site.login), json.dumps(PASSWORD)))
        browser.wait_for("!!document.querySelector('.nav-group')")
        from bookflow.core import registry
        registry.load_all()

        def run(command, body, *, reason=None):
            headers = {"X-Bookflow-Reason": reason or "Job report browser fixture"} \
                if registry.get(command).is_write else {}
            return _command(browser, browser_site, command.replace(" ", "."), body, **headers)

        made = dict(dimensions=build_dimensions(run), work=build_work(run),
                    receivables=build_receivables(run))
        alpha = run("customer show", {"customer": made["receivables"]["alpha"]})
        run("customer update", {"customer": made["receivables"]["alpha"],
                                "expected_version": alpha["version"], "contacts": CONTACTS})
        yield browser, browser_site, made
    finally:
        browser.close()


@pytest.mark.timeout(420)
@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_a_bookkeeper_reaches_all_four_reports_and_reads_the_money(site_with_a_month, width, height):
    browser, site, made = site_with_a_month
    browser.viewport(width, height)

    # ---------------------------------------------------------- profit and loss by job
    _open_from_the_reports_tile(browser, site, "profit-and-loss-by-job", "Profit and loss by job")
    _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200", "columns": "50"})
    browser.wait_for("!!document.querySelector('#dimensional-lines')")
    headings = browser.evaluate(
        "[...document.querySelectorAll('#dimensional-lines thead th')].map(th => th.textContent.trim())")
    assert headings == ["Account", "Dimension Alpha", "Dimension Alpha:Phase one",
                        "Dimension Beta", "Unassigned", "Total"]
    assert browser.evaluate(_CELLS % "#dimensional-lines") == [
        ["Dimension income Income", "300.00", "200.00", "100.00", "0.00", "600.00"],
        ["Dimension swing Income", "25.00", "0.00", "-25.00", "0.00", "0.00"],
        ["Dimension expense Expense", "0.00", "50.00", "0.00", "50.00", "100.00"],
    ]
    # The figure the reader came for, off the page: what each job made.
    assert browser.evaluate("""[...document.querySelectorAll('#dimensional-column-summary > div')]
        .map(row => [row.querySelector('dt').textContent.trim(), row.querySelector('dd').textContent.trim()])""") == [
        ["Dimension Alpha", "325.00"], ["Dimension Alpha:Phase one", "150.00"],
        ["Dimension Beta", "75.00"], ["Unassigned", "-50.00"], ["Total — net income", "500.00"]]
    assert browser.evaluate(
        "document.querySelector('#dimensional-totals [data-total=net_income] dd').textContent") == "500.00 USD"
    assert browser.evaluate(
        "document.querySelector('[data-column-net-income=total]').textContent").endswith("500.00")
    # Wide on purpose: the table scrolls inside its own wrapper, the page never does.
    _no_sideways_scroll(browser)
    wrapper = browser.evaluate("""(() => {const w=document.querySelector('.report-lines-wrap');
        return [w.scrollWidth > w.clientWidth, getComputedStyle(w).overflowX];})()""")
    if width == 390:
        # On a phone nothing is pinned and nothing is off the side: every cell says
        # which column it is, so no job can be scrolled past and missed.
        assert wrapper[1] == "visible"
        labels = browser.evaluate("""[...document.querySelectorAll('#dimensional-lines tbody tr')][0]
            .querySelectorAll('.document-cell-label').length""")
        assert labels == len(headings)
        assert browser.evaluate(
            "getComputedStyle(document.querySelector('#dimensional-lines tbody tr')).display") == "grid"
    else:
        assert wrapper[1] == "auto"
        # The account a row is about and what that row came to altogether stay
        # readable at any scroll position; they are the two that must not vanish.
        for edge in ("dimensional-account", "dimensional-total"):
            assert browser.evaluate(
                f"getComputedStyle(document.querySelector('#dimensional-lines tbody .{edge}')).position") == "sticky"

    # ------------------------------------------------------ profit and loss by class
    _open_from_the_reports_tile(browser, site, "profit-and-loss-by-class", "Profit and loss by class")
    _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200", "columns": "50"})
    browser.wait_for("!!document.querySelector('#dimensional-lines')")
    assert browser.evaluate(
        "[...document.querySelectorAll('#dimensional-lines thead th')].map(th => th.textContent.trim())") == [
        "Account", "Dimension North", "Dimension South", "Unclassified", "Total"]
    assert browser.evaluate(_CELLS % "#dimensional-lines") == [
        ["Dimension income Income", "300.00", "200.00", "100.00", "600.00"],
        ["Dimension swing Income", "0.00", "0.00", "0.00", "0.00"],
        ["Dimension expense Expense", "50.00", "40.00", "10.00", "100.00"],
    ]
    _no_sideways_scroll(browser)

    # ------------------------------------------------------------------ unbilled costs
    _open_from_the_reports_tile(browser, site, "unbilled-costs", "Unbilled costs by job")
    _fill(browser, {"as_of": WORK_AS_OF, "customer": made["work"]["job"], "limit": "200"})
    browser.wait_for("!!document.querySelector('#receivables-unbilled')")
    assert browser.evaluate(_CELLS % "#receivables-unbilled") == [
        ["Unbilled Customer:Site A", "UB-EST-OPEN", "2027-04-02",
         "Unbilled service Unbilled income", "Two days on site", "Not billed", "2", "0.00", "200.00"],
        ["Unbilled Customer:Site A", "UB-EST-PARTIAL", "2027-04-02",
         "Unbilled service Unbilled income", "Phase one of two", "Partly billed", "4", "400.00", "400.00"],
        ["Unbilled Customer:Site A subtotal", "", "", "", "", "", "", "400.00", "600.00"],
    ]
    assert browser.evaluate(_CELLS % "#receivables-totals") == [["400.00", "600.00"]]
    _no_sideways_scroll(browser)
    # The page a reader goes to next is the one that turns this into an invoice.
    browser.evaluate("""[...document.querySelectorAll('#receivables-unbilled a')]
        .find(a => a.textContent.trim() === 'UB-EST-OPEN').click()""")
    browser.wait_for(f'location.pathname.startsWith("/c/{site.company_id}/estimate/")'
                     ' && document.readyState !== "loading"')
    assert "Two days on site" in browser.evaluate("document.body.innerText")

    # -------------------------------------------------------------------- collections
    _open_from_the_reports_tile(browser, site, "collections", "Collections")
    _fill(browser, {"as_of": RECEIVABLE_AS_OF, "limit": "200"})
    browser.wait_for("!!document.querySelector('#receivables-collections')")
    assert browser.evaluate("""[...document.querySelectorAll('#receivables-collections tbody tr')]
        .map(row => [row.dataset.kind, row.cells[0].textContent.trim().replace(/\\s+/g, ' ')])""") == [
        ["customer", "Aging Alpha"], ["invoice", "AGE-91 Over 90"], ["invoice", "AGE-90 61-90"],
        ["invoice", "AGE-30 1-30"], ["customer", "Aging Alpha:North Job"], ["invoice", "AGE-31 31-60"],
        ["customer", "Aging Beta"], ["invoice", "AGE-PARTLY 61-90"],
        ["customer", "Aging Gamma"], ["invoice", "AGE-OLD Over 90"]]
    assert browser.evaluate(_CELLS % "#receivables-totals") == [
        ["900.00", "150.00", "300.00", "900.00", "1100.00", "3350.00", "2450.00"]]
    # The contact details, as links a person can actually press.
    alpha = browser.evaluate("""(() => {const row=document.querySelector(
        '#receivables-collections tbody tr[data-kind=customer]');
        return [...row.cells[1].querySelectorAll('a')].map(a => [a.getAttribute('href'), a.textContent.trim()]);})()""")
    assert alpha == [["tel:555-0200", "555-0200"], ["mailto:robin@alpha.example.test", "robin@alpha.example.test"],
                     ["tel:555-0201", "555-0201"], ["mailto:site@alpha.example.test", "site@alpha.example.test"],
                     ["tel:555-0202", "555-0202"]]
    # A job that records no contacts of its own is chased through its parent's.
    assert "from the parent customer" in browser.evaluate("""[...document.querySelectorAll(
        '#receivables-collections tbody tr[data-kind=customer]')][1].innerText""")
    _no_sideways_scroll(browser)


@pytest.mark.timeout(420)
def test_a_wide_report_pages_its_rows_and_restarts_when_the_books_move(site_with_a_month):
    browser, site, _made = site_with_a_month
    browser.viewport(1280, 900)

    def run(command, body, *, reason=None):
        from bookflow.core import registry
        registry.load_all()
        headers = {"X-Bookflow-Reason": reason or "Job report browser fixture"} \
            if registry.get(command).is_write else {}
        return _command(browser, site, command.replace(" ", "."), body, **headers)

    browser.navigate(f"{site.base_url}/c/{site.company_id}/report/profit-and-loss-by-job")
    browser.wait_for("!!document.querySelector('[name=\"f:date_from\"]')")
    _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "1", "columns": "50"})
    browser.wait_for("!!document.querySelector('#dimensional-lines')")
    assert browser.evaluate(_CELLS % "#dimensional-lines") == [
        ["Dimension income Income", "300.00", "200.00", "100.00", "0.00", "600.00"]]
    # Whole-statement totals on every page, not this page's own arithmetic.
    assert browser.evaluate(
        "document.querySelector('#dimensional-totals [data-total=net_income] dd').textContent") == "500.00 USD"
    assert browser.evaluate("!!document.querySelector('#statement-next-page')")
    browser.evaluate("document.querySelector('#statement-next-page button').click()")
    browser.wait_for("""document.querySelector('#dimensional-lines')
        ?.innerText.includes('Dimension swing')""")
    assert browser.evaluate(_CELLS % "#dimensional-lines") == [
        ["Dimension swing Income", "25.00", "0.00", "-25.00", "0.00", "0.00"]]
    assert browser.evaluate(
        "document.querySelector('#dimensional-totals [data-total=net_income] dd').textContent") == "500.00 USD"

    # A company write while the reader is mid-report stales the continuation, and
    # the visible filter form still restarts a fresh report from here.
    run("customer create", {"name": "Dimensional browser witness"})
    browser.evaluate("document.querySelector('#statement-next-page button').click()")
    browser.wait_for("!!document.querySelector('#report-restart')")
    assert "E_QUERY_STALE" in browser.evaluate("document.querySelector('.error').textContent")
    assert not browser.evaluate("!!document.querySelector('#dimensional-lines')")
    assert browser.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == FROM
    assert browser.evaluate("!document.querySelector('form[data-generated-form] [name=\"f:cursor\"]')")
    _fill(browser, {"date_from": FROM, "date_to": TO, "limit": "200", "columns": "1"})
    browser.wait_for("!!document.querySelector('#dimensional-lines')")
    # A narrow column request folds the rest into one column that says how many.
    assert browser.evaluate(
        "[...document.querySelectorAll('#dimensional-lines thead th')].map(th => th.textContent.trim())") == [
        "Account", "Dimension Alpha", "Other (2)", "Unassigned", "Total"]
    assert "added together in the Other column" in browser.evaluate(
        "document.querySelector('#report-page-count').textContent")
    assert browser.evaluate(_CELLS % "#dimensional-lines") == [
        ["Dimension income Income", "300.00", "300.00", "0.00", "600.00"],
        ["Dimension swing Income", "25.00", "-25.00", "0.00", "0.00"],
        ["Dimension expense Expense", "0.00", "50.00", "50.00", "100.00"],
    ]
