"""Real browser checks for shared register navigation and logout boundaries."""
import base64
import pytest

from tests.test_row8_register_browser import (  # noqa: F401
    browser_site, register_browser, _command, pytestmark,
)


def test_history_opens_immutable_revision_and_logout_elsewhere_clears_intent(register_browser):
    env, browser = register_browser, register_browser.browser
    today = browser.evaluate("document.querySelector('#register-date').value")
    first = _command(browser, env.site, 'register.post', {
        'account': env.bank['id'], 'category': env.expense['id'], 'date': today,
        'direction': 'decrease', 'amount': '7.25', 'number': 'LINK-ORIGINAL', 'memo': 'Original explanation'})
    _command(browser, env.site, 'journal.update', {'journal': first['id'], 'expected_version': 1,
        'number': 'LINK-CURRENT', 'memo': 'Current explanation'})
    browser.navigate(env.url)
    browser.wait_for("document.querySelectorAll('#register-history tr[data-kind=posting]').length === 3")
    history = browser.evaluate("[...document.querySelectorAll('#register-history a')].find(a => a.textContent === 'History' && a.href.includes('revision_number=1')).href")
    browser.navigate(history)
    browser.wait_for("!!document.querySelector('h2')")
    assert browser.evaluate("document.querySelector('h2').textContent") == 'Journal LINK-ORIGINAL'
    assert 'Original explanation' in browser.evaluate("document.querySelector('main').textContent")
    # Session storage survives ordinary navigation, so logout must clear it even
    # when the register script is no longer present on the current page.
    browser.evaluate("sessionStorage.setItem('bookflow-register-pending-v1', JSON.stringify({privateDraft:'retained'}))")
    browser.navigate(env.site.base_url + '/companies')
    browser.wait_for("!!document.querySelector('form[action=\"/logout\"]')")
    browser.evaluate("document.querySelector('form[action=\"/logout\"]').requestSubmit()")
    browser.wait_for("location.pathname === '/login'")
    assert browser.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None


@pytest.mark.parametrize('width,height', [(1280,900), (390,844)])
def test_seeded_register_demo_balances_and_layout(register_browser, width, height, tmp_path):
    env, browser = register_browser, register_browser.browser
    bank = _command(browser, env.site, 'account.show', {'account':'Checking'})
    browser.viewport(width, height)
    url = f'{env.site.base_url}/c/{env.site.company_id}/account/{bank["id"]}/register?date_from=2026-01-01&date_to=2026-12-31'
    browser.navigate(url)
    browser.wait_for("document.querySelector('#register-current').textContent.includes('6105.00')")
    assert '6105.00' in browser.evaluate("document.querySelector('#register-period-totals').textContent")
    assert 'REG-SPLIT' in browser.evaluate("document.querySelector('#register-history').textContent")
    assert 'Splits' in browser.evaluate("document.querySelector('#register-history').textContent")
    assert browser.evaluate('document.documentElement.scrollWidth') <= width + 1
    browser.evaluate('window.scrollTo(0,0)')
    for part in ('entry', 'history'):
        if part == 'history':
            browser.evaluate("document.querySelector('#register-history').scrollIntoView({block:'start'})")
        picture = browser.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':False})
        (tmp_path / f'register-demo-{part}-{width}.png').write_bytes(base64.b64decode(picture['data']))
