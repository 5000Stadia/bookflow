"""R108 increment 5: add more details from a bank line (design/bank-feeds.md §4, §11).

A waiting line of money in becomes a deposit of the Undeposited Funds payments it banked (`bank-line deposit`, through
`deposit post`); a line of money out marks bills paid (`bank-line pay-bills`, through `bill pay`). The selection must
come to the line exactly or is refused showing the difference remaining (a deposit may instead name an adjustment
row); a payment already deposited is refused by `deposit post`'s own code; one click is the same as typing (criterion
5: the same postings and document lines as the typed command); and reversing the import, or undoing the act, returns
the payments to Undeposited Funds and leaves the bills open again.
"""
import json

from tests.test_bank_feeds import books, csv_file, run, table
from tests.test_bank_feed_reversal import grant
from tests.test_bank_line_sort import entry, imp, inbox_line, line_of, refused, source_ref
from tests.test_payment_receipts import method
from tests.test_service_sales_lifecycle import sale  # noqa: F401 - fixture


def receive(client, sale, cash, amount, key):
    return run(client, 'payment receive', dict(customer=sale['customer'], date='2026-10-02', amount=amount,
                                               payment_method=cash, operation_key=key))['id']


def undeposited(client):
    """The ids of every receipt waiting in Undeposited Funds, as `deposit sources` lists them."""
    return json.dumps(run(client, 'deposit sources', dict(date='2026-10-31', limit=200)))


def test_a_deposit_line_matches_three_payments_with_no_difference_and_reversal_returns_them(client, sale):
    grant(client, 'transaction.deposit.delete')
    cash = method(client)
    three = [receive(client, sale, cash, a, f'r108-5-{n}') for n, a in enumerate(('100.00', '250.00', '75.50'))]
    twins = [receive(client, sale, cash, a, f'r108-5-twin-{n}') for n, a in enumerate(('100.00', '250.00', '75.50'))]
    small, spare = receive(client, sale, cash, '55.00', 'r108-5-small'), receive(client, sale, cash, '100.00', 'r108-5-spare')
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
    fee = run(client, 'bank-line deposit', dict(line=less['id'], payments=[spare],
                                                adjustment_account='R108 bank charges', adjustment_memo='Deposit fee'))
    assert fee['difference'] == '-5.00' and fee['entries'][0]['input']['document']['cash_back']['amount'] == '5.00'

    # Undo the fee act alone: its payment is waiting again, the line too.
    run(client, 'bank-line undo', dict(batch=fee['batch_id']))
    assert spare in undeposited(client) and inbox_line(client, less['id'], 'Checking')['state'] == 'waiting'

    # Reversing the import deletes the deposits (preserved history) and returns the three payments.
    out = run(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    assert out['state'] == 'reversed'
    waiting = undeposited(client)
    assert all(p in waiting for p in three)
    assert table(client, 'SELECT count(*) FROM deposit_deletions WHERE transaction_id = ?', deposit) == [(1,)]


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

