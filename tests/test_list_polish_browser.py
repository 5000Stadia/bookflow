"""The list's own paging controls, in a real browser at a phone width.

What is checked here: the newest invoice is the first row without scrolling, the paging
controls never widen the page, and each one is a real link a thumb can hit.
"""
import pytest

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


def _controls(b):
    return b.evaluate('''[...document.querySelectorAll(".pagination a")].map(e => ({
        text: e.textContent.trim(), href: e.getAttribute("href"),
        height: e.getBoundingClientRect().height}))''')


def test_the_paging_controls_walk_both_ways_and_never_widen_a_phone(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    b.viewport(PHONE, 844)
    books = _fixture(run, 'Phone list')
    written = _posted(run, books)
    # The books already hold other invoices; this customer's three are the ones under test.
    base = (f'{env.site.base_url}/c/{env.site.company_id}/invoice'
            f'?customer={books["customer"]}')

    b.navigate(f'{base}&limit=1')
    b.wait_for('!!document.querySelector(".pagination")')
    _contained(b, PHONE)
    # The list opens on the invoice written last, so it is the row a thumb reaches first.
    assert b.evaluate('document.querySelector("table a").getAttribute("href")').endswith(written[-1])

    first = _controls(b)
    assert [row['text'] for row in first] == ['Next page →'], first
    assert all(row['href'] and row['height'] >= 30 for row in first), first

    b.navigate(env.site.base_url + first[0]['href'])
    b.wait_for('!!document.querySelector(".pagination")')
    _contained(b, PHONE)
    second = _controls(b)
    assert [row['text'] for row in second] == ['← Previous page', 'Next page →'], second
    assert all(row['href'] and row['height'] >= 30 for row in second), second

    b.navigate(env.site.base_url + second[0]['href'])
    b.wait_for('!!document.querySelector("table a")')
    assert b.evaluate('document.querySelector("table a").getAttribute("href")').endswith(written[-1])
    assert 'customer=' in second[0]['href'], second
