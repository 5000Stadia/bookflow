"""Move money between two of the company's own accounts, and check the books by hand.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything: 500.00 moved from Checking to Savings, 250.00 sent from Checking to
a credit card, and what the trial balance and the profit and loss say before and after a
transfer is voided.

The property most worth holding is the one a transfer is most likely to break: a transfer is
neither income nor expense, so the profit and loss must read exactly the same after it as
before it.
"""
from copy import deepcopy

import anyio
import pytest

import bookflow
from bookflow.company import transfers
from bookflow.company.accounts import AccountType
from bookflow.core import registry
from bookflow.core.errors import BookflowError

# The transfers this module posts. Everything below is arithmetic on these.
MOVED = '500.00'
CARD_PAYMENT = '250.00'
CHARGE = '400.00'
SALE = '900.00'


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    root = tmp_path / 'transfer'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Transfer organization')
    company = client.company.new(legal_name='Transfer', home_currency='USD',
                                 organization='Transfer organization', timezone='UTC',
                                 chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    checking = next(row['id'] for row in accounts if row['type'] == 'bank')
    savings = client.account.create(name='Savings', type='bank', company=company)['id']
    card = client.account.create(name='Company Card', type='credit_card', company=company)['id']
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    expense = next(row['id'] for row in accounts if row['type'] == 'expense')
    receivable = next(row['id'] for row in accounts if row['type'] == 'accounts_receivable')

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, checking=checking, savings=savings, card=card,
                income=income, expense=expense, receivable=receivable, run=run)


def _move(books, **extra):
    return dict(from_account=books['checking'], to_account=books['savings'],
                date='2026-05-04', amount=MOVED, memo='Sweep to savings', **extra)


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


def _trial(books):
    return books['run']('report trial-balance', {'date_to': '2026-12-31', 'limit': 200})


def _loss(books):
    return books['run']('report profit-and-loss',
                        {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})


def _stand_up_a_profit(books):
    """A sale and a card purchase, so the profit and loss has something to be unchanged."""
    books['run']('journal post', dict(date='2026-05-01', memo='Cash sale', lines=[
        {'account': books['checking'], 'side': 'debit', 'amount': SALE},
        {'account': books['income'], 'side': 'credit', 'amount': SALE}]), reason='Record a sale')
    books['run']('card-charge post', dict(
        account=books['card'], date='2026-05-02', amount=CHARGE,
        expenses=[{'account': books['expense'], 'amount': CHARGE}]), reason='Card purchase')


# ----------------------------------------------------------------- the transfer, by hand

def test_moving_money_takes_it_out_of_one_account_and_puts_it_in_the_other(books):
    posted = books['run']('transfer post', _move(books), reason='Sweep to savings')

    assert posted['status'] == 'posted'
    # 500.00 out of Checking and 500.00 into Savings: Checking is credited 50000 minor units
    # and Savings is debited 50000, so the two sides are both 500.00.
    assert _net(posted['revision']) == {books['checking']: -50000, books['savings']: 50000}
    assert posted['revision']['debit_total']['amount'] == MOVED
    assert posted['revision']['credit_total']['amount'] == MOVED
    assert [line['side'] for line in posted['revision']['lines']] == ['credit', 'debit']
    # Two legs and no more: there is no line grid to add a third with.
    assert len(posted['revision']['lines']) == 2
    # The memo a person typed once reads on both legs, the way it does in a register.
    assert [line['description'] for line in posted['revision']['lines']] == \
        ['Sweep to savings', 'Sweep to savings']
    # A transfer names nobody.
    assert all(line['name_id'] is None for line in posted['revision']['lines'])

    # The document's own footer figures, computed by the server and never in the browser.
    assert posted['document'] == {
        'kind': 'transfer', 'currency': 'USD',
        'amount': {'amount': MOVED, 'currency': 'USD', 'minor_units': 50000},
        'from_account': {'account_id': books['checking'], 'name': 'Checking', 'type': 'bank',
                         'normal_balance': 'debit', 'side': 'credit', 'effect': 'decrease'},
        'to_account': {'account_id': books['savings'], 'name': 'Savings', 'type': 'bank',
                       'normal_balance': 'debit', 'side': 'debit', 'effect': 'increase'}}

    # The audit trail names the document a person wrote, not the register underneath it.
    events = books['client'].audit.list(company=books['company'], limit=5)['items']
    assert events[0]['command'] == 'transfer post'


def test_a_transfer_leaves_the_profit_and_loss_exactly_where_it_found_it(books):
    _stand_up_a_profit(books)
    # 900.00 of income and 400.00 of expense: net income 500.00.
    before = _loss(books)
    assert before['totals']['income']['minor_units'] == 90000
    assert before['totals']['expense']['minor_units'] == 40000
    assert before['totals']['net_income']['amount'] == '500.00'

    books['run']('transfer post', _move(books), reason='Sweep to savings')
    books['run']('transfer post', dict(
        from_account=books['checking'], to_account=books['card'], date='2026-05-05',
        amount=CARD_PAYMENT, memo='Pay the card down'), reason='Pay the card down')

    after = _loss(books)
    assert after['totals'] == before['totals']
    assert after['rows'] == before['rows']
    assert after['totals']['net_income']['amount'] == '500.00'

    # And the trial balance still balances, with both transfers in it. Each account's own
    # net: Checking 900.00 - 500.00 - 250.00 = 150.00 debit; Savings 500.00 debit; Expense
    # 400.00 debit; Card 400.00 - 250.00 = 150.00 credit; Income 900.00 credit.
    trial = _trial(books)
    # Debits 150.00 + 500.00 + 400.00 = 1050.00. Credits 150.00 + 900.00 = 1050.00.
    assert trial['totals']['debit']['minor_units'] == 105000
    assert trial['totals']['credit']['minor_units'] == 105000
    assert trial['totals']['signed_net']['minor_units'] == 0
    # Checking: 900.00 in, 500.00 to savings, 250.00 to the card, so 150.00 left.
    assert _row(trial, books['checking'])['debit']['amount'] == '150.00'
    assert _row(trial, books['savings'])['debit']['amount'] == MOVED
    # The card was charged 400.00 and paid 250.00, so 150.00 is still owed.
    assert _row(trial, books['card'])['credit']['amount'] == '150.00'


def test_paying_a_card_down_from_a_bank_gets_both_signs_right(books):
    """A card is credit-normal: money sent to it is a debit, and what is owed goes down."""
    _stand_up_a_profit(books)
    posted = books['run']('transfer post', dict(
        from_account=books['checking'], to_account=books['card'], date='2026-05-05',
        amount=CARD_PAYMENT, memo='Pay the card down'), reason='Pay the card down')

    # Checking is credited 250.00 and the card is debited 250.00.
    assert _net(posted['revision']) == {books['checking']: -25000, books['card']: 25000}
    assert posted['document']['from_account'] == {
        'account_id': books['checking'], 'name': 'Checking', 'type': 'bank',
        'normal_balance': 'debit', 'side': 'credit', 'effect': 'decrease'}
    assert posted['document']['to_account'] == {
        'account_id': books['card'], 'name': 'Company Card', 'type': 'credit_card',
        'normal_balance': 'credit', 'side': 'debit', 'effect': 'decrease'}
    # 400.00 was charged and 250.00 paid, so 150.00 is still owed on the card.
    assert _row(_trial(books), books['card'])['credit']['amount'] == '150.00'
    assert _loss(books)['totals']['net_income']['amount'] == '500.00'

    # The other direction is a cash advance: the card is credited, so what is owed goes up.
    advance = books['run']('transfer post', dict(
        from_account=books['card'], to_account=books['checking'], date='2026-05-06',
        amount='100.00', memo='Cash advance'), reason='Take a cash advance')
    assert _net(advance['revision']) == {books['card']: -10000, books['checking']: 10000}
    assert advance['document']['from_account']['effect'] == 'increase'
    assert advance['document']['to_account']['effect'] == 'increase'
    # 150.00 owed plus a 100.00 advance is 250.00.
    assert _row(_trial(books), books['card'])['credit']['amount'] == CARD_PAYMENT
    assert _loss(books)['totals']['net_income']['amount'] == '500.00'


# ------------------------------------------------------------------------- the refusals

def test_the_same_account_at_both_ends_is_refused_by_name(books):
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer post', dict(
            from_account=books['checking'], to_account=books['checking'],
            date='2026-05-04', amount=MOVED), reason='Nowhere to go')
    err = raised.value
    assert err.code == 'E_VALIDATION'
    assert 'Checking' in err.message and 'both ends' in err.message
    assert err.details['fields'] == [{'field': 'to_account',
                                      'problem': '"Checking" is named at both ends'}]
    assert err.details['account_id'] == books['checking']
    assert _trial(books)['totals']['debit']['minor_units'] == 0


def test_an_account_that_cannot_be_an_end_is_refused_naming_it_and_why(books):
    for field, other in (('to_account', 'from_account'), ('from_account', 'to_account')):
        with pytest.raises(BookflowError) as raised:
            books['run']('transfer post', {field: books['income'], other: books['checking'],
                                           'date': '2026-05-04', 'amount': MOVED},
                         reason='Not a transfer')
        err = raised.value
        assert err.code == 'E_VALIDATION'
        assert err.details['fields'][0]['field'] == field
        assert err.details['account_type'] == 'income'
        assert err.details['account_name'] in err.message
        assert 'is an income account' in err.message
        assert 'never changes profit' in err.message
        assert 'sales-receipt post' in err.message

    # An expense account says the same thing and points at the documents that do spend money.
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer post', dict(
            from_account=books['checking'], to_account=books['expense'],
            date='2026-05-04', amount=MOVED), reason='Not a transfer')
    assert 'is an expense account' in raised.value.message
    assert 'check post' in raised.value.message

    # Receivable is a balance-sheet account and is still refused: its lines name a customer.
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer post', dict(
            from_account=books['receivable'], to_account=books['checking'],
            date='2026-05-04', amount=MOVED), reason='Not a transfer')
    assert 'accounts receivable account' in raised.value.message
    assert 'names the customer' in raised.value.message
    assert 'payment receive' in raised.value.message

    assert _trial(books)['totals']['debit']['minor_units'] == 0


def test_every_account_type_is_either_eligible_or_refused_with_a_reason():
    """A new account type cannot fall through the eligibility rule unnoticed."""
    every = set(AccountType.__args__)
    assert transfers.ELIGIBLE <= every
    assert set(transfers.REFUSED) <= every
    assert transfers.ELIGIBLE | set(transfers.REFUSED) == every
    assert not transfers.ELIGIBLE & set(transfers.REFUSED)
    # Nothing eligible reaches a profit and loss.
    from bookflow.company.accounts import STATEMENT_FAMILY
    assert {STATEMENT_FAMILY[kind] for kind in transfers.ELIGIBLE} == {'balance_sheet'}


def test_a_transfer_into_a_closed_period_is_refused(books):
    shown = books['run']('company show', {})
    books['run']('company update',
                 {'closing_date': '2026-05-31', 'expected_version': shown['version']},
                 reason='Close May')
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer post', _move(books), reason='Sweep to savings')
    assert raised.value.code == 'E_PERIOD_CLOSED'
    assert _trial(books)['totals']['debit']['minor_units'] == 0


def test_an_inactive_account_is_refused_and_nothing_is_written(books):
    books['run']('account deactivate', {'account': books['savings']}, reason='Closed')
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer post', _move(books), reason='Sweep to savings')
    assert raised.value.code == 'E_INACTIVE_REFERENCE'
    assert _trial(books)['totals']['debit']['minor_units'] == 0


# ------------------------------------------------------------------------------ the void

def test_voiding_a_transfer_reverses_it_at_its_own_date_and_keeps_the_history(books):
    _stand_up_a_profit(books)
    posted = books['run']('transfer post', _move(books), reason='Sweep to savings')
    before = _loss(books)

    voided = books['run']('journal void',
                          {'journal': posted['id'], 'expected_version': posted['version']},
                          reason='Swept the wrong way')
    assert voided['status'] == 'voided'
    kinds = [(batch['kind'], batch['effective_date'], batch['total']['amount'])
             for batch in voided['revision']['batches']]
    assert kinds == [('original', '2026-05-04', MOVED), ('reversal', '2026-05-04', MOVED)]

    # The original revision is still readable, with both of its legs intact.
    history = books['run']('journal history', {'journal': posted['id'], 'limit': 10})
    assert history['items'][0]['line_count'] == 2

    # Savings is back to nothing and Checking has the whole 900.00 again.
    trial = _trial(books)
    assert not [row for row in trial['rows'] if row['account_id'] == books['savings']]
    assert _row(trial, books['checking'])['debit']['amount'] == SALE
    # A transfer changed no profit, so undoing it changes none either.
    assert _loss(books)['totals'] == before['totals']


# ------------------------------------------------------------- one posting path, not two

def test_a_transfer_posts_the_same_lines_the_register_entry_would(books):
    """The document is the register entry under another name, not a second posting path."""
    document = books['run']('transfer post', _move(books), reason='Sweep to savings')
    entry = books['run']('register post', dict(
        account=books['checking'], date='2026-05-04', amount=MOVED, memo='Sweep to savings',
        direction='decrease', category=books['savings']), reason='The same thing by hand')
    volatile = {'id', 'line_id', 'document_line_id', 'transaction_id', 'revision_id', 'number',
                'created_at', 'audit_event_id', 'position'}

    def scrub(line):
        return {key: value for key, value in line.items() if key not in volatile}

    assert [scrub(line) for line in document['revision']['lines']] == \
           [scrub(line) for line in entry['revision']['lines']]


def test_a_posted_transfer_can_be_corrected_the_way_its_own_help_says(books):
    """`transfer post` tells the reader to correct with `register update`; that has to be true."""
    posted = books['run']('transfer post', _move(books), reason='Sweep to savings')
    selected = posted['revision']['lines'][0]['line_id']
    corrected = books['run']('register update', dict(
        journal=posted['id'], expected_version=posted['version'], selected_line_id=selected,
        account=books['checking'], date='2026-05-04', memo='Sweep to savings',
        direction='decrease', amount='600.00',
        category=books['savings'],
        category_line_id=posted['revision']['lines'][1]['line_id']),
        reason='Swept more than that')
    assert corrected['version'] == 2
    assert _net(corrected['revision']) == {books['checking']: -60000, books['savings']: 60000}
    assert _trial(books)['totals']['debit']['minor_units'] == 60000


def test_a_transfer_carries_no_line_grid_no_payee_and_no_number(books):
    fields = registry.get('transfer post').input_model.model_fields
    assert set(fields) == {'from_account', 'to_account', 'date', 'amount', 'memo'}
    from bookflow.adapters.workbench import document_form as Document
    placed = Document.layout('transfer', [{'path': path, 'kind': 'scalar'} for path in
                                          ('from_account', 'to_account', 'date', 'amount', 'memo')])
    assert [leaf['path'] for leaf in placed['primary']] == \
        ['from_account', 'to_account', 'date', 'amount']
    assert [leaf['path'] for leaf in placed['footer']] == ['memo']
    assert placed['lines'] is None and placed['columns'] == [] and placed['record'] == []


def test_the_footer_says_what_each_end_did_in_the_words_that_end_uses(books):
    from bookflow.adapters.workbench import document_form as Document
    posted = books['run']('transfer post', dict(
        from_account=books['checking'], to_account=books['card'], date='2026-05-05',
        amount=CARD_PAYMENT, memo='Pay the card down'), reason='Pay the card down')
    rows, said, reconciled = Document.transfer_totals(posted)
    assert rows == [{'label': 'Out of Checking', 'value': '250.00 USD', 'strong': False},
                    {'label': 'Into Company Card', 'value': '250.00 USD', 'strong': True}]
    assert said == ('Checking goes down 250.00 USD and what you owe on Company Card goes down '
                    '250.00 USD. Neither end is income or expense, so this changes no profit.')
    assert reconciled is True
    # Nothing computed means nothing shown; the footer never invents a figure.
    assert Document.transfer_totals(None) == ([], None, None)


# ---------------------------------------------------------------- every surface, same result

COMMANDS = frozenset(('transfer post',))


@pytest.mark.timeout(300)
def test_the_same_transfer_through_python_cli_http_and_mcp(root, tmp_path):
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

                checking = (await matrix.call(surface, 'account create',
                                              dict(name='Parity checking', type='bank')))['id']
                savings = (await matrix.call(surface, 'account create',
                                             dict(name='Parity savings', type='bank')))['id']
                income = (await matrix.call(surface, 'account create',
                                            dict(name='Parity income', type='income')))['id']
                move = dict(from_account=checking, to_account=savings, date='2026-05-04',
                            amount=MOVED, memo='Sweep to savings')
                assert (await call('transfer post', move, dry_run=True))['dry_run']
                posted = await call('transfer post', move, idempotency_key='transfer-1')
                replay = await call('transfer post', move, idempotency_key='transfer-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                wrong = {**move, 'to_account': income}
                refused = await call('transfer post', wrong, rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert 'Parity income' in refused['message']
                same = {**move, 'to_account': checking}
                assert (await call('transfer post', same, rejected=True))['code'] == 'E_VALIDATION'
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
