"""The way back to earlier documents, in a real browser at a phone width.

What is checked here: the toolbar and its lazily fetched recent list never widen the
page, the arrows are real links a phone can hit, and the recent list is not on the form
until after the form has rendered.
"""
import pytest

from bookflow.adapters.workbench import document_nav as Nav
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_sales_document_browser import _fixture
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

PHONE = 390


def _posted(run, books, count=3):
    return [run('invoice.post', dict(date=f'2026-05-0{index}', customer=books['customer'],
                ar_account=books['receivable'],
                lines=[dict(item=books['item'], quantity=str(index))]))['id']
            for index in range(1, count + 1)]


def test_the_toolbar_and_its_recent_list_never_widen_the_page_at_phone_width(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    b.viewport(PHONE, 844)
    books = _fixture(run, 'Phone nav')
    written = _posted(run, books)
    base = f'{env.site.base_url}/c/{env.site.company_id}/invoice'

    b.navigate(f'{base}/{written[1]}')
    b.wait_for('!!document.querySelector(".document-nav")')
    _contained(b, PHONE)
    steps = b.evaluate('''[...document.querySelectorAll(".document-step")]
        .map(e => ({tag: e.tagName, href: e.getAttribute("href"),
                    box: e.getBoundingClientRect().height}))''')
    assert len(steps) == 2, steps
    # Both arrows are real links here, and each is big enough to hit with a thumb.
    assert all(step['tag'] == 'A' and step['href'] for step in steps), steps
    assert all(step['box'] >= 30 for step in steps), steps

    b.navigate(f'{base}/post')
    b.wait_for('!!document.querySelector(".document-nav")')
    # The form is on screen before the recent list is: it is fetched, not rendered.
    assert b.evaluate('!!document.querySelector("[data-sales-form]")')
    b.wait_for('!!document.querySelector(".document-recent-list a")')
    shown = b.evaluate('[...document.querySelectorAll(".document-recent-list a")]'
                       '.map(a => a.getAttribute("href"))')
    assert 1 <= len(shown) <= Nav.RECENT and len(set(shown)) == len(shown), shown
    assert all(href.startswith(f'/c/{env.site.company_id}/invoice/') for href in shown), shown
    _contained(b, PHONE)
