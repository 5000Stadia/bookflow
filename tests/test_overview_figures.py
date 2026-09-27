"""The Overview's figures, needs-attention lists and recent activity: each shows what a read
command returned, links to the report that explains it, and quietly drops out for a reader who
cannot run that command."""
import re
from datetime import date, timedelta

from fastapi.testclient import TestClient

from bookflow.adapters.workbench.date_defaults import company_today
from bookflow.adapters.workbench.display import money
from bookflow.core import registry
from bookflow.core.errors import BookflowError

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def _browser(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _figures(html):
    """{key: (href, value text)} for each figure on the page."""
    found = {}
    for href, key, body in re.findall(r'<a href="([^"]+)" data-figure="(\w+)">(.*?)</a>', html, re.S):
        value = re.search(r'<strong class="figure-value">(.*?)</strong>', body).group(1)
        found[key] = (href.replace("&amp;", "&"), value)
    return found


def _seed(hosted, browser):
    """An invoice 20 days overdue and a bill due in three days, relative to the company's today."""
    def run(name, raw):
        answered = browser.post(f"/companies/{hosted.company_id}/commands/{name.replace(' ', '.')}", json=raw,
                                headers={"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"})
        assert answered.status_code == 200, (name, answered.text[:400])
        return answered.json()
    today = date.fromisoformat(company_today(run("company show", {})))
    run("customer create", {"name": "Harbour Mills"})
    invoice = run("invoice post", {"customer": "Harbour Mills", "date": (today - timedelta(days=50)).isoformat(),
                                   "due_date": (today - timedelta(days=20)).isoformat(),
                                   "lines": [{"item": "Payment Example Labor", "quantity": "1", "unit_price": "1234.50"}]})
    bill = run("bill post", {"vendor": "Regional Parts", "date": (today - timedelta(days=5)).isoformat(),
                             "due_date": (today + timedelta(days=3)).isoformat(),
                             "expenses": [{"account": "Office Supplies", "amount": "42.50"}]})
    return run, today.isoformat(), invoice, bill


def test_the_overview_figures_are_the_reports_own_totals_and_link_to_them(hosted):
    browser = _browser(hosted)
    run, today, invoice, bill = _seed(hosted, browser)
    page = browser.get(f"/c/{hosted.company_id}/")
    assert page.status_code == 200
    figures = _figures(page.text)
    assert list(figures) == ["cash", "receivable", "overdue", "payable", "income"], list(figures)

    # Each figure is one report's own total, never a sum of its rows.
    flows = run("report cash-flows", {"date_from": today[:8] + "01", "date_to": today})
    aging = run("report ar-aging", {"as_of": today})
    overdue = run("report open-invoices", {"as_of": today, "past_due_only": True})
    bills = run("report unpaid-bills", {"as_of": today})
    month = run("report profit-and-loss", {"date_from": today[:8] + "01", "date_to": today})
    assert figures["cash"][1] == money(flows["totals"]["closing_cash"])
    assert figures["receivable"][1] == money(aging["totals"]["total"])
    assert figures["overdue"][1] == money(overdue["totals"]["balance"])
    assert figures["payable"][1] == money(bills["totals"]["balance"])
    assert figures["income"][1] == money(month["totals"]["income"])
    assert figures["cash"][0] == f"/c/{hosted.company_id}/report/cash-flows?f:date_from={today[:8]}01&f:date_to={today}"
    assert figures["payable"][0] == f"/c/{hosted.company_id}/report/unpaid-bills?f:as_of={today}"

    # Each figure opens the report that explains it, for the same date.
    assert figures["overdue"][0] == f"/c/{hosted.company_id}/report/open-invoices?f:as_of={today}&f:past_due_only=true"
    for href, _ in figures.values():
        landed = browser.get(href, follow_redirects=False)
        assert landed.status_code == 200 and "<h1" in landed.text, href

    # Needs attention names the overdue invoice and the bill due soon, each linking to its document.
    assert "Harbour Mills" in page.text and "20 days overdue" in page.text and "$1,234.50" in page.text
    assert f'href="/c/{hosted.company_id}/invoice/{invoice["id"]}"' in page.text
    assert f'href="/c/{hosted.company_id}/bill/{bill["id"]}"' in page.text
    # Recent activity leads with the latest recorded change.
    latest = run("audit list", {"limit": 1})["items"][0]
    activity = page.text.split('id="activity-title"', 1)[1]
    assert f'href="/c/{hosted.company_id}/audit/{latest["id"]}"' in activity
    assert "just now" in activity or "min ago" in activity


def test_a_reader_who_cannot_run_a_command_just_does_not_see_its_part(hosted, monkeypatch):
    browser = _browser(hosted)

    def refused(*args, **kwargs):
        raise BookflowError("E_PERMISSION", message="Not allowed.")

    monkeypatch.setattr(registry.get("report ar-aging"), "plan", refused)
    monkeypatch.setattr(registry.get("audit list"), "plan", refused)
    page = browser.get(f"/c/{hosted.company_id}/")
    assert page.status_code == 200
    figures = _figures(page.text)
    assert "receivable" not in figures and {"cash", "overdue", "payable", "income"} <= set(figures)
    assert "Recent activity" not in page.text and 'class="error"' not in page.text
    assert "Needs attention" in page.text
