"""Independent register command witnesses: movements, dimensions and lossless edits."""
import pytest

from bookflow import BookflowError
from tests.test_row8_journal import COMPANY, assert_oracle, ledger


@pytest.fixture
def register_accounts(client):
    return [client.account.create(name='Register bank', type='bank', company=COMPANY)['id'],
            client.account.create(name='Register expense', type='expense', company=COMPANY)['id']]


def post(client, accounts, **kw):
    values = dict(account=accounts[0], date='2026-02-12', direction='decrease',
                  amount='100.00', category=accounts[1])
    if 'allocations' in kw:
        values.pop('category')
    values.update(kw)
    return client.register.post(**values, company=COMPANY)


def edit(first):
    selected, *offsets = first['revision']['lines']
    normal = first['receipt']['normal_balance']
    return dict(journal=first['id'], expected_version=first['version'],
                selected_line_id=selected['line_id'], account=selected['account_id'],
                date=first['date'], number=first['number'], memo=first['memo'],
                payee=({'name_type': selected['name_type'], 'name_id': selected['name_id']}
                       if selected['name_id'] else None),
                direction=first['receipt']['direction'], amount=selected['amount'],
                allocations=[dict(line_id=l['line_id'], account=l['account_id'],
                                  amount=l['amount'], memo=l['description'],
                                  direction='decrease' if l['side'] == normal else 'increase',
                                  party=({'name_type': l['name_type'], 'name_id': l['name_id']}
                                         if l['name_id'] else None),
                                  class_mode='value' if l['class_id'] else 'none',
                                  class_id=l['class_id']) for l in offsets])


@pytest.mark.parametrize('kind,normal', [
    ('bank', 'debit'), ('accounts_receivable', 'debit'), ('other_current_asset', 'debit'),
    ('fixed_asset', 'debit'), ('other_asset', 'debit'), ('accounts_payable', 'credit'),
    ('credit_card', 'credit'), ('other_current_liability', 'credit'),
    ('long_term_liability', 'credit'), ('equity', 'credit'),
])
@pytest.mark.parametrize('direction', ['increase', 'decrease'])
def test_all_balance_sheet_signs(client, register_accounts, kind, normal, direction):
    selected = client.account.create(name='Selected ' + kind, type=kind, company=COMPANY)['id']
    party = None
    if kind in ('accounts_receivable', 'accounts_payable'):
        name_type = 'customer' if kind == 'accounts_receivable' else 'vendor'
        person = client.run(name_type + ' create', {'name': 'Register party'}, company=COMPANY)
        party = dict(name_type=name_type, name_id=person['id'])
    first = post(client, [selected, register_accounts[1]], direction=direction, payee=party)
    expected = 10000 if (normal == 'debit') == (direction == 'increase') else -10000
    assert_oracle(client, first['id'], {('2026-02-12', selected): expected,
                                      ('2026-02-12', register_accounts[1]): -expected})
    assert first['receipt'] == dict(account_id=selected, normal_balance=normal,
                                   direction=direction,
                                   amount=dict(amount='100.00', currency='USD', minor_units=10000))


def test_mixed_split_net_receipt_and_calculation(client, register_accounts):
    bank, expense = register_accounts
    allocations = [dict(account=expense, amount='120.00'),
                   dict(account=expense, amount='20.00', direction='increase')]
    calculated = client.register.calculate(account=bank, direction='decrease',
                                            allocations=allocations, company=COMPANY)
    assert calculated == dict(amount=dict(amount='100.00', minor_units=10000, currency='USD'),
                              direction='decrease', currency='USD')
    first = post(client, register_accounts, allocations=allocations)
    assert first['total_minor_units'] == 12000
    assert first['receipt']['amount']['minor_units'] == 10000
    assert [(l['side'], l['amount_minor_units']) for l in first['revision']['lines']] == [
        ('credit', 10000), ('debit', 12000), ('credit', 2000)]
    assert_oracle(client, first['id'], {('2026-02-12', bank): -10000, ('2026-02-12', expense): 10000})


def test_simple_inheritance_and_lossless_simple_id_noop(client, register_accounts):
    vendor = client.vendor.create(name='Register supplier', company=COMPANY)['id']
    klass = client.run('class create', {'name': 'Register class'}, company=COMPANY)['id']
    first = post(client, register_accounts, memo='Header and category',
                 payee=dict(name_type='vendor', name_id=vendor), class_id=klass)
    main, offset = first['revision']['lines']
    assert main['class_id'] is None and offset['class_id'] == klass
    assert main['name_id'] == offset['name_id'] == vendor
    assert main['description'] == offset['description'] == first['memo']
    request = edit(first)
    request.pop('allocations')
    request.update(category=register_accounts[1], category_line_id=offset['line_id'], class_id=klass)
    before = ledger(client, first['id'])
    saved = client.register.update(**request, company=COMPANY)
    assert not saved['changed'] and saved['version'] == 1
    assert saved['receipt'] == first['receipt']
    assert ledger(client, first['id']) == before


def test_split_dimensions_noop_snapshots_and_class_mode_key(client, register_accounts):
    vendor = client.vendor.create(name='Row payee', company=COMPANY)['id']
    klass = client.run('class create', {'name': 'Row class'}, company=COMPANY)['id']
    allocations = [dict(account=register_accounts[1], amount='60.00', memo='Independent'),
                   dict(account=register_accounts[1], amount='40.00', class_mode='none')]
    first = post(client, register_accounts, allocations=allocations, memo='Row memo',
                 payee=dict(name_type='vendor', name_id=vendor), class_id=klass,
                 idempotency_key='register-classes')
    main, one, two = first['revision']['lines']
    assert main['name_id'] == vendor and main['class_id'] is None
    assert one['class_id'] == klass and two['class_id'] is None
    assert one['name_id'] is two['name_id'] is None
    assert one['description'] == 'Independent' and two['description'] is None
    replay = post(client, register_accounts, allocations=allocations, memo='Row memo',
                  payee=dict(name_type='vendor', name_id=vendor), class_id=klass,
                  idempotency_key='register-classes')
    assert replay['idempotent_replay'] and replay['receipt'] == first['receipt']
    different = [dict(allocations[0], class_mode='none'), allocations[1]]
    with pytest.raises(BookflowError) as err:
        post(client, register_accounts, allocations=different, memo='Row memo',
             payee=dict(name_type='vendor', name_id=vendor), class_id=klass,
             idempotency_key='register-classes')
    assert err.value.code == 'E_IDEMPOTENCY_MISMATCH'
    client.account.update(account=register_accounts[1], name='Renamed expense', company=COMPANY)
    client.run('class update', {'class': klass, 'name': 'Renamed class'}, company=COMPANY)
    saved = client.register.update(**edit(first), company=COMPANY)
    assert not saved['changed'] and saved['revision'] == first['revision']


def test_ar_ap_split_independent_parties_and_calculator_scope(client, register_accounts):
    ar = client.account.create(name='Register AR', type='accounts_receivable', company=COMPANY)['id']
    ap = client.account.create(name='Register AP', type='accounts_payable', company=COMPANY)['id']
    customer = client.customer.create(name='Register customer', company=COMPANY)['id']
    vendor = client.vendor.create(name='Register vendor', company=COMPANY)['id']
    allocation = dict(account=ap, amount='100.00', party=dict(name_type='vendor', name_id=vendor))
    calculated = client.register.calculate(account=ar, direction='increase', allocations=[allocation], company=COMPANY)
    assert calculated['amount']['minor_units'] == 10000
    first = post(client, [ar, ap], direction='increase',
                 payee=dict(name_type='customer', name_id=customer), allocations=[allocation])
    assert [l['name_type'] for l in first['revision']['lines']] == ['customer', 'vendor']
    for action in ('calculate', 'post'):
        with pytest.raises(BookflowError) as err:
            values = dict(account=ar, direction='increase', allocations=[dict(account=ap, amount='100.00')])
            if action == 'post':
                values.update(date='2026-02-12', amount='100.00', payee=dict(name_type='customer', name_id=customer))
            client.run('register ' + action, values, company=COMPANY)
        assert err.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError) as err:
        post(client, [ar, ap], direction='increase', allocations=[allocation])
    assert err.value.code == 'E_VALIDATION'


@pytest.mark.parametrize('bad', ['0', '-1', '1.001', '1 JPY', 1.0, True,
                                {'minor_units': 0, 'currency': 'USD'},
                                {'minor_units': 1, 'currency': 'USD', 'amount': '2'}])
def test_exact_positive_domestic_amounts(client, register_accounts, bad):
    with pytest.raises(BookflowError):
        post(client, register_accounts, amount=bad)


@pytest.mark.parametrize('action', ['post', 'calculate'])
def test_same_account_and_bad_split_totals(client, register_accounts, action):
    bank, expense = register_accounts
    examples = [([dict(account=bank, amount='100.00')], 'E_VALIDATION'),
                ([dict(account=expense, amount='100.00', direction='increase')], 'E_VALIDATION'),
                ([dict(account=expense, amount='100'), dict(account=expense, amount='100', direction='increase')], 'E_VALIDATION'),
                ([dict(account=expense, amount={'minor_units': 9223372036854775807, 'currency': 'USD'}),
                  dict(account=expense, amount='1')], 'E_VALUE_RANGE')]
    for allocations, code in examples:
        with pytest.raises(BookflowError) as err:
            if action == 'post':
                post(client, register_accounts, allocations=allocations)
            else:
                client.register.calculate(account=bank, direction='decrease', allocations=allocations, company=COMPANY)
        assert err.value.code == code
    if action == 'post':
        with pytest.raises(BookflowError) as err:
            post(client, register_accounts, allocations=[dict(account=expense, amount='99')])
        assert err.value.code == 'E_UNBALANCED_ENTRY'
        with pytest.raises(BookflowError) as err:
            post(client, register_accounts, category=bank)
        assert err.value.code == 'E_VALIDATION'


def test_retained_reordered_new_removed_and_foreign_ids(client, register_accounts):
    allocations = [dict(account=register_accounts[1], amount='60', memo='one'),
                   dict(account=register_accounts[1], amount='40', memo='two')]
    first = post(client, register_accounts, allocations=allocations)
    other = post(client, register_accounts)
    request = edit(first)
    original_ids = [l['line_id'] for l in first['revision']['lines']]
    with pytest.raises(BookflowError):
        client.register.update(**dict(request, selected_line_id=other['revision']['lines'][0]['line_id']), company=COMPANY)
    bad = [dict(request['allocations'][0], line_id=other['revision']['lines'][1]['line_id']), request['allocations'][1]]
    with pytest.raises(BookflowError):
        client.register.update(**dict(request, allocations=bad), company=COMPANY)
    request['allocations'].reverse()
    second = client.register.update(**request, company=COMPANY)
    assert [l['line_id'] for l in second['revision']['lines']] == [original_ids[0], original_ids[2], original_ids[1]]
    request = edit(second)
    request['allocations'] = [dict(request['allocations'][0], amount='70'),
                              dict(account=register_accounts[1], amount='30')]
    third = client.register.update(**request, company=COMPANY)
    assert third['revision']['lines'][0]['line_id'] == original_ids[0]
    assert third['revision']['lines'][1]['line_id'] == original_ids[2]
    assert third['revision']['lines'][2]['line_id'] not in original_ids
    with pytest.raises(BookflowError):
        client.register.update(**dict(edit(third), allocations=edit(first)['allocations']), company=COMPANY)
    assert_oracle(client, first['id'], {('2026-02-12', register_accounts[0]): -10000,
                                      ('2026-02-12', register_accounts[1]): 10000})


@pytest.mark.parametrize('shape', ['selected_class', 'description', 'order', 'repeated'])
def test_incompatible_journal_rejects_and_stale_wins(client, register_accounts, shape):
    first = post(client, register_accounts)
    request = edit(first)
    entered = [dict(account=l['account_id'], side=l['side'], amount=l['amount'],
                    line_id=l['line_id']) for l in first['revision']['lines']]
    if shape == 'selected_class':
        klass = client.run('class create', {'name': 'Selected class'}, company=COMPANY)['id']
        entered[0]['class_id'] = klass
    elif shape == 'description':
        entered[0]['description'] = 'Not header memo'
    elif shape == 'order':
        entered.reverse()
    else:
        entered[0]['amount'] = '50'
        entered.append(dict(account=register_accounts[0], side='credit', amount='50'))
    client.journal.update(journal=first['id'], expected_version=1, lines=entered, company=COMPANY)
    with pytest.raises(BookflowError) as err:
        client.register.update(**request, company=COMPANY)
    assert err.value.code == 'E_VERSION_CONFLICT'
    with pytest.raises(BookflowError) as err:
        client.register.update(**dict(request, expected_version=2), company=COMPANY)
    assert err.value.code == 'E_VALIDATION' and err.value.details['open_journal']['journal'] == first['id']


def test_post_forbids_ids_and_required_update_version(client, register_accounts):
    first = post(client, register_accounts)
    with pytest.raises(BookflowError):
        post(client, register_accounts, allocations=[dict(account=register_accounts[1], amount='100', line_id=None)])
    request = edit(first)
    request.pop('expected_version')
    with pytest.raises(BookflowError):
        client.register.update(**request, company=COMPANY)


def test_readonly_can_calculate_but_cannot_write(client, root, register_accounts):
    from tests.conftest import make_actor, as_user
    company = client.company.show(company=COMPANY)
    make_actor(root, 'register-reader', company_role=(company['id'], 'readonly'))
    reader = as_user(root, 'register-reader')
    first = post(client, register_accounts)
    calculated = reader.register.calculate(account=register_accounts[0], direction='increase',
        allocations=[dict(account=register_accounts[1], amount='2.50')], company=COMPANY)
    assert calculated['amount']['minor_units'] == 250
    for action in (lambda: post(reader, register_accounts),
                   lambda: reader.register.update(**edit(first), company=COMPANY)):
        with pytest.raises(BookflowError) as err:
            action()
        assert err.value.code == 'E_PERMISSION'


@pytest.mark.parametrize('mode,klass', [('value', None), ('none', 'unexpected'), ('inherit', 'unexpected')])
def test_class_mode_rejects_contradictory_shapes(client, register_accounts, mode, klass):
    with pytest.raises(BookflowError) as err:
        post(client, register_accounts, allocations=[dict(account=register_accounts[1], amount='100',
             class_mode=mode, class_id=klass)])
    assert err.value.code == 'E_VALIDATION'


def test_mixed_side_overflow_even_when_net_fits(client, register_accounts):
    allocations = [dict(account=register_accounts[1], amount={'minor_units': 9223372036854775807, 'currency': 'USD'}),
                   dict(account=register_accounts[1], amount='0.01'),
                   dict(account=register_accounts[1], amount={'minor_units': 9223372036854775807, 'currency': 'USD'}, direction='increase')]
    with pytest.raises(BookflowError) as err:
        client.register.calculate(account=register_accounts[0], direction='decrease', allocations=allocations, company=COMPANY)
    assert err.value.code == 'E_VALUE_RANGE'



def test_calculator_validates_explicit_split_class(client, register_accounts):
    args = dict(account=register_accounts[0], direction='decrease', company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.register.calculate(**args, allocations=[dict(account=register_accounts[1],
            amount='1.00', class_mode='value', class_id='Missing explicit split class')])
    assert caught.value.code == 'E_RECORD_NOT_FOUND'
