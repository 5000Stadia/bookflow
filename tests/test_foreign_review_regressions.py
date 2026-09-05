"""Independent FX artifact-review regressions retained after review."""
from fractions import Fraction

import pytest

from bookflow.core.exchange import convert_money
from bookflow.core.money import Money
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_foreign_journal_browser import _set, _click

def test_independent_fraction_oracle():
    for units in [1, 3, 125, 175, 2345, 9007199254740993, 9223372036854775807]:
        for original,p,home,h,rate in [('JPY',0,'USD',2,'0.01'), ('KWD',3,'JPY',0,'1000'), ('USD',2,'JPY',0,'50'), ('JPY',0,'USD',2,'0.0068')]:
            expected = round(Fraction(units) * Fraction(rate) * 10**h / 10**p)
            if 0 < expected <= 9223372036854775807:
                assert convert_money(Money(units,original),home,rate).minor_units == expected



@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
@pytest.mark.parametrize('width',[1280,390])
def test_foreign_browser_error_preserves_attempt(register_browser,width):
    e,b=register_browser,register_browser.browser
    b.viewport(width,844)
    saved=_command(b,e.site,'journal.post',{'date':'2026-03-11','rate':'0.0068','lines':[
        {'account':e.bank['id'],'side':'debit','amount':'2345 JPY'},
        {'account':e.expense['id'],'side':'credit','amount':'2345 JPY'}]})
    b.navigate(f'{e.site.base_url}/c/{e.site.company_id}/journal/{saved["id"]}/update')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _set(b,'c:lines:0:amount','2350 JPY')
    _set(b,'f:rate','0.007')
    _set(b,'f:refresh_rates','true')
    _click(b,'submit')
    b.wait_for('document.body.textContent.includes("E_UNBALANCED_ENTRY")')
    values=b.evaluate('Object.fromEntries([...document.querySelectorAll("input,select")].map(e=>[e.name,e.value]))')
    assert values['c:lines:0:amount']=='2350 JPY'
    assert values['c:lines:1:amount']=='2345 JPY'
    assert values['c:lines:0:line_id']==saved['revision']['lines'][0]['line_id']
    assert values['f:rate']=='0.007' and values['f:refresh_rates']=='true'
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert _command(b,e.site,'journal.show',{'journal':saved['id']})['version']==1



@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
@pytest.mark.parametrize('width',[1280,390])
def test_browser_rate_stale_version_preserved(register_browser,width):
    e,b=register_browser,register_browser.browser
    b.viewport(width,844)
    row=_command(b,e.site,'rate.set',{'date':'2026-03-11','from_currency':'JPY','rate':'0.0068'})
    b.navigate(f'{e.site.base_url}/c/{e.site.company_id}/rate/{row["id"]}/set')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _set(b,'f:rate','0.007123456789012345')
    _command(b,e.site,'rate.set',{'date':'2026-03-11','from_currency':'JPY','rate':'0.008','expected_version':1})
    _click(b,'submit')
    b.wait_for('document.body.textContent.includes("E_VERSION_CONFLICT")')
    assert b.evaluate('document.getElementsByName("f:expected_version")[0].value')=='1'
    assert b.evaluate('document.getElementsByName("f:rate")[0].value')=='0.007123456789012345'
    assert _command(b,e.site,'rate.show',{'rate_id':row['id']})['rate']=='0.008'
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth')

