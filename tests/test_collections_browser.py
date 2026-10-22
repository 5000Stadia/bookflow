"""R148 in the browser: the Receive Payments window's Underpayment and Overpayment choices.

A preview that finds the cash short shows the anchor's two choices, "Leave this as an
underpayment" and "Write off the extra amount"; one that finds cash left over shows "Leave the
credit to be used later" and "Refund the amount to the customer". What the books say afterwards is
read back through the commands, never from the page.
"""
import base64
import os
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_customer_payment_browser import click, field, wait
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

SHOTS = Path(os.environ.get('BOOKFLOW_SHOTS', '/tmp/m4-shots/collections'))


def shot(b, name, width):
    if not os.environ.get('BOOKFLOW_SHOTS'):
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    _contained(b, width)
    (SHOTS / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format': 'png'})['data']))


def _setup(run, name):
    run('company update', dict(automatically_calculate_payments=True))
    income = run('account create', dict(name=f'{name} income', type='income'))['id']
    payer = run('customer create', dict(name=f'{name} Customer'))['id']
    exempt = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    item = run('item create', dict(name=f'{name} labor', type='service', sales_enabled=True, income_account_id=income,
                                   price='100', description='Work', sales_tax_code_id=exempt))['id']
    return payer, item


def _open(b, env, run, payer, amount):
    check = next(row['id'] for row in run('payment-method query', dict(limit=50))['items'] if row['name'] == 'Check')
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/receive-payments?customer={payer}')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    field(b, 'date', '2026-06-10'); field(b, 'amount', amount); field(b, 'method', check); field(b, 'destination', env.bank['id'])
    click(b, 'load')


@pytest.mark.parametrize('width', [1440, 390])
def test_an_underpayment_is_written_off_from_the_receive_window(register_browser, width):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name.replace(' ', '.'), data, **headers)
    b.viewport(width, 900)
    tag = f'Short {width}'
    run('account create', dict(name=f'{tag} Bad Debt', type='expense'))
    run('class create', dict(name=f'{tag} East'))
    payer, item = _setup(run, tag)
    invoice = run('invoice post', dict(customer=payer, date='2026-06-01', lines=[dict(item=item, quantity='1', net_amount='500.00')]))
    _open(b, env, run, payer, '480.00')
    b.evaluate(f"document.querySelector('[data-invoice=\"{invoice['id']}\"] input[type=checkbox]').click()")
    wait(b)
    assert b.evaluate("document.querySelector('#payment-underpayment').hidden")
    click(b, 'preview')
    # The preview finds the cash 20.00 short: the anchor's two choices appear, leaving it open by default.
    assert not b.evaluate("document.querySelector('#payment-underpayment').hidden")
    assert b.evaluate("document.querySelector('#payment-underpayment-open').checked")
    assert '20.00' in b.evaluate("document.querySelector('#payment-underpayment').innerText")
    assert b.evaluate("document.querySelector('#payment-overpayment').hidden")
    assert 'Underpayment of 20.00 USD left open' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    click(b, 'underpayment-write-off')
    field(b, 'discount-account', f'{tag} Bad Debt'); field(b, 'discount-class', f'{tag} East')
    # Changing the choice asks for a fresh preview before saving.
    assert b.evaluate("document.querySelector('#payment-save').disabled")
    click(b, 'preview')
    proposed = b.evaluate("document.querySelector('#payment-preview-result').innerText")
    assert f'Underpayment of 20.00 USD written off to {tag} Bad Debt' in proposed, proposed
    shot(b, 'underpayment-write-off', width)
    click(b, 'save')
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    receipt = run('payment query', dict(customer=payer))['items'][0]
    shown = run('payment show', dict(payment=receipt['id']))
    assert shown['current']['received_minor_units'] == 48000 and shown['current']['discount_minor_units'] == 2000
    names = {row['current_account_name']: row['debit']['minor_units'] - row['credit']['minor_units']
             for row in run('report trial-balance', dict(date_to='2026-12-31', limit=200))['rows']}
    assert names[f'{tag} Bad Debt'] == 2000


@pytest.mark.parametrize('width', [1440, 390])
def test_an_overpayment_refund_opens_the_refund_window(register_browser, width):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name.replace(' ', '.'), data, **headers)
    b.viewport(width, 900)
    payer, item = _setup(run, f'Over {width}')
    invoice = run('invoice post', dict(customer=payer, date='2026-06-01', lines=[dict(item=item, quantity='1', net_amount='100.00')]))
    _open(b, env, run, payer, '150.00')
    b.evaluate(f"document.querySelector('[data-invoice=\"{invoice['id']}\"] input[type=checkbox]').click()")
    wait(b)
    click(b, 'preview')
    assert not b.evaluate("document.querySelector('#payment-overpayment').hidden")
    assert b.evaluate("document.querySelector('#payment-overpayment-credit').checked")
    assert '50.00' in b.evaluate("document.querySelector('#payment-overpayment').innerText")
    click(b, 'overpayment-refund')
    shot(b, 'overpayment-refund', width)
    b.evaluate("document.getElementById('payment-save').click()")
    b.wait_for("location.pathname.endsWith('/customer-refund/post')")
    receipt = run('payment query', dict(customer=payer))['items'][0]
    assert b.evaluate("new URL(location.href).searchParams.get('payment')") == receipt['id']
    assert receipt['unapplied_minor_units'] == 5000
