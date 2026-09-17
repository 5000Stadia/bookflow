"""Company defaults and independent financial views of real posted business."""
import pytest

from tests.test_summary_reports import books, COMPANY, FROM, TO
from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted


def read(client, verb, **extra):
    body = {"date_to": TO, "limit": 200, **extra}
    if verb not in {"balance-sheet", "trial-balance"}:
        body["date_from"] = FROM
    return client.run("report " + verb, body, company=COMPANY)


def test_company_default_changes_recognition_and_override_preserves_books(books):
    client, _ = books
    accrual = read(client, "profit-and-loss")
    assert accrual["totals"]["income"]["minor_units"] == 62500
    client.run("company update", {"report_basis": "cash"}, company=COMPANY,
               reason="Choose cash financial reporting")
    import sqlite3
    from pathlib import Path
    path = Path(client.company.show(company=COMPANY)["path"]) / "company.db"
    with sqlite3.connect(path) as connection:
        before = "\n".join(connection.iterdump())
    cash = read(client, "profit-and-loss")
    assert cash["metadata"]["basis"] == "cash"
    # Paid sales receipt $100 + $100 direct income journals; invoices remain unpaid.
    assert cash["totals"]["income"]["minor_units"] == 20000
    # Cheque $100, card charge $30 and direct interest journal $15.
    assert cash["totals"]["net_income"]["minor_units"] == 5500
    override = read(client, "profit-and-loss", basis="accrual")
    assert override["metadata"]["basis"] == "accrual"
    assert override["totals"] == accrual["totals"]
    for verb in ("sales-by-customer", "sales-by-item", "sales-by-rep"):
        result = read(client, verb)
        assert result["metadata"]["basis"] == "cash"
        assert result["totals"]["income"]["minor_units"] == 20000
        if verb == "sales-by-item":
            assert all(row["average_price"] is None for row in result["rows"])
    for verb in ("profit-and-loss-by-job", "profit-and-loss-by-class", "income-tax-summary", "cash-flows"):
        result = read(client, verb)
        assert result["metadata"]["basis"] == "cash"
        assert result["totals"]["net_income"]["minor_units"] == 5500
    for verb in ("balance-sheet", "cash-flows"):
        assert read(client, verb)["totals"]["difference"]["minor_units"] == 0
    assert read(client, "expenses-by-vendor")["metadata"]["basis"] == "cash"
    assert read(client, "trial-balance")["metadata"]["basis"] == "accrual"
    with sqlite3.connect(path) as connection:
        assert before == "\n".join(connection.iterdump())
    with pytest.raises(BookflowError) as exc:
        read(client, "trial-balance", basis="cash")
    assert exc.value.code == "E_VALIDATION"


def test_basis_default_change_invalidates_existing_report_page(books):
    client, _ = books
    first = read(client, "sales-by-customer", limit=1)
    assert first["next_cursor"]
    client.run("company update", {"report_basis": "cash"}, company=COMPANY,
               reason="Change reporting default")
    with pytest.raises(BookflowError) as exc:
        read(client, "sales-by-customer", limit=1, cursor=first["next_cursor"])
    assert exc.value.code == "E_QUERY_STALE"


def test_browser_company_default_override_export_and_print(hosted):
    from tests.test_report_export import _browser, _run_report, _shown, _export_link
    from bookflow.adapters.workbench.report_print import print_url
    company = hosted.company_id
    other = hosted.ok('company.new', {'legal_name': 'Independent cash preference', 'home_currency': 'USD'}, headers={'X-Bookflow-Reason': 'Test company isolation'})['company_id']
    previous = hosted.ok('company.show', company=other)['info']['report_basis']
    hosted.ok('company.update', {'report_basis': 'cash'}, company=company, headers={'X-Bookflow-Reason': 'Choose report basis'})
    assert hosted.ok('company.show', company=other)['info']['report_basis'] == previous
    browser = _browser(hosted)
    fields = {'f:date_from': '2000-01-01', 'f:date_to': '2099-12-31'}
    page = _run_report(browser, company, 'profit-and-loss', fields)
    assert _shown(page)['metadata']['basis'] == 'cash'
    assert 'Company default' in page.text and 'Cash-basis view' in page.text
    saved = browser.get(_export_link(page))
    assert saved.status_code == 200
    assert 'Basis,Cash' in saved.text
    printed = browser.get(print_url(company, 'profit-and-loss', {'date_from': '2000-01-01', 'date_to': '2099-12-31'}))
    assert printed.status_code == 200 and 'Cash basis' in printed.text
    override = _run_report(browser, company, 'profit-and-loss', {**fields, 'f:basis': 'accrual'})
    assert _shown(override)['metadata']['basis'] == 'accrual'
