"""Real browser checks for shared register navigation and logout boundaries."""
import base64
import pytest

from tests.test_row8_register_browser import (  # noqa: F401
    browser_site, register_browser, _command, pytestmark,
    _simple_draft, _tab_to, _type, _record_keyboard, _key,
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
    browser.wait_for("document.querySelector('#register-current').textContent.includes('6120.95')")
    assert '6120.95' in browser.evaluate("document.querySelector('#register-period-totals').textContent")
    assert 'DEMO-JPY' in browser.evaluate("document.querySelector('#register-history').textContent")
    assert 'REG-SPLIT' in browser.evaluate("document.querySelector('#register-history').textContent")
    assert 'Splits' in browser.evaluate("document.querySelector('#register-history').textContent")
    assert browser.evaluate('document.documentElement.scrollWidth') <= width + 1
    browser.evaluate('window.scrollTo(0,0)')
    for part in ('entry', 'history'):
        if part == 'history':
            browser.evaluate("document.querySelector('#register-history').scrollIntoView({block:'start'})")
        picture = browser.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':False})
        (tmp_path / f'register-demo-{part}-{width}.png').write_bytes(base64.b64decode(picture['data']))


def test_unicode_attribution_roundtrips_and_local_invalid_text_remains_editable(register_browser):
    env, browser = register_browser, register_browser.browser
    _simple_draft(env, '12.34')
    _tab_to(browser, '[name=reason]'); _type(browser, '修理 receipt + 100%')
    _tab_to(browser, '[name=source_ref]'); _type(browser, '工事/é😀')
    _record_keyboard(browser)
    journal_url = browser.evaluate("document.querySelector('#register-receipt a').href")
    saved = _command(browser, env.site, 'journal.show', {'journal':journal_url.rsplit('/',1)[1]})
    event = _command(browser, env.site, 'audit.show', {'event':saved['revision']['audit_event_id']})
    assert event['reason'] == '修理 receipt + 100%'
    assert event['source_ref'] == '工事/é😀'
    assert browser.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    # An unpaired surrogate cannot be encoded as UTF-8. This is provably local,
    # so no write was sent and the draft must remain revisable.
    browser.navigate(env.url)
    browser.wait_for("document.activeElement.id === 'register-date'")
    _simple_draft(env, '1.00')
    browser.evaluate("document.querySelector('[name=reason]').value=String.fromCharCode(0xd800)")
    _tab_to(browser, '#register-record'); _key(browser, 'Enter')
    browser.wait_for("document.querySelector('#register-error').textContent.includes('Not sent')")
    assert browser.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    assert not browser.evaluate("document.querySelector('#register-fields').disabled")
    assert _command(browser, env.site, 'account.show', {'account':env.bank['id']})['balance']['minor_units'] == -1234


@pytest.mark.parametrize('memo', [None, ''])
def test_inspecting_splits_preserves_blank_memo_identity_and_history(register_browser, memo):
    env, browser = register_browser, register_browser.browser
    today=browser.evaluate("document.querySelector('#register-date').value")
    first=_command(browser, env.site, 'register.post', {'account':env.bank['id'], 'category':env.expense['id'],
        'date':today,'direction':'decrease','amount':'12.34','memo':memo})
    browser.navigate(env.url+'?edit='+first['id'])
    browser.wait_for("document.querySelector('#register-entry-title').textContent.includes('version 1')")
    _tab_to(browser, '#register-splits-open'); _key(browser, 'Enter')
    _tab_to(browser, '#register-split-close'); _key(browser, 'Enter')
    _record_keyboard(browser)
    shown=_command(browser, env.site, 'journal.show', {'journal':first['id']})
    assert shown['version'] == 1 and shown['revision'] == first['revision']
    assert len(_command(browser, env.site, 'journal.history', {'journal':first['id']})['items']) == 1
