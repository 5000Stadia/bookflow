"""Recording time, correcting it and billing it, in actual Chrome at a desk and on a phone.

A backend nobody can reach is not finished, so this file drives the same journey twice -- at
1280 and at 390 -- through the real workbench: log the hours, read them back as a document,
correct them with a reason, carry them onto an invoice, and watch the page refuse the second
bill and the withdrawal of time a sale is standing on. Every page the journey stops on is
checked for horizontal overflow at that width, and a screenshot of the billing panel is kept.

The service item sells at 12.34 an hour, so an hour and a half is 18.51 and two hours 24.68.
"""
import base64

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_service_sales_browser import (
    _fill, _choose, _value, _click, _contained, _error, _preview, _saved)
from tests.test_customer_work_browser import preview, saved, visit

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def document_text(b):
    return b.evaluate('document.querySelector(".work-document").innerText')


def billing_text(b):
    return b.evaluate('document.querySelector(\'[aria-label="Work billing"]\').innerText')


@pytest.mark.parametrize('width', [1280, 390])
def test_record_correct_and_bill_time_in_the_browser(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Time income', type='income'))['id']
    run('account.create', dict(name='Time receivables', type='accounts_receivable'))
    run('customer.create', dict(name='Time customer'))
    run('employee.create', dict(name='Dana Fitter'))
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('item.create', dict(name='Time labor', type='service', sales_enabled=True,
        income_account_id=income, price='12.34', description='Labour', sales_tax_code_id=code))
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    # The home window is where somebody who does not know the command name starts.
    b.navigate(base + '/')
    b.wait_for('!!document.querySelector(".nav-group")')
    assert 'Record time worked' in b.evaluate('document.body.innerText')
    assert b.evaluate(f'!!document.querySelector("a[href=\\"/c/{env.site.company_id}/time-activity/create\\"]")')
    _contained(b, width)

    # Record an hour and a half.
    visit(b, base + '/time-activity/create')
    _choose(b, 'f:employee', 'Dana Fitter')
    _choose(b, 'f:customer', 'Time customer')
    _fill(b, 'f:date', '2026-01-12')
    _fill(b, 'f:duration', '1.5')
    _choose(b, 'f:item', 'Time labor')
    _fill(b, 'f:note', 'Traced the leak')
    _fill(b, 'ctx:reason', 'Timesheet for Monday')
    preview(b)
    assert '18.51' in document_text(b)
    _contained(b, width)
    _click(b, 'submit')
    entry = saved(b, 'time-activity')

    # What a person reads back: whose time, how long, what it is charged as, and the charge.
    text = document_text(b)
    assert 'Time entry' in text and 'Dana Fitter' in text
    assert 'Recorded' in text and '2026-01-12' in text
    assert '1.5' in text and '12.34' in text and '18.51' in text
    assert 'Time labor' in text and 'Traced the leak' in text
    _contained(b, width)

    # The remaining-work panel is the same one an estimate has, because the hour is billed
    # through the same ledger.
    assert 'Remaining net 18.51' in billing_text(b)
    (tmp_path / f'time-billing-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']))

    # The list reads as a list of time, not of storage columns.
    b.navigate(base + '/time-activity')
    b.wait_for('!!document.querySelector("table")')
    listing = b.evaluate('document.body.innerText')
    assert 'Time entries' in listing and 'Time — Dana Fitter' in listing
    assert '18.51' in listing and 'Recorded' in listing
    # The status filter offers the states recorded time actually holds.
    assert b.evaluate('Array.from(document.querySelector("select[name=status]").options).map(o=>o.value)') == [
        '', 'recorded', 'voided']
    _contained(b, width)

    # A correction with no reason is refused where the person can see it.
    visit(b, base + '/time-activity/' + entry + '/update')
    _fill(b, 'f:duration', '2')
    _click(b, 'preview')
    _error(b, 'E_REASON_REQUIRED')
    _contained(b, width)

    _fill(b, 'ctx:reason', 'Timesheet said two hours')
    preview(b)
    assert '24.68' in document_text(b)
    _click(b, 'submit')
    assert saved(b, 'time-activity') == entry
    assert 'Revision 2' in document_text(b)

    # Bill it from the panel, the way the page offers.
    visit(b, base + '/time-activity/' + entry + '/invoice')
    _fill(b, 'f:date', '2026-01-13')
    _choose(b, 'f:ar_account', 'Time receivables')
    key = _value(b, 'f:conversion_key')
    assert len(key) >= 32, 'the conversion key is prefilled, not typed'
    _fill(b, 'ctx:reason', 'Billing Monday')
    _preview(b)
    assert _value(b, 'f:conversion_key') == key, 'a preview does not mint a second key'
    assert '24.68' in b.evaluate('document.querySelector(".sales-document").innerText')
    _contained(b, width)
    _click(b, 'submit')
    invoice = _saved(b, 'invoice')
    assert run('invoice.show', dict(invoice=invoice))['total_minor_units'] == 2468

    # Back on the entry: the hour is spoken for, and the page says so rather than offering it again.
    b.navigate(base + '/time-activity/' + entry)
    b.wait_for('!!document.querySelector(".work-document")')
    assert 'Remaining net 0.00' in billing_text(b)
    _contained(b, width)

    # Withdrawing time a sale is standing on is refused in the page, by name.
    current = run('time-activity.show', dict(time_activity=entry))
    visit(b, base + '/time-activity/' + entry + '/void')
    _fill(b, 'f:expected_version', str(current['version']))
    _fill(b, 'ctx:reason', 'Logged against the wrong job')
    _click(b, 'preview')
    _error(b, 'E_WORK_DEPENDENCY')
    assert 'void the sale first' in b.evaluate('document.querySelector(".error").innerText')
    _contained(b, width)
