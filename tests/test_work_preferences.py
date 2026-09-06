"""Customer work policy, immutable closure, replay and exact preview dependencies."""
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, snapshot, COMPANY
from tests.test_customer_work_lifecycle import make, run
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_progress_billing_lifecycle import current, scope_line


def settings(client, **values):
    return client.run('company update', values, company=COMPANY)


@pytest.mark.parametrize('field', ['estimates_enabled', 'progress_billing_enabled', 'close_estimates_after_billing'])
@pytest.mark.parametrize('value', [None, 'true', 'false', 0, 1])
def test_strict_nonnull_update(client, field, value):
    before = client.run('company show', {}, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        settings(client, **{field: value})
    assert caught.value.code == 'E_VALIDATION'
    assert client.run('company show', {}, company=COMPANY) == before


@pytest.mark.parametrize('verb', ['invoice', 'sales-receipt'])
def test_final_selected_closure_preview_replay_release_reactivate(client, sale, verb):
    source = accepted(client, sale, lines=[dict(item=sale['item'], net_amount='1'),
        dict(item=sale['item'], net_amount='2'), dict(item=sale['item'], net_amount='9', billable=False)])
    acceptance = {k: source['revision'][k] for k in ('accepted_at', 'accepted_by', 'accepted_revision_id')}
    settings(client, estimates_enabled=False, progress_billing_enabled=False, close_estimates_after_billing=True)
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert state['closes_on_remaining_bill'] and state['preferences']['auto_close_effective']
    extra = {}
    if verb == 'sales-receipt':
        extra = dict(deposit_to=client.account.create(name='Preference bank', type='bank', company=COMPANY)['id'],
            payment_method=client.run('payment-method create', dict(name='Preference cash', kind='cash'), company=COMPANY)['id'],
            amount_received='1')
    first = bill(client, source, verb=verb, line_ids=[scope_line(source)], **extra)
    assert not first['source_effect']['automatically_closed'] and first['source_current']['active']
    now = current(client, source)
    if extra:
        extra['amount_received'] = '2'
    values = dict(estimate=source['id'], expected_version=now['version'], date='2026-01-13',
                  conversion_key='finish-preferences', line_ids=[scope_line(source, 1)], **extra)
    before = snapshot(client)
    preview = client.run('estimate ' + verb, values, company=COMPANY, dry_run=True)
    assert snapshot(client) == before
    assert preview['source_effect']['automatically_closed'] and not preview['source_current']['active']
    last = client.run('estimate ' + verb, dict(values, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert last['source_effect'] == preview['source_effect']
    after = current(client, source)
    assert after['version'] == now['version'] + 1 and after['status'] == 'accepted' and not after['active']
    assert {k: after['revision'][k] for k in acceptance} == acceptance
    client.run(verb + ' void', {verb.replace('-', '_'): last['id'], 'expected_version': 1}, company=COMPANY, reason='Release example')
    assert not current(client, source)['active']
    run(client, 'estimate', 'update', estimate=source['id'], expected_version=after['version'], active=True)
    settings(client, progress_billing_enabled=True, close_estimates_after_billing=False)
    replay = client.run('estimate ' + verb, values, company=COMPANY)
    assert replay['id'] == last['id'] and replay['status'] == 'voided'
    assert replay['source_effect'] == last['source_effect'] and replay['source_current']['active']
    assert replay['source_current']['version'] == after['version'] + 1


def test_disabled_estimate_gates_preserve_operational_replay_and_existing_work(client, sale):
    proposal = make(client, sale, noun='proposal')
    values = dict(proposal=proposal['id'], expected_version=1, date='2026-01-12', conversion_key='proposal-preferences')
    converted = run(client, 'proposal', 'estimate', **values)
    existing = accepted(client, sale)
    settings(client, estimates_enabled=False)
    assert run(client, 'proposal', 'estimate', **values)['id'] == converted['id']
    for attempt in (lambda: make(client, sale), lambda: run(client, 'estimate', 'copy', estimate=existing['id'],
            expected_version=existing['version'], date='2026-01-13'),
            lambda: run(client, 'proposal', 'estimate', **dict(values, expected_version=2, conversion_key='new-key'))):
        with pytest.raises(BookflowError) as caught:
            attempt()
        assert caught.value.code == 'E_FEATURE_DISABLED'
        assert caught.value.details['setting'] == 'estimates_enabled'
    order = run(client, 'estimate', 'work-order', estimate=existing['id'], expected_version=existing['version'],
                date='2026-01-13', conversion_key='order-preferences')
    assert bill(client, order, noun='work-order', percent='25')['subtotal_minor_units'] == 617


@pytest.mark.parametrize('mode', ['percent', 'quantity', 'net_amount', 'rebill_allocation_id'])
def test_disabled_modes_and_recovery_candidate_precedence(client, sale, mode):
    source = accepted(client, sale)
    settings(client, progress_billing_enabled=False)
    value = '25' if mode == 'percent' else scope_line(source) if mode == 'rebill_allocation_id' else '1'
    selection = dict(selections=[dict(line_id=scope_line(source), **{mode: value})])
    with pytest.raises(BookflowError) as caught:
        bill(client, source, **selection)
    assert caught.value.code == 'E_FEATURE_DISABLED'
    with pytest.raises(BookflowError) as caught:
        bill(client, source, expected_facts_fingerprint='0' * 64, **selection)
    assert caught.value.code == ('E_PREVIEW_STALE' if mode == 'net_amount' else 'E_FEATURE_DISABLED')
    assert caught.value.details['preference_changes'][0]['field'] == 'progress_billing_enabled'


@pytest.mark.parametrize('noun', ['estimate', 'work-order'])
def test_fingerprint_excludes_irrelevant_policy_and_attributes_actual_edit(client, sale, noun):
    source = accepted(client, sale) if noun == 'estimate' else make(client, sale, noun=noun)
    values = {noun.replace('-', '_'): source['id'], 'expected_version': source['version'],
              'date': '2026-01-13', 'conversion_key': 'policy-preview'}
    command = noun + ' invoice'
    preview = client.run(command, values, company=COMPANY, dry_run=True)
    settings(client, estimates_enabled=False, close_estimates_after_billing=True)
    unchanged = client.run(command, values, company=COMPANY, dry_run=True)
    assert preview['facts_fingerprint'] == unchanged['facts_fingerprint']
    changed = settings(client, progress_billing_enabled=False)
    event = client.run('audit list', dict(record_type='company_info', limit=1), company=COMPANY)['items'][0]['id']
    settings(client, contact_name='Unrelated edit')
    with pytest.raises(BookflowError) as caught:
        client.run(command, dict(values, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert caught.value.code == 'E_PREVIEW_STALE'
    entries = caught.value.details['preference_changes']
    assert {v['field'] for v in entries} == ({'progress_billing_enabled', 'close_estimates_after_billing'} if noun == 'estimate' else {'progress_billing_enabled'})
    assert entries[0]['audit_event_id'] == event
    fresh = client.run(command, values, company=COMPANY, dry_run=True)
    assert fresh['source_effect']['automatically_closed'] == (noun == 'estimate')


@pytest.mark.parametrize('required', [False, True])
def test_independent_closure_validator_rejects_resolver_fault(client, sale, monkeypatch, required):
    from bookflow.company import work_preferences
    source = accepted(client, sale)
    settings(client, progress_billing_enabled=False, close_estimates_after_billing=required)
    before = snapshot(client)
    monkeypatch.setattr(work_preferences, 'closes', lambda *args: not required)
    with pytest.raises(BookflowError) as caught:
        bill(client, source)
    assert caught.value.code == 'E_INTERNAL'
    assert snapshot(client) == before
    assert current(client, source)['version'] == source['version']


@pytest.mark.parametrize('failure', ['after_persistence', 'closed_date', 'receipt_total'])
def test_required_closure_failures_roll_back_entire_company(client, sale, monkeypatch, failure):
    from bookflow.company import billing
    source = accepted(client, sale)
    settings(client, progress_billing_enabled=False, close_estimates_after_billing=True)
    extra, verb = {}, 'invoice'
    if failure == 'closed_date':
        settings(client, closing_date='2026-01-31')
    if failure == 'receipt_total':
        verb = 'sales-receipt'
        extra = dict(deposit_to=client.account.create(name='Rollback bank', type='bank', company=COMPANY)['id'],
            payment_method=client.run('payment-method create', dict(name='Rollback cash', kind='cash'), company=COMPANY)['id'], amount_received='1')
    if failure == 'after_persistence':
        persist = billing.persist
        def fail(*args, **kwargs):
            persist(*args, **kwargs)
            raise BookflowError('E_INTERNAL', message='Injected after complete persistence')
        monkeypatch.setattr(billing, 'persist', fail)
    before = snapshot(client)
    with pytest.raises(BookflowError) as caught:
        bill(client, source, verb=verb, **extra)
    assert caught.value.code == {'after_persistence':'E_INTERNAL', 'closed_date':'E_PERIOD_CLOSED', 'receipt_total':'E_VALIDATION'}[failure]
    assert snapshot(client) == before


def test_committed_partial_key_replays_after_disable_but_new_key_cannot(client, sale):
    source = accepted(client, sale)
    values = dict(estimate=source['id'], expected_version=2, date='2026-01-13', conversion_key='old-partial', percent='25')
    first = client.run('estimate invoice', values, company=COMPANY, idempotency_key='old-partial-cache')
    settings(client, progress_billing_enabled=False, estimates_enabled=False, close_estimates_after_billing=True)
    before = snapshot(client)
    for cache in ({}, dict(idempotency_key='old-partial-cache')):
        replay = client.run('estimate invoice', values, company=COMPANY, **cache)
        assert replay['id'] == first['id'] and not replay['source_effect']['automatically_closed']
    assert snapshot(client) == before
    with pytest.raises(BookflowError) as caught:
        client.run('estimate invoice', dict(values, percent='50'), company=COMPANY)
    assert caught.value.code == 'E_CONVERSION_KEY_REUSED'
    with pytest.raises(BookflowError) as caught:
        client.run('estimate invoice', dict(values, expected_version=3, conversion_key='new-partial'), company=COMPANY)
    assert caught.value.code == 'E_FEATURE_DISABLED'
