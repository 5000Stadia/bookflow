"""V1.5 retest R80 day 3: a payment saved from its own preview is not refused as stale.

The trial's agent previewed `payment receive`, then saved the same input with the preview's
facts_fingerprint, a differently worded reason and an idempotency key. The fingerprint covered
the reason, so the save was refused E_PREVIEW_STALE and the agent dropped the guard to get past
it. The guard covers the business input and the records' versions, not the reason or the key.
"""

from tests.test_service_sales_lifecycle import sale, COMPANY  # noqa: F401
from tests.test_payment_receipts import posted, method


def test_a_reworded_reason_and_a_retry_key_keep_the_preview_current(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'REASON-INVOICE')
    args = dict(customer=sale['customer'], date='2026-06-02', amount='100.00', payment_method=method(client),
                operation_key='reason-receive', reference='7781', applications=dict(mode='inline', items=[
                    dict(invoice=invoice['id'], expected_version=1, amount='100.00')]))
    preview = client.run('payment receive', args, company=COMPANY, dry_run=True, reason='Preview the check')

    # A different business input is a different fingerprint.
    other = dict(args, operation_key='reason-other', amount='90.00', applications=dict(mode='inline', items=[
        dict(invoice=invoice['id'], expected_version=1, amount='90.00')]))
    changed = client.run('payment receive', other, company=COMPANY, dry_run=True, reason='Preview the check')
    assert changed['facts_fingerprint'] != preview['facts_fingerprint']

    saved = client.run('payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']),
                       company=COMPANY, reason='Record check 7781', idempotency_key='reason-receive')
    assert saved['facts_fingerprint'] == preview['facts_fingerprint']
    assert saved['current']['applied_minor_units'] == 10000

