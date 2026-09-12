"""Named report filters preserve whole-report meaning on desktop and phone."""
import pytest

from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site
from tests.test_row8_register_browser import _command
from tests.test_summary_reports import FROM, TO, build
from tests.test_summary_reports_browser import _login, _fill, _CELLS, _no_sideways_scroll
from tests.test_service_sales_browser import _choose

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.timeout(240)
def test_named_class_filter_paging_and_inactive_readback(browser_site, tmp_path):
    site = browser_site
    b = _Cdp(tmp_path / 'filter-chrome')
    try:
        _login(b, site)
        def run(command, body, *, reason=None):
            return _command(b, site, command.replace(' ', '.'), body,
                            **({'X-Bookflow-Reason': reason} if reason else {}))
        fixture = build(run)
        run('company update', {'use_classes': True})
        klass = run('class create', {'name': 'Service calls'})
        run('invoice post', {'date': '2027-03-12', 'number': 'FILTER-GUI',
            'customer': fixture['cafe'], 'lines': [
                {'item': fixture['drain'], 'quantity': '1', 'unit_price': '100.00', 'class_id': klass['id']},
                {'item': fixture['valve'], 'quantity': '1', 'unit_price': '25.00', 'class_id': klass['id']},
            ]})
        b.navigate(f'{site.base_url}/c/{site.company_id}/')
        b.wait_for('!!document.querySelector(\'a[href$="/_group/reports"]\')')
        b.evaluate('document.querySelector(\'a[href$="/_group/reports"]\').click()')
        b.wait_for('!!document.querySelector(\'a[href$="/report/sales-by-item"]\')')
        b.evaluate('document.querySelector(\'a[href$="/report/sales-by-item"]\').click()')
        b.wait_for('!!document.querySelector(\'[name="label:f:class_id"]\')')
        for width in (1280, 390):
            b.viewport(width, 900)
            _choose(b, 'f:class_id', 'Service calls')
            _fill(b, {'date_from': FROM, 'date_to': TO, 'limit': '1'})
            b.wait_for('!!document.querySelector("#summary-next-page")')
            assert 'class Service calls' in b.evaluate('document.querySelector("#summary-filter").textContent')
            assert b.evaluate(_CELLS % '#summary-scope') == [['750.00', '625.00']]
            totals = b.evaluate(_CELLS % '#summary-totals')
            assert totals == [['125.00', '0.00', '125.00']]
            assert b.evaluate(_CELLS % '#summary-rows') == [['Summary Drain Service', '1', '100.00', '100.00', '80.00%']]
            assert b.evaluate('document.querySelectorAll("#summary-rows a").length') == 0
            b.evaluate('document.querySelector("#summary-next-page button").click()')
            b.wait_for('!document.querySelector("#summary-next-page") && !!document.querySelector("#summary-rows")')
            assert b.evaluate(_CELLS % '#summary-rows') == [['Summary Valve Fitting', '1', '25.00', '25.00', '20.00%']]
            assert b.evaluate(_CELLS % '#summary-totals') == totals
            assert b.evaluate(_CELLS % '#summary-scope') == [['750.00', '625.00']]
            _no_sideways_scroll(b)
        run('class deactivate', {'class': klass['id'], 'expected_version': klass['version']})
        _fill(b, {'limit': '200'})
        b.wait_for('document.querySelector("#summary-filter")?.textContent.includes("(inactive)")')
        assert b.evaluate(_CELLS % '#summary-totals') == [['125.00', '0.00', '125.00']]
        assert not b.evaluate('document.querySelector(".error")?.textContent')
        _no_sideways_scroll(b)
    finally:
        b.close()
