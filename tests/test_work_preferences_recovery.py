"""Finite disabled-progress recovery from real, independently counted fragmentation."""
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_customer_work_lifecycle import run
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_progress_billing_lifecycle import current, scope_line
from tests.test_work_preferences import settings


@pytest.mark.timeout(600)
def test_201_free_spans_recovery_then_final_remaining_closure(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], quantity='402', net_amount='4.02')])
    installments = []
    for index in range(401):
        installments.append(bill(client, dict(source, version=2+index), f'fragment-{index}',
            selections=[dict(line_id=scope_line(source), quantity='1')]))
    for index in range(0,401,2):
        client.run('invoice void', dict(invoice=installments[index]['id'], expected_version=1),
                   company=COMPANY, reason='Create independently counted free intervals')
    # Odd numbered unit intervals remain occupied: 200 separated occupied spans,
    # 201 free gaps, with the last gap two units wide. Earliest 200 gaps cost 200 cents.
    settings(client, progress_billing_enabled=False, close_estimates_after_billing=True)
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    line = state['lines'][0]
    assert line['requires_bounded_recovery'] and line['recommended_net_amount']['minor_units'] == 200
    assert state['remaining_net_minor_units'] == 202
    source = current(client, source)
    values = dict(estimate=source['id'], expected_version=source['version'], date='2026-01-13',
        conversion_key='bounded-recovery', selections=[dict(line_id=scope_line(source), net_amount='2.00')])
    preview = client.run('estimate invoice', values, company=COMPANY, dry_run=True)
    assert not preview['source_effect']['automatically_closed']
    first = client.run('estimate invoice', dict(values, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert first['subtotal_minor_units'] == 200 and len(first['revision']['billing_sources'][0]['allocation_proof']['spans']) == 200
    now = current(client, source)
    stale = dict(values, expected_version=now['version'], conversion_key='stale-recovery')
    for fingerprint, code in [(None, 'E_FEATURE_DISABLED'), (preview['facts_fingerprint'], 'E_PREVIEW_STALE')]:
        with pytest.raises(BookflowError) as caught:
            client.run('estimate invoice', dict(stale, **({'expected_facts_fingerprint':fingerprint} if fingerprint else {})), company=COMPANY)
        assert caught.value.code == code
        if fingerprint:
            assert caught.value.details['consumption_changes'] and caught.value.details['preference_changes']
    final = bill(client, now, 'finish-fragments', line_ids=[scope_line(source)])
    assert final['subtotal_minor_units'] == 2 and final['source_effect']['automatically_closed']
