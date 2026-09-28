"""R85: recording a customer payment warns when that customer's check reference is already on file.

In the blind payment trial the agent avoided recording check #5120 twice only from its own
conversation memory. `payment receive` now says, without refusing, which live receipt of the
same customer already carries the reference; an exact replay of the same operation stays
quiet, and a voided receipt, another customer or another reference never warns.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401

COMPANY = 'Demo Plumbing Co'


def receive(client, customer, key, reference, **kw):
    method = client.run('payment-method list', {}, company=COMPANY)['items'][0]['id']
    return client.run('payment receive', {'customer': customer, 'date': '2026-09-21', 'amount': '25.00',
                                          'payment_method': method, 'operation_key': key, 'reference': reference},
                      company=COMPANY, reason='Check received', **kw)


def test_same_customer_and_reference_warns_before_and_after_saving(client):
    first = receive(client, 'Payment Example Customer', 'r85-first', '5120')
    assert first['warnings'] == []
    replay = receive(client, 'Payment Example Customer', 'r85-first', '5120')
    assert replay['idempotent_replay'] is True and replay['id'] == first['id'] and replay['warnings'] == []

    preview = receive(client, 'Payment Example Customer', 'r85-second', ' 5120 ', dry_run=True)
    assert len(preview['warnings']) == 1
    warning = preview['warnings'][0]
    number = client.run('payment show', {'payment': first['id']}, company=COMPANY)['number']
    for fact in (first['id'], number, '2026-09-21', '25.00 USD'):
        assert fact in warning, warning
    second = receive(client, 'Payment Example Customer', 'r85-second', ' 5120 ')
    assert second['warnings'] == preview['warnings']
    # Replaying the second operation returns its effect without advice about itself or the first.
    assert receive(client, 'Payment Example Customer', 'r85-second', ' 5120 ')['warnings'] == []


def test_no_warning_for_other_customer_other_reference_or_a_voided_receipt(client):
    first = receive(client, 'Payment Example Customer', 'r85-a', '7781')
    assert receive(client, 'Commercial Example Customer', 'r85-b', '7781', dry_run=True)['warnings'] == []
    assert receive(client, 'Payment Example Customer', 'r85-c', '7782', dry_run=True)['warnings'] == []
    assert receive(client, 'Payment Example Customer', 'r85-d', None, dry_run=True)['warnings'] == []
    client.run('payment void', {'payment': first['id'], 'expected_version': first['version'],
                                'operation_key': 'r85-void'}, company=COMPANY, reason='Recorded in error')
    assert receive(client, 'Payment Example Customer', 'r85-e', '7781', dry_run=True)['warnings'] == []


@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_browser_shows_the_warning_in_the_preview_before_saving(register_browser):  # noqa: F811
    """The receive-payment form is where the person records a check; the warning shows before Save."""
    from tests.test_customer_payment_browser import click, field, wait
    from tests.test_row8_register_browser import _command
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name.replace(' ', '.'), data, **headers)
    payer = run('customer create', dict(name='Repeated Check Customer'))['id']
    method = run('payment-method create', dict(name='Repeated check', kind='check'))['id']
    first = run('payment receive', dict(customer=payer, date='2026-06-02', amount='40.00', payment_method=method,
                                        reference='BR-5120', operation_key='r85-browser-first'))
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/receive-payments?customer={payer}')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    field(b, 'date', '2026-06-03'); field(b, 'amount', '40.00'); field(b, 'method', method)
    field(b, 'reference', 'BR-5120')
    click(b, 'preview')
    shown = b.evaluate("Array.from(document.querySelectorAll('#payment-preview-result [data-payment-warning]')).map(x=>x.innerText).join('\\n')")
    assert first['id'] in shown and 'BR-5120' in shown, shown
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
