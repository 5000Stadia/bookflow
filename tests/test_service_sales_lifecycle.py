"""Public commercial writes, exact accounting effects and immutable corrections."""
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from tests.test_row8_journal import assert_oracle, ledger, database_path
from tests.conftest import make_actor, as_user

COMPANY = 'Demo Plumbing Co'


@pytest.fixture
def sale(client):
    income = client.account.create(name='Sale witness income', type='income', company=COMPANY)['id']
    customer = client.customer.create(name='Sale witness customer', company=COMPANY)['id']
    code = next(row['id'] for row in client.run('sales-tax-code list', {}, company=COMPANY)['items'] if not row['taxable'])
    item = client.run('item create', dict(name='Sale witness service', type='service', sales_enabled=True,
        description='Service labor', income_account_id=income, price='12.34', sales_tax_code_id=code), company=COMPANY)['id']
    return dict(customer=customer, item=item, income=income)


def post(client, sale, **kwargs):
    return client.run('invoice post', dict(date='2026-01-12', customer=sale['customer'],
        lines=[dict(item=sale['item'], quantity='2.5')], **kwargs), company=COMPANY)


def test_invoice_correction_void_and_customer_balance(client, sale):
    first = post(client, sale)
    assert first['version'] == 1 and first['total_minor_units'] == 3085
    ar = first['revision']['profile']['control_account']['id']
    assert_oracle(client, first['id'], {('2026-01-12', ar): 3085, ('2026-01-12', sale['income']): -3085})
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 3085
    changed = client.run('invoice update', dict(invoice=first['id'], date='2026-02-01', expected_version=1,
        lines=[dict(item=sale['item'], line_id=first['revision']['lines'][0]['line_id'], quantity='3')]), company=COMPANY)
    assert changed['version'] == 2 and changed['total_minor_units'] == 3702
    assert_oracle(client, first['id'], {('2026-02-01', ar): 3702, ('2026-02-01', sale['income']): -3702})
    void = client.run('invoice void', dict(invoice=first['id'], expected_version=2), reason='Duplicate invoice', company=COMPANY)
    assert void['status'] == 'voided' and void['version'] == 3
    assert_oracle(client, first['id'], {})
    history = client.run('invoice history', dict(invoice=first['id']), company=COMPANY)
    assert [r['revision_number'] for r in history['items']] == [1, 2]
    assert [len(r['batches']) for r in history['items']] == [2, 2]
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 0


def test_leading_decimal_quantity_posts_exact_half_price(client, sale):
    data = dict(date='2026-01-12', customer=sale['customer'],
        lines=[dict(item=sale['item'], quantity='.5')])
    preview = client.run('invoice post', data, company=COMPANY, dry_run=True)
    invoice = client.run('invoice post', dict(data,
        expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert invoice['total_minor_units'] == preview['total_minor_units'] == 617
    line = invoice['revision']['lines'][0]
    assert line['quantity'] == '0.5' and line['quantity_microunits'] == 500_000
    ar = invoice['revision']['profile']['control_account']['id']
    assert_oracle(client, invoice['id'], {('2026-01-12', ar): 617,
        ('2026-01-12', sale['income']): -617})


def test_preview_replay_noop_and_stale_facts(client, sale):
    data = dict(date='2026-01-12', customer=sale['customer'], lines=[dict(item=sale['item'])])
    preview = client.run('invoice post', data, company=COMPANY, dry_run=True)
    fingerprint = preview['facts_fingerprint']
    assert len(fingerprint) == 64
    data['expected_facts_fingerprint'] = fingerprint
    first = client.run('invoice post', data, company=COMPANY, idempotency_key='sale-once')
    replay = client.run('invoice post', data, company=COMPANY, idempotency_key='sale-once')
    assert replay['id'] == first['id'] and replay['idempotent_replay']
    before = ledger(client, first['id'])
    noop = client.run('invoice update', dict(invoice=first['id'], expected_version=1), company=COMPANY)
    assert not noop['changed'] and noop['version'] == 1
    assert ledger(client, first['id']) == before
    with pytest.raises(BookflowError) as caught:
        client.run('invoice post', data, company=COMPANY)
    assert caught.value.code == 'E_PREVIEW_STALE'


def test_sales_receipt_paid_bank_effects_and_type_isolation(client, sale):
    bank = client.account.create(name='Sale witness bank', type='bank', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Sale witness cash', kind='cash'), company=COMPANY)['id']
    receipt = client.run('sales-receipt post', dict(date='2026-01-12', customer=sale['customer'],
        deposit_to=bank, payment_method=method, lines=[dict(item=sale['item'])]), company=COMPANY)
    assert_oracle(client, receipt['id'], {('2026-01-12', bank): 1234, ('2026-01-12', sale['income']): -1234})
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 0
    for noun, selector in [('invoice', 'invoice'), ('journal', 'journal')]:
        with pytest.raises(BookflowError) as caught:
            client.run(noun + ' show', {selector: receipt['id']}, company=COMPANY)
        assert caught.value.code == 'E_RECORD_NOT_FOUND'


def snapshot(client):
    with open_database(database_path(client), writable=False) as db:
        names = [row[0] for row in db.raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: list(db.raw.execute('SELECT * FROM "' + name + '" ORDER BY rowid')) for name in names}


def test_tax_components_round_each_line_and_preserve_captured_policy(client, sale):
    agency = client.vendor.create(name='Sale witness tax agency', is_tax_agency=True, company=COMPANY)['id']
    taxable = next(row['id'] for row in client.run('sales-tax-code list', {}, company=COMPANY)['items'] if row['taxable'])
    with open_database(database_path(client), writable=True) as db:
        liability = db.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        db.conn.execute(c.company_info.update().values(sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
        db.conn.commit()
    tax_item = client.run('item create', dict(name='Sale witness five percent', type='sales_tax_item', tax_percent='5',
        tax_agency_vendor_id=agency, liability_account_id=liability), company=COMPANY)['id']
    data = dict(date='2026-01-12', customer=sale['customer'], sales_tax_item=tax_item, sales_tax_calculation='line_component_half_even',
        lines=[dict(item=sale['item'], unit_price=value, tax_code=taxable) for value in ('0.10', '0.30')])
    first = client.run('invoice post', data, company=COMPANY)
    # 0.5 minor unit rounds to even0; 1.5 rounds to even2, independently.
    assert [line['tax_minor_units'] for line in first['revision']['lines']] == [0, 2]
    assert first['subtotal_minor_units'] == 40 and first['tax_minor_units'] == 2 and first['total_minor_units'] == 42
    ar = first['revision']['profile']['control_account']['id']
    assert_oracle(client, first['id'], {('2026-01-12', ar): 42, ('2026-01-12', sale['income']): -40, ('2026-01-12', liability): -2})
    assert len(first['revision']['lines'][0]['tax_components']) == 1
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.company_info.update().values(sales_tax_enabled=False, sales_tax_liability_basis='payment_receipt'))
        db.conn.execute(c.items.update().where(c.items.c.id == tax_item).values(tax_percent_millionths=9_000_000, active=False))
        db.conn.commit()
    updated = client.run('invoice update', dict(invoice=first['id'], expected_version=1, memo='Historical tax policy'), company=COMPANY)
    assert updated['tax_minor_units'] == 2 and updated['revision']['profile']['preferences']['sales_tax_enabled']
    assert client.run('invoice show', dict(invoice=first['id'], revision_number=1), company=COMPANY)['revision']['lines'] == first['revision']['lines']


def test_closed_periods_conflicts_and_retired_line_identity(client, sale):
    first = post(client, sale)
    removed = first['revision']['lines'][0]['line_id']
    current = client.run('invoice update', dict(invoice=first['id'], expected_version=1,
        lines=[dict(item=sale['item'], quantity='4')]), company=COMPANY)
    before = snapshot(client)
    for args, code in [
        (dict(expected_version=1, memo='stale'), 'E_VERSION_CONFLICT'),
        (dict(expected_version=2, lines=[dict(item=sale['item'], line_id=removed)]), 'E_VALIDATION'),
    ]:
        with pytest.raises(BookflowError) as caught:
            client.run('invoice update', dict(invoice=first['id'], **args), company=COMPANY)
        assert caught.value.code == code
        assert snapshot(client) == before
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.company_info.update().values(closing_date='2026-01-31'))
        db.conn.commit()
    before = snapshot(client)
    for verb, args in [('update', dict(date='2026-02-01')), ('void', {})]:
        with pytest.raises(BookflowError) as caught:
            client.run('invoice ' + verb, dict(invoice=first['id'], expected_version=2, **args), company=COMPANY, reason='Correct entry')
        assert caught.value.code == 'E_PERIOD_CLOSED'
        assert snapshot(client) == before


def test_query_history_cursor_and_readonly_cli(client, sale, cli, root):
    first = post(client, sale)
    post(client, sale)
    page = client.run('invoice query', dict(customer=sale['customer'], limit=1), company=COMPANY)
    assert page['has_more'] and page['count'] == 1
    assert client.run('invoice query', dict(customer=sale['customer'], limit=1, cursor=page['next_cursor']), company=COMPANY)['count'] == 1
    client.run('invoice update', dict(invoice=first['id'], expected_version=1, memo='Later'), company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run('invoice query', dict(customer=sale['customer'], limit=1, cursor=page['next_cursor']), company=COMPANY)
    assert caught.value.code == 'E_QUERY_STALE'
    company_id = client.company.show(company=COMPANY)['id']
    make_actor(root, 'sale-reader', company_role=(company_id, 'readonly'))
    reader = as_user(root, 'sale-reader')
    shown = reader.run('invoice show', dict(invoice=first['id']), company=COMPANY)
    assert cli.json('invoice', 'show', first['id'], '--company', COMPANY) == shown
    with pytest.raises(BookflowError):
        post(reader, sale)


@pytest.mark.parametrize('point', ['posting_lines', 'sales_profiles', 'audit_entries'])
def test_failed_insert_rolls_back_entire_sale(client, sale, point):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    before = snapshot(client)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO ' + point + ' '):
            raise RuntimeError('witness interrupted insert')
    event.listen(Engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='interrupted insert'):
            post(client, sale)
    finally:
        event.remove(Engine, 'before_cursor_execute', fail)
    assert snapshot(client) == before


def test_balanced_but_wrong_plan_rejected_before_persistence(client, sale, monkeypatch):
    from bookflow.company import sales
    original = sales.prepare
    before = snapshot(client)
    def corrupt(*args, **kwargs):
        plan = original(*args, **kwargs)
        # A balanced journal is insufficient: debit must match commercial total.
        for leg in plan.data['pending']['posting_lines']:
            if leg['debit_minor_units']:
                leg['debit_minor_units'] += 1
            else:
                leg['credit_minor_units'] += 1
        return plan
    monkeypatch.setattr(sales, 'prepare', corrupt)
    with pytest.raises(BookflowError) as caught:
        post(client, sale)
    assert caught.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


@pytest.mark.parametrize('kind', ['fixed_percent', 'per_item'])
def test_added_line_uses_selected_header_price_level_version(client, sale, kind):
    client.company.update(enable_price_levels=True, company=COMPANY)
    rule = dict(percent='10') if kind == 'fixed_percent' else dict(items=[dict(item_id=sale['item'], price='15.00', adjustment_basis='standard_price')])
    level = client.run('price-level create', dict(name='Saved sale level', kind=kind, **rule), company=COMPANY)
    first = post(client, sale, price_level=level['id'])
    expected_price = 1357 if kind == 'fixed_percent' else 1500
    assert first['revision']['lines'][0]['unit_price_minor_units'] == expected_price
    changed_rule = dict(percent='20') if kind == 'fixed_percent' else dict(items=[dict(item_id=sale['item'], price='25.00', adjustment_basis='standard_price')])
    client.run('price-level update', dict(price_level=level['id'], expected_version=level['version'], **changed_rule), company=COMPANY)
    added = client.run('invoice update', dict(invoice=first['id'], expected_version=1,
        lines=[dict(item=sale['item'], line_id=first['revision']['lines'][0]['line_id']), dict(item=sale['item'])]), company=COMPANY)
    assert [line['unit_price_minor_units'] for line in added['revision']['lines']] == [expected_price, expected_price]
    refreshed = client.run('invoice update', dict(invoice=first['id'], expected_version=2, refresh_defaults=True), company=COMPANY)
    current_price = 1481 if kind == 'fixed_percent' else 2500
    assert [line['unit_price_minor_units'] for line in refreshed['revision']['lines']] == [current_price, current_price]


@pytest.mark.parametrize('change', [
    {'quantity': 1.5}, {'quantity': True}, {'quantity': '0'}, {'quantity': '-1'}, {'quantity': '1e3'},
    {'quantity': '0.0000001'}, {'unit_price': 10}, {'unit_price': True}, {'unit_price': 1.5},
    {'unit_price': '1.001'}, {'unit_price': '1.00 EUR'}, {'unit_price': '-1'}, {'unit_price': '0'},
    {'unit_price': {'minor_units': 100, 'currency': 'USD', 'amount': '1.01'}},
    {'quantity': '2', 'unit_price': {'minor_units': 9223372036854775807, 'currency': 'USD'}},
])
def test_invalid_commercial_money_and_quantities_write_nothing(client, sale, change):
    before = snapshot(client)
    with pytest.raises(BookflowError):
        client.run('invoice post', dict(date='2026-01-12', customer=sale['customer'],
            lines=[dict(item=sale['item'], **change)]), company=COMPANY)
    assert snapshot(client) == before


def test_apply_rechecks_closing_date_after_replanning(client, sale, monkeypatch):
    from bookflow.company import sales
    original = sales.prepare
    before = snapshot(client)
    calls = 0
    def late_close(s, *args, **kwargs):
        nonlocal calls
        calls += 1
        plan = original(s, *args, **kwargs)
        if calls == 2:
            s.company.conn.execute(c.company_info.update().values(closing_date='2026-01-31'))
        return plan
    monkeypatch.setattr(sales, 'prepare', late_close)
    with pytest.raises(BookflowError) as caught:
        post(client, sale)
    assert caught.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(client) == before
