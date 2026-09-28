"""The money lists and the wide receivable and payable reports, read at phone and desktop width.

On a phone nothing scrolls sideways, not the page and not a box inside it: each deposit,
payment, bill payment, customer, vendor, invoice or bill is a card whose figure is on screen.
On a desktop the same page is a ledger table with its figures in their columns.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome unavailable")

PAGES = ("deposit", "payment", "bill-payment", "report/ar-aging", "report/ap-aging",
         "report/open-invoices", "report/unpaid-bills")

# What a reader sees: sideways scroll of the page and of any box on it, the cards and ledger
# rows actually shown, and how many of them show a figure. The raw result a report keeps
# behind a disclosure is exact JSON and is left to scroll.
_READ = """(() => {
  const shown = e => e.checkVisibility();
  const sideways = [...document.querySelectorAll('main *')].filter(e => shown(e) && !e.closest('.report-structured')
      && ['auto', 'scroll'].includes(getComputedStyle(e).overflowX) && e.scrollWidth > e.clientWidth + 1);
  const cards = [...document.querySelectorAll('.list-cards .list-card:not(.list-card-empty)')].filter(shown);
  const rows = [...document.querySelectorAll('.list-desktop tbody tr')].filter(shown);
  const digits = e => /\\d/.test(e ? e.textContent : '');
  return {page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          boxes: sideways.map(e => e.tagName + '.' + e.className),
          cards: cards.length, card_figures: cards.filter(c => digits(c.querySelector('.list-card-amount'))).length,
          off_screen: cards.filter(c => c.getBoundingClientRect().right > innerWidth + 0.5).length,
          rows: rows.length, row_figures: rows.filter(r => [...r.querySelectorAll('td.num')].some(digits)).length};
})()"""


@pytest.mark.timeout(600)
def test_money_lists_and_wide_reports_are_cards_on_a_phone_and_ledgers_on_a_desktop(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    b = _Cdp(tmp_path / "chrome")
    try:
        b.viewport(1280, 900)
        b.navigate(site.base_url + "/login")
        b.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
                   % (json.dumps(site.login), json.dumps(PASSWORD)))
        b.wait_for("!location.pathname.startsWith('/login')", timeout=20)
        # One open bill, so the payables reports have a vendor to show whatever the demo holds today.
        run = lambda name, body: _command(b, site, name, body)
        vendor = run("vendor.create", {"name": "Ledger Card Supply"})["id"]
        expense = run("account.create", {"name": "Ledger card expense", "type": "expense"})["id"]
        run("bill.post", {"number": "LC-1", "date": "2026-01-05", "due_date": "2026-02-04", "vendor": vendor,
                          "expenses": [{"account": expense, "amount": "84.20"}]})
        for width, height in ((390, 844), (1280, 900)):
            b.viewport(width, height)
            for page in PAGES:
                b.navigate(f"{site.base_url}/c/{site.company_id}/{page}")
                b.wait_for("!!document.querySelector('.list-cards')", timeout=30)
                seen = b.evaluate(_READ)
                assert seen["page"] <= 0, (width, page, seen)
                if width == 390:
                    assert not seen["boxes"], (page, seen)
                    assert seen["cards"] > 0 and seen["card_figures"] == seen["cards"], (page, seen)
                    assert seen["off_screen"] == 0 and seen["rows"] == 0, (page, seen)
                else:
                    assert seen["rows"] > 0 and seen["row_figures"] == seen["rows"], (page, seen)
                    assert seen["cards"] == 0, (page, seen)
    finally:
        b.close()


@pytest.mark.timeout(300)
def test_a_payment_list_filter_waits_behind_the_phone_sheet_and_still_submits(browser_site, tmp_path):  # noqa: F811
    site = browser_site
    b = _Cdp(tmp_path / "chrome")
    try:
        b.viewport(390, 844)
        b.navigate(site.base_url + "/login")
        b.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
                   % (json.dumps(site.login), json.dumps(PASSWORD)))
        b.wait_for("!location.pathname.startsWith('/login')", timeout=20)
        b.navigate(f"{site.base_url}/c/{site.company_id}/payment")
        b.wait_for("!!document.querySelector('[data-sheet-open]')")
        status = "document.querySelector('#list-sheet [name=status]')"
        assert not b.evaluate(f"{status}.checkVisibility()")
        b.evaluate("document.querySelector('[data-sheet-open]').click()")
        b.wait_for(f"{status}.checkVisibility()")
        b.evaluate(f"""(() => {{{status}.value = 'voided';
            document.querySelector('#list-sheet .list-apply').click();}})()""")
        b.wait_for("location.search.includes('status=voided') && document.readyState === 'complete'"
                   " && !!document.querySelector('.list-card')", timeout=30)
        cards = b.evaluate("[...document.querySelectorAll('.list-card')].map(c => c.innerText)")
        assert cards and all("Voided" in card for card in cards), cards
        assert b.evaluate("document.querySelector('.list-bar-button').textContent.includes('(1)')")
    finally:
        b.close()
