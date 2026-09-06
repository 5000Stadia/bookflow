# Independent Gate A witness copied unchanged from the frozen review report.
"""Independent business-data witnesses; no authority changes or disabled guards."""
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots


def run(client, command, data, **kw):
    return client.run(command, data, company=COMPANY, **kw)


def receipt_args(client, sale, invoice=None):
    args = dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
                payment_method=method(client), operation_key='critic-receipt')
    if invoice:
        args['applications'] = dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='1.00')])
    return args


def test_prospective_every_kind_preserves_financial_fingerprint(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-PAGE')
    args = receipt_args(client, sale, invoice)
    before = snapshots(client)
    preview = run(client, 'payment receive', args, dry_run=True)
    mismatches = []
    for descriptor in preview['prospective_pages']:
        page = run(client, 'payment preview items', {k: descriptor[k] for k in ('request','facts_fingerprint','kind')})
        if page['facts_fingerprint'] != preview['facts_fingerprint']:
            mismatches.append(descriptor['kind'])
    assert snapshots(client) == before
    assert mismatches == [], f'Prospective pages replace reviewed financial fingerprint: {mismatches}'


def test_existing_credit_calculate_caps_available_exact_owner(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'CRITIC-CREDIT')
    paid = run(client, 'payment receive', receipt_args(client, sale))
    result = run(client, 'payment calculate', dict(mode='existing_credit', payment=paid['id'],
        date='2026-06-02', amount_mode='selection_total', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount_origin='calculated')])))
    assert result['items'][0]['amount_minor_units'] == 100, result


def test_explicit_calculate_resolves_unspecified_rows_with_default_off(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-EXPLICIT')
    result = run(client, 'payment calculate', dict(mode='new_receipt', customer=sale['customer'],
        date='2026-06-02', amount_mode='selection_total', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1)])))
    assert result['amount'] is not None and result['amount']['minor_units'] == 200, result


def test_explicit_receipt_ignores_unused_auto_apply_preference(client, sale):
    args = receipt_args(client, sale)
    args['deposit_to'] = client.account.create(name='Critic Explicit Bank', type='bank', company=COMPANY)['id']
    preview = run(client, 'payment receive', args, dry_run=True)
    info = run(client, 'company show', {})
    run(client, 'company update', dict(expected_version=info['info_version'], automatically_apply_payments=True))
    paid = run(client, 'payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']))
    assert paid['current']['received_minor_units'] == 100


def test_selected_job_reparent_invalidates_receipt_preview(client, sale):
    branch = client.customer.create(name='Critic Branch', parent_id=sale['customer'], company=COMPANY)['id']
    job = client.customer.create(name='Critic Job', parent_id=sale['customer'], company=COMPANY)['id']
    invoice = posted(client, job, sale['item'], '2.00', 'CRITIC-LINEAGE')
    args = receipt_args(client, sale, invoice)
    preview = run(client, 'payment receive', args, dry_run=True)
    client.customer.update(customer=job, expected_version=1, parent_id=branch, company=COMPANY)
    before = snapshots(client)
    with pytest.raises(BookflowError) as error:
        run(client, 'payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']))
    assert error.value.code == 'E_PREVIEW_STALE'
    assert snapshots(client) == before


def test_second_ar_does_not_disable_system_ar_default(client, sale):
    args = receipt_args(client, sale)
    client.account.create(name='Critic Other Receivables', type='accounts_receivable', company=COMPANY)
    paid = run(client, 'payment receive', args)
    assert paid['current']['received_minor_units'] == 100


def test_permanent_retry_explicit_omissions_money_normalization_and_duplicates(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-RETRY')
    args = receipt_args(client, sale, invoice)
    paid = run(client, 'payment receive', args)
    before = snapshots(client)
    assert run(client, 'payment receive', args)['idempotent_replay']
    normalized = dict(args, amount={'minor_units': 100, 'currency': 'USD'})
    assert run(client, 'payment receive', normalized)['idempotent_replay']
    for patch, code in [(dict(reference=None), 'E_PAYMENT_OPERATION_KEY_REUSED'),
                        (dict(amount='1.01'), 'E_PAYMENT_OPERATION_KEY_REUSED')]:
        with pytest.raises(BookflowError) as error:
            run(client, 'payment receive', dict(args, **patch))
        assert error.value.code == code
    recovered = run(client, 'payment operation show', dict(operation_key=args['operation_key']))
    assert 'reference' not in recovered['request']['input']
    assert snapshots(client) == before
    # Fresh duplicate aliases resolve to the same invoice and must not apply twice.
    dupe = dict(args, operation_key='critic-duplicate', applications=dict(mode='inline', items=[
        dict(invoice=invoice['id'], expected_version=2, amount='0.50'),
        dict(invoice='CRITIC-RETRY', expected_version=2, amount='0.50')]))
    with pytest.raises(BookflowError) as error:
        run(client, 'payment receive', dupe)
    assert error.value.code == 'E_VALIDATION'
    assert snapshots(client) == before


def test_existing_credit_calculation_paging_rejects_funding_change(client, sale):
    first = posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-FUND-A')
    second = posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-FUND-B')
    third = posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-FUND-C')
    paid = run(client, 'payment receive', receipt_args(client, sale))
    args = dict(mode='existing_credit', payment=paid['id'], date='2026-06-02', amount='1.00', limit=1,
        applications=dict(mode='inline', items=[dict(invoice=i['id'], expected_version=1, amount_origin='calculated') for i in (first, second)]))
    page = run(client, 'payment calculate', args)
    run(client, 'payment apply', dict(payment=paid['id'], expected_version=1, date='2026-06-02', operation_key='critic-use-credit',
        applications=dict(mode='inline', items=[dict(invoice=third['id'], expected_version=1, amount='1.00')])))
    with pytest.raises(BookflowError) as error:
        run(client, 'payment calculate', dict(args, cursor=page['next_cursor']))
    assert error.value.code == 'E_QUERY_STALE'


def test_existing_credit_suggestions_do_not_predate_cash(client, sale):
    posted(client, sale['customer'], sale['item'], '2.00', 'CRITIC-EARLY')
    paid = run(client, 'payment receive', receipt_args(client, sale))
    result = run(client, 'payment suggest', dict(mode='existing_credit', payment=paid['id'],
        date='2026-06-01', amount='1.00', strategy='exact_then_oldest'))
    assert result['items'] == [], result
