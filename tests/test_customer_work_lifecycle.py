"""Public work lifecycle, irreversible lineage identity and zero posting effects."""
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path, hosted  # noqa: F401


def run(client, noun, verb, **data):
    return client.run(noun + ' ' + verb, data, company=COMPANY, reason='Customer work test')


def make(client, sale, noun='estimate', **extra):
    values = dict(date='2026-01-12', title='Replace kitchen tap', customer=sale['customer'],
                  lines=[dict(item=sale['item'], quantity='2')])
    values.update(extra)
    return run(client, noun, 'create', **values)


def test_estimate_immutable_edit_and_noop(client, sale):
    first = make(client, sale)
    assert first['gross_minor_units'] == 2468
    assert first['revision']['lines'][0]['facts']['pricing_basis'] == 'catalog'
    unchanged = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1)
    assert unchanged['version'] == 1 and not unchanged['changed']
    changed = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, memo='Site visit arranged')
    assert changed['version'] == 2
    old = run(client, 'estimate', 'show', estimate=first['id'], revision_number=1)
    assert old['revision']['facts']['memo'] is None
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, title='Stale title')
    assert caught.value.code == 'E_VERSION_CONFLICT'
    history = run(client, 'estimate', 'history', estimate=first['id'])
    assert [row['revision_number'] for row in history['items']] == [1, 2]


def test_linked_chain_permanent_replay_completion_and_no_ledger_effect(client, sale):
    with open_database(database_path(client), writable=False) as db:
        before = {table.name: db.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                  for table in (c.transactions, c.posting_batches, c.posting_lines)}
    proposal = make(client, sale, 'proposal')
    estimate = run(client, 'proposal', 'estimate', proposal=proposal['id'], expected_version=1,
                   conversion_key='proposal estimate once', date='2026-01-13')
    accepted = run(client, 'estimate', 'update', estimate=estimate['id'], expected_version=1,
                   status='accepted', decision_note='Customer accepted by telephone')
    data = dict(estimate=estimate['id'], expected_version=accepted['version'],
                conversion_key='accepted work once', date='2026-01-14')
    order = run(client, 'estimate', 'work-order', **data)
    assert order['revision']['lines'][0]['root_document_id'] == estimate['id']
    completed = run(client, 'work-order', 'complete', work_order=order['id'], expected_version=1,
                    actual_start='2026-01-14T10:00:00Z', actual_end='2026-01-14T11:00:00Z')
    assert completed['status'] == 'complete'
    assert completed['revision']['lines'][0]['completed_quantity'] == '2'
    replay = run(client, 'estimate', 'work-order', **data)
    assert replay['id'] == order['id'] and replay['status'] == 'complete' and replay['idempotent_replay']
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'work-order', **dict(data, date='2026-01-15'))
    assert caught.value.code == 'E_CONVERSION_KEY_REUSED'
    with open_database(database_path(client), writable=False) as db:
        after = {table.name: db.conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
                 for table in (c.transactions, c.posting_batches, c.posting_lines)}
    assert after == before


def test_alternatives_and_captured_manual_amount_prices(client, sale):
    first = make(client, sale)
    alternative = run(client, 'estimate', 'copy', estimate=first['id'], expected_version=1, date='2026-01-13')
    assert first['estimate_group_id'] == alternative['estimate_group_id']
    run(client, 'estimate', 'update', estimate=first['id'], expected_version=1,
        status='accepted', decision_note='Accepted option one')
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'update', estimate=alternative['id'], expected_version=1,
            status='accepted', decision_note='Conflicting alternative')
    assert caught.value.code == 'E_WORK_DEPENDENCY'
    item = alternative['revision']['lines'][0]
    changed = run(client, 'estimate', 'update', estimate=alternative['id'], expected_version=1,
        lines=[dict(item=sale['item'], line_id=item['line_id'], net_amount='10.01')])
    assert changed['net_minor_units'] == 1001
    assert changed['revision']['lines'][0]['unit_price'] is None


def test_preview_stale_defaults_and_current_preview_execution(client, sale):
    data = dict(date='2026-01-12', title='Previewed quote', customer=sale['customer'], lines=[dict(item=sale['item'])])
    preview = client.run('estimate create', data, company=COMPANY, dry_run=True)
    item = client.run('item show', dict(item=sale['item']), company=COMPANY)
    client.run('item update', dict(item=sale['item'], expected_version=item['version'], price='15.00'), company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'create', **data, expected_facts_fingerprint=preview['facts_fingerprint'])
    assert caught.value.code == 'E_PREVIEW_STALE'
    preview = client.run('estimate create', data, company=COMPANY, dry_run=True)
    created = run(client, 'estimate', 'create', **data, expected_facts_fingerprint=preview['facts_fingerprint'])
    assert created['net_minor_units'] == 1500


def test_complete_preview_time_is_not_a_stale_master_fact(client, sale):
    order = make(client, sale, 'work-order')
    data = dict(work_order=order['id'], expected_version=1, actual_start='2026-01-14T10:00:00Z')
    preview = client.run('work-order complete', data, company=COMPANY, dry_run=True)
    completed = run(client, 'work-order', 'complete', **data, expected_facts_fingerprint=preview['facts_fingerprint'])
    assert completed['status'] == 'complete'
    no_change = run(client, 'work-order', 'complete', work_order=order['id'], expected_version=2)
    assert not no_change['changed']


def test_completed_work_must_reopen_before_scope_or_quantity_changes(client, sale):
    order = make(client, sale, 'work-order')
    complete = run(client, 'work-order', 'complete', work_order=order['id'], expected_version=1,
        actual_start='2026-01-12T10:00:00Z', actual_end='2026-01-12T12:00:00Z')
    with pytest.raises(BookflowError) as caught:
        run(client, 'work-order', 'update', work_order=order['id'], expected_version=2,
            lines=[dict(item=sale['item'], line_id=complete['revision']['lines'][0]['line_id'], completed_quantity='1')])
    assert caught.value.code == 'E_VALIDATION'
    reopened = run(client, 'work-order', 'update', work_order=order['id'], expected_version=2, status='in_progress')
    assert reopened['revision']['facts']['actual_end'] is None
    assert reopened['revision']['lines'][0]['completed_quantity'] == '2'
    revised = run(client, 'work-order', 'update', work_order=order['id'], expected_version=3,
        lines=[dict(item=sale['item'], line_id=complete['revision']['lines'][0]['line_id'], completed_quantity='1')])
    assert revised['revision']['lines'][0]['completed_quantity'] == '1'


def test_retired_and_foreign_line_identities_cannot_return(client, sale):
    order = make(client, sale, 'work-order')
    identity = order['revision']['lines'][0]['line_id']
    run(client, 'work-order', 'update', work_order=order['id'], expected_version=1, lines=[])
    with pytest.raises(BookflowError) as caught:
        run(client, 'work-order', 'update', work_order=order['id'], expected_version=2,
            lines=[dict(item=sale['item'], line_id=identity)])
    assert caught.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as caught:
        make(client, sale, 'work-order', lines=[dict(item=sale['item'], line_id=identity)])
    # Use direct create so no test helper argument can mask the public rejection.
    assert caught.value.code == 'E_VALIDATION'


def test_inactive_sources_copy_with_captured_prices_and_visible_warning(client, sale):
    estimate = make(client, sale)
    item = client.run('item show', dict(item=sale['item']), company=COMPANY)
    client.run('item deactivate', dict(item=sale['item'], expected_version=item['version']), company=COMPANY)
    copied = run(client, 'estimate', 'copy', estimate=estimate['id'], expected_version=1, date='2026-01-13')
    assert copied['net_minor_units'] == estimate['net_minor_units']
    assert any('inactive historical reference' in value for value in copied['warnings'])
    old = estimate['revision']['lines'][0]
    assert copied['revision']['lines'][0]['facts'] == old['facts']
    assert copied['revision']['lines'][0]['root_line_id'] != old['root_line_id']


def test_converted_header_and_quoted_lines_are_frozen_but_operations_are_editable(client, sale):
    estimate = make(client, sale)
    accepted = run(client, 'estimate', 'update', estimate=estimate['id'], expected_version=1,
        status='accepted', decision_note='Customer approval')
    order = run(client, 'estimate', 'work-order', estimate=estimate['id'], expected_version=2,
        conversion_key='quoted scope', date='2026-01-13')
    other = client.customer.create(name='Another customer', company=COMPANY)['id']
    for changes in (dict(customer=other), dict(scope='Replace all pipes'),
                    dict(lines=[dict(item=sale['item'], line_id=order['revision']['lines'][0]['line_id'], quantity='3')])):
        with pytest.raises(BookflowError) as caught:
            run(client, 'work-order', 'update', work_order=order['id'], expected_version=1, **changes)
        assert caught.value.code == 'E_WORK_DEPENDENCY'
    revised = run(client, 'work-order', 'update', work_order=order['id'], expected_version=1,
        status='scheduled', scheduled_start='2026-01-20T11:00:00-06:00', site_address={'line1': 'Service entrance'})
    assert revised['status'] == 'scheduled' and revised['revision']['facts']['scheduled_start'].startswith('2026-01-20T17:00:00')
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'update', estimate=estimate['id'], expected_version=3, status='cancelled')
    assert caught.value.code == 'E_WORK_DEPENDENCY'


def test_acceptance_anchor_survives_memo_and_conversion_revisions(client, sale):
    first = make(client, sale)
    accepted = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1,
        status='accepted', decision_note='Signed scope received')
    anchor = accepted['revision']['id']
    memo = run(client, 'estimate', 'update', estimate=first['id'], expected_version=2, memo='Office copy')
    assert memo['revision']['accepted_revision_id'] == anchor
    run(client, 'estimate', 'work-order', estimate=first['id'], expected_version=3,
        conversion_key='acceptance anchor', date='2026-01-13')
    current = run(client, 'estimate', 'show', estimate=first['id'])
    assert current['revision']['accepted_revision_id'] == anchor and current['version'] == 4


def test_query_filters_inactive_and_stale_watermark(client, sale):
    first = make(client, sale, number='SEARCH-1')
    second = make(client, sale, number='SEARCH-2')
    page = run(client, 'estimate', 'query', customer=sale['customer'], title='kitchen', number='SEARCH',
               minimum_net='20.00', maximum_net='30.00', limit=1)
    assert len(page['items']) == 1 and page['has_more']
    next_page = run(client, 'estimate', 'query', customer=sale['customer'], title='kitchen', number='SEARCH',
                    minimum_net='20.00', maximum_net='30.00', limit=1, cursor=page['next_cursor'])
    assert next_page['items'][0]['id'] != page['items'][0]['id']
    run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, active=False)
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'query', customer=sale['customer'], title='kitchen', number='SEARCH',
            minimum_net='20.00', maximum_net='30.00', limit=1, cursor=page['next_cursor'])
    assert caught.value.code == 'E_QUERY_STALE'
    assert len(run(client, 'estimate', 'query', customer=sale['customer'])['items']) == 1
    assert len(run(client, 'estimate', 'query', customer=sale['customer'], active=None)['items']) == 2


def test_destination_required_custom_values_and_historical_false_clear(client, sale):
    shared = client.run('custom-field create', dict(name='Work approval flag', kind='bool',
        scopes=['proposal', 'estimate', 'work_order']), company=COMPANY)
    required = client.run('custom-field create', dict(name='Dispatch reference', kind='text',
        scopes=['work_order'], required=True), company=COMPANY)
    source = make(client, sale, 'proposal', custom_fields={shared['id']: False})
    quote = run(client, 'proposal', 'estimate', proposal=source['id'], expected_version=1,
        date='2026-01-13', conversion_key='custom estimate')
    source_slot = source['revision']['custom_fields_snapshot'][shared['id']]['value_id']
    dest_slot = quote['revision']['custom_fields_snapshot'][shared['id']]['value_id']
    assert source_slot != dest_slot and quote['revision']['custom_fields_snapshot'][shared['id']]['value'] is False
    run(client, 'estimate', 'update', estimate=quote['id'], expected_version=1,
        status='accepted', decision_note='Confirmed scope')
    data = dict(estimate=quote['id'], expected_version=2, date='2026-01-14', conversion_key='dispatch custom')
    with pytest.raises(BookflowError) as caught:
        run(client, 'estimate', 'work-order', **data)
    assert caught.value.code == 'E_VALIDATION'
    order = run(client, 'estimate', 'work-order', **data, custom_fields={required['id']: 'D-123'})
    assert order['revision']['custom_fields_snapshot'][required['id']]['value'] == 'D-123'
    cleared = run(client, 'work-order', 'update', work_order=order['id'], expected_version=1,
        custom_fields={shared['id']: None})
    assert shared['id'] not in cleared['revision']['custom_fields_snapshot']
    original = run(client, 'work-order', 'show', work_order=order['id'], revision_number=1)
    assert original['revision']['custom_fields_snapshot'][shared['id']]['value'] is False
    restored = run(client, 'work-order', 'update', work_order=order['id'], expected_version=2,
        custom_fields={shared['id']: False})
    assert restored['revision']['custom_fields_snapshot'][shared['id']]['value_id'] == original['revision']['custom_fields_snapshot'][shared['id']]['value_id']


def test_invalid_pending_title_is_rejected_before_audit_or_history(client, sale, monkeypatch):
    from bookflow.company import work
    from tests.test_service_sales_lifecycle import snapshot
    original = work.prepare
    before = snapshot(client)
    def corrupt(*args, **kwargs):
        plan = original(*args, **kwargs)
        if plan.data['changed']:
            plan.data['pending']['work_revisions'][0]['title'] = 'Not the requested title'
        return plan
    monkeypatch.setattr(work, 'prepare', corrupt)
    with pytest.raises(BookflowError) as caught:
        make(client, sale)
    assert caught.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


def test_permanent_conversion_survives_generic_cache_expiration(client, sale):
    proposal = make(client, sale, 'proposal')
    data = dict(proposal=proposal['id'], expected_version=1, date='2026-01-13', conversion_key='permanent after cache')
    created = client.run('proposal estimate', data, company=COMPANY, idempotency_key='temporary cache')
    run(client, 'proposal', 'update', proposal=proposal['id'], expected_version=2, title='New source scope')
    # Empty only the disposable request cache through SQL, simulating maintenance;
    # work lineage is untouched. A later logical retry must still find its result.
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.idempotency_keys.delete())
        db.conn.commit()
    repeated = client.run('proposal estimate', data, company=COMPANY, idempotency_key='new request cache')
    assert repeated['id'] == created['id'] and repeated['idempotent_replay']
    assert repeated['revision']['title'] == 'Replace kitchen tap'


@pytest.mark.parametrize('point', ['work_revisions', 'work_lines', 'work_links', 'audit_entries'])
def test_failed_conversion_rolls_back_source_destination_and_key(client, sale, point):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from tests.test_service_sales_lifecycle import snapshot
    proposal = make(client, sale, 'proposal')
    before = snapshot(client)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO ' + point + ' '):
            raise RuntimeError('interrupted work conversion')
    event.listen(Engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='interrupted work conversion'):
            run(client, 'proposal', 'estimate', proposal=proposal['id'], expected_version=1,
                conversion_key='rollback work', date='2026-01-13')
    finally:
        event.remove(Engine, 'before_cursor_execute', fail)
    assert snapshot(client) == before
    retry = run(client, 'proposal', 'estimate', proposal=proposal['id'], expected_version=1,
        conversion_key='rollback work', date='2026-01-13')
    assert retry['kind'] == 'estimate'


def test_competing_conversion_requests_cannot_duplicate_work(client, sale):
    from concurrent.futures import ThreadPoolExecutor
    estimate = make(client, sale)
    run(client, 'estimate', 'update', estimate=estimate['id'], expected_version=1,
        status='accepted', decision_note='Accepted for dispatch')
    def attempt(key):
        try:
            return run(client, 'estimate', 'work-order', estimate=estimate['id'], expected_version=2,
                conversion_key=key, date='2026-01-13')
        except BookflowError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, ['dispatcher one', 'dispatcher two']))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert [value for value in results if isinstance(value, str)] == ['E_VERSION_CONFLICT']
    assert len(run(client, 'work-order', 'query', customer=sale['customer'])['items']) == 1


def test_readonly_cli_and_cross_company_selectors(client, sale, cli, root):
    from tests.conftest import make_actor, as_user
    first = make(client, sale)
    company_id = client.company.show(company=COMPANY)['id']
    make_actor(root, 'work-reader', company_role=(company_id, 'readonly'))
    reader = as_user(root, 'work-reader')
    shown = reader.run('estimate show', dict(estimate=first['id']), company=COMPANY)
    assert cli.json('estimate', 'show', first['id'], '--company', COMPANY) == shown
    with pytest.raises(BookflowError) as caught:
        run(reader, 'estimate', 'update', estimate=first['id'], expected_version=1, title='Unauthorized')
    assert caught.value.code == 'E_PERMISSION'
    other = client.company.new(legal_name='Work isolation sibling', display_name='Work isolation sibling',
        home_currency='USD', chart='general', organization='Demo Holdings LLC')
    with pytest.raises(BookflowError) as caught:
        client.run('estimate show', dict(estimate=first['id']), company='Work isolation sibling')
    assert caught.value.code == 'E_RECORD_NOT_FOUND'


def test_http_library_cli_work_parity(hosted, client, cli):
    cid = hosted.company_id
    income = hosted.ok('account.create', dict(name='Work parity income', type='income'), company=cid)['id']
    customer = hosted.ok('customer.create', dict(name='Work parity customer'), company=cid)['id']
    exempt = next(row['id'] for row in hosted.ok('sales-tax-code.list', {}, company=cid)['items'] if not row['taxable'])
    item = hosted.ok('item.create', dict(name='Work parity service', type='service', sales_enabled=True,
        income_account_id=income, description='Service labor', price='10.00', sales_tax_code_id=exempt), company=cid)['id']
    created = hosted.ok('proposal.create', dict(date='2026-01-12', title='Parity scope', customer=customer,
        lines=[dict(item=item, quantity='2', net_amount='10.01')]), company=cid)
    estimate = hosted.ok('proposal.estimate', dict(proposal=created['id'], expected_version=1,
        conversion_key='http scope', date='2026-01-13'), company=cid)
    accepted = hosted.ok('estimate.update', dict(estimate=estimate['id'], expected_version=1,
        status='accepted', decision_note='Accepted over HTTP'), company=cid)
    order = hosted.ok('estimate.work-order', dict(estimate=estimate['id'], expected_version=2,
        conversion_key='http dispatch', date='2026-01-14'), company=cid)
    completed = hosted.ok('work-order.complete', dict(work_order=order['id'], expected_version=1,
        actual_start='2026-01-14T10:00:00Z', actual_end='2026-01-14T11:00:00Z'), company=cid)
    shown = hosted.ok('work-order.show', dict(work_order=order['id']), company=cid)
    assert shown['status'] == 'complete' and shown['net_minor_units'] == 1001
    assert shown == cli.json('work-order', 'show', order['id'], '--company', cid)
    hosted.handle.stop()
    assert shown == client.run('work-order show', dict(work_order=order['id']), company=cid)


def test_source_links_have_bounded_continuation_and_reject_stale_pages(client, sale, monkeypatch):
    from bookflow.company import work
    monkeypatch.setattr(work, 'LINK_PAGE_SIZE', 2)
    proposal = make(client, sale, 'proposal')
    for index in range(3):
        run(client, 'proposal', 'estimate', proposal=proposal['id'], expected_version=index + 1,
            conversion_key=f'alternative page {index}', date='2026-01-13')
    first = run(client, 'proposal', 'show', proposal=proposal['id'])
    assert len(first['links']) == 2 and first['links_has_more'] and first['next_links_cursor']
    second = run(client, 'proposal', 'show', proposal=proposal['id'], links_cursor=first['next_links_cursor'])
    assert len(second['links']) == 1 and not second['links_has_more']
    assert len({row['id'] for row in first['links'] + second['links']}) == 3
    run(client, 'proposal', 'update', proposal=proposal['id'], expected_version=4, memo='Updated office note')
    with pytest.raises(BookflowError) as caught:
        run(client, 'proposal', 'show', proposal=proposal['id'], links_cursor=first['next_links_cursor'])
    assert caught.value.code == 'E_QUERY_STALE'


def test_generic_cached_conversion_refreshes_current_destination(client, sale):
    first = make(client, sale)
    run(client, 'estimate', 'update', estimate=first['id'], expected_version=1,
        status='accepted', decision_note='Confirmed work')
    data = dict(estimate=first['id'], expected_version=2, conversion_key='permanent plus cache', date='2026-01-13')
    created = client.run('estimate work-order', data, company=COMPANY, idempotency_key='cached conversion')
    run(client, 'work-order', 'complete', work_order=created['id'], expected_version=1,
        actual_start='2026-01-13T10:00:00Z', actual_end='2026-01-13T11:00:00Z')
    repeated = client.run('estimate work-order', data, company=COMPANY, idempotency_key='cached conversion')
    assert repeated['id'] == created['id'] and repeated['status'] == 'complete'
    assert repeated['idempotent_replay'] and not repeated['changed']
    assert repeated['version'] == 2
