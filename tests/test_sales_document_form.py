"""Sales documents open as a document window, not a schema dump.

Header band, one line grid, footer band, action bar. The browser submits the same command
input an agent sends, and every amount on screen is the server's own computed figure.
"""
import html
import json
import re

import pytest

from bookflow.adapters.workbench import document_form as D
from bookflow.adapters.workbench import forms as F
from bookflow.core import registry
from bookflow.core.money import Money
from tests.test_row3_host import WB, hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser

DOCUMENTS = (('invoice', 'post'), ('invoice', 'update'), ('sales-receipt', 'post'),
             ('sales-receipt', 'update'), ('estimate', 'create'), ('estimate', 'update'))

# Wire-level identifiers and generated-form phrasing that must never reach a person.
MACHINERY = ('refresh_defaults', 'use_defaults', 'ar_account', 'class_id', 'deposit_to',
             'customer_purchase_order', 'sales_tax_item', 'shipping_address_id',
             'customer_message_item', 'expected_facts_fingerprint', 'expected_version',
             'sales_tax_calculation', 'payment_reference', 'line_id', 'price_basis_amount',
             'estimated_unit_cost', 'markup_percent', 'net_amount', 'source_id',
             'Set empty list', 'collection:', 'c:lines:')


def _visible_labels(page):
    """Every string the form presents as the name of a control or a section."""
    found = []
    for pattern in (r'(?s)<label[^>]*>(.*?)(?:<small|</label>)',
                    r'(?s)<summary[^>]*>(.*?)</summary>',
                    r'(?s)<legend[^>]*>(.*?)</legend>',
                    r'(?s)<h1[^>]*>(.*?)</h1>',
                    r'(?s)<h2 class="band-title"[^>]*>(.*?)</h2>',
                    r'(?s)<div class="line-head">(.*?)</div>',
                    r'(?s)<dt[^>]*>(.*?)</dt>',
                    r'<option[^>]*>([^<]*)</option>',
                    r'aria-label="([^"]*)"'):
        found += re.findall(pattern, page)
    return [html.unescape(re.sub(r'(?s)<[^>]*>', ' ', text)).strip() for text in found]


def _leaves(noun, verb):
    command = registry.get(f'{noun} {verb}')
    described = F.describe_fields(noun, verb, command.input_model, {}, {})
    for leaf in described:
        if leaf['kind'] == 'collection':
            leaf['collection'] = F.collection_schema(leaf['annotation'])
            leaf['collection']['values'] = []
    return D.describe(described, noun)


@pytest.mark.parametrize('noun,verb', DOCUMENTS)
def test_every_typed_leaf_reaches_the_document_exactly_once(noun, verb):
    described = _leaves(noun, verb)
    bands = D.layout(noun, described)
    placed = [leaf['path'] for key in ('primary', 'terms', 'scope', 'footer', 'pricing', 'record')
              for leaf in bands[key]]
    placed += [leaf['path'] for group in bands['addresses'] for leaf in group['leaves']]
    placed.append(bands['lines']['path'])
    assert sorted(placed) == sorted(leaf['path'] for leaf in described)
    assert len(placed) == len(set(placed))


@pytest.mark.parametrize('noun,verb', DOCUMENTS)
def test_the_line_grid_carries_every_line_field_as_a_column_or_in_the_row_panel(noun, verb):
    bands = D.layout(noun, _leaves(noun, verb))
    fields = {child['name'] for child in bands['lines']['collection']['item']['fields']}
    shown = {column['name'] for column in bands['columns'] if column['field']}
    shown |= {child['name'] for child in bands['line_extras']}
    assert fields - shown == {'line_id'}, fields - shown
    assert [column['label'] for column in bands['columns']][:4] == [
        'Item', 'Description', 'Quantity', 'Unit of measure']
    assert [column['label'] for column in bands['columns']][-2:] == ['Amount', 'Tax']
    # Every head says what it means, because a reader asked what Unit was next to Quantity.
    assert all(column['hint'] for column in bands['columns']), bands['columns']


@pytest.mark.parametrize('noun,verb', DOCUMENTS)
def test_the_pricing_rule_is_in_the_row_panel_and_not_in_the_columns(noun, verb):
    """It chooses which price input is live: a rule for the line, not one of its numbers."""
    bands = D.layout(noun, _leaves(noun, verb))
    assert '@pricing' not in {column['name'] for column in bands['columns']}
    assert 'Pricing' not in {column['label'] for column in bands['columns']}
    assert bands['line_pricing']['label'] and bands['line_pricing']['description']


@pytest.mark.parametrize('noun,verb', DOCUMENTS)
def test_the_pricing_control_still_submits_the_name_it_always_did(hosted, noun, verb):
    browser = _browser(hosted)
    company = hosted.company_id
    if verb == 'update':
        page = browser.get(f'/c/{company}/{noun}/{_seed(hosted, noun)}/update')
    else:
        page = browser.get(f'/c/{company}/{noun}/{verb}')
    assert page.status_code == 200, page.text[:600]
    assert 'name="price-mode:lines:__INDEX0__"' in page.text
    # Reachable, and reachable from the row's own panel rather than from the columns.
    panel = re.search(r'(?s)<details class="line-extras">.*?</details>', page.text)
    assert panel is not None and 'price-mode:' in panel.group(0)
    grid_head = re.search(r'(?s)<div class="line-head".*?</div>', page.text)
    assert grid_head is not None and 'Pricing' not in grid_head.group(0)


def test_the_estimate_carries_acceptance_only_where_the_command_accepts_it():
    # Naming the gap rather than inventing a control: a new estimate is always a draft.
    assert 'status' not in {leaf['path'] for leaf in _leaves('estimate', 'create')}
    accepted = D.layout('estimate', _leaves('estimate', 'update'))
    assert 'status' in {leaf['path'] for leaf in accepted['primary']}
    assert 'decision_note' in {leaf['path'] for leaf in accepted['primary']}


@pytest.mark.parametrize('noun,verb', DOCUMENTS)
def test_no_machinery_identifier_reaches_a_visible_label(hosted, noun, verb):
    browser = _browser(hosted)
    company = hosted.company_id
    if verb in ('update',):
        record = _seed(hosted, noun)
        page = browser.get(f'/c/{company}/{noun}/{record}/update')
    else:
        page = browser.get(f'/c/{company}/{noun}/{verb}')
    assert page.status_code == 200, page.text[:600]
    # The command's own reference text is agent documentation and stays collapsed under it.
    body = re.sub(r'(?s)<details class="technical-details"><summary>Command reference</summary>.*?</details>',
                  '', page.text)
    offenders = [(token, label) for label in _visible_labels(body) for token in MACHINERY
                 if token in label]
    assert offenders == [], offenders


def _seed(hosted, noun):
    """One saved document of each kind, made through the command surface."""
    company = hosted.company_id
    income = hosted.ok('account.create', dict(name=f'Doc income {noun}', type='income'), company=company)['id']
    customer = hosted.ok('customer.create', dict(name=f'Doc customer {noun}'), company=company)['id']
    code = next(row['id'] for row in hosted.ok('sales-tax-code.list', {}, company=company)['items']
                if not row['taxable'])
    item = hosted.ok('item.create', dict(name=f'Doc service {noun}', type='service', sales_enabled=True,
        income_account_id=income, price='12.34', description='Doc work', sales_tax_code_id=code),
        company=company)['id']
    payload = dict(date='2026-01-12', customer=customer, memo='Seeded document',
                   lines=[dict(item=item, quantity='2.5')])
    if noun == 'invoice':
        return hosted.ok('invoice.post', payload, company=company)['id']
    if noun == 'sales-receipt':
        bank = hosted.ok('account.create', dict(name='Doc bank', type='bank'), company=company)['id']
        method = hosted.ok('payment-method.create', dict(name='Doc cash', kind='cash'), company=company)['id']
        return hosted.ok('sales-receipt.post', dict(payload, deposit_to=bank, payment_method=method),
                         company=company)['id']
    return hosted.ok('estimate.create', dict(payload, title='Doc job'), company=company)['id']


def test_the_header_opens_with_the_customer_and_folds_each_address_into_one_expander(hosted):
    browser = _browser(hosted)
    page = browser.get(f'/c/{hosted.company_id}/invoice/post')
    assert page.status_code == 200
    header = re.search(r'(?s)<section class="document-band" aria-label="Invoice header">(.*?)</section>',
                       page.text)
    assert header is not None
    first = re.search(r'(?s)<label[^>]*>(.*?)<small', header.group(1))
    assert first.group(1).strip().startswith('Customer:Job')
    assert page.text.count('<details class="address-block"') == 2
    assert 'Bill To' in page.text and 'Ship To' in page.text
    # Each address is one expander, not six rows in the header's own grid.
    rows = re.search(r'(?s)<div class="document-row">(.*?)</div>\s*<div class="document-addresses"',
                     header.group(1))
    assert 'billing_address' not in rows.group(1)


def test_the_line_grid_shows_one_row_per_line_and_a_read_only_computed_amount(hosted):
    browser = _browser(hosted)
    company = hosted.company_id
    invoice = _seed(hosted, 'invoice')
    saved = hosted.ok('invoice.show', dict(invoice=invoice), company=company)
    page = browser.get(f'/c/{company}/invoice/{invoice}/update')
    assert page.status_code == 200
    body = re.search(r'(?s)<div class="line-body" data-collection-items>(.*?)</div>\s*<template', page.text)
    assert body.group(1).count('<div class="line-row" data-collection-item>') == len(saved['revision']['lines'])
    amounts = re.findall(r'<span class="line-amount">([^<]*)</span>', body.group(1))
    assert amounts == [line['net']['amount'] for line in saved['revision']['lines']]
    # A computed amount is read-only: no control of any kind sits inside that cell.
    for cell in re.findall(r'(?s)<span class="line-cell-label"[^>]*>Amount</span>(.*?)</div>', body.group(1)):
        assert '<input' not in cell and '<select' not in cell and '<textarea' not in cell


def test_the_footer_shows_the_servers_own_totals_and_only_an_invoice_has_a_balance(hosted):
    browser = _browser(hosted)
    company = hosted.company_id
    for noun in ('invoice', 'sales-receipt'):
        record = _seed(hosted, noun)
        selector = noun.replace('-', '_')
        saved = hosted.ok(f'{noun}.show', {selector: record}, company=company)
        page = browser.get(f'/c/{company}/{noun}/{record}/update')
        totals = re.search(r'(?s)<dl class="totals-list">(.*?)</dl>', page.text)
        assert totals is not None, page.text[:400]
        pairs = dict(zip(re.findall(r'<dt>([^<]*)</dt>', totals.group(1)),
                         [re.sub(r'(?s)<[^>]*>', '', cell).strip()
                          for cell in re.findall(r'(?s)<dd>(.*?)</dd>', totals.group(1))]))
        assert pairs['Subtotal'] == saved['revision']['subtotal']['amount']
        assert pairs['Tax'] == saved['revision']['tax']['amount']
        assert pairs['Total'].split()[0] == saved['revision']['total']['amount']
        if noun == 'invoice':
            settlement = saved['settlement_current']
            currency = settlement['currency']
            assert pairs['Payments Applied'] == Money(settlement['applied_minor_units'], currency).to_dict()['amount']
            assert pairs['Balance Due'].split()[0] == Money(settlement['due_minor_units'], currency).to_dict()['amount']
        else:
            assert 'Balance Due' not in pairs and 'Payments Applied' not in pairs


def test_the_action_bar_offers_save_save_and_new_and_cancel(hosted):
    browser = _browser(hosted)
    company = hosted.company_id
    fresh = browser.get(f'/c/{company}/invoice/post').text
    bar = re.search(r'(?s)<div class="document-actions">(.*?)</div>', fresh).group(1)
    assert '>Save<' in bar and 'value="submit-new"' in bar and 'Cancel' in bar
    assert 'value="preview"' in bar
    invoice = _seed(hosted, 'invoice')
    correction = browser.get(f'/c/{company}/invoice/{invoice}/update').text
    bar = re.search(r'(?s)<div class="document-actions">(.*?)</div>', correction).group(1)
    # A correction has no "and new": it is an edit of one document.
    assert 'value="submit-new"' not in bar and '>Save<' in bar


def test_a_correction_asks_for_its_reason_where_a_person_can_see_it(hosted):
    browser = _browser(hosted)
    company = hosted.company_id
    invoice = _seed(hosted, 'invoice')
    # A reason is what a keyed settlement correction needs, and money applied to the
    # invoice is what makes a correction keyed. An untouched invoice is an ordinary
    # edit on every surface and asks for no reason.
    plain = browser.get(f'/c/{company}/invoice/{invoice}/update').text
    assert not re.search(r'<input id="ctx-reason"[^>]*required', plain)
    customer = hosted.ok('invoice.show', dict(invoice=invoice), company=company)['customer_id']
    bank = hosted.ok('account.create', dict(name='Doc reason bank', type='bank'), company=company)['id']
    method = hosted.ok('payment-method.create', dict(name='Doc reason cash', kind='cash'), company=company)['id']
    hosted.ok('payment.receive', dict(customer=customer, date='2026-01-12', amount='10.00',
        payment_method=method, deposit_to=bank, operation_key='doc-reason-receipt',
        applications=dict(mode='inline', items=[dict(invoice=invoice, expected_version=1, amount='10.00')])),
        company=company)
    page = browser.get(f'/c/{company}/invoice/{invoice}/update').text
    reason = re.search(r'(?s)<label for="ctx-reason">(.*?)</label>', page)
    assert reason is not None and 'Reason for this correction (required)' in reason.group(1)
    assert re.search(r'<input id="ctx-reason"[^>]*required', page)


def _originals(page):
    return json.loads(html.unescape(re.search(r'name="originals" value=\'([^\']*)\'', page).group(1)))


def test_a_payment_applied_mid_edit_keys_the_correction_without_losing_the_draft(hosted):
    """Money can reach the invoice between opening the form and saving it."""
    browser = _browser(hosted)
    company = hosted.company_id
    invoice = _seed(hosted, 'invoice')
    route = f'/c/{company}/invoice/{invoice}/update'
    opened = browser.get(route).text
    assert re.search(r'name="f:operation_key" value=""', opened)
    form = {'originals': json.dumps(_originals(opened)), 'f:expected_version': '1',
            'f:memo': 'Draft written before the payment', 'f:operation_key': '',
            'f:settlement_guard': re.search(r'name="f:settlement_guard" value="([^"]*)"', opened).group(1)}
    previewed = browser.post(route, headers=WB, data={**form, 'action': 'preview'}).text
    fingerprint = re.search(r'name="f:expected_facts_fingerprint" value="([0-9a-f]{64})"', previewed).group(1)

    customer = hosted.ok('invoice.show', dict(invoice=invoice), company=company)['customer_id']
    bank = hosted.ok('account.create', dict(name='Late payment bank', type='bank'), company=company)['id']
    method = hosted.ok('payment-method.create', dict(name='Late payment cash', kind='cash'), company=company)['id']
    hosted.ok('payment.receive', dict(customer=customer, date='2026-01-12', amount='10.00',
        payment_method=method, deposit_to=bank, operation_key='late-payment-receipt',
        applications=dict(mode='inline', items=[dict(invoice=invoice, expected_version=1, amount='10.00')])),
        company=company)

    saved = browser.post(route, headers=WB, data={**form, 'action': 'submit',
        'f:expected_facts_fingerprint': fingerprint})
    assert saved.status_code == 200
    assert 'A payment was applied to this invoice while you were editing.' in saved.text
    assert re.search(r'name="f:operation_key" value="WB-[^"]+"', saved.text)
    assert re.search(r'<input id="ctx-reason"[^>]*required', saved.text)
    assert 'name="f:memo" value="Draft written before the payment"' in saved.text
    assert 'name="f:expected_facts_fingerprint" value=""' in saved.text
    current = hosted.ok('invoice.show', dict(invoice=invoice), company=company)
    assert current['revision']['memo'] == 'Seeded document'
