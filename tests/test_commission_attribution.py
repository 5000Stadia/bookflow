"""R189 invoice attribution: immutable names, exact final net and adapter contracts."""
import anyio
import pytest
import bookflow
from bookflow.core.errors import BookflowError
from bookflow.core import registry
from tests.test_scheduling import books, member
from tests.test_row3_host import hosted


@pytest.fixture
def shop(books):
    def call(command, **raw):
        return books.run(command, raw, reason='Invoice attribution setup')
    call('company update', commission_attribution_enabled=True)
    income = call('account create', name='Attribution sales', type='income')['id']
    item = call('item create', name='Attribution service', type='service', sales_enabled=True,
        income_account_id=income, price='100.00', commission_enabled=True)['id']
    people = []
    for name, initials in [('Ada', 'ADA'), ('Bo', 'BO')]:
        person = call('other-name create', name=name)['id']
        people.append(call('sales-rep create', name=name, initials=initials,
            name_type='other_name', name_id=person)['id'])
    return dict(call=call, item=item, people=people, income=income)


def line(shop, who=0, **kw):
    return dict(item=shop['item'], commission={'recipients': [{'recipient': shop['people'][who]}]}, **kw)


def post(books, lines, **kw):
    return books.run('invoice post', dict(customer='Rivera Construction', date='2026-01-15', lines=lines, **kw), reason='Credit the sellers')


def test_two_people_final_net_snapshots_reopen_and_off_layer(books, shop):
    call = shop['call']
    subtotal = call('item create', name='Attribution subtotal', type='subtotal', description='Attribution subtotal')['id']
    discount = call('item create', name='Attribution discount', type='discount', description='Attribution discount', discount_amount='30.00',
        income_account_id=shop['income'])['id']
    sale = post(books, [line(shop), line(shop, 1, unit_price='50.00'), {'item': subtotal}, {'item': discount}])
    assert sale['total_minor_units'] == 12000
    report = books.run('report commission-attribution', {})
    assert [(r['recipient_name'], r['net_minor_units']) for r in report['rows']] == [('Ada', 8000), ('Bo', 4000)]
    assert sum(r['net_minor_units'] for r in report['rows']) == sale['subtotal_minor_units']
    call('company update', commission_attribution_enabled=False)
    call('sales-rep update', sales_rep=shop['people'][0], name='Ada renamed', expected_version=1)
    reopened = bookflow.connect(data_root=str(books.root)).run('report commission-attribution', {}, company=books.company)
    assert reopened == report
    shown = books.run('invoice show', {'invoice': sale['id']})
    assert shown['revision']['lines'][0]['item_snapshot']['commission']['recipients'][0]['recipient_label'] == 'Ada'


def test_required_inactive_missing_disabled_are_atomic(books, shop):
    before = books.run('invoice query', {})['items']
    for payload, code in [([{'item': shop['item']}], 'E_COMMISSION_REQUIRED'),
                          ([dict(item=shop['item'], commission={'recipients': [{'recipient': 'Missing person'}]})], 'E_RECORD_NOT_FOUND')]:
        with pytest.raises(BookflowError) as err:
            post(books, payload)
        assert err.value.code == code
    shop['call']('sales-rep deactivate', sales_rep=shop['people'][0], expected_version=1)
    with pytest.raises(BookflowError) as err:
        post(books, [line(shop)])
    assert err.value.code == 'E_INACTIVE_REFERENCE'
    shop['call']('company update', commission_attribution_enabled=False)
    with pytest.raises(BookflowError) as err:
        post(books, [line(shop, 1)])
    assert err.value.code == 'E_FEATURE_DISABLED'
    assert books.run('invoice query', {})['items'] == before


def test_legacy_line_unchanged_but_changed_item_requires_selection(books, shop):
    call = shop['call']
    call('company update', commission_attribution_enabled=False)
    sale = post(books, [{'item': shop['item']}])
    snapshot = sale['revision']['lines'][0]['item_snapshot']
    assert 'commission' not in snapshot
    call('company update', commission_attribution_enabled=True)
    same = call('invoice update', invoice=sale['id'], expected_version=sale['version'], memo='New memo')
    assert same['revision']['lines'][0]['item_snapshot'] == snapshot
    new_item = call('item create', name='Another attributed service', type='service', sales_enabled=True,
        income_account_id=shop['income'], price='25.00', commission_enabled=True)['id']
    with pytest.raises(BookflowError) as err:
        call('invoice update', invoice=sale['id'], expected_version=same['version'],
             lines=[{'line_id': same['revision']['lines'][0]['line_id'], 'item': new_item}])
    assert err.value.code == 'E_COMMISSION_REQUIRED'


def test_recipient_lookup_narrow_and_permission_denial(books, shop):
    options = books.run('commission recipient-options', {'item': shop['item'], 'sales_rep': shop['people'][1]})
    assert options['commission_enabled'] and options['suggested_recipient_id'] == shop['people'][1]
    assert all(set(row) == {'id', 'name'} for row in options['items'])
    _, denied = member(books, 'attribution_denied', denies=['commission.attribute', 'commission.read'])
    for command, raw in [('commission recipient-options', {}), ('report commission-attribution', {}),
        ('invoice post', {'customer': 'Rivera Construction', 'date': '2026-01-15', 'lines': [line(shop)]})]:
        with pytest.raises(BookflowError) as err:
            denied(command, raw, **({'reason': 'Test denied selection'} if registry.get(command).is_write else {}))
        assert err.value.code == 'E_PERMISSION'


def test_receipts_cannot_accept_attribution_yet(books, shop):
    bank = shop['call']('account create', name='Receipt till', type='bank')['id']
    with pytest.raises(BookflowError) as err:
        shop['call']('sales-receipt post', customer='Rivera Construction', date='2026-01-15',
            deposit_to=bank, lines=[line(shop)])
    assert err.value.code == 'E_VALIDATION'


def test_exactly_one_recipient_typed_contract():
    from bookflow.company.commission_models import CommissionInput
    assert CommissionInput.model_validate({'recipients': [{'recipient': 'Ada'}]}).recipients[0].share == 100
    with pytest.raises(ValueError):
        CommissionInput.model_validate({'recipients': [{'recipient': 'Ada'}, {'recipient': 'Bo'}]})
    with pytest.raises(ValueError):
        CommissionInput.model_validate({'recipients': [{'recipient': 'Ada', 'share': 50}]})


def test_commission_contracts_python_cli_http_mcp(root, tmp_path):
    from tests.mcp_matrix_support import Matrix
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in ('python', 'cli', 'http', 'mcp'):
                async def call(command, raw, **kw):
                    return await matrix.call(surface, command, raw, **kw)
                await call('company update', {'commission_attribution_enabled': True})
                options = await call('commission recipient-options', {})
                assert options['items']
                rep = options['items'][0]['id']
                account = await call('account create', {'name': 'Contract attribution income', 'type': 'income'})
                item = await call('item create', {'name': 'Contract attribution service', 'type': 'service',
                    'sales_enabled': True, 'income_account_id': account['id'], 'price': '12.34', 'commission_enabled': True})
                customer = await call('customer create', {'name': 'Contract attribution customer'})
                payload = {'customer': customer['id'], 'date': '2026-11-02', 'lines': [{'item': item['id']}]}
                error = await call('invoice post', payload, rejected=True)
                assert error['code'] == 'E_COMMISSION_REQUIRED'
                payload['lines'][0]['commission'] = {'recipients': [{'recipient': rep, 'share': 100}]}
                sale = await call('invoice post', payload)
                report = await call('report commission-attribution', {'item': item['id']})
                assert report['rows'][0]['invoice_id'] == sale['id']
                assert report['rows'][0]['net_minor_units'] == 1234
        finally:
            await matrix.close()
    anyio.run(witness)


def test_agent_admin_role_does_not_grant_commission_capabilities(hosted):
    from fastapi.testclient import TestClient
    from tests.conftest import make_agent, hosted_call
    from tests.test_item_pictures_review import _membership_version
    cid = hosted.company_id
    principal = hosted.ok('user.home.show', {})['user_id']
    agent = make_agent(hosted_call(hosted), 'attribution-bot', principals=principal, company=cid, role='admin')
    issued = hosted.ok('token.issue', {'user': agent, 'principal': principal, 'label': 'Attribution test'})
    api = TestClient(hosted.handle.app)
    bearer = {'Authorization': 'Bearer ' + issued['secret']}
    for command, capability in [('commission.recipient-options', 'commission.attribute'),
                                ('report.commission-attribution', 'commission.read')]:
        refused = api.post(f'/companies/{cid}/commands/{command}', headers=bearer, json={})
        assert refused.status_code == 403, refused.text
        assert refused.json()['details']['capability'] == capability
        assert refused.json()['details']['reason'] == 'capability_not_granted'
    hosted.ok('membership.grant', {'user': agent, 'company': cid, 'role': 'admin',
        'grants': ['commission.attribute'], 'expected_version': _membership_version(hosted, agent)})
    granted = api.post(f'/companies/{cid}/commands/commission.recipient-options', headers=bearer, json={})
    assert granted.status_code == 200, granted.text
