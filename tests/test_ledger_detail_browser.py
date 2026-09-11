"""One real browser journey to transaction detail by account and to missing checks.

Both pages are reached the way a person reaches them -- from the Reports group of the
company window -- and both are read off the rendered table rather than off the command,
so a report that is registered but not printable fails here.
"""
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


def printed(browser):
    """Every rendered row, as the cells a person actually reads."""
    assert not browser.evaluate('document.querySelector(".error")?.textContent'), browser.evaluate('document.body.innerText')
    return browser.evaluate('''[...document.querySelectorAll('#report-lines tbody tr[data-account]')].map(row => ({
        account: row.dataset.account, kind: row.dataset.kind,
        values: Object.fromEntries([...row.querySelectorAll('[data-value]')].map(cell => {
            const copy = cell.cloneNode(true);
            copy.querySelectorAll('.document-cell-label').forEach(node => node.remove());
            return [cell.dataset.value, copy.textContent.replace(/\\s+/g, ' ').trim()];}))}))''')


def totals_shown(browser):
    return browser.evaluate('''Object.fromEntries([...document.querySelectorAll('[data-total]')].map(
        box => [box.dataset.total, box.querySelector('dd').textContent.trim()]))''')


def detail_expectation(result):
    rows = []
    for row in result['rows']:
        values = {'party': row['party_name'] or '', 'split': row['split_account_label'] or '',
                  'debit': row['debit']['amount'], 'credit': row['credit']['amount'],
                  'balance': row['balance']['amount']}
        if row['date']:
            values['date'] = row['date']
        rows.append({'account': row['account_id'], 'kind': row['kind'], 'values': values})
    return rows


def check_expectation(result):
    return [{'account': row['account_id'], 'kind': row['kind'], 'values': {
        'kind': 'Missing' if row['kind'] == 'gap' else 'Used twice',
        'number': (str(row['first_missing']) if row['missing_count'] == 1
                   else f"{row['first_missing']}–{row['last_missing']}")
                  if row['kind'] == 'gap' else str(row['duplicate_number']),
        'count': (f"{row['missing_count']} missing" if row['kind'] == 'gap'
                  else f"{row['times_used']} checks")}}
        for row in result['rows']]


def usable(browser, tmp_path, name, expected, count):
    """The page reads as a report at both widths, on screen and on paper."""
    for width in (1280, 390):
        browser.viewport(width, 900)
        assert printed(browser) == expected
        assert browser.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
        # Nothing scrolls sideways: not the page, not the table, and not a single cell.
        # Naming the offender matters -- a nowrap column two places from the right is
        # what pushed the whole table past its wrapper the first time this was written.
        overflowing = browser.evaluate('''[...document.querySelectorAll('#report-lines, .report-lines-wrap, #report-lines td')]
            .filter(e => e.scrollWidth !== e.clientWidth)
            .map(e => [e.tagName + '.' + e.className, e.scrollWidth, e.clientWidth, e.textContent.slice(0, 60)])''')
        assert overflowing == [], (name, width, overflowing)
        assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines tbody tr")).display') == ('grid' if width == 390 else 'table-row')
        assert f'{count} rows on this page' in browser.evaluate('document.querySelector("#report-page-count").textContent')
        (tmp_path / f'{name}-{width}.png').write_bytes(base64.b64decode(
            browser.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})['data']))
    browser.call('Emulation.setEmulatedMedia', {'media': 'print'})
    assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines")).display') == 'table'
    assert browser.evaluate('getComputedStyle(document.querySelector("#report-lines tbody tr")).display') == 'table-row'
    (tmp_path / f'{name}-print.pdf').write_bytes(base64.b64decode(
        browser.call('Page.printToPDF', {'printBackground': True})['data']))
    browser.call('Emulation.setEmulatedMedia', {'media': 'screen'})


def open_from_reports(browser, site, verb, heading):
    """Walk in through the Reports group, which is where the home window sends a person."""
    browser.navigate(f'{site.base_url}/c/{site.company_id}/_group/reports')
    browser.wait_for(f'!!document.querySelector(\'a[href$="/report/{verb}"]\')')
    browser.evaluate(f'document.querySelector(\'a[href$="/report/{verb}"]\').click()')
    browser.wait_for('!!document.querySelector("form[data-generated-form]")')
    assert browser.evaluate('document.querySelector("h1").textContent').strip() == heading


@pytest.mark.timeout(180)
def test_transaction_detail_and_missing_checks_pages(register_browser, tmp_path):
    env = register_browser
    b = env.browser
    run = lambda name, args: _command(b, env.site, name, args)
    vendor = run('vendor.create', {'name': 'CDP Plumbing Supply'})
    other = run('account.create', {'name': 'CDP travel', 'type': 'expense'})
    written = lambda number, date, amount, lines: run('check.post', dict(
        account=env.bank['id'], date=date, number=number, amount=amount,
        pay_to={'name_type': 'vendor', 'name_id': vendor['id']},
        memo=f'Check {number} to the supplier', expenses=lines))
    one = lambda amount: [dict(account=env.expense['id'], amount=amount, memo='materials on the job')]
    written('1001', '2026-04-01', '10.00', one('10.00'))
    written('1002', '2026-04-02', '20.00', [dict(account=env.expense['id'], amount='12.00'),
                                            dict(account=other['id'], amount='8.00')])
    written('1005', '2026-04-05', '30.00', one('30.00'))
    written('1007', '2026-04-07', '40.00', one('40.00'))

    # ---------------------------------------------------------- transaction detail
    open_from_reports(b, env.site, 'transaction-detail', 'Transaction detail by account')
    detail = dict(date_from='2026-04-01', date_to='2026-04-30', accounts=[env.bank['id']], limit=3)
    expected = run('report.transaction-detail', detail)
    # The account filter is a repeated control, so it is opened the way a drill-down link
    # hands it over: the collection marker and one entered value in the query string.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/report/transaction-detail'
               f'?collection:accounts=1&c:accounts:0:value={env.bank["id"]}')
    b.wait_for('!!document.querySelector("form[data-generated-form]")')
    assert b.evaluate('document.querySelector(`[name="c:accounts:0:value"]`).value') == env.bank['id']
    submit(b, dict(date_from=detail['date_from'], date_to=detail['date_to'], limit='3'))
    usable(b, tmp_path, 'transaction-detail', detail_expectation(expected), expected['count'])
    assert totals_shown(b) == {key: f"{value['amount']} {value['currency']}"
                               for key, value in expected['totals'].items()}
    visible = b.evaluate('document.querySelector("#report-lines").innerText')
    assert 'CDP Plumbing Supply' in visible, visible
    assert 'Check 1001 to the supplier' in visible, visible
    assert '-SPLIT-' in visible and env.expense['full_name'] in visible, visible
    # The document a line came from opens where it was written.
    posted = next(row for row in expected['rows'] if row['kind'] == 'posting')
    assert b.evaluate(f'!!document.querySelector(`a[href$="/journal/{posted["transaction_id"]}"]`)')

    following = run('report.transaction-detail', dict(detail, cursor=expected['next_cursor']))
    b.evaluate('document.querySelector("#statement-next-page button").click()')
    b.wait_for('!!document.querySelector(`#report-lines tbody tr[data-kind="closing"]`)')
    assert printed(b) == detail_expectation(following)
    # The balance column is one unbroken run across the boundary the page just crossed.
    assert ([row['balance']['minor_units'] for row in expected['rows']]
            + [row['balance']['minor_units'] for row in following['rows']]
            == [0, -1000, -3000, -6000, -10000, -10000])
    assert b.evaluate('document.querySelector(`[name="c:accounts:0:value"]`).value') == env.bank['id']
    # The visible filter form never carries a continuation, so the next filter change runs
    # the report that was asked for instead of being refused as a mismatched cursor.
    assert not b.evaluate('!!document.querySelector(`form[data-generated-form] [name="f:cursor"]`)')

    # The repeated control is a real control: retype it and the other side of the same
    # entries is what prints, with each line's own words on it.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/report/transaction-detail'
               f'?collection:accounts=1&c:accounts:0:value={env.bank["id"]}')
    b.wait_for('!!document.querySelector("form[data-generated-form]")')
    b.evaluate(f'document.querySelector(`[name="c:accounts:0:value"]`).value = {env.expense["id"]!r}')
    submit(b, dict(date_from=detail['date_from'], date_to=detail['date_to'], limit='50'))
    other_side = run('report.transaction-detail',
                     dict(detail, accounts=[env.expense['id']], limit=50))
    assert printed(b) == detail_expectation(other_side)
    visible = b.evaluate('document.querySelector("#report-lines").innerText')
    assert 'materials on the job' in visible and env.bank['full_name'] in visible, visible

    # ---------------------------------------------------------------- missing checks
    open_from_reports(b, env.site, 'missing-checks', 'Missing checks')
    asked = dict(as_of='2026-12-31', limit=1)
    expected = run('report.missing-checks', asked)
    submit(b, dict(as_of=asked['as_of'], limit='1'))
    assert not b.evaluate('!!document.querySelector(`form[data-generated-form] [name="f:cursor"]`)')
    usable(b, tmp_path, 'missing-checks', check_expectation(expected), expected['count'])
    assert totals_shown(b) == {key: str(value) for key, value in expected['totals'].items()}
    visible = b.evaluate('document.querySelector("#report-lines").innerText')
    assert '1003–1004' in visible and '1002' in visible and '1005' in visible
    assert 'CDP Plumbing Supply' in visible

    following = run('report.missing-checks', dict(asked, cursor=expected['next_cursor']))
    b.evaluate('document.querySelector("#statement-next-page button").click()')
    b.wait_for('document.querySelector("#report-lines").innerText.includes("1006")')
    assert printed(b) == check_expectation(following)
    assert following['rows'][0]['before']['number'] == '1005'
    assert b.evaluate(f'!!document.querySelector(`a[href$="/check/{following["rows"][0]["before"]["transaction_id"]}"]`)')
