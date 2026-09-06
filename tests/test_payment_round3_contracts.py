"""Cross-path contracts complement the unchanged independent Gate B witnesses."""
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.core import audit, clock
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_payment_gate_b_oracles import run, receive, allrows
from tests.test_row8_journal import database_path


def test_unchanged_destination_preserves_snapshot_and_posting_activity_policy(client, sale):
    bank = client.account.create(name='Captured receipt bank', type='bank', company=COMPANY)['id']
    paid = run(client, 'payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
        deposit_to=bank, payment_method=method(client), operation_key='retained-bank'))
    captured = run(client, 'payment show', dict(payment=paid['id']))['revision']['profile']['deposit_account']
    run(client, 'account update', dict(account=bank, expected_version=1, name='Renamed receipt bank'))
    run(client, 'payment update', dict(payment=paid['id'], expected_version=1, deposit_to=bank,
        memo='Correct note', operation_key='retained-bank-edit'), reason='Correct note')
    assert run(client, 'payment show', dict(payment=paid['id']))['revision']['profile']['deposit_account'] == captured
    # Existing account lifecycle forbids deactivating a used posting account.
    with pytest.raises(BookflowError) as error:
        run(client, 'account deactivate', dict(account=bank, expected_version=2))
    assert error.value.code == 'E_RECORD_IN_USE'
    inactive = client.account.create(name='Unused inactive bank', type='bank', company=COMPANY)['id']
    run(client, 'account deactivate', dict(account=inactive, expected_version=1))
    unchanged = run(client, 'payment update', dict(payment=paid['id'], expected_version=2, deposit_to=bank,
        operation_key='retained-inactive-bank-noop'), reason='Retain receipt')
    assert not unchanged['changed'] and unchanged['version'] == 2
    before = allrows(client)
    with pytest.raises(BookflowError) as error:
        run(client, 'payment update', dict(payment=paid['id'], expected_version=2, deposit_to=inactive,
            memo='Requires replacement posting', operation_key='retained-inactive-bank-change'), reason='Correct note')
    assert error.value.code == 'E_INACTIVE_REFERENCE' and allrows(client) == before


@pytest.mark.parametrize('field', ['unit_price', 'net_amount', 'price_basis_amount'])
def test_keyed_invoice_money_equivalence_preserves_real_changes(client, sale, field):
    invoice = posted(client, sale['customer'], sale['item'], '1.00', 'TYPED-MONEY')
    args = dict(invoice=invoice['id'], expected_version=1, operation_key='typed-money-edit',
        lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'], item=sale['item'], **{field: '2.00'})])
    saved = run(client, 'invoice update', args, reason='Correct exact charge')
    before = allrows(client)
    args['lines'][0][field] = dict(minor_units=200, currency='USD', amount='2.00')
    replay = run(client, 'invoice update', args, reason='Correct exact charge')
    assert replay['idempotent_replay'] and replay['settlement']['effect'] == saved['settlement']['effect']
    assert allrows(client) == before
    args['lines'][0][field] = dict(minor_units=201, currency='USD')
    with pytest.raises(BookflowError) as error:
        run(client, 'invoice update', args, reason='Correct exact charge')
    assert error.value.code == 'E_PAYMENT_OPERATION_KEY_REUSED' and allrows(client) == before


@pytest.mark.parametrize('verb', ['payment invoices', 'payment suggest', 'payment calculate'])
def test_preparation_lineage_and_unrelated_draft_write(client, sale, verb):
    job = client.customer.create(name='Selection child', parent_id=sale['customer'], company=COMPANY)['id']
    branch = client.customer.create(name='Selection branch', parent_id=sale['customer'], company=COMPANY)['id']
    invoices = [posted(client, job, sale['item'], '1.00', f'LINEAGE-PAGE-{n}') for n in range(2)]
    args = dict(mode='new_receipt', customer=sale['customer'], date='2026-06-02', limit=1)
    if verb != 'payment invoices':
        args['amount'] = '2.00'
    if verb == 'payment suggest':
        args['strategy'] = 'exact_then_oldest'
    if verb == 'payment calculate':
        args['applications'] = dict(mode='inline', items=[dict(invoice=row['id'], expected_version=1, amount='1.00') for row in invoices])
    first = run(client, verb, args)
    assert first['next_cursor'] and first['total_count'] == 2
    run(client, 'payment selection create', dict(mode='new_receipt', customer=sale['customer'], date='2026-06-02'))
    second = run(client, verb, dict(args, cursor=first['next_cursor']))
    assert second['facts_fingerprint'] == first['facts_fingerprint']
    client.customer.update(customer=job, expected_version=1, parent_id=branch, company=COMPANY)
    before = allrows(client)
    with pytest.raises(BookflowError) as error:
        run(client, verb, dict(args, cursor=first['next_cursor']))
    assert error.value.code == 'E_QUERY_STALE' and allrows(client) == before


def test_payment_dependency_fields_are_owned_business_facts(client, sale):
    paid = receive(client, sale)
    baseline = run(client, 'payment show', dict(payment=paid['id']))['settlement_guard']
    run(client, 'payment update', dict(payment=paid['id'], expected_version=1, amount='2.00', date='2026-06-03',
        reference='Check 18', memo='Correct remittance', operation_key='commercial-fields'), reason='Correct receipt')
    changed = run(client, 'payment settlement changes', dict(guard=baseline))
    assert not changed['unknown_history'] and changed['total_count'] == 1
    assert set(changed['items'][0]['fields']) == {'amount', 'date', 'memo', 'reference'}
    assert changed['items'][0]['settlement_fields'] == []
    with pytest.raises(BookflowError) as error:
        run(client, 'payment update', dict(payment=paid['id'], expected_version=1, memo='Stale', operation_key='stale-commercial'), reason='Correct note')
    assert {'amount', 'date', 'memo', 'reference'} <= set(error.value.details['changed_fields'])
    assert 'current_revision_id' not in error.value.details['changed_fields']


def test_foreign_intervening_revision_reports_unknown_without_guess(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '1.00', 'OWNED-EVENT')
    foreign = posted(client, sale['customer'], sale['item'], '2.00', 'FOREIGN-EVENT')
    guard = run(client, 'invoice settlement', dict(invoice=invoice['id']))['settlement_guard']
    run(client, 'invoice update', dict(invoice=invoice['id'], expected_version=1, memo='Changed memo', operation_key='owned-event-edit'), reason='Correct note')
    with sqlite3.connect(database_path(client)) as db:
        key, blob = db.execute("SELECT id,after FROM audit_entries WHERE record_type='transaction' AND record_id=? AND version_after=2", (invoice['id'],)).fetchone()
        after = audit.decode_snapshot(blob)
        after['current_revision_id'] = foreign['revision']['id']
        db.execute('UPDATE audit_entries SET after=? WHERE id=?', (audit.encode_snapshot(after), key))
    changed = run(client, 'payment settlement changes', dict(guard=guard))
    assert changed['unknown_history'] and invoice['id'] in changed['unknown_record_ids']
    assert changed['items'][0]['unknown_fields'] and changed['items'][0]['fields'] is None


def test_effective_projection_metadata_and_no_obligation_are_truthful(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '1.00', 'PROJECTION-BASIS')
    paid = receive(client, sale)
    for verb, ref in [('invoice settlement', dict(invoice=invoice['id'])), ('payment settlement', dict(payment=paid['id']))]:
        current = run(client, verb, ref)
        assert current['projection'] == 'current' and current['projection_basis'] == 'all_committed_current'
        cutoff = run(client, verb, dict(ref, as_of='2026-05-31'))
        assert cutoff['projection'] == 'effective_date' and cutoff['projection_basis'] == 'all_current_knowledge_effective_date'
        assert cutoff['audit_watermark'] == current['audit_watermark']
        assert clock.parse_iso(cutoff['generated_at']) >= clock.parse_iso(current['generated_at'])
        if verb == 'invoice settlement':
            assert cutoff['status'] == 'not_effective' and cutoff['gross_minor_units'] == 0
            assert cutoff['all_committed_current']['status'] == 'unpaid'
        else:
            assert cutoff['received_minor_units'] == 0 and cutoff['all_committed_current']['received_minor_units'] == 100
    run(client, 'invoice void', dict(invoice=invoice['id'], expected_version=1), reason='Cancel invoice')
    before = run(client, 'invoice settlement', dict(invoice=invoice['id'], as_of='2026-05-31'))
    after = run(client, 'invoice settlement', dict(invoice=invoice['id'], as_of='2026-06-01'))
    assert before['status'] == 'not_effective' and after['status'] == 'voided'


def test_custom_text_named_amount_is_not_coerced_to_money(client, sale):
    definition = run(client, 'custom-field create', dict(name='amount', kind='text', scopes=['payment']))['id']
    args = dict(customer=sale['customer'], date='2026-06-02', amount='1.00', payment_method=method(client),
        operation_key='custom-amount-name', custom_fields={definition: 'One envelope, not a decimal'})
    saved = run(client, 'payment receive', args)
    assert run(client, 'payment show', dict(payment=saved['id']))['revision']['custom_fields_snapshot'][definition]['value'] == 'One envelope, not a decimal'
    before = allrows(client)
    replay = run(client, 'payment receive', dict(args, amount=dict(minor_units=100, currency='USD')))
    assert replay['idempotent_replay'] and allrows(client) == before
