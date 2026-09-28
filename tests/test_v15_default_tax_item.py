"""R79: a sale that names no sales tax item takes the company's normal one.

The blind trials' agents were told "select one or use_defaults"; `use_defaults` failed the
same way because the demo company had no default set, and the agents guessed another item.
The demo now records its most common sales tax item (Combined Sales Tax, as the anchor asks
for whenever sales tax is on); every sale in the commercial family resolves it when none is
named, and a company with no default is told so, with the items it could name.
"""
import json

import pytest

from bookflow import BookflowError

COMPANY = 'Demo Plumbing Co'
SALE = {'date': '2026-09-20', 'customer': 'Payment Example Customer',
        'lines': [{'item': 'Mainline Clearing', 'quantity': '1'}]}


def run(client, name, body, **kw):
    return client.run(name, body, company=COMPANY, reason='R79 witness', **kw)


def tax_item(invoice):
    return invoice['revision']['profile']['sales_tax_item']['label']


def test_omitted_and_use_defaults_capture_the_company_default(client):
    omitted = run(client, 'invoice post', SALE)
    assert tax_item(omitted) == 'Combined Sales Tax'
    assert omitted['tax_minor_units'] > 0
    defaulted = run(client, 'invoice post', {**SALE, 'use_defaults': ['sales_tax_item']}, dry_run=True)
    assert tax_item(defaulted) == 'Combined Sales Tax'
    assert defaulted['tax_minor_units'] == omitted['tax_minor_units']


def test_explicit_item_wins_and_a_correction_keeps_what_was_captured(client):
    explicit = run(client, 'invoice post', {**SALE, 'sales_tax_item': 'Springfield Sales Tax'})
    assert tax_item(explicit) == 'Springfield Sales Tax'
    captured = run(client, 'invoice post', SALE)
    # A default changed later does not rewrite what a saved sale captured (spec 24).
    springfield = explicit['revision']['profile']['sales_tax_item']['id']
    run(client, 'company update', {'default_sales_tax_item_id': springfield})
    corrected = run(client, 'invoice update', {'invoice': captured['id'], 'expected_version': 1,
                                               'memo': 'Corrected after the default changed'})
    assert tax_item(corrected) == 'Combined Sales Tax'


def test_every_sale_in_the_family_resolves_the_default(client):
    uf = next(row['id'] for row in client.run('account list', {}, company=COMPANY)['items']
              if row['system_role'] == 'undeposited_funds')
    method = client.run('payment-method list', {}, company=COMPANY)['items'][0]['id']
    receipt = run(client, 'sales-receipt post', {**SALE, 'deposit_to': uf, 'payment_method': method}, dry_run=True)
    credit = run(client, 'credit-memo post', SALE, dry_run=True)
    estimate = run(client, 'estimate create', {**SALE, 'title': 'Drain quote'}, dry_run=True)
    for document in (receipt, credit, estimate):
        assert 'Combined Sales Tax' in json.dumps(document)


def test_no_default_anywhere_says_so_and_names_the_choices(client):
    info = client.run('company show', {}, company=COMPANY)['info']
    assert info['default_sales_tax_item_id'] is not None
    run(client, 'company update', {'default_sales_tax_item_id': None})
    for body in (SALE, {**SALE, 'use_defaults': ['sales_tax_item']}):
        with pytest.raises(BookflowError) as caught:
            run(client, 'invoice post', body, dry_run=True)
        field = caught.value.details['fields'][0]
        assert field['field'] == 'sales_tax_item'
        assert field['problem'].startswith('no default sales tax item is set for this company or customer')
        assert 'Combined Sales Tax' in field['problem'] and 'Springfield Sales Tax' in field['problem']
