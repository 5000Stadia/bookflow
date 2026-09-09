"""One real browser journey from source commands through both basic reports."""
import base64

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_financial_statements_browser import fill

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def submit(browser, fields):
    browser.evaluate('void(window.previousForm=document.querySelector("form[data-generated-form]"))')
    fill(browser, fields)
    browser.wait_for('!window.previousForm?.isConnected')


def compare(browser, expected):
    assert not browser.evaluate('document.querySelector(".error")?.textContent'), browser.evaluate('document.body.innerText')
    actual = browser.evaluate('''[...document.querySelectorAll('#report-lines tbody tr[data-account]')].map(row => ({
        account_id:row.dataset.account, kind:row.dataset.kind,
        values:[...row.querySelectorAll('[data-value]')].map(cell => {
            const copy=cell.cloneNode(true);copy.querySelectorAll('.document-cell-label').forEach(e=>e.remove());return copy.textContent.trim();})}))''')
    assert actual == [dict(account_id=row['account_id'], kind=row.get('kind','account'),
        values=[row['debit']['amount'],row['credit']['amount'],row.get('signed_balance',row.get('signed_net'))['amount']]) for row in expected['rows']]
    for key, money in expected['totals'].items():
        assert browser.evaluate(f'document.querySelector("[data-total={key}] dd").textContent') == money['amount']+' '+money['currency']
    assert str(expected['count'])+' rows on this page' in browser.evaluate('document.querySelector("#report-page-count").textContent')
    assert bool(browser.evaluate('!!document.querySelector("#statement-next-page")')) == bool(expected['next_cursor'])
    for row in expected['rows']:
        assert row['display_account_label'] in browser.evaluate('document.querySelector("#report-lines").innerText')


def layouts(browser, tmp_path, name, expected):
    for width in (1280,390):
        browser.viewport(width,900)
        compare(browser,expected)
        assert browser.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
        assert browser.evaluate('[document.querySelector("#report-lines"),document.querySelector(".report-lines-wrap")].every(e=>e.scrollWidth===e.clientWidth)')
        assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines tbody tr")).display') == ('grid' if width==390 else 'table-row')
        if width == 390:
            assert browser.evaluate('document.querySelector("#report-lines caption").clientWidth >= document.querySelector("#report-lines").clientWidth - 2')
        (tmp_path/f'{name}-{width}.png').write_bytes(base64.b64decode(browser.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True})['data']))
    browser.call('Emulation.setEmulatedMedia', {'media':'print'})
    assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines")).display') == 'table'
    assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines tbody tr")).display') == 'table-row'
    assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines thead")).position') != 'absolute'
    (tmp_path/f'{name}-print.pdf').write_bytes(base64.b64decode(browser.call('Page.printToPDF', {'printBackground':True})['data']))
    browser.call('Emulation.setEmulatedMedia', {'media':'screen'})


@pytest.mark.timeout(120)
def test_trial_balance_and_general_ledger_forms_pages_and_retained_restart(register_browser,tmp_path):
    env=register_browser; b=env.browser
    run=lambda name,args: _command(b,env.site,name,args)
    prose='Pipe inspection and documented follow-up. '+('Full historical description remains readable. '*8)
    for index,(date,amount) in enumerate([('2026-05-30','10.01'),('2026-06-02','20.02'),('2026-06-03','30.03'),('2026-06-04','40.04')]):
        run('journal.post',dict(date=date,number='BASIC-'+str(index),lines=[
            dict(account=env.bank['id'],side='debit',amount=amount,description=prose),
            dict(account=env.expense['id'],side='credit',amount=amount)]))
    run('account.update',dict(account=env.bank['id'],expected_version=1,name='Current report bank',number='1'))
    run('account.update',dict(account=env.expense['id'],expected_version=1,number='2'))
    zero=run('account.create',dict(name='Unused inactive report account',type='expense',number='3'))
    run('account.deactivate',dict(account=zero['id'],expected_version=1))
    prefix=f'{env.site.base_url}/c/{env.site.company_id}/report/'
    tb=dict(date_to='2026-06-30',include_zero=True,limit=2)
    expected=run('report.trial-balance',tb)
    b.navigate(prefix+'trial-balance');b.wait_for('!!document.querySelector("form[data-generated-form]")')
    submit(b,dict(date_to=tb['date_to'],include_zero='true',limit='2'))
    layouts(b,tmp_path,'trial-balance',expected)
    next_expected=run('report.trial-balance',dict(tb,cursor=expected['next_cursor']))
    b.evaluate('document.querySelector("#statement-next-page button").click()')
    b.wait_for('!!document.querySelector(`[data-account="'+zero['id']+'"]`)')
    compare(b,next_expected)
    assert 'Inactive' in b.evaluate('document.querySelector("#report-lines").innerText')
    assert 'Zero balance' in b.evaluate('document.querySelector("#report-lines").innerText')
    assert b.evaluate('document.querySelector(`form[data-generated-form] [name="f:include_zero"]`).value') == 'true'
    assert not b.evaluate('document.querySelector(`form[data-generated-form] [name="f:cursor"]`) !== null')
    # An audited change stales continuation; the visible form must still restart.
    run('account.create',dict(name='Stale report witness',type='expense'))
    b.evaluate('document.querySelector("#statement-next-page button").click()')
    b.wait_for('!!document.querySelector("#report-restart")')
    assert 'E_QUERY_STALE' in b.evaluate('document.querySelector(".error").textContent')
    assert b.evaluate('document.querySelector(`[name="f:date_to"]`).value') == tb['date_to']
    submit(b,{})
    compare(b,run('report.trial-balance',tb))

    gl=dict(account=env.bank['id'],date_from='2026-06-01',date_to='2026-06-30',limit=3)
    expected=run('report.general-ledger',gl)
    b.navigate(prefix+'general-ledger');b.wait_for('!!document.querySelector("form[data-generated-form]")')
    submit(b,{**gl,'limit':'3'})
    layouts(b,tmp_path,'general-ledger',expected)
    b.evaluate('document.querySelectorAll(".report-captured").forEach(e=>e.open=true)')
    visible=b.evaluate('document.querySelector("#report-lines").innerText')
    assert 'CDP bank' in visible and 'Current report bank' in visible and prose in visible
    for row in expected['rows']:
        if row['kind']=='posting':
            for key in ('posting_line_id','batch_id','revision_id','transaction_id','recorded_at'):
                assert str(row[key]) in visible
    assert b.evaluate('document.querySelector(".report-lines-wrap").scrollWidth===document.querySelector(".report-lines-wrap").clientWidth')
    (tmp_path/'general-ledger-captured-390.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True})['data']))
    following=run('report.general-ledger',dict(gl,cursor=expected['next_cursor']))
    b.evaluate('document.querySelector("#statement-next-page button").click()')
    b.wait_for('!!document.querySelector(`[data-kind="closing"]`)')
    compare(b,following)
    for key in ('account','date_from','date_to','limit'):
        assert b.evaluate(f'document.querySelector(`form[data-generated-form] [name="f:{key}"]`).value') == str(gl[key])
    submit(b,dict(date_from='2026-07-01'))
    assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
    assert b.evaluate('document.querySelector(`[name="f:account"]`).value') == gl['account']
    assert b.evaluate('document.querySelector(`[name="f:date_from"]`).value') == '2026-07-01'
    submit(b,dict(date_from=gl['date_from']))
    compare(b,run('report.general-ledger',gl))
