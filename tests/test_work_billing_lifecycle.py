"""Public billing, permanent replay and corrections across shared work roots."""
import pytest
from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import hosted  # noqa: F401
from tests.test_customer_work_lifecycle import make, run


def accepted(client, sale, **extra):
    created = make(client, sale, **extra)
    return run(client, 'estimate', 'update', estimate=created['id'], expected_version=1,
        status='accepted', decision_note='Customer accepted the scope')


def bill(client, source, key='first', noun='estimate', verb='invoice', **extra):
    return run(client, noun, verb, **{noun.replace('-', '_'): source['id']},
        expected_version=source['version'], conversion_key=key, date='2026-01-13', **extra)


def test_invoice_full_replay_void_and_rebill(client, sale):
    source = accepted(client, sale)
    first = bill(client, source)
    assert first['total_minor_units'] == 2468
    assert len(first['revision']['billing_sources']) == 1
    assert bill(client, source)['id'] == first['id']
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert state['source_version'] == 3
    assert state['lines'][0]['billed_quantity'] == '2'
    assert state['remaining_net_minor_units'] == 0
    client.run('invoice void', dict(invoice=first['id'], expected_version=1), company=COMPANY, reason='Cancelled invoice')
    assert bill(client, source)['status'] == 'voided'
    current = run(client, 'estimate', 'show', estimate=source['id'])
    second = bill(client, current, 'rebill')
    assert second['id'] != first['id']
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 2468


def test_amount_invoice_correction_and_source_freeze(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], quantity='2', net_amount='10.01')])
    first = bill(client, source)
    line = first['revision']['lines'][0]
    assert line['unit_price'] is None and line['pricing_basis'] == 'amount'
    assert line['net_minor_units'] == 1001
    corrected = client.run('invoice update', dict(invoice=first['id'], expected_version=1, memo='Invoice note'), company=COMPANY)
    assert corrected['version'] == 2 and corrected['revision']['billing_sources']
    noop = client.run('invoice update', dict(invoice=first['id'], expected_version=2), company=COMPANY)
    assert not noop['changed'] and noop['version'] == 2
    with pytest.raises(BookflowError) as err:
        client.run('invoice update', dict(invoice=first['id'], expected_version=2,
            lines=[dict(item=sale['item'], line_id=line['line_id'], quantity='3')]), company=COMPANY)
    assert err.value.code == 'E_WORK_DEPENDENCY'
    with pytest.raises(BookflowError) as err:
        run(client, 'estimate', 'update', estimate=source['id'], expected_version=3, title='Different agreement')
    assert err.value.code == 'E_WORK_DEPENDENCY'


def test_billed_estimate_work_order_shares_consumption(client, sale):
    source = accepted(client, sale)
    first = bill(client, source)
    wo = run(client, 'estimate', 'work-order', estimate=source['id'], expected_version=3,
        date='2026-01-14', conversion_key='make-work')
    state = run(client, 'work-order', 'billing', work_order=wo['id'])
    assert state['lines'][0]['destination_id'] == first['id']
    with pytest.raises(BookflowError) as err:
        bill(client, wo, 'double', noun='work-order')
    assert err.value.code == 'E_WORK_DEPENDENCY'
    old = run(client, 'estimate', 'show', estimate=source['id'])
    with pytest.raises(BookflowError) as err:
        bill(client, old, 'ancestor')
    assert err.value.code == 'E_WORK_DEPENDENCY'


def test_zero_nonbillable_selection_and_line_release(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item'], net_amount='10.01'),
        dict(item=sale['item'], unit_price='0'), dict(item=sale['item'], unit_price='2', billable=False)])
    ids = [row['line_id'] for row in source['revision']['lines']]
    first = bill(client, source, line_ids=[ids[0]])
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert [line['state'] for line in state['lines']] == ['billed', 'no_charge', 'nonbillable']
    assert not state['can_invoice']
    # Remove the linked line and keep an independent actual-sale line.
    edited = client.run('invoice update', dict(invoice=first['id'], expected_version=1,
        lines=[dict(item=sale['item'], unit_price='5')]), company=COMPANY)
    assert not edited['revision']['billing_sources']
    assert run(client, 'estimate', 'billing', estimate=source['id'])['remaining_net_minor_units'] == 1001


@pytest.mark.parametrize('noun', ['estimate', 'work-order'])
def test_paid_receipt_requires_exact_received_amount(client, sale, noun):
    source = accepted(client, sale) if noun == 'estimate' else make(client, sale, noun='work-order')
    bank = client.account.create(name='Billing cash bank', type='bank', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Billing cash', kind='cash'), company=COMPANY)['id']
    with pytest.raises(BookflowError) as err:
        bill(client, source, noun=noun, verb='sales-receipt', deposit_to=bank, payment_method=method, amount_received='1')
    assert err.value.code == 'E_VALIDATION'
    receipt = bill(client, source, noun=noun, verb='sales-receipt', deposit_to=bank, payment_method=method, amount_received='24.68')
    assert receipt['type'] == 'sales_receipt' and receipt['total_minor_units'] == 2468
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == 0
    changed = client.run('sales-receipt update', dict(sales_receipt=receipt['id'], expected_version=1, memo='Payment note'), company=COMPANY)
    assert changed['revision']['billing_sources']
    client.run('sales-receipt void', dict(sales_receipt=receipt['id'], expected_version=2), reason='Return this sale', company=COMPANY)
    assert run(client, noun, 'billing', **{noun.replace('-', '_'): source['id']})['remaining_net_minor_units'] == 2468


def test_generic_cache_current_receipt_and_expiration(client, sale):
    import sqlalchemy as sa
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    from tests.test_row8_journal import database_path
    source = accepted(client, sale)
    data = dict(estimate=source['id'], expected_version=source['version'], conversion_key='durable', date='2026-01-13')
    first = client.run('estimate invoice', data, company=COMPANY, idempotency_key='cache')
    client.run('invoice void', dict(invoice=first['id'], expected_version=1), company=COMPANY, reason='Void witness')
    repeated = client.run('estimate invoice', data, company=COMPANY, idempotency_key='cache')
    assert repeated['id'] == first['id'] and repeated['status'] == 'voided'
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.idempotency_keys.delete())
        db.conn.commit()
    assert client.run('estimate invoice', data, company=COMPANY)['id'] == first['id']
    with pytest.raises(BookflowError) as err:
        client.run('estimate invoice', dict(data, date='2026-01-14'), company=COMPANY)
    assert err.value.code == 'E_CONVERSION_KEY_REUSED'


@pytest.mark.parametrize('financial_first', [False, True])
def test_durable_keys_are_shared_with_operational_conversions(client, sale, financial_first):
    source = accepted(client, sale)
    proposal = make(client, sale, noun='proposal')
    financial = lambda: bill(client, source, 'shared')
    operational = lambda: run(client, 'proposal', 'estimate', proposal=proposal['id'], expected_version=1,
        date='2026-01-13', conversion_key='shared')
    first, second = (financial, operational) if financial_first else (operational, financial)
    first()
    with pytest.raises(BookflowError) as err:
        second()
    assert err.value.code == 'E_CONVERSION_KEY_REUSED'


def test_preview_stales_when_a_different_sale_releases_work(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item']), dict(item=sale['item'])])
    first = bill(client, source, line_ids=[source['revision']['lines'][0]['line_id']])
    current = run(client, 'estimate', 'show', estimate=source['id'])
    data = dict(estimate=current['id'], expected_version=current['version'], conversion_key='remainder', date='2026-01-13')
    preview = client.run('estimate invoice', data, company=COMPANY, dry_run=True)
    assert preview['total_minor_units'] == 1234
    client.run('invoice void', dict(invoice=first['id'], expected_version=1), company=COMPANY, reason='Release one root')
    with pytest.raises(BookflowError) as err:
        client.run('estimate invoice', dict(data, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert err.value.code == 'E_PREVIEW_STALE'
    posted = client.run('estimate invoice', data, company=COMPANY)
    assert posted['total_minor_units'] == 2468


@pytest.mark.parametrize('point', ['work_revisions', 'work_lines', 'work_billing_conversions',
    'work_billing_allocations', 'posting_lines', 'audit_entries'])
def test_billing_failure_rolls_back_source_sale_key_and_audit(client, sale, point):
    from tests.test_service_sales_lifecycle import snapshot
    from tests.test_row8_journal import database_path
    from bookflow.storage.engine import open_database
    source = accepted(client, sale)
    with open_database(database_path(client), writable=True) as db:
        db.raw.execute(f"CREATE TRIGGER fail_billing BEFORE INSERT ON {point} BEGIN SELECT RAISE(ABORT, 'injected billing failure'); END")
        db.conn.commit()
    before = snapshot(client)
    with pytest.raises(BookflowError):
        bill(client, source)
    assert snapshot(client) == before
    with open_database(database_path(client), writable=True) as db:
        db.raw.execute('DROP TRIGGER fail_billing')
        db.conn.commit()
    assert bill(client, source)['total_minor_units'] == 2468


def test_source_economics_unfreeze_only_when_consumption_released(client, sale):
    source = accepted(client, sale)
    first = bill(client, source)
    line = source['revision']['lines'][0]
    values = dict(estimate=source['id'], expected_version=3,
        lines=[dict(item=sale['item'], line_id=line['line_id'], quantity='3')])
    with pytest.raises(BookflowError) as err:
        run(client, 'estimate', 'update', **values)
    assert err.value.code == 'E_WORK_DEPENDENCY'
    run(client, 'invoice', 'void', invoice=first['id'], expected_version=1)
    revised = run(client, 'estimate', 'update', **values)
    assert revised['net_minor_units'] == 3702
    original = client.run('invoice show', dict(invoice=first['id']), company=COMPANY)
    assert original['revision']['billing_sources'][0]['quantity_microunits'] == 2000000


def test_readonly_and_other_company_cannot_convert_or_replay(client, sale, root):
    from tests.conftest import make_actor, as_user
    source = accepted(client, sale)
    data = dict(estimate=source['id'], expected_version=2, conversion_key='guarded', date='2026-01-13')
    client.run('estimate invoice', data, company=COMPANY, idempotency_key='guarded-cache')
    company_id = client.company.show(company=COMPANY)['id']
    make_actor(root, 'billing-reader', company_role=(company_id, 'readonly'))
    reader = as_user(root, 'billing-reader')
    assert reader.run('estimate billing', {'estimate': source['id']}, company=COMPANY)['source_id'] == source['id']
    with pytest.raises(BookflowError) as err:
        reader.run('estimate invoice', data, company=COMPANY, idempotency_key='guarded-cache')
    assert err.value.code == 'E_PERMISSION'
    client.company.new(legal_name='Other billing company', display_name='Other billing company',
        home_currency='USD', chart='general', organization='Demo Holdings LLC')
    other = 'Other billing company'
    with pytest.raises(BookflowError) as err:
        reader.run('estimate billing', {'estimate': source['id']}, company=other)
    assert err.value.code == 'E_COMPANY_NOT_FOUND'


def test_current_item_mapping_change_preserves_captured_income(client, sale):
    from bookflow.storage.engine import open_database
    from tests.test_row8_journal import database_path, assert_oracle
    from bookflow.company import schema as c
    source = accepted(client, sale)
    alternative = client.account.create(name='Changed income mapping', type='income', company=COMPANY)['id']
    client.run('item update', dict(item=sale['item'], income_account_id=alternative, price='99'), company=COMPANY)
    first = bill(client, source)
    assert first['total_minor_units'] == 2468
    assert first['revision']['lines'][0]['item_snapshot']['income_account']['id'] == sale['income']
    assert any('mapping change' in text for text in first['warnings'])
    ar = first['revision']['profile']['control_account']['id']
    assert_oracle(client, first['id'], {('2026-01-13', ar):2468, ('2026-01-13',sale['income']):-2468})


@pytest.mark.parametrize('noun', ['invoice', 'sales-receipt'])
def test_ordinary_amount_sale_quantity_edit_preserves_amount(client, sale, noun):
    extra = {}
    if noun == 'sales-receipt':
        extra['deposit_to'] = client.account.create(name='Amount bank', type='bank', company=COMPANY)['id']
        extra['payment_method'] = client.run('payment-method create', dict(name='Amount cash', kind='cash'), company=COMPANY)['id']
    first = run(client, noun, 'post', customer=sale['customer'], date='2026-01-13',
        lines=[dict(item=sale['item'], quantity='2', net_amount='10.01')], **extra)
    assert first['total_minor_units'] == 1001 and first['revision']['lines'][0]['unit_price'] is None
    updated = run(client, noun, 'update', **{noun.replace('-', '_'):first['id']}, expected_version=1,
        lines=[dict(item=sale['item'], line_id=first['revision']['lines'][0]['line_id'], quantity='3')])
    assert updated['total_minor_units'] == 1001
    assert updated['revision']['lines'][0]['quantity'] == '3'


def test_captured_amount_tax_mapping_and_closed_period(client, sale):
    import sqlalchemy as sa
    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    from tests.test_row8_journal import database_path
    agency = client.vendor.create(name='Billing tax agency', is_tax_agency=True, company=COMPANY)['id']
    taxable = next(x['id'] for x in client.run('sales-tax-code list', {}, company=COMPANY)['items'] if x['taxable'])
    with open_database(database_path(client), writable=True) as db:
        liability = db.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        db.conn.execute(c.company_info.update().values(sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
        db.conn.commit()
    tax = client.run('item create', dict(name='Billing eight percent', type='sales_tax_item', tax_percent='8',
        tax_agency_vendor_id=agency, liability_account_id=liability), company=COMPANY)['id']
    source = accepted(client, sale, sales_tax_item=tax, lines=[dict(item=sale['item'], quantity='2', net_amount='10.01', tax_code=taxable)])
    client.run('item update', dict(item=tax, tax_percent='9'), company=COMPANY)
    posted = bill(client, source)
    assert posted['total_minor_units'] == 1081 and posted['tax_minor_units'] == 80
    assert posted['revision']['lines'][0]['tax_components'][0]['rate_percent_millionths'] == 8000000
    with open_database(database_path(client), writable=True) as db:
        db.conn.execute(c.company_info.update().values(closing_date='2026-01-31'))
        db.conn.commit()
    with pytest.raises(BookflowError) as err:
        run(client, 'invoice', 'void', invoice=posted['id'], expected_version=1)
    assert err.value.code == 'E_PERIOD_CLOSED'
    assert run(client, 'estimate', 'billing', estimate=source['id'])['lines'][0]['state'] == 'billed'


@pytest.mark.parametrize('resource', ['customer-work', 'ledger.post'])
def test_composite_resource_check_happens_before_cached_replay(client, sale, monkeypatch, resource):
    from bookflow.hub import access
    from bookflow.core import registry
    source = accepted(client, sale)
    data = dict(estimate=source['id'], expected_version=2, conversion_key='auth-before-replay', date='2026-01-13')
    first = client.run('estimate invoice', data, company=COMPANY, idempotency_key='auth-cached')
    if resource == 'customer-work':
        original = access.require_resource
        def deny(s, capability, required_role):
            if capability == resource:
                raise BookflowError('E_PERMISSION', details={'capability': capability})
            return original(s, capability, required_role)
        monkeypatch.setattr(access, 'require_resource', deny)
    else:
        # Current evaluator is role-only. Exercise denial of the main resource's
        # ordinary authorization path, without claiming granular policy exists.
        monkeypatch.setattr(access, 'role_satisfies', lambda *a, **kw: False)
    with pytest.raises(BookflowError) as err:
        client.run('estimate invoice', data, company=COMPANY, idempotency_key='auth-cached')
    assert err.value.code == 'E_PERMISSION'


def test_two_concurrent_conversions_cannot_consume_same_root(client, sale):
    from concurrent.futures import ThreadPoolExecutor
    source = accepted(client, sale)
    def attempt(key):
        try:
            return bill(client, source, key)
        except BookflowError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ['race-a', 'race-b']))
    assert len([x for x in outcomes if isinstance(x, dict)]) == 1
    rejected = [(key, result) for key, result in zip(['race-a', 'race-b'], outcomes) if isinstance(result, str)]
    assert len(rejected) == 1
    key, result = rejected[0]
    if result == 'E_DB_BUSY':
        result = attempt(key)  # Retry the same intent once the other writer has finished.
    assert result == 'E_VERSION_CONFLICT'
    state = run(client, 'estimate', 'billing', estimate=source['id'])
    assert len(state['destinations']) == 1 and state['remaining_net_minor_units'] == 0


def test_billing_cursor_invalidates_and_preserves_source_history(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item']), dict(item=sale['item'])])
    first = bill(client, source, line_ids=[source['revision']['lines'][0]['line_id']])
    current = run(client, 'estimate', 'show', estimate=source['id'])
    second = bill(client, current, 'second')
    page = client.run('estimate billing', {'estimate':source['id'], 'limit':1}, company=COMPANY)
    assert page['count'] == 1 and page['has_more']
    nextpage = client.run('estimate billing', {'estimate':source['id'], 'limit':1, 'cursor':page['next_cursor']}, company=COMPANY)
    assert nextpage['count'] == 1 and not nextpage['has_more']
    assert {page['destinations'][0]['id'],nextpage['destinations'][0]['id']} == {first['id'],second['id']}
    run(client, 'invoice', 'void', invoice=first['id'], expected_version=1)
    with pytest.raises(BookflowError) as err:
        client.run('estimate billing', {'estimate':source['id'], 'limit':1, 'cursor':page['next_cursor']}, company=COMPANY)
    assert err.value.code == 'E_QUERY_STALE'
    shown = client.run('invoice show', {'invoice':second['id']}, company=COMPANY)
    assert shown['revision']['billing_sources'][0]['facts_snapshot']['document']['scope'] is None


@pytest.mark.parametrize('noun,verb', [('estimate','invoice'), ('work-order','sales-receipt')])
def test_billing_http_cli_library_parity(hosted, client, cli, noun, verb):
    cid = hosted.company_id
    customer = hosted.ok('customer.create', dict(name='Billing parity customer'), company=cid)['id']
    income = hosted.ok('account.create', dict(name='Billing parity income', type='income'), company=cid)['id']
    exempt = next(row['id'] for row in hosted.ok('sales-tax-code.list', {}, company=cid)['items'] if not row['taxable'])
    item = hosted.ok('item.create', dict(name='Billing parity service', type='service', sales_enabled=True,
        income_account_id=income, description='Service labor', price='5', sales_tax_code_id=exempt), company=cid)['id']
    source = hosted.ok(noun+'.create', dict(date='2026-01-12', title='Billing parity scope', customer=customer,
        lines=[dict(item=item, quantity='2', net_amount='10.01')]), company=cid)
    if noun == 'estimate':
        source = hosted.ok('estimate.update', dict(estimate=source['id'], expected_version=1,
            status='accepted', decision_note='Customer acceptance'), company=cid)
    extra = {}
    if verb == 'sales-receipt':
        extra['deposit_to'] = hosted.ok('account.create', dict(name='Billing parity bank', type='bank'), company=cid)['id']
        extra['payment_method'] = hosted.ok('payment-method.create', dict(name='Billing parity cash', kind='cash'), company=cid)['id']
        extra['amount_received'] = '10.01'
    first = hosted.ok(noun+'.'+verb, dict(**{noun.replace('-','_'):source['id']},
        expected_version=source['version'], conversion_key='http-bill', date='2026-01-13', **extra), company=cid)
    shown = hosted.ok(verb+'.show', {verb.replace('-','_'):first['id']}, company=cid)
    state = hosted.ok(noun+'.billing', {noun.replace('-','_'):source['id']}, company=cid)
    assert shown == cli.json(verb, 'show', first['id'], '--company', cid)
    assert state == cli.json(noun, 'billing', source['id'], '--company', cid)
    hosted.handle.stop()
    assert shown == client.run(verb+' show', {verb.replace('-','_'):first['id']}, company=cid)
    assert state == client.run(noun+' billing', {noun.replace('-','_'):source['id']}, company=cid)


@pytest.mark.parametrize('change', ['scope', 'provenance', 'allocation'])
def test_tampered_pending_billing_fails_before_any_write(client, sale, monkeypatch, change):
    from bookflow.company import billing_validation
    from tests.test_service_sales_lifecycle import snapshot
    source = accepted(client, sale)
    before = snapshot(client)
    original = billing_validation.validate
    def corrupt(plan, s, ctx):
        if plan.data['changed']:
            if change == 'scope':
                import json
                row = plan.data['billing_allocations'][0]
                value = json.loads(row['facts_snapshot'])
                value['document']['scope'] = 'Not the agreed scope'
                row['facts_snapshot'] = json.dumps(value)
            elif change == 'provenance':
                plan.data['work_pending']['work_lines'][0]['created_by'] = 'wrong-actor'
            else:
                plan.data['billing_allocations'][0]['quantity_microunits'] += 1
        return original(plan, s, ctx)
    monkeypatch.setattr(billing_validation, 'validate', corrupt)
    with pytest.raises(BookflowError) as err:
        bill(client, source)
    assert err.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


def test_independent_billing_check_rejects_consistently_wrong_sales_resolver(client, sale, monkeypatch):
    from bookflow.company import billing
    from tests.test_service_sales_lifecycle import snapshot
    source = accepted(client, sale, lines=[dict(item=sale['item'], net_amount='10.01')])
    before = snapshot(client)
    original = billing.resolved_line
    def wrong(lf):
        value = original(lf)
        value['net_minor_units'] += 100
        value['gross_minor_units'] += 100
        value['profile'].net_amount_minor_units += 100
        return value
    monkeypatch.setattr(billing, 'resolved_line', wrong)
    with pytest.raises(BookflowError) as err:
        bill(client, source)
    assert err.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


def test_shared_core_mcp_attribution_and_replay_without_transport_claim(client, sale):
    from bookflow.core.context import Context, Interface
    from bookflow.core.dispatch import run as dispatch
    from bookflow.core.registry import get
    source = accepted(client, sale)
    cmd = get('estimate invoice')
    data = dict(estimate=source['id'], expected_version=2, conversion_key='mcp-core', date='2026-01-13')
    ctx = Context.new(Interface.mcp, 'billing-core-witness', reason='Invoice approved work')
    first = dispatch(cmd, data, ctx, data_root=client.data_root, company_selector=COMPANY, _login=client._login)
    assert first['created_via'] == 'mcp'
    assert first['revision']['billing_sources'][0]['created_via'] == 'mcp'
    again = dispatch(cmd, data, ctx, data_root=client.data_root, company_selector=COMPANY, _login=client._login)
    assert again['id'] == first['id'] and again['idempotent_replay']
    with pytest.raises(BookflowError) as err:
        dispatch(cmd, dict(data, date='2026-01-14'), ctx, data_root=client.data_root, company_selector=COMPANY, _login=client._login)
    assert err.value.code == 'E_CONVERSION_KEY_REUSED'


def test_billing_custom_values_carry_required_destination_and_original_files(client, sale):
    import io
    shared = client.run('custom-field create', dict(name='Billing approved', kind='bool',
        scopes=['estimate', 'invoice']), company=COMPANY)
    required = client.run('custom-field create', dict(name='Billing reference', kind='text',
        scopes=['invoice'], required=True), company=COMPANY)
    source = accepted(client, sale, custom_fields={shared['id']: False})
    body = b'Original signed work scope\n'
    attachment = client.attachment.add(record_type='work_document', record_id=source['id'],
        original_filename='signed-scope.txt', media_type='text/plain', input_stream=io.BytesIO(body), company=COMPANY)
    with pytest.raises(BookflowError) as err:
        bill(client, source)
    assert err.value.code == 'E_VALIDATION'
    first = bill(client, source, custom_fields={required['id']: 'B-100'})
    snapshot = first['revision']['custom_fields_snapshot']
    assert snapshot[shared['id']]['value'] is False
    assert snapshot[shared['id']]['value_id'] != source['revision']['custom_fields_snapshot'][shared['id']]['value_id']
    assert snapshot[required['id']]['value'] == 'B-100'
    link = first['revision']['billing_sources'][0]
    assert link['source_document_id'] == source['id'] and link['source_revision_id'] == source['revision']['id']
    sink = io.BytesIO()
    client.attachment.get(attachment=attachment['attachment']['id'], output_stream=sink, company=COMPANY)
    assert sink.getvalue() == body
    assert client.run('attachment list', dict(record_type='transaction', record_id=first['id']), company=COMPANY)['count'] == 0


@pytest.mark.parametrize('target', ['customer', 'item', 'income'])
def test_billing_rejects_current_ineligible_captured_posting_reference(client, sale, target):
    source = accepted(client, sale)
    if target == 'income':
        replacement = client.account.create(name='New billing income mapping', type='income', company=COMPANY)['id']
        item = client.item.show(item=sale['item'], company=COMPANY)
        client.item.update(item=sale['item'], expected_version=item['version'], income_account_id=replacement, company=COMPANY)
    noun = 'account' if target == 'income' else target
    shown = client.run(noun + ' show', {noun: sale[target]}, company=COMPANY)
    client.run(noun + ' deactivate', {noun: sale[target], 'expected_version': shown['version']}, company=COMPANY)
    with pytest.raises(BookflowError) as err:
        bill(client, source)
    assert err.value.code == 'E_INACTIVE_REFERENCE'
    assert run(client, 'estimate', 'show', estimate=source['id'])['version'] == source['version']
    assert run(client, 'estimate', 'billing', estimate=source['id'])['remaining_net_minor_units'] == 2468



def test_receipt_preview_stales_before_requiring_new_received_total(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item']), dict(item=sale['item'])])
    first = bill(client, source, line_ids=[source['revision']['lines'][0]['line_id']])
    current = run(client, 'estimate', 'show', estimate=source['id'])
    bank = client.account.create(name='Receipt preview bank', type='bank', company=COMPANY)['id']
    method = client.run('payment-method create', dict(name='Receipt preview cash', kind='cash'), company=COMPANY)['id']
    data = dict(estimate=current['id'], expected_version=current['version'], conversion_key='receipt remainder',
        date='2026-01-13', deposit_to=bank, payment_method=method, amount_received='12.34')
    preview = client.run('estimate sales-receipt', data, company=COMPANY, dry_run=True)
    client.run('invoice void', dict(invoice=first['id'], expected_version=1), company=COMPANY, reason='Release earlier root')
    with pytest.raises(BookflowError) as err:
        client.run('estimate sales-receipt', dict(data, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert err.value.code == 'E_PREVIEW_STALE'
    with pytest.raises(BookflowError) as err:
        client.run('estimate sales-receipt', data, company=COMPANY, dry_run=True)
    assert err.value.code == 'E_VALIDATION'
    assert client.run('estimate sales-receipt', dict(data, amount_received='24.68'), company=COMPANY)['total_minor_units'] == 2468



@pytest.mark.parametrize('change', ['income_account', 'customer'])
def test_independent_billing_classification_matches_source(client, sale, monkeypatch, change):
    from bookflow.company import billing
    from tests.test_service_sales_lifecycle import snapshot
    source = accepted(client, sale)
    if change == 'income_account':
        other = client.account.create(name='Substituted income', type='income', company=COMPANY)
        original = billing.resolved_line
        def wrong(lf):
            result = original(lf)
            result['profile'].income_account = result['profile'].income_account.model_copy(update={'id': other['id'], 'label': other['name']})
            return result
        monkeypatch.setattr(billing, 'resolved_line', wrong)
    else:
        other = client.customer.create(name='Substituted customer', company=COMPANY)
        original = billing.financial_profile
        def wrong(*args, **kwargs):
            result, warnings = original(*args, **kwargs)
            result.customer = result.customer.model_copy(update={'id': other['id'], 'label': other['name']})
            return result, warnings
        monkeypatch.setattr(billing, 'financial_profile', wrong)
    before = snapshot(client)
    with pytest.raises(BookflowError) as err:
        bill(client, source)
    assert err.value.code == 'E_INTERNAL'
    assert snapshot(client) == before


def test_explicit_line_preview_includes_other_consumed_source_lines(client, sale):
    source = accepted(client, sale, lines=[dict(item=sale['item']), dict(item=sale['item'])])
    a, b = [line['line_id'] for line in source['revision']['lines']]
    first = bill(client, source, line_ids=[a])
    current = run(client, 'estimate', 'show', estimate=source['id'])
    data = dict(estimate=current['id'], expected_version=current['version'], conversion_key='selected second', date='2026-01-13', line_ids=[b])
    preview = client.run('estimate invoice', data, company=COMPANY, dry_run=True)
    client.run('invoice void', dict(invoice=first['id'], expected_version=1), company=COMPANY, reason='Release first root')
    with pytest.raises(BookflowError) as err:
        client.run('estimate invoice', dict(data, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert err.value.code == 'E_PREVIEW_STALE'
    assert client.run('estimate invoice', data, company=COMPANY)['total_minor_units'] == 1234
