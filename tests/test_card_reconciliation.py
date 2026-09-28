"""R136: a credit card account reconciles against its statement as a bank account does.

The card, worked by hand. Adopted at zero on 2026-05-31; June statement dated 2026-06-30:

    2026-06-02  card charge, parts              +120.00
    2026-06-05  card credit, valve returned      -45.50
    2026-06-10  card charge, fuel                +30.00
    2026-06-15  bill paid from the card          +25.00
    2026-06-20  payment to the card from bank    -50.00
                                                --------
                statement ending balance (owed)   79.50

    2026-07-02  card charge, parts after the statement date   +10.00  (not on June)

On a card the statement counts what is owed, so a charge moves the balance up and a credit or a
payment moves it down. July's statement opens at June's 79.50 and ends at 89.50.
"""
from bookflow.core.ids import new_id
from tests.test_card_credits import books, charge, credit  # noqa: F401


def _call(books, name, data):
    return books['run'](name, data, reason='R136 reconciliation')


def _candidates(books, draft):
    rows, cursor = [], None
    while True:
        page = books['run']('reconcile candidates', dict(draft=draft, limit=200, **({'cursor': cursor} if cursor else {})))
        rows.extend(page['items'])
        cursor = page['next_cursor']
        if not cursor:
            return rows


def _statement(books, statement_date, ending_balance, opening_draft=None):
    extra = dict(opening_draft_id=opening_draft) if opening_draft else {}
    started = _call(books, 'reconcile start', dict(operation_key=new_id(), account=books['card'],
                                                    statement_date=statement_date, ending_balance=ending_balance, **extra))
    draft = started['draft']['id']
    rows = _candidates(books, draft)
    marked = _call(books, 'reconcile mark', dict(operation_key=new_id(), draft=draft, expected_version=1, entries=[
        dict(movement=row['movement'], group_fingerprint=row['group_fingerprint'], action='mark')
        for row in rows if row['eligible'] and not row['claimed']]))
    preview = books['run']('reconcile preview', dict(draft=draft, expected_version=marked['draft']['version']))
    finished = _call(books, 'reconcile finish', dict(
        operation_key=new_id(), draft=draft, expected_version=marked['draft']['version'],
        expected_facts_fingerprint=preview['expected_facts_fingerprint'], dependency_guard=preview['dependency_guard']))
    return rows, preview, finished


def test_a_card_statement_clears_charges_credits_bill_payments_and_card_payments(books):
    run = books['run']
    charge(books)                                             # +120.00
    credit(books)                                             # -45.50
    charge(books, amount='30.00', date='2026-06-10')          # +30.00
    bill = run('bill post', dict(vendor=books['vendor'], date='2026-06-12',
                                 expenses=[dict(account=books['parts'], amount='25.00')]), reason='Enter the bill')
    method = next(row['id'] for row in run('payment-method query', dict(limit=50))['items'] if row['name'] == 'Credit Card')
    run('bill pay', dict(date='2026-06-15', funding_account=books['card'], method=method,
                         bills=[dict(bill=bill['id'], amount='25.00', expected_version=bill['version'])]), reason='Pay on the card')
    run('transfer post', dict(from_account=books['bank'], to_account=books['card'], date='2026-06-20', amount='50.00',
                              memo='Pay the card down'), reason='Pay the card')
    charge(books, amount='10.00', date='2026-07-02')          # after the June statement date

    opening = _call(books, 'reconcile opening start', dict(
        operation_key=new_id(), account=books['card'], opening_date='2026-05-31', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='Card adopted at zero'), references=[]))
    rows, preview, finished = _statement(books, '2026-06-30', '79.50', opening['draft']['id'])
    # The June statement sees exactly the five June movements, each signed as what is owed moves.
    assert sorted(row['amount'] for row in rows) == [-5000, -4550, 2500, 3000, 12000]
    totals = preview['totals']
    assert preview['balanced'] is True
    assert (totals['beginning_balance'], totals['cleared_balance'], totals['ending_balance'], totals['difference']) \
        == (0, 7950, 7950, 0)
    assert (totals['positive_sum'], totals['negative_sum']) == (17500, -9550)
    assert finished['totals']['difference'] == 0
    # The card's own balance on the ledger agrees: 79.50 owed at June 30.
    report = run('report trial-balance', dict(date_to='2026-06-30', limit=200))
    card = next(row for row in report['rows'] if row['account_id'] == books['card'])
    assert card['credit']['minor_units'] - card['debit']['minor_units'] == 7950

    # July opens where June closed and clears only the July charge.
    rows, preview, finished = _statement(books, '2026-07-31', '89.50')
    assert [row['amount'] for row in rows if row['eligible'] and not row['claimed']] == [1000]
    totals = preview['totals']
    assert (totals['beginning_balance'], totals['cleared_balance'], totals['difference']) == (7950, 8950, 0)
    assert finished['totals']['ending_balance'] == 8950
