"""R108 increment 3: sorting the bank inbox -- match, add, ignore, transfers (design/bank-feeds.md §2, §4; criteria 5
and 7).

Against the demo's Checking, Payment Example Bank and Business Credit Card. An added line's entry is the one its own
command makes when typed in (check, deposit, card charge, card credit, transfer: the same postings and document lines;
the audit differs only in `source_ref`); a closed period refuses with that command's code; no payee or account asks;
equal candidates are listed, never chosen; transfers end as one entry linked to both lines in either import order;
and a sort is recorded so the import's reversal (or a --lines batch's undo) takes it back.
"""
import pytest

from bookflow import BookflowError
from tests.test_bank_feeds import books, csv_file, ofx, run, table
from tests.test_bank_feed_reversal import grant

_POSTING = ('line_no, account_id, debit_minor_units, credit_minor_units, currency, name_type, name_id, party_name, '
            'class_id, description')
_DOCUMENT = ('position, kind, account_id, side, amount_minor_units, currency, name_type, name_id, party_name, '
             'class_id, description')


def imp(client, account, content, **kw):
    return run(client, 'bank-feed import', dict(account=account, content=content, **kw))


def refused(client, name, data, code='E_VALIDATION'):
    with pytest.raises(BookflowError) as raised:
        run(client, name, data)
    assert raised.value.code == code, raised.value
    return raised.value


def kind_of(client, transaction_id):
    """The document an entry is (a check, card charge and transfer are stored as journals with a money-out marker)."""
    marked = table(client, 'SELECT kind FROM money_out_documents WHERE transaction_id = ?', transaction_id)
    return marked[0][0] if marked else table(client, 'SELECT type FROM transactions WHERE id = ?', transaction_id)[0][0]


def entry(client, transaction_id):
    """What an entry is, without its ids, times or document number: kind, postings and document lines."""
    return (kind_of(client, transaction_id), table(client, 'SELECT status FROM transactions WHERE id = ?',
                                                   transaction_id),
            table(client, f'SELECT {_POSTING} FROM posting_lines WHERE transaction_id = ? ORDER BY line_no',
                  transaction_id),
            table(client, f'SELECT {_DOCUMENT} FROM document_lines WHERE transaction_id = ? ORDER BY position, kind',
                  transaction_id))


def source_ref(client, transaction_id, command):
    return table(client, 'SELECT e.source_ref FROM audit_events e JOIN audit_entries n ON n.event_id = e.id '
                         "WHERE n.record_id = ? AND e.command = ? LIMIT 1", transaction_id, command)[0][0]


def line_of(done, amount):
    return next(v for v in done['lines'] if v['amount'] == amount)


def inbox_line(client, line_id, account):
    return next(v for v in run(client, 'bank-line query', dict(account=account, limit=1000))['lines']
                if v['id'] == line_id)


def test_each_add_kind_is_the_entry_typed_in_and_reversal_takes_it_back(client):
    before = books(client)
    checking = imp(client, 'Checking', csv_file([('2026-10-06', 'SHELL OIL 4471', '-45.67'),
                                                 ('2026-10-07', 'RIVERSIDE APTS ACH', '300.00'),
                                                 ('2026-10-08', 'TRANSFER TO PEB', '-75.00')]))
    card = imp(client, 'Business Credit Card', ofx([('2026-10-06', '-25.10', 'C1', 'OFFICE DEPOT 12'),
                                                    ('2026-10-09', '10.00', 'C2', 'OFFICE DEPOT REFUND')],
                                                   acct='4111000011112222', card=True))
    fuel, deposit, out = (line_of(checking, n) for n in (-4567, 30000, -7500))
    charge, credit = (line_of(card, n) for n in (-2510, 1000))
    vendor = {'name_type': 'vendor', 'name_id': 'Central Supply'}
    payee = dict(payee='Central Supply')

    # Ask, don't guess: no payee, or no account, is refused asking for it; nothing is entered.
    asked = refused(client, 'bank-line add', dict(line=fuel['id'], account='Vehicle Expense'))
    assert asked.details['ask'] == ['payee'] and 'never guesses' in asked.message
    asked = refused(client, 'bank-line add', dict(line=fuel['id'], **payee))
    assert asked.details['ask'] == ['account']
    # Exact or refused: splits that do not add up to the line.
    refused(client, 'bank-line add', dict(line=fuel['id'], **payee, splits=[
        dict(account='Vehicle Expense', amount='40.00'), dict(account='Office Supplies', amount='5.00')]))
    assert books(client) == before

    # A closed period refuses with the posting command's own code, and the line waits.
    run(client, 'company update', dict(closing_date='2026-10-31'))
    assert refused(client, 'bank-line add', dict(line=fuel['id'], **payee, account='Vehicle Expense'),
                   'E_PERIOD_CLOSED')
    assert refused(client, 'check post', dict(account='Checking', pay_to=vendor, date='2026-10-06', amount='45.67',
                                              expenses=[dict(account='Vehicle Expense', amount='45.67')],
                                              no_number=True), 'E_PERIOD_CLOSED')
    run(client, 'company update', dict(closing_date=None))
    assert inbox_line(client, fuel['id'], 'Checking')['state'] == 'waiting' and books(client) == before

    # Each kind, then the same entry typed in by its own command: the same postings and document lines.
    preview = run(client, 'bank-line add', dict(line=fuel['id'], **payee, splits=[
        dict(account='Vehicle Expense', amount='40.00'), dict(account='Office Supplies', amount='5.67',
                                                               class_id='Operations')]), dry_run=True)
    assert preview['entries'][0]['command'] == 'check post' and books(client) == before
    added = {}
    added['check'] = run(client, 'bank-line add', dict(line=fuel['id'], **payee, splits=[
        dict(account='Vehicle Expense', amount='40.00'), dict(account='Office Supplies', amount='5.67',
                                                               class_id='Operations')]))
    typed = {'check': run(client, 'check post', dict(
        account='Checking', pay_to=vendor, date='2026-10-06', amount='45.67', memo='SHELL OIL 4471', no_number=True,
        expenses=[dict(account='Vehicle Expense', amount='40.00'),
                  dict(account='Office Supplies', amount='5.67', class_id='Operations')]))['id']}
    customer = run(client, 'customer show', dict(customer='Riverside Apartments'))['id']
    added['deposit'] = run(client, 'bank-line add', dict(
        line=deposit['id'], payee='Riverside Apartments', payee_type='customer',
        account='Service Income', class_id='Operations'))
    typed['deposit'] = run(client, 'deposit post', dict(operation_key='typed-deposit-1', document=dict(
        mode='inline', deposit_to='Checking', date='2026-10-07', memo='RIVERSIDE APTS ACH', additional=[dict(
            received_from=dict(kind='customer', id=customer), from_account='Service Income', amount='300.00',
            **{'class': 'Operations'})])))['deposit']['id']
    added['card_charge'] = run(client, 'bank-line add', dict(line=charge['id'], **payee,
                                                             account='Office Supplies'))
    typed['card_charge'] = run(client, 'card-charge post', dict(
        account='Business Credit Card', pay_to=vendor, date='2026-10-06', amount='25.10', memo='OFFICE DEPOT 12',
        expenses=[dict(account='Office Supplies', amount='25.10')]))['id']
    added['card_credit'] = run(client, 'bank-line add', dict(line=credit['id'], **payee,
                                                             account='Office Supplies', memo='Returned toner'))
    typed['card_credit'] = run(client, 'card-credit post', dict(
        account='Business Credit Card', pay_to=vendor, date='2026-10-09', amount='10.00', memo='Returned toner',
        expenses=[dict(account='Office Supplies', amount='10.00')]))['id']
    added['transfer'] = run(client, 'bank-line transfer', dict(line=out['id'], to='Payment Example Bank'))
    typed['transfer'] = run(client, 'transfer post', dict(from_account='Checking', to_account='Payment Example Bank',
                                                          date='2026-10-08', amount='75.00',
                                                          memo='TRANSFER TO PEB'))['id']
    for kind, done in added.items():
        made = done['entries'][0]
        assert entry(client, made['entry_id']) == entry(client, typed[kind]), kind
        assert kind_of(client, made['entry_id']) == kind
        assert source_ref(client, made['entry_id'], made['command']) == f"bank-line:{made['line_id']}"
        shown = done['lines'][0]
        assert shown['state'] == ('transfer' if kind == 'transfer' else 'added') and shown['created']
        assert shown['entry_id'] == made['entry_id']
    assert added['check']['lines'][0]['payee_display'] == 'Central Supply'
    # An added line is not unmatched: its entry is taken back with its own command, or by the import's reversal.
    refusal = refused(client, 'bank-line unmatch', dict(line=fuel['id']))
    assert 'check delete' in refusal.message
    # Each act is on the import's batch: the line's change, and the entry it made.
    effects = table(client, "SELECT record_type, action FROM batch_effects WHERE batch_id = ? AND seq > 5 "
                            'ORDER BY seq', checking['batch_id'])
    assert effects.count(('transaction', 'created')) == 3 and effects.count(('bank_line', 'updated')) == 3

    # Reversing the imports deletes what their lines were entered as (each family's own delete grant); the typed
    # entries stay.
    grant(client, 'transaction.check.delete', 'transaction.deposit.delete', 'transaction.card_charge.delete')
    plan = run(client, 'bank-feed reverse', dict(import_id=checking['import_id']), dry_run=True)
    assert plan['entries'] == 3 and plan['blockers'] == []
    assert run(client, 'bank-feed reverse', dict(import_id=checking['import_id']))['state'] == 'reversed'
    assert run(client, 'bank-feed reverse', dict(import_id=card['import_id']))['state'] == 'reversed'
    for kind, done in added.items():
        status = table(client, 'SELECT status, voided_at IS NOT NULL FROM transactions WHERE id = ?',
                       done['entries'][0]['entry_id'])[0]
        assert status[0] == 'deleted' or status[1], (kind, status)
        assert entry(client, typed[kind])[1] == [('posted',)]


def test_match_ignore_unmatch_never_guess_and_a_lines_batch_undoes_whole(client):
    journal = lambda day, amount: run(client, 'journal post', dict(date=day, memo='entry', lines=[
        dict(account='Checking', side='debit', amount=amount),
        dict(account='Service Income', side='credit', amount=amount)]))['id']
    twin_a, twin_b = journal('2026-10-12', '20.00'), journal('2026-10-12', '20.00')
    other = journal('2026-10-13', '31.00')
    done = imp(client, 'Checking', csv_file([('2026-10-12', 'DEPOSIT A', '20.00'), ('2026-10-13', 'DEPOSIT B', '31.00'),
                                             ('2026-10-14', 'COFFEE', '-4.50'), ('2026-10-15', 'COFFEE', '-4.25'),
                                             ('2026-10-16', 'MYSTERY', '-9.99')]))
    twin, single = line_of(done, 2000), line_of(done, 3100)
    coffee_a, coffee_b, mystery = line_of(done, -450), line_of(done, -425), line_of(done, -999)
    assert single['state'] == 'matched' and single['entry_id'] == other
    # Two equal entries: proposed with both, never chosen.
    waiting = inbox_line(client, twin['id'], 'Checking')
    assert waiting['state'] == 'waiting'
    match, = [p for p in waiting['proposals'] if p['kind'] == 'match']
    assert set(match['entry_ids']) == {twin_a, twin_b} and not match['unique']
    tie = refused(client, 'bank-line match', dict(line=twin['id']))
    assert {c['entry_id'] for c in tie.details['candidates']} == {twin_a, twin_b}
    # Exact or refused; an entry already matched is refused naming its line.
    refused(client, 'bank-line match', dict(line=coffee_a['id'], entry=twin_a))
    taken = refused(client, 'bank-line match', dict(line=twin['id'], entry=other))
    assert 'another amount' in taken.message
    matched = run(client, 'bank-line match', dict(line=twin['id'], entry=twin_b))['lines'][0]
    assert (matched['state'], matched['entry_id'], matched['link_how']) == ('matched', twin_b, 'person')
    # Unmatch: back to waiting, the entry untouched; ignore and back.
    assert run(client, 'bank-line unmatch', dict(line=single['id']))['lines'][0]['state'] == 'waiting'
    ignored = run(client, 'bank-line ignore', dict(line=mystery['id'], ignore_reason='not_ours'))['lines'][0]
    assert (ignored['state'], ignored['ignore_reason']) == ('ignored', 'not_ours')
    refused(client, 'bank-line ignore', dict(line=mystery['id'], ignore_reason='other'))
    assert run(client, 'bank-line unmatch', dict(line=mystery['id']))['lines'][0]['state'] == 'waiting'
    kinds = [k for (k,) in table(client, 'SELECT kind FROM bank_line_events WHERE line_id = ? ORDER BY rowid',
                                 mystery['id'])]
    assert kinds == ['imported', 'ignored', 'unignored']

    # A --lines act is all or nothing: one line that cannot be refuses the batch, naming it.
    before = table(client, 'SELECT id, state, version FROM bank_lines ORDER BY id')
    whole = refused(client, 'bank-line ignore', dict(lines=[coffee_a['id'], twin['id']], ignore_reason='other'))
    assert [v['line'] for v in whole.details['lines']] == [twin['id']]
    assert table(client, 'SELECT id, state, version FROM bank_lines ORDER BY id') == before
    sort = run(client, 'bank-line ignore', dict(lines=[coffee_a['id'], coffee_b['id']], ignore_reason='other'))
    assert sort['batch_id'] and {v['state'] for v in sort['lines']} == {'ignored'}
    matched = run(client, 'bank-line match', dict(lines=[dict(line=single['id'], entry=other)]))
    # Sorted otherwise since (not as the batch left it, nor as it was before): the undo is refused naming the line
    # and what clears it, and changes nothing.
    run(client, 'bank-line unmatch', dict(line=coffee_b['id']))
    run(client, 'bank-line ignore', dict(line=coffee_b['id'], ignore_reason='duplicate'))
    blocked = refused(client, 'bank-line undo', dict(batch=sort['batch_id']), 'E_BATCH_BLOCKED')
    assert [(b['code'], b['record_id']) for b in blocked.details['blockers']] == [('line_changed', coffee_b['id'])]
    assert f"bank-line unmatch --line {coffee_b['id']}" in blocked.details['blockers'][0]['clears']
    assert inbox_line(client, coffee_a['id'], 'Checking')['state'] == 'ignored'
    # Back as it was before the batch (by any path), the line needs nothing; the undo goes ahead for the rest.
    run(client, 'bank-line unmatch', dict(line=coffee_b['id']))
    assert run(client, 'bank-line undo', dict(batch=sort['batch_id']))['state'] == 'reversed'
    assert inbox_line(client, coffee_a['id'], 'Checking')['state'] == 'waiting'
    # A batch whose lines stand as it left them is undone whole.
    plan = run(client, 'bank-line undo', dict(batch=matched['batch_id']), dry_run=True)
    assert plan['lines'] == 1 and plan['blockers'] == [] and plan['fingerprint']
    assert run(client, 'bank-line undo', dict(batch=matched['batch_id']))['state'] == 'reversed'
    assert inbox_line(client, single['id'], 'Checking')['state'] == 'waiting'
    # The import's reversal takes back every sort of its lines, batches included.
    assert run(client, 'bank-feed reverse', dict(import_id=done['import_id']))['state'] == 'reversed'
    assert table(client, 'SELECT count(*) FROM bank_lines WHERE live = 1') == [(0,)]


def test_transfers_end_as_one_entry_in_either_import_order(client):
    transfers = lambda: table(client, "SELECT count(*) FROM transactions t JOIN money_out_documents d ON "
                                      "d.transaction_id = t.id WHERE d.kind = 'transfer' AND t.voided_at IS NULL")
    start = transfers()
    # Checking first, then Payment Example Bank: the pair is proposed and accepted as one posted transfer.
    first = imp(client, 'Checking', csv_file([('2026-10-20', 'ONLINE TRANSFER TO PEB', '-500.00')]))
    second = imp(client, 'Payment Example Bank', csv_file([('2026-10-22', 'TRANSFER FROM CHECKING', '500.00')]))
    out_line, in_line = first['lines'][0], second['lines'][0]
    shown = inbox_line(client, out_line['id'], 'Checking')
    proposal, = [p for p in shown['proposals'] if p['kind'] == 'transfer']
    assert proposal['unique'] and proposal['line_ids'] == [in_line['id']]
    done = run(client, 'bank-line transfer', dict(line=out_line['id']))
    entry_id = done['entries'][0]['entry_id']
    assert {v['entry_id'] for v in done['lines']} == {entry_id} and transfers() == [(start[0][0] + 1,)]
    assert inbox_line(client, in_line['id'], 'Payment Example Bank')['pair_line_id'] == out_line['id']
    assert table(client, "SELECT date FROM bank_lines WHERE id = ?", out_line['id']) == [('2026-10-20',)]
    # Payment Example Bank first, then Checking: the same, from the other end.
    third = imp(client, 'Payment Example Bank', csv_file([('2026-10-23', 'TRANSFER FROM CHECKING', '600.00')]))
    fourth = imp(client, 'Checking', csv_file([('2026-10-21', 'ONLINE TRANSFER TO PEB', '-600.00')]))
    done = run(client, 'bank-line transfer', dict(line=third['lines'][0]['id']))
    assert len({v['entry_id'] for v in done['lines']}) == 1 and len(done['lines']) == 2
    assert done['entries'][0]['input']['from_account'] != done['entries'][0]['input']['to_account']
    assert done['entries'][0]['input']['date'] == '2026-10-21', 'dated the day the money left'
    # One-sided: sent now; the other account's line matches the transfer when it is imported.
    fifth = imp(client, 'Checking', csv_file([('2026-10-24', 'ONLINE TRANSFER TO PEB', '-700.00')]))
    sent = run(client, 'bank-line transfer', dict(line=fifth['lines'][0]['id'], to='Payment Example Bank'))
    later = imp(client, 'Payment Example Bank', csv_file([('2026-10-26', 'TRANSFER FROM CHECKING', '700.00')]))
    assert later['lines'][0]['entry_id'] == sent['entries'][0]['entry_id']
    assert transfers() == [(start[0][0] + 3,)]
    # Never guessed: two Checking lines fit one Payment Example Bank line.
    imp(client, 'Checking', csv_file([('2026-10-27', 'TRANSFER A', '-800.00'), ('2026-10-27', 'TRANSFER B', '-800.00')]))
    lone = imp(client, 'Payment Example Bank', csv_file([('2026-10-28', 'TRANSFER IN', '800.00')]))['lines'][0]
    tie = refused(client, 'bank-line transfer', dict(line=lone['id']))
    assert len(tie.details['candidates']) == 2
    # Reversal: the created transfer is linked by the other account's live line, which is named; reversed in turn.
    blocked = refused(client, 'bank-feed reverse', dict(import_id=first['import_id'], through=True), 'E_BATCH_BLOCKED')
    assert {b['code'] for b in blocked.details['blockers']} == {'entry_linked'}
