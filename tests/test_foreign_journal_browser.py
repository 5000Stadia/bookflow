"""Actual Chrome foreign post/edit and rate receipts at desktop and phone widths."""
import base64
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command, _key, _type  # noqa: F401
from tests.test_row8_custom_field_browser import _tab

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
DATE = '2026-03-11'


def _set(b, name, value):
    b.evaluate('document.getElementsByName(' + json.dumps(name) + ')[0].value=' + json.dumps(value))


def _click(b, value):
    _tab(b, f'button[value="{value}"]'); _key(b, 'Enter')


@pytest.mark.parametrize('width', [1280, 390])
def test_rate_form_saved_receipt_is_readable(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900 if width == 1280 else 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/rate/set')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    for name, value in {'date':DATE,'from_currency':'JPY','rate':'000.006800','expected_version':'0'}.items():
        _set(b, 'f:'+name, value)
    _click(b, 'preview')
    b.wait_for("document.body.textContent.includes('Preview (nothing written)')")
    assert not _command(b, env.site, 'rate.query', {'from_currency':'JPY','date_from':DATE,'date_to':DATE})['items']
    _click(b, 'submit')
    b.wait_for("!document.querySelector('[data-generated-form]') && document.body.textContent.includes('Saved successfully')")
    saved = _command(b, env.site, 'rate.show', {'date':DATE,'from_currency':'JPY'})
    assert b.evaluate('location.pathname') == f'/c/{env.site.company_id}/rate/{saved["id"]}'
    body = b.evaluate('document.body.innerText')
    assert '0.0068' in body and 'E_VALIDATION' not in body and 'E_USAGE' not in body
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth')
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/rate/{saved["id"]}/set')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate("document.getElementsByName('f:expected_version')[0].value") == '1'
    assert b.evaluate("document.getElementsByName('f:date')[0].value") == DATE
    _set(b, 'f:rate', '0.007')
    _click(b, 'submit')
    b.wait_for("!document.querySelector('[data-generated-form]') && document.body.textContent.includes('Saved successfully')")
    corrected = _command(b, env.site, 'rate.show', {'rate_id':saved['id']})
    assert corrected['version']==2 and corrected['rate']=='0.007'


@pytest.mark.parametrize('width', [1280, 390])
def test_original_amount_survives_post_preview_edit_and_explicit_refresh(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900 if width == 1280 else 844)
    row = _command(b, env.site, 'rate.set', {'date':DATE,'from_currency':'JPY','rate':'0.0068'})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/journal/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _set(b, 'f:date', DATE)
    b.evaluate('''(() => {const collection=document.querySelector('[data-collection-path="lines"]');
      collection.querySelector(':scope > [data-collection-add]').click();
      collection.querySelector(':scope > [data-collection-add]').click();})()''')
    b.evaluate(f'''(() => {{const rows=[...document.querySelector('[data-collection-path="lines"] > [data-collection-items]').children];
      const accounts={json.dumps([env.bank['id'],env.expense['id']])};
      rows.forEach((r,i)=>{{r.querySelector('[name^="c:"][name$=":account"]').value=accounts[i];
        r.querySelector('[name^="c:"][name$=":side"]').value=i?'credit':'debit';
        r.querySelector('[name^="c:"][name$=":amount"]').value='2345 JPY';}});}})()''')
    _click(b, 'preview')
    b.wait_for("document.body.textContent.includes('Preview (nothing written)')")
    assert '0.0068' in b.evaluate('document.body.textContent')
    _click(b, 'submit')
    b.wait_for("!document.querySelector('[data-generated-form]') && document.body.textContent.includes('Saved successfully')")
    identifier = b.evaluate('location.pathname').rsplit('/',1)[1]
    saved = _command(b, env.site, 'journal.show', {'journal':identifier})
    assert saved['version']==1 and saved['total_minor_units']==1595
    _command(b, env.site, 'rate.set', {'date':DATE,'from_currency':'JPY','rate':'0.007','expected_version':row['version']})
    edit = f'{env.site.base_url}/c/{env.site.company_id}/journal/{identifier}/update'
    b.navigate(edit)
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate("document.getElementsByName('c:lines:0:amount')[0].value") == '2345 JPY'
    assert b.evaluate("document.getElementsByName('f:rate')[0].value") == ''
    _tab(b, '[name="f:memo"]'); _type(b, 'Foreign memo only')
    _click(b, 'preview')
    b.wait_for("document.body.textContent.includes('Preview (nothing written)')")
    assert _command(b, env.site, 'journal.show', {'journal':identifier})['version']==1
    _click(b, 'submit')
    b.wait_for("!document.querySelector('[data-generated-form]') && document.body.textContent.includes('Saved successfully')")
    unchanged = _command(b, env.site, 'journal.show', {'journal':identifier})
    assert unchanged['version']==2 and unchanged['total_minor_units']==1595
    assert unchanged['revision']['lines'][0]['rate_used']=='0.0068'
    b.navigate(edit); b.wait_for('!!document.querySelector("[data-generated-form]")')
    _set(b,'f:refresh_rates','true')
    _click(b, 'preview'); b.wait_for("document.body.textContent.includes('Preview (nothing written)')")
    assert _command(b, env.site, 'journal.show', {'journal':identifier})['version']==2
    _click(b,'submit')
    b.wait_for("!document.querySelector('[data-generated-form]') && document.body.textContent.includes('Saved successfully')")
    refreshed = _command(b, env.site, 'journal.show', {'journal':identifier})
    assert refreshed['version']==3 and refreshed['total_minor_units']==1642
    assert _command(b, env.site, 'journal.show', {'journal':identifier,'revision_number':1})['total_minor_units']==1595
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth')
    (tmp_path/'foreign-detail.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'format':'png'})['data']))
