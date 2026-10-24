"""R104 increment 1: the Sites and transfers page in real headless Chrome.

A person moves a service kit from the warehouse to the van with the Transfer inventory form (submitted
through htmx, as every workbench form is), sees that nothing was posted and both sites' quantities,
and the page fits a phone.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_a_transfer_submitted_from_the_sites_page_moves_the_stock(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(1440, 900)
    b.navigate(f'{base}/inventory/sites?as_of=2026-12-31')
    b.wait_for('!!document.querySelector("[data-transfer-form]")')
    b.evaluate("""(() => {
        const f = document.querySelector('[data-transfer-form]').closest('form');
        f.querySelector('[name=from_site]').value = 'Warehouse';
        f.querySelector('[name=to_site]').value = 'Van 1';
        f.querySelector('[name=date]').value = '2026-12-12';
        f.querySelector('[name=item_0]').value = 'Service Call Kit';
        f.querySelector('[name=quantity_0]').value = '1';
        f.querySelector('[name="ctx:reason"]').value = 'Load the van';
        f.querySelector('[data-transfer-save]').click();
        return true; })()""")
    b.wait_for('!!document.querySelector("[data-site-result]") || !!document.querySelector("[role=alert]")')
    text = b.evaluate('(document.querySelector("[data-site-result]") || document.querySelector("[role=alert]")).innerText')
    assert 'nothing was posted' in text and 'Van 1' in text, text
    listed = b.evaluate('document.querySelector("[data-transfer-list]").innerText')
    assert 'Service Call Kit' in listed and 'posted' in listed, listed
    b.viewport(390, 900)
    b.navigate(f'{base}/inventory/sites?as_of=2026-12-31')
    b.wait_for('!!document.querySelector("[data-site-stock]")')
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
