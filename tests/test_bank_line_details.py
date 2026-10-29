"""R108 increment 5: add more details from a bank line (design/bank-feeds.md §4, §11).

A waiting line of money in becomes a deposit of the Undeposited Funds payments it banked (`bank-line deposit`, through
`deposit post`); a line of money out marks bills paid (`bank-line pay-bills`, through `bill pay`). The selection must
come to the line exactly or is refused showing the difference remaining (a deposit may instead name an adjustment
row); a payment already deposited is refused by `deposit post`'s own code; one click is the same as typing (criterion
5: the same postings and document lines as the typed command); and reversing the import, or undoing the act, returns
the payments to Undeposited Funds and leaves the bills open again.
"""
import json

from tests.test_bank_feeds import books, csv_file, ofx, run, table
from tests.test_bank_feed_reversal import grant
from tests.test_bank_line_sort import entry, imp, inbox_line, line_of, refused, source_ref
from tests.test_payment_receipts import method
from tests.test_service_sales_lifecycle import sale  # noqa: F401 - fixture


def receive(client, sale, cash, amount, key, day='2026-10-02'):
    return run(client, 'payment receive', dict(customer=sale['customer'], date=day, amount=amount,
                                               payment_method=cash, operation_key=key))['id']


def undeposited(client):
    """The ids of every receipt waiting in Undeposited Funds, as `deposit sources` lists them."""
    return json.dumps(run(client, 'deposit sources', dict(date='2026-10-31', limit=200)))


def test_a_deposit_line_matches_three_payments_with_no_difference_and_reversal_returns_them(client, sale):
    grant(client, 'transaction.deposit.delete')
    cash = method(client)
    three = [receive(client, sale, cash, a, f'r108-5-{n}') for n, a in enumerate(('100.00', '250.00', '75.50'))]
    twins = [receive(client, sale, cash, a, f'r108-5-twin-{n}') for n, a in enumerate(('100.00', '250.00', '75.50'))]
    small, spare = (receive(client, sale, cash, a, f'r108-5-{k}') for k, a in (('small', '55.00'), ('spare', '100.00')))
    start = balances(client)
    done = imp(client, 'Checking', csv_file([('2026-10-07', 'DEPOSIT', '425.50'), ('2026-10-08', 'DEPOSIT', '60.00'),
                                             ('2026-10-09', 'DEPOSIT', '95.00')]))
    line, more, less = (line_of(done, n) for n in (42550, 6000, 9500))
    for name, kind in (('R108 interest', 'income'), ('R108 bank charges', 'expense')):
        client.account.create(name=name, type=kind, company='Demo Plumbing Co')
    before = books(client)

    # Difference remaining: two of the three come to 350.00 against 425.50; refused, showing 75.50, dry run too.
    for dry in (True, False):
        with_two = refused_dry(client, 'bank-line deposit', dict(line=line['id'], payments=three[:2]), dry)
        assert with_two.details['difference'] == '75.50' and with_two.details['selected'] == '350.00'
        assert '75.50 remaining' in with_two.message
    assert books(client) == before and inbox_line(client, line['id'], 'Checking')['state'] == 'waiting'

    # An adjustment with no difference to adjust is refused, naming it, rather than dropped.
    nothing = refused(client, 'bank-line deposit', dict(line=line['id'], payments=three,
                                                        adjustment_account='R108 interest'))
    assert nothing.details['field'] == 'adjustment_account'

    # All three: difference 0.00, one deposit, the line added and linked to it.
    preview = run(client, 'bank-line deposit', dict(line=line['id'], payments=three), dry_run=True)
    assert preview['difference'] == '0.00' and preview['entries'][0]['command'] == 'deposit post'
    assert books(client) == before
    added = run(client, 'bank-line deposit', dict(line=line['id'], payments=three))
    assert (added['line_amount'], added['selected'], added['difference']) == ('425.50', '425.50', '0.00')
    deposit = added['entries'][0]['entry_id']
    shown = added['lines'][0]
    assert shown['state'] == 'added' and shown['created'] and shown['entry_id'] == deposit
    assert source_ref(client, deposit, 'deposit post') == f"bank-line:{line['id']}"
    waiting = undeposited(client)
    assert not any(p in waiting for p in three) and all(p in waiting for p in twins)

    # One click is the same as typing: the twins deposited by `deposit post` post the same.
    typed = run(client, 'deposit post', dict(operation_key='r108-5-typed', document=dict(
        mode='inline', deposit_to='Checking', date='2026-10-07', memo='DEPOSIT', sources=[
            dict(source_type='payment', source=p, expected_version=1) for p in twins])))['deposit']['id']
    assert entry(client, deposit) == entry(client, typed)

    # A payment already deposited is refused by deposit post's own code; the line waits, nothing changes.
    after = books(client)
    claimed = refused(client, 'bank-line deposit', dict(line=more['id'], payments=[three[0]]),
                      code=_code_of(client, three[0]))
    assert claimed.code.startswith('E_DEPOSIT_SOURCE')
    assert books(client) == after and inbox_line(client, more['id'], 'Checking')['state'] == 'waiting'

    # An adjustment enters the difference as its own row: more than the payments asks who from, then other money;
    # less, money kept back (a fee).
    asked = refused(client, 'bank-line deposit', dict(line=more['id'], payments=[small],
                                                      adjustment_account='R108 interest'))
    assert asked.details['ask'] == ['adjustment_payee']
    extra = run(client, 'bank-line deposit', dict(line=more['id'], payments=[small],
                                                  adjustment_account='R108 interest', adjustment_payee=sale['customer'],
                                                  adjustment_payee_type='customer'))
    assert extra['difference'] == '5.00'
    assert extra['entries'][0]['input']['document']['additional'][0]['amount'] == '5.00'
    # What was posted: the bank moves by exactly the line, the other money credited to the adjustment account.
    made = extra['entries'][0]['entry_id']
    assert moved(client, made, 'Checking') == 6000 and moved(client, made, 'R108 interest') == -500
    # Money kept back takes an account and a memo only: a payee or class there is refused, naming it.
    for field, value in (('adjustment_payee', sale['customer']), ('adjustment_class_id', 'Operations')):
        dropped = refused(client, 'bank-line deposit', dict(line=less['id'], payments=[spare],
                                                            adjustment_account='R108 bank charges', **{field: value}))
        assert dropped.details['field'] == field
    fee = run(client, 'bank-line deposit', dict(line=less['id'], payments=[spare],
                                                adjustment_account='R108 bank charges', adjustment_memo='Deposit fee'))
    assert fee['difference'] == '-5.00' and fee['entries'][0]['input']['document']['cash_back']['amount'] == '5.00'
    made = fee['entries'][0]['entry_id']
    assert moved(client, made, 'Checking') == 9500 and moved(client, made, 'R108 bank charges') == 500

    # Undo the fee act alone: its payment is waiting again, the line too.
    run(client, 'bank-line undo', dict(batch=fee['batch_id']))
    assert spare in undeposited(client) and inbox_line(client, less['id'], 'Checking')['state'] == 'waiting'

    # Reversing the import deletes the deposits (preserved history) and returns the three payments.
    out = run(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    assert out['state'] == 'reversed'
    waiting = undeposited(client)
    assert all(p in waiting for p in three)
    assert table(client, 'SELECT count(*) FROM deposit_deletions WHERE transaction_id = ?', deposit) == [(1,)]
    # Criterion 2: every account's balance is as before the import (the typed twin aside).
    assert balances(client, typed) == start


def test_bills_marked_paid_from_a_line_are_the_typed_payment_and_reversal_opens_them_again(client):
    vendor = run(client, 'vendor create', dict(name='R108 inc 5 vendor'))['id']
    other = run(client, 'vendor create', dict(name='R108 inc 5 other vendor'))['id']

    def bill(who, amount):
        return run(client, 'bill post', dict(vendor=who, date='2026-09-20',
                                             expenses=[dict(account='Office Supplies', amount=amount)]))['id']
    bills, twins, stray = [bill(vendor, '40.00'), bill(vendor, '60.00')], [bill(vendor, '40.00'), bill(vendor, '60.00')], \
        bill(other, '25.00')
    transfer = next(x['id'] for x in run(client, 'payment-method query', dict(limit=50))['items']
                    if x['name'] == 'Bank Transfer')
    start = balances(client)
    done = imp(client, 'Checking', csv_file([('2026-10-06', 'ONLINE PMT R108 VENDOR', '-100.00')]))
    line = done['lines'][0]
    before = books(client)

    # No method asks; two vendors are two payments, refused; a selection short of the line shows the difference.
    asked = refused(client, 'bank-line pay-bills', dict(line=line['id'], bills=[dict(bill=b) for b in bills]))
    assert asked.details['ask'] == ['method']
    two = refused(client, 'bank-line pay-bills', dict(line=line['id'], method=transfer,
                                                      bills=[dict(bill=bills[0]), dict(bill=stray)]))
    assert 'one vendor' in two.message
    short = refused(client, 'bank-line pay-bills', dict(line=line['id'], method=transfer, bills=[
        dict(bill=bills[0]), dict(bill=bills[1], amount='30.00')]))
    assert (short.details['selected'], short.details['difference']) == ('70.00', '30.00')
    assert books(client) == before

    paid = run(client, 'bank-line pay-bills', dict(line=line['id'], method=transfer,
                                                   bills=[dict(bill=b) for b in bills]))
    payment = paid['entries'][0]['entry_id']
    assert paid['difference'] == '0.00' and paid['lines'][0]['state'] == 'added'
    assert paid['entries'][0]['command'] == 'bill pay'
    assert source_ref(client, payment, 'bill pay') == f"bank-line:{line['id']}"
    assert all(run(client, 'bill show', dict(bill=b))['settlement_current']['status'] == 'paid' for b in bills)
    # One click is the same as typing: the same postings, and the same document lines but for the bill each names.
    typed = run(client, 'bill pay', dict(date='2026-10-06', bills=[dict(bill=b) for b in twins],
                                         funding_account='Checking', method=transfer,
                                         memo='ONLINE PMT R108 VENDOR'))['payments'][0]['id']
    numbers = {b: run(client, 'bill show', dict(bill=b))['number'] for b in bills + twins}
    clicked, keyed = entry(client, payment), entry(client, typed)
    assert clicked[:3] == keyed[:3]
    assert [r[:-1] for r in clicked[3]] == [r[:-1] for r in keyed[3]]
    assert [r[-1] for r in clicked[3]] == [f'Bill {numbers[b]}' for b in bills]
    assert [r[-1] for r in keyed[3]] == [f'Bill {numbers[b]}' for b in twins]

    # Undoing the act unapplies and voids the payment: the bills are open again, the line waits.
    run(client, 'bank-line undo', dict(batch=paid['batch_id']))
    assert run(client, 'bill payment show', dict(payment=payment))['status'] == 'voided'
    assert all(run(client, 'bill show', dict(bill=b))['settlement_current']['status'] != 'paid' for b in bills)
    assert inbox_line(client, line['id'], 'Checking')['state'] == 'waiting'

    # Paid again, then the import reversed: the same.
    again = run(client, 'bank-line pay-bills', dict(line=line['id'], method=transfer,
                                                    bills=[dict(bill=b) for b in bills]))
    assert run(client, 'bank-feed reverse', dict(import_id=done['import_id']))['state'] == 'reversed'
    assert run(client, 'bill payment show', dict(payment=again['entries'][0]['entry_id']))['status'] == 'voided'
    assert all(run(client, 'bill show', dict(bill=b))['settlement_current']['status'] != 'paid' for b in bills)
    assert balances(client, typed) == start


# ------------------------------------------------------------------ helpers

def refused_dry(client, name, data, dry_run):
    import pytest
    from bookflow import BookflowError
    with pytest.raises(BookflowError) as raised:
        run(client, name, data, dry_run=dry_run)
    assert raised.value.code == 'E_VALIDATION', raised.value
    return raised.value


def _code_of(client, payment):
    """deposit post's own refusal for a payment already in a deposit, as typed."""
    import pytest
    from bookflow import BookflowError
    with pytest.raises(BookflowError) as raised:
        run(client, 'deposit post', dict(operation_key='r108-5-probe', document=dict(
            mode='inline', deposit_to='Checking', date='2026-10-08', sources=[
                dict(source_type='payment', source=payment, expected_version=1)])), dry_run=True)
    return raised.value.code



def moved(client, transaction_id, account):
    """What an entry moved an account by, debit positive."""
    return table(client, 'SELECT COALESCE(SUM(p.debit_minor_units - p.credit_minor_units), 0) FROM posting_lines p '
                         'JOIN accounts a ON a.id = p.account_id WHERE p.transaction_id = ? AND a.full_name = ?',
                 transaction_id, account)[0][0]


def balances(client, *besides):
    """Every account's balance, leaving out the named entries."""
    marks = ','.join('?' * len(besides)) or "''"
    return dict(table(client, 'SELECT account_id, SUM(debit_minor_units - credit_minor_units) FROM posting_lines '
                              f'WHERE transaction_id NOT IN ({marks}) GROUP BY account_id '
                              'HAVING SUM(debit_minor_units - credit_minor_units) != 0', *besides))


def transfer_method(client):
    return next(x['id'] for x in run(client, 'payment-method query', dict(limit=50))['items']
                if x['name'] == 'Bank Transfer')


def cheque_method(client):
    return next(x['id'] for x in run(client, 'payment-method query', dict(limit=50))['items'] if x['name'] == 'Check')


def test_a_card_line_a_discount_a_part_payment_and_a_cheque_number_from_the_line(client):
    vendor = run(client, 'vendor create', dict(name='R108 inc 5 card vendor'))['id']

    def bill(amount):
        return run(client, 'bill post', dict(vendor=vendor, date='2026-09-20',
                                             expenses=[dict(account='Office Supplies', amount=amount)]))['id']
    on_card, discounted, part, by_cheque, unnumbered = bill('50.00'), bill('100.00'), bill('30.00'), bill('80.00'), \
        bill('15.00')
    transfer = transfer_method(client)
    # A card line pays a bill from the card: the card's balance rises by the line, as `bill pay` from the card does.
    card = imp(client, 'Business Credit Card', ofx([('2026-10-06', '-50.00', 'C9', 'VENDOR CARD')],
                                                   acct='4111000011112222', card=True))
    paid = run(client, 'bank-line pay-bills', dict(line=card['lines'][0]['id'], method=transfer,
                                                   bills=[dict(bill=on_card)]))
    assert moved(client, paid['entries'][0]['entry_id'], 'Business Credit Card') == -5000
    assert run(client, 'bill show', dict(bill=on_card))['settlement_current']['status'] == 'paid'
    # A discount: 100.00 settled by 98.00 paid and 2.00 taken; a part payment leaves the rest open.
    cheque = ofx([('2026-10-09', '-80.00', 'K1', 'CHECK 4321'), ('2026-10-10', '-15.00', 'K2', 'CHECK')]).replace(
        '<TRNTYPE>OTHER\n<DTPOSTED>20261009', '<TRNTYPE>CHECK\n<CHECKNUM>4321\n<DTPOSTED>20261009').replace(
        '<TRNTYPE>OTHER\n<DTPOSTED>20261010', '<TRNTYPE>CHECK\n<DTPOSTED>20261010')
    done = imp(client, 'Checking', csv_file([('2026-10-07', 'DISC PMT', '-98.00'), ('2026-10-08', 'PART', '-20.00')]))
    disc = run(client, 'bank-line pay-bills', dict(line=line_of(done, -9800)['id'], method=transfer,
                                                   bills=[dict(bill=discounted, discount='2.00')]))
    assert disc['difference'] == '0.00' and moved(client, disc['entries'][0]['entry_id'], 'Checking') == -9800
    assert run(client, 'bill show', dict(bill=discounted))['settlement_current']['status'] == 'paid'
    partly = run(client, 'bank-line pay-bills', dict(line=line_of(done, -2000)['id'], method=transfer,
                                                     bills=[dict(bill=part, amount='20.00')]))
    assert partly['difference'] == '0.00'
    assert run(client, 'bill show', dict(bill=part))['settlement_current']['status'] != 'paid'
    # A cheque the bank has shown keeps its own number, never the chequebook's next; a cheque line without one asks.
    shown = imp(client, 'Checking', cheque)
    numbered = run(client, 'bank-line pay-bills', dict(line=line_of(shown, -8000)['id'], method=cheque_method(client),
                                                       bills=[dict(bill=by_cheque)]))
    assert numbered['entries'][0]['input']['check_number'] == '4321'
    assert run(client, 'bill payment show', dict(payment=numbered['entries'][0]['entry_id']))['check_number'] == '4321'
    before = books(client)
    asked = refused(client, 'bank-line pay-bills', dict(line=line_of(shown, -1500)['id'], method=cheque_method(client),
                                                        bills=[dict(bill=unnumbered)]))
    assert asked.details['ask'] == ['check_number'] and books(client) == before


def test_reconciled_and_later_imports_block_undo_and_reverse_and_change_nothing(client, sale):
    """A reconciled bill payment or transfer is voided by its inverse, which a reconciliation allows by design: the
    hold is read directly, so neither the act's undo nor the import's reversal goes through."""
    import pytest
    from bookflow import BookflowError
    from bookflow.core.ids import new_id
    from tests.test_reconcile_import import opening
    grant(client, 'transaction.deposit.delete')
    pay = receive(client, sale, method(client), '50.00', 'r108-5-held', '2026-06-02')
    vendor = run(client, 'vendor create', dict(name='R108 inc 5 held vendor'))['id']
    bill = run(client, 'bill post', dict(vendor=vendor, date='2026-05-20',
                                         expenses=[dict(account='Office Supplies', amount='100.00')]))['id']
    done = imp(client, 'Checking', csv_file([('2026-06-06', 'PMT', '-100.00'), ('2026-06-07', 'DEP', '50.00'),
                                             ('2026-06-08', 'TO PEB', '-30.00')]))
    paid = run(client, 'bank-line pay-bills', dict(line=line_of(done, -10000)['id'], method=transfer_method(client),
                                                   bills=[dict(bill=bill)]))
    dep = run(client, 'bank-line deposit', dict(line=line_of(done, 5000)['id'], payments=[pay]))
    sent = run(client, 'bank-line transfer', dict(line=line_of(done, -3000)['id'], to='Payment Example Bank'))
    made = {e['entries'][0]['entry_id'] for e in (paid, dep, sent)}

    def blocked(name, data, dry_run=False):
        before = (books(client), table(client, 'SELECT id, state, entry_id FROM bank_lines ORDER BY id'))
        with pytest.raises(BookflowError) as raised:
            run(client, name, data, dry_run=dry_run)
        assert raised.value.code == 'E_BATCH_BLOCKED', raised.value
        assert (books(client), table(client, 'SELECT id, state, entry_id FROM bank_lines ORDER BY id')) == before
        return {b['record_id']: b['code'] for b in raised.value.details['blockers']}

    # A later import on the account blocks the reversal; reversed, it no longer does.
    later = imp(client, 'Checking', csv_file([('2026-06-20', 'LATER', '-1.00')]))
    assert list(blocked('bank-feed reverse', dict(import_id=done['import_id'])).values()) == ['later_import']
    run(client, 'bank-feed reverse', dict(import_id=later['import_id']))

    # Reconciled: all three ticked on a finished reconciliation.
    opening(client)
    draft = run(client, 'reconcile start', dict(operation_key=new_id(), account='Checking',
                                                statement_date='2026-06-30', ending_balance='-80.00'))['draft']
    rows = [x for x in run(client, 'reconcile candidates', dict(draft=draft['id'], limit=200))['items']
            if any(t in json.dumps(x['movement']) for t in made)]
    assert len(rows) == 3
    marked = run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
        entries=[dict(movement=x['movement'], group_fingerprint=x['group_fingerprint'], action='mark')
                 for x in rows]))['draft']
    preview = run(client, 'reconcile preview', dict(draft=draft['id'], expected_version=marked['version']))
    run(client, 'reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                                         expected_facts_fingerprint=preview['expected_facts_fingerprint'],
                                         dependency_guard=preview['dependency_guard']))
    for act in (paid, dep):
        entry_id = act['entries'][0]['entry_id']
        for dry in (True, False):
            assert blocked('bank-line undo', dict(batch=act['batch_id']), dry) == {entry_id: 'entry_reconciled'}
    for dry in (True, False):
        assert blocked('bank-feed reverse', dict(import_id=done['import_id']), dry) == {
            t: 'entry_reconciled' for t in made}
    assert run(client, 'bill payment show', dict(payment=paid['entries'][0]['entry_id']))['status'] == 'posted'
    assert run(client, 'bill show', dict(bill=bill))['settlement_current']['status'] == 'paid'
