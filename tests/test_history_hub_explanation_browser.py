"""Actual admitted hub explanation in authenticated desktop and phone pages."""
from types import SimpleNamespace
import pytest
from bookflow.adapters.http.app import create_app
from tests.test_history_hub_explanations import admitted_hub, REASON, CITATION
from tests.test_row3_host import live as live_server
from tests.test_row5_browser_acceptance import CHROME, _Cdp
from tests.test_service_sales_browser import _contained


@pytest.fixture
def site(admitted_hub):
    host,_,_=admitted_hub
    yield from live_server.__wrapped__(SimpleNamespace(handle=SimpleNamespace(app=create_app(host,secure_cookies=False))))


@pytest.mark.skipif(not CHROME.is_file(),reason='Chrome unavailable')
@pytest.mark.parametrize('width',(1280,390))
@pytest.mark.timeout(180)
def test_hub_explanation_escaped_and_citation_unavailable(admitted_hub,site,tmp_path,width):
    _,_,event=admitted_hub
    browser=_Cdp(tmp_path/'chrome')
    try:
        browser.viewport(width,850)
        browser.call('Network.enable')
        browser.call('Network.setExtraHTTPHeaders',{'headers':{'Authorization':'Bearer secret-H'}})
        for route in ('/hub/audit','/hub/audit/'+event):
            browser.navigate(site+route)
            browser.wait_for("!!document.querySelector('.history-explanation')")
            text=browser.evaluate("document.querySelector('.history-explanation').textContent")
            assert REASON in text and 'unavailable' in text.lower()
            assert CITATION not in browser.evaluate('document.body.textContent')
            assert browser.evaluate("document.querySelectorAll('.history-explanation a,.history-explanation hub').length")==0
            _contained(browser,width)
    finally:browser.close()
