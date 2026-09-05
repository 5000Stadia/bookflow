"""Foreign journal contracts through public writes and independent accounting facts."""
import copy

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import journals, rates, schema
from bookflow.core import registry
from tests.test_row8_journal import COMPANY, journal_accounts, ledger, assert_oracle  # noqa: F401
from tests.test_row8_custom_field_integration import complete_state, definition

DATE = '2026-03-11'


def rate(client, value='0.0068', currency='JPY', date=DATE, version=0):
    return client.run('rate set', {'date': date, 'from_currency': currency,
        'rate': value, 'expected_version': version}, company=COMPANY)


def post(client, accounts, amount='2345 JPY', **kw):
    return client.journal.post(date=DATE, lines=[
        {'account': accounts[0], 'side': 'debit', 'amount': amount},
        {'account': accounts[1], 'side': 'credit', 'amount': amount}], company=COMPANY, **kw)


def entered(saved, amount=None):
    return [{'line_id': line['line_id'], 'account': line['account_id'], 'side': line['side'],
        'amount': amount if amount is not None else line['original_amount'] or line['amount']}
        for line in saved['revision']['lines']]


def facts(saved):
    return [{key: line[key] for key in ('original_amount', 'original_minor_units',
        'original_currency', 'rate_used', 'rate_source', 'amount')} for line in saved['revision']['lines']]


@pytest.mark.parametrize('amount,override,expected', [
    ('2345 JPY', '0.0068', 1595), ('125 JPY', '0.0002', 2),
    ('175 JPY', '0.0002', 4), ('135 JPY', '0.0002', 3),
    ('1.005 BHD', '2.5', 251), ('1.01 CHF', '0.99', 100),
    ('9223372036854775807 JPY', '0.01', 9223372036854775807),
])
def test_exact_manual_conversion_and_original_money(client, journal_accounts, amount, override, expected):
    saved = post(client, journal_accounts, amount, rate=override)
    assert saved['total_minor_units'] == expected
    assert saved['revision']['lines'][0]['rate_source'] == 'manual'
    assert saved['revision']['lines'][0]['original_amount']['currency'] == amount[-3:]
    assert_oracle(client, saved['id'], {(DATE, journal_accounts[0]): expected, (DATE, journal_accounts[1]): -expected})
    batches, legs, _ = ledger(client, saved['id'])
    assert sum(leg['debit_minor_units'] for leg in legs) == sum(leg['credit_minor_units'] for leg in legs) == expected
    assert all(leg['rate_used'] == override for leg in legs)


@pytest.mark.parametrize('amount,override,code', [
    ('1 JPY', '0.0001', 'E_VALUE_RANGE'),
    ('9223372036854775807 JPY', '1', 'E_VALUE_RANGE'),
    ('1.00 JPY', '0.0068', 'E_AMOUNT_PRECISION'),
    ('1.00 USD', '1', 'E_VALIDATION'),
    ('2345 JPY', None, 'E_VALIDATION'),
])
def test_invalid_conversion_leaves_no_effects(client, journal_accounts, amount, override, code):
    before = complete_state(client)
    with pytest.raises(BookflowError) as error:
        post(client, journal_accounts, amount, rate=override, idempotency_key='invalid-foreign')
    assert error.value.code == code
    assert complete_state(client) == before


def test_table_exact_date_no_fallback_and_balanced_conversion(client, journal_accounts):
    rate(client, date='2026-03-10')
    before = complete_state(client)
    with pytest.raises(BookflowError) as error:
        post(client, journal_accounts)
    assert error.value.code == 'E_NO_EXCHANGE_RATE'
    assert error.value.details == {'date': DATE, 'from_currency': 'JPY', 'to_currency': 'USD'}
    assert complete_state(client) == before
    rate(client)
    saved = post(client, journal_accounts)
    assert saved['total_minor_units'] == 1595
    assert all(line['rate_source'] == 'table:manual' for line in saved['revision']['lines'])
    before = complete_state(client)
    with pytest.raises(BookflowError) as error:
        client.journal.post(date=DATE, lines=[
            {'account': journal_accounts[0], 'side': 'debit', 'amount': '2345 JPY'},
            {'account': journal_accounts[1], 'side': 'credit', 'amount': '15.94'}], company=COMPANY)
    assert error.value.code == 'E_UNBALANCED_ENTRY' and complete_state(client) == before


def test_multiple_table_currencies_and_manual_scope(client, journal_accounts):
    rate(client, '0.01'); rate(client, '1', currency='EUR')
    args = {'date': DATE, 'lines': [
        {'account': journal_accounts[0], 'side': 'debit', 'amount': '100 JPY'},
        {'account': journal_accounts[1], 'side': 'credit', 'amount': '1.00 EUR'}]}
    result = client.journal.post(**args, company=COMPANY)
    assert result['total_minor_units'] == 100
    before = complete_state(client)
    with pytest.raises(BookflowError) as error:
        client.journal.post(**args, rate='1', company=COMPANY)
    assert error.value.code == 'E_VALIDATION' and complete_state(client) == before


def test_history_date_metadata_and_explicit_reprice(client, journal_accounts, monkeypatch):
    initial = rate(client)
    field = definition(client, 'Foreign work order')
    first = post(client, journal_accounts)
    captured = copy.deepcopy(facts(first))
    rate(client, '0.0070', version=initial['version'])
    previous = first
    for patch in ({'memo': 'same original'}, {'date': '2026-04-01'},
                  {'refresh_defaults': True}, {'custom_fields': {field['id']: 'JP-001'}},
                  {'lines': entered(first)}):
        previous = client.journal.update(journal=first['id'], expected_version=previous['version'],
                                         company=COMPANY, **patch)
        assert facts(previous) == captured
    before = complete_state(client)
    with pytest.raises(BookflowError) as error:
        client.journal.update(journal=first['id'], expected_version=previous['version'],
                              refresh_rates=True, company=COMPANY)
    assert error.value.code == 'E_NO_EXCHANGE_RATE' and complete_state(client) == before
    rate(client, '0.0070', date='2026-04-01')
    repriced = client.journal.update(journal=first['id'], expected_version=previous['version'],
                                    refresh_rates=True, company=COMPANY)
    assert repriced['total_minor_units'] == 1642
    assert facts(client.journal.show(journal=first['id'], revision_number=1, company=COMPANY)) == captured
    monkeypatch.setattr(rates, 'lookup', lambda *a: (_ for _ in ()).throw(AssertionError('void looked up a rate')))
    void = client.journal.void(journal=first['id'], expected_version=repriced['version'],
                              reason='Void foreign witness', company=COMPANY)
    assert facts(void) == facts(repriced)
    _, legs, _ = ledger(client, first['id'])
    assert sum(leg['debit_minor_units'] for leg in legs) == sum(leg['credit_minor_units'] for leg in legs)
    for account in journal_accounts:
        assert sum(leg['debit_minor_units']-leg['credit_minor_units'] for leg in legs if leg['account_id'] == account) == 0


def test_override_source_change_new_original_and_domestic_replacement(client, journal_accounts):
    rate(client)
    first = post(client, journal_accounts)
    changed = client.journal.update(journal=first['id'], expected_version=1, rate='000.006800', company=COMPANY)
    assert changed['version'] == 2 and changed['total_minor_units'] == 1595
    assert all(line['rate_source'] == 'manual' for line in changed['revision']['lines'])
    unchanged = client.journal.update(journal=first['id'], expected_version=2, rate='0.0068', company=COMPANY)
    assert not unchanged['changed'] and unchanged['version'] == 2
    changed = client.journal.update(journal=first['id'], expected_version=2,
        lines=entered(changed, '2500 JPY'), company=COMPANY)
    assert changed['version'] == 3 and changed['total_minor_units'] == 1700
    assert all(line['rate_source'] == 'table:manual' for line in changed['revision']['lines'])
    domestic = client.journal.update(journal=first['id'], expected_version=3,
        lines=entered(changed, '17.00'), company=COMPANY)
    assert all(line['original_amount'] is None and line['rate_used'] is None for line in domestic['revision']['lines'])


def test_writer_rate_and_retry_receipt_are_decisive(client, journal_accounts, monkeypatch):
    row = rate(client)
    original = journals.apply
    def change_rate(plan, ctx, s):
        s.company.conn.execute(schema.exchange_rates.update().where(schema.exchange_rates.c.id == row['id']).values(rate='0.007'))
        return original(plan, ctx, s)
    monkeypatch.setattr(registry.get('journal post'), 'apply', change_rate)
    saved = post(client, journal_accounts, idempotency_key='rate-writer')
    assert saved['total_minor_units'] == 1642
    assert saved['revision']['lines'][0]['rate_used'] == '0.007'
    replay = post(client, journal_accounts, idempotency_key='rate-writer')
    assert replay['id'] == saved['id'] and replay['total_minor_units'] == 1642


@pytest.mark.parametrize('damage', ['original', 'source', 'erase', 'date', 'account'])
def test_coherent_foreign_plan_cannot_rewrite_command(client, journal_accounts, monkeypatch, damage):
    rate(client); rate(client, date='2026-03-12')
    before = complete_state(client)
    original = journals.prepare
    def corrupt(s, ctx, inp, operation):
        plan = original(s, ctx, inp, operation)
        if s.company.write_transaction:
            pending = plan.data['pending']
            other = pending['document_lines'][-1].copy()
            for row in pending['document_lines'] + pending['posting_lines']:
                if damage == 'original': row['original_minor_units'] += 1  # same rounded home amount
                elif damage == 'source': row['rate_source'] = 'manual'
                elif damage == 'erase': row.update(dict.fromkeys(journals.FACTS))
                elif damage == 'account':
                    row['account_id'] = other['account_id']
                    row['account_snapshot'] = other['account_snapshot']
            if damage == 'date':
                pending['transaction_revisions'][0]['date'] = '2026-03-12'
                pending['posting_batches'][0]['effective_date'] = '2026-03-12'
        return plan
    monkeypatch.setattr(journals, 'prepare', corrupt)
    with pytest.raises(BookflowError) as error:
        post(client, journal_accounts, idempotency_key='foreign-corrupt')
    assert error.value.code == 'E_INTERNAL' and complete_state(client) == before
