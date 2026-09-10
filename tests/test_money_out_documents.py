"""Write a check, enter a credit card charge, and check the books by hand.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything: a 284.60 check split 184.60 / 100.00, a 75.25 card charge, and what
the trial balance and the profit and loss say before and after the check is voided.
"""
from copy import deepcopy

import anyio
import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.errors import BookflowError

# The check, its split, and the card charge. Everything below is arithmetic on these.
CHECK = '284.60'
FIRST = '184.60'
SECOND = '100.00'
CHARGE = '75.25'


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    root = tmp_path / 'money-out'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Money out organization')
    company = client.company.new(legal_name='Money out', home_currency='USD',
                                 organization='Money out organization', timezone='UTC',
                                 chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    card = client.account.create(name='Company Card', type='credit_card', company=company)['id']
    expenses = [row['id'] for row in accounts if row['type'] == 'expense'][:2]
    vendor = client.vendor.create(name='Northside Supply', company=company)['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, bank=bank, card=card, vendor=vendor,
                first=expenses[0], second=expenses[1], run=run)


def _check(books, **extra):
    return dict(account=books['bank'],
                pay_to={'name_type': 'vendor', 'name_id': books['vendor']},
                date='2026-03-04', number='1042', amount=CHECK, memo='March supplies',
                expenses=[{'account': books['first'], 'amount': FIRST, 'memo': 'Parts'},
                          {'account': books['second'], 'amount': SECOND, 'memo': 'Fuel'}],
                **extra)


def _net(revision):
    """Signed minor units per account, debits positive, from the posted revision itself."""
    net = {}
    for line in revision['lines']:
        units = line['amount']['minor_units']
        net[line['account_id']] = net.get(line['account_id'], 0) + (
            units if line['side'] == 'debit' else -units)
    return net


def _row(report, account_id):
    return next(row for row in report['rows'] if row['account_id'] == account_id)


def test_a_check_puts_the_bank_down_and_each_expense_account_up_its_own_line(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')

    assert posted['status'] == 'posted'
    assert posted['number'] == '1042'
    # 284.60 out of the bank; 184.60 and 100.00 into the two expense accounts. 184.60 + 100.00
    # is 284.60, so the debits and the credits are both 28460 minor units.
    assert _net(posted['revision']) == {books['bank']: -28460, books['first']: 18460,
                                        books['second']: 10000}
    assert posted['revision']['debit_total']['amount'] == CHECK
    assert posted['revision']['credit_total']['amount'] == CHECK
    assert [line['side'] for line in posted['revision']['lines']] == ['credit', 'debit', 'debit']

    # The document's own footer figures, computed by the server and never in the browser.
    assert posted['document'] == {
        'kind': 'check', 'account_id': books['bank'], 'funding': 'bank', 'currency': 'USD',
        'amount': {'amount': CHECK, 'currency': 'USD', 'minor_units': 28460},
        'expense_total': {'amount': CHECK, 'currency': 'USD', 'minor_units': 28460},
        'expense_lines': 2}

    # The payee is carried on the document, not invented per line.
    assert posted['revision']['lines'][0]['name_id'] == books['vendor']
    assert posted['revision']['lines'][0]['name_type'] == 'vendor'

    # The audit trail names the document a bookkeeper wrote, not the register underneath it.
    events = books['client'].audit.list(company=books['company'], limit=5)['items']
    assert events[0]['command'] == 'check post'


def test_the_trial_balance_and_the_profit_and_loss_agree_with_the_check_by_hand(books):
    books['run']('check post', _check(books), reason='Pay Northside Supply')
    books['run']('card-charge post', dict(
        account=books['card'], pay_to={'name_type': 'vendor', 'name_id': books['vendor']},
        date='2026-03-05', amount=CHARGE, memo='Fuel on the company card',
        expenses=[{'account': books['second'], 'amount': CHARGE}]), reason='Record a card purchase')

    trial = books['run']('report trial-balance', {'date_to': '2026-12-31', 'limit': 200})
    # Debits: 184.60 + (100.00 + 75.25) = 359.85. Credits: 284.60 + 75.25 = 359.85.
    assert trial['totals']['debit']['minor_units'] == 35985
    assert trial['totals']['credit']['minor_units'] == 35985
    assert trial['totals']['signed_net']['minor_units'] == 0
    assert _row(trial, books['bank'])['credit']['amount'] == CHECK
    assert _row(trial, books['first'])['debit']['amount'] == FIRST
    assert _row(trial, books['second'])['debit']['amount'] == '175.25'
    assert _row(trial, books['card'])['credit']['amount'] == CHARGE

    # Before this, nothing but income could ever appear on a profit and loss.
    loss = books['run']('report profit-and-loss',
                        {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})
    assert loss['totals']['income']['minor_units'] == 0
    assert loss['totals']['expense']['minor_units'] == 35985
    assert loss['totals']['net_income']['amount'] == '-359.85'
    assert {row['account_id']: row['amount']['amount'] for row in loss['rows']} == {
        books['first']: FIRST, books['second']: '175.25'}
    assert {row['section'] for row in loss['rows']} == {'expense'}


def test_lines_that_do_not_add_up_are_refused_and_the_error_names_the_difference(books):
    short = _check(books)
    short['expenses'][1]['amount'] = '95.40'  # 184.60 + 95.40 = 280.00, which is 4.60 short
    with pytest.raises(BookflowError) as raised:
        books['run']('check post', short, reason='Pay Northside Supply')
    err = raised.value
    assert err.code == 'E_UNBALANCED_ENTRY'
    assert '280.00' in err.message and '4.60' in err.message and CHECK in err.message
    assert err.details['difference'] == {'amount': '4.60', 'currency': 'USD', 'minor_units': 460}
    assert err.details['difference_minor_units'] == -460
    assert err.details['expense_total']['minor_units'] == 28000
    assert err.details['amount']['minor_units'] == 28460
    assert err.details['fields'][0]['field'] == 'expenses'

    over = _check(books)
    over['expenses'][1]['amount'] = '105.00'  # 184.60 + 105.00 = 289.60, 5.00 over
    with pytest.raises(BookflowError) as raised:
        books['run']('check post', over, reason='Pay Northside Supply')
    assert raised.value.details['difference_minor_units'] == 500
    assert 'more than' in raised.value.message

    # Nothing was written by either refusal.
    trial = books['run']('report trial-balance', {'date_to': '2026-12-31', 'limit': 200})
    assert trial['totals']['debit']['minor_units'] == 0


def test_a_card_charge_funds_from_the_card_and_a_check_refuses_to(books):
    charged = books['run']('card-charge post', dict(
        account=books['card'], date='2026-03-05', amount=CHARGE,
        expenses=[{'account': books['second'], 'amount': CHARGE}]), reason='Record a card purchase')
    # A liability rises by being credited; the expense is debited. No bank account is touched.
    assert _net(charged['revision']) == {books['card']: -7525, books['second']: 7525}
    assert charged['document']['funding'] == 'credit_card'
    assert charged['document']['kind'] == 'card_charge'
    assert books['bank'] not in _net(charged['revision'])
    # A card charge carries no check number of its own.
    assert 'number' not in registry.get('card-charge post').input_model.model_fields

    with pytest.raises(BookflowError) as raised:
        books['run']('check post', dict(
            account=books['card'], date='2026-03-06', amount='10.00',
            expenses=[{'account': books['first'], 'amount': '10.00'}]), reason='Wrong account')
    assert raised.value.code == 'E_VALIDATION'
    assert 'bank account' in raised.value.details['fields'][0]['problem']

    with pytest.raises(BookflowError) as raised:
        books['run']('card-charge post', dict(
            account=books['bank'], date='2026-03-06', amount='10.00',
            expenses=[{'account': books['first'], 'amount': '10.00'}]), reason='Wrong account')
    assert raised.value.code == 'E_VALIDATION'
    assert 'credit card account' in raised.value.details['fields'][0]['problem']


def test_voiding_a_check_reverses_it_at_its_own_date_and_keeps_the_history(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    books['run']('card-charge post', dict(
        account=books['card'], date='2026-03-05', amount=CHARGE,
        expenses=[{'account': books['second'], 'amount': CHARGE}]), reason='Record a card purchase')

    voided = books['run']('journal void',
                          {'journal': posted['id'], 'expected_version': posted['version']},
                          reason='Wrong vendor')
    assert voided['status'] == 'voided'
    kinds = [(batch['kind'], batch['effective_date'], batch['total']['amount'])
             for batch in voided['revision']['batches']]
    assert kinds == [('original', '2026-03-04', CHECK), ('reversal', '2026-03-04', CHECK)]

    # The original revision is still readable, with its lines intact.
    history = books['run']('journal history', {'journal': posted['id'], 'limit': 10})
    assert history['items'][0]['line_count'] == 3

    # Only the card charge is left: 75.25 each side.
    trial = books['run']('report trial-balance', {'date_to': '2026-12-31', 'limit': 200})
    assert trial['totals']['debit']['minor_units'] == 7525
    assert trial['totals']['credit']['minor_units'] == 7525
    assert _row(trial, books['second'])['debit']['amount'] == CHARGE
    assert not [row for row in trial['rows'] if row['account_id'] == books['first']]

    loss = books['run']('report profit-and-loss',
                        {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})
    assert loss['totals']['expense']['amount'] == CHARGE


def test_a_class_typed_on_a_line_is_that_line_s_class(books):
    """Choosing a class in the Class column is what a person means by it; nothing else is asked."""
    job = books['run']('class create', {'name': 'Riverside job'})['id']
    overhead = books['run']('class create', {'name': 'Overhead'})['id']
    posted = books['run']('check post', dict(
        account=books['bank'], date='2026-03-04', amount=CHECK, class_id=overhead,
        expenses=[{'account': books['first'], 'amount': FIRST, 'class_id': job},
                  {'account': books['second'], 'amount': SECOND}]),
        reason='Pay Northside Supply')
    classes = {line['account_id']: line['class_id'] for line in posted['revision']['lines']}
    assert classes[books['first']] == job          # its own
    assert classes[books['second']] == overhead    # the document's
    assert classes[books['bank']] is None          # the funding line never carries one

    unclassed = books['run']('check post', dict(
        account=books['bank'], date='2026-03-06', amount=SECOND, class_id=overhead,
        expenses=[{'account': books['second'], 'amount': SECOND, 'class_mode': 'none'}]),
        reason='Pay Northside Supply')
    assert unclassed['revision']['lines'][1]['class_id'] is None

    with pytest.raises(BookflowError) as raised:
        books['run']('check post', dict(
            account=books['bank'], date='2026-03-07', amount=SECOND,
            expenses=[{'account': books['second'], 'amount': SECOND, 'class_mode': 'value'}]),
            reason='Pay Northside Supply')
    assert raised.value.code == 'E_VALIDATION'


def test_a_posted_check_can_be_corrected_the_way_its_own_help_says(books):
    """`check post` tells the reader to correct with `register update`; that has to be true."""
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    selected = posted['revision']['lines'][0]['line_id']
    corrected = books['run']('register update', dict(
        journal=posted['id'], expected_version=posted['version'], selected_line_id=selected,
        account=books['bank'], date='2026-03-04', number='1042', memo='March supplies',
        payee={'name_type': 'vendor', 'name_id': books['vendor']},
        direction='decrease', amount='300.00',
        allocations=[{'line_id': line['line_id'], 'account': line['account_id'],
                      'amount': '200.00' if index == 0 else SECOND, 'memo': line['description']}
                     for index, line in enumerate(posted['revision']['lines'][1:])]),
        reason='Corrected the parts line')
    assert corrected['version'] == 2
    assert _net(corrected['revision']) == {books['bank']: -30000, books['first']: 20000,
                                           books['second']: 10000}
    trial = books['run']('report trial-balance', {'date_to': '2026-12-31', 'limit': 200})
    assert trial['totals']['debit']['minor_units'] == 30000


def test_a_check_posts_the_same_lines_the_register_split_would(books):
    """The document is the register split under another name, not a second posting path."""
    document = books['run']('check post', _check(books), reason='Pay Northside Supply')
    split = books['run']('register post', dict(
        account=books['bank'], payee={'name_type': 'vendor', 'name_id': books['vendor']},
        date='2026-03-04', amount=CHECK, memo='March supplies', direction='decrease',
        allocations=[{'account': books['first'], 'amount': FIRST, 'memo': 'Parts'},
                     {'account': books['second'], 'amount': SECOND, 'memo': 'Fuel'}]),
        reason='The same thing by hand')
    volatile = {'id', 'line_id', 'document_line_id', 'transaction_id', 'revision_id', 'number',
                'created_at', 'audit_event_id', 'position'}

    def scrub(line):
        return {key: value for key, value in line.items() if key not in volatile}

    assert [scrub(line) for line in document['revision']['lines']] == \
           [scrub(line) for line in split['revision']['lines']]


def test_the_expenses_collection_leaves_room_for_a_second_line_kind(books):
    """An Items tab attaches as a sibling collection; nothing here is named ``lines``."""
    fields = registry.get('check post').input_model.model_fields
    assert 'expenses' in fields and 'lines' not in fields
    from bookflow.adapters.workbench import document_form as Document
    assert dict(Document.EXPENSE_GRID)['account'] == 'Account'
    assert Document.layout('check', [
        {'path': 'expenses', 'kind': 'collection',
         'collection': {'item': {'kind': 'object', 'fields': []}}}])['lines_title'] == 'Expenses'


# ---------------------------------------------------------------- every surface, same result

COMMANDS = frozenset(('check post', 'card-charge post'))


@pytest.mark.timeout(300)
def test_the_same_check_and_card_charge_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                bank = (await matrix.call(surface, 'account create',
                                          dict(name='Parity bank', type='bank')))['id']
                card = (await matrix.call(surface, 'account create',
                                          dict(name='Parity card', type='credit_card')))['id']
                expense = (await matrix.call(surface, 'account create',
                                             dict(name='Parity supplies', type='expense')))['id']
                check = dict(account=bank, date='2026-03-04', number='PARITY-1', amount=CHECK,
                             expenses=[{'account': expense, 'amount': FIRST},
                                       {'account': expense, 'amount': SECOND}])
                assert (await call('check post', check, dry_run=True))['dry_run']
                posted = await call('check post', check, idempotency_key='money-out-1')
                replay = await call('check post', check, idempotency_key='money-out-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                charge = dict(account=card, date='2026-03-05', amount=CHARGE,
                              expenses=[{'account': expense, 'amount': CHARGE}])
                await call('card-charge post', charge)
                short = {**check, 'number': 'PARITY-2',
                         'expenses': [{'account': expense, 'amount': FIRST}]}
                refused = await call('check post', short, rejected=True)
                assert refused['code'] == 'E_UNBALANCED_ENTRY'
                assert refused['details']['difference']['amount'] == SECOND
                assert set(calls) == COMMANDS
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
