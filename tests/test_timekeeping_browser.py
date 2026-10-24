"""R106 in actual Chrome, at a desk (1440) and on a phone (390): the Weekly Timesheet, the time clock and
the job calendar, each submitted for real through htmx (so the workbench header goes with it).

Dana works 8 hours Monday and 7.5 Tuesday on the Riverside job; the grid saves them as two time entries
and totals 15.5. She clocks in and out from the time clock, and Dave is scheduled on the calendar.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _set(b, name, value, scope='form[method=post]'):
    b.evaluate(f'''(() => {{const e=document.querySelector({scope!r} + ' [name="{name}"]'); e.value={value!r};
        e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()''')


def _submit(b, selector, done):
    b.evaluate(f'void (window.beforeSubmit = document.querySelector({selector!r}).closest("form"))')
    b.evaluate(f'document.querySelector({selector!r}).click()')
    b.wait_for(f'!window.beforeSubmit.isConnected && !!document.querySelector({done!r})')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')


@pytest.mark.parametrize('width', [1440, 390])
def test_timesheet_clock_and_calendar_in_the_browser(register_browser, width):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)  # noqa: E731
    b.viewport(width, 900)
    income = run('account.create', dict(name='TK income', type='income'))['id']
    run('customer.create', dict(name='TK Riverside'))
    run('employee.create', dict(name='Dana Fitter'))
    run('employee.create', dict(name='Dave Brazer'))
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('item.create', dict(name='TK labor', type='service', sales_enabled=True, income_account_id=income,
                            price='12.34', description='Labour', sales_tax_code_id=code))
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    # The Weekly Timesheet: type two days into the first row and save.
    b.navigate(base + '/timesheet?employee=Dana%20Fitter&week=2026-01-12')
    b.wait_for('!!document.querySelector("[data-timesheet-grid]")')
    _contained(b, width)
    _set(b, 'customer_0', 'TK Riverside')
    _set(b, 'item_0', 'TK labor')
    _set(b, 'notes_0', 'Rough-in')
    _set(b, 'h_0_0', '8')
    _set(b, 'h_0_1', '7.5')
    _submit(b, '[data-timesheet-save]', '[data-timesheet-result]')
    assert b.evaluate('document.querySelector("[data-week-total]").innerText.trim()') == '15.5'
    assert 'Saved Dana Fitter' in b.evaluate('document.querySelector("[data-timesheet-result]").innerText')
    sheet = run('timesheet.show', {'employee': 'Dana Fitter', 'week': '2026-01-12'})
    assert sheet['week_total'] == '15.5' and [len(c['entries']) for c in sheet['rows'][0]['cells'][:3]] == [1, 1, 0]
    _contained(b, width)

    # The time clock: clock Dave in, then out.
    b.navigate(base + '/clock')
    b.wait_for('!!document.querySelector("[data-clock-form]")')
    _set(b, 'employee', 'Dave Brazer')
    _set(b, 'customer', 'TK Riverside')
    _set(b, 'item', 'TK labor')
    _submit(b, '[data-clock-in]', '[data-open-clock]')
    _contained(b, width)
    b.navigate(base + '/')
    b.wait_for('!!document.querySelector("[data-open-clocks]")')
    assert 'Dave Brazer' in b.evaluate('document.querySelector("[data-open-clocks]").innerText')
    b.navigate(base + '/clock')
    b.wait_for('!!document.querySelector("[data-clock-out]")')
    _submit(b, '[data-clock-out]', '[data-clock-result]')
    assert 'clocked out' in b.evaluate('document.querySelector("[data-clock-result]").innerText')

    # The job calendar: schedule Dave on Riverside.
    b.navigate(base + '/calendar?view=week&date=2026-01-12')
    b.wait_for('!!document.querySelector("[data-calendar-grid]")')
    _set(b, 'employee', 'Dave Brazer')
    _set(b, 'customer', 'TK Riverside')
    _set(b, 'day', '2026-01-13')
    _set(b, 'notes', 'Water heater')
    _submit(b, '[data-schedule-save]', '[data-calendar-result]')
    assert 'Water heater' in b.evaluate('document.querySelector("[data-calendar-day=\\"2026-01-13\\"]").innerText')
    _contained(b, width)
