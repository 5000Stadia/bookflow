"""A report opens with its numbers, and running it again lands on the result (R57).

The phone audit photographed the profit and loss as a seven-field form with no figures,
and after Run the page sat mid-report with "Skip to content" showing. In a real browser at
phone and desktop width: the figures are there on arrival, the page does not scroll
sideways, and after Run the focus is on the result heading, not the skip link.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")


@pytest.mark.timeout(240)
@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_profit_and_loss_opens_with_figures_and_run_lands_on_the_result(browser_site, tmp_path, width, height):  # noqa: F811
    site = browser_site
    browser = _Cdp(tmp_path / f"report-opens-{width}")
    try:
        browser.viewport(width, height)
        browser.navigate(site.base_url + "/login")
        browser.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
            % (json.dumps(site.login), json.dumps(PASSWORD)))
        browser.wait_for("!location.pathname.startsWith('/login')")

        browser.navigate(f"{site.base_url}/c/{site.company_id}/report/profit-and-loss")
        browser.wait_for("document.readyState === 'complete'")
        # Figures without pressing Run: the headline and the bold last line are on the page.
        assert browser.evaluate("document.querySelector('#report-result').textContent") == "Net income"
        assert browser.evaluate("document.querySelector('.report-figure').textContent").startswith(("$", "-$"))
        assert browser.evaluate("!!document.querySelector('#statement-accounts tr.statement-final[data-total=net_income]')")
        # The filters are one closed summary line; nothing on the first screen asks for input.
        assert not browser.evaluate("document.querySelector('#report-filters').open")
        assert browser.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        if width > 1000:
            # A statement is a readable column on a desktop, not the width of the window.
            assert browser.evaluate("document.querySelector('.statement-wrap').getBoundingClientRect().width") <= 642

        # Run it again from the form, scrolled down to the button as a person would be.
        browser.evaluate("document.querySelector('#report-filters summary').click()")
        browser.evaluate("""(() => {const form=document.querySelector('form[data-generated-form]');
            form.querySelector('button[value=submit]').scrollIntoView({block:'center'});
            document.querySelector('main').dataset.stale='1';
            form.elements.namedItem('f:date_from').value='2025-01-01';
            form.querySelector('button[value=submit]').click();})()""")
        browser.wait_for("!document.querySelector('main[data-stale]') && !!document.querySelector('[data-report-focus]')")
        browser.wait_for("document.activeElement?.id === 'report-result'")
        assert not browser.evaluate("document.activeElement.classList.contains('skip-link')")
        top = browser.evaluate("document.querySelector('#report-result').getBoundingClientRect().top")
        assert 0 <= top < height, top
    finally:
        browser.close()
