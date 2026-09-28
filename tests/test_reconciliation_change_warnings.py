"""Correcting or voiding a transaction after its reconciliation is finished: allowed, and said.

The books, written out so a reader can check every figure without running anything. Checking
is adopted at a zero opening balance on 2026-05-31 and reconciled to a June statement dated
2026-06-30 whose ending balance is 910.00:

    2026-06-01  journal entry, owner puts money in        +1,000.00
    2026-06-03  deposit of two receipts (100.00 + 60.00)    +160.00
    2026-06-10  check to the plumbing supplier               -250.00
                                                            --------
                statement ending balance                     910.00

What each case must say, by hand:

* Correcting the check to 275.00 moves the June cleared balance by -25.00, to 885.00: the
  reconciliation is off by 25.00 until it is re-done. Correcting it back to 250.00 brings it back
  to tie.
* Voiding the deposit takes its 160.00 off: the cleared balance becomes 750.00, off by 160.00.
* Correcting the journal's memo, or its date to another day in June, moves nothing reconciled:
  no warning, as the anchor gives none. Moving its date past the statement date takes the whole
  1,000.00 out of the June statement (it is then dated after it), off by 1,000.00.
* A transaction ticked on an unfinished reconciliation is not reconciled yet: correcting it warns
  about nothing, and the draft keeps its own rules.
* On a card the statement counts what is owed: a 40.00 charge reconciled and corrected to 45.00
  leaves the statement's cleared balance at 45.00 against its 40.00, off by 5.00.
"""
import pytest

from bookflow.core import registry
from tests.test_deposit_command import COMPANY, books, receipts_in_undeposited_funds  # noqa: F401

WHERE = 'This transaction was reconciled on the Checking statement dated 2026-06-30.'


def run(client, name, data, **ctx):
    if registry.get(name).is_write:
        ctx.setdefault('reason', 'R146 witness')
    return client.run(name, data, company=COMPANY, **ctx)


def reconcile(client, account, *, opening_date, statement_date, ending_balance, finish=True):
    """Adopt `account` at zero, start a statement, tick everything it can see, and finish it."""
    from bookflow.core.ids import new_id

    def call(name, data):
        return client.run(name, data, company=COMPANY, reason='R146 reconciliation')

    opening = call('reconcile opening start', dict(
        operation_key=new_id(), account=account, opening_date=opening_date, entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='Adopted at zero'), references=[]))
    statement = call('reconcile start', dict(
        operation_key=new_id(), account=account, statement_date=statement_date,
        ending_balance=ending_balance, opening_draft_id=opening['draft']['id']))
    draft = statement['draft']['id']
    marked = call('reconcile mark', dict(operation_key=new_id(), draft=draft, expected_version=1,
                                         entries=_marks(client, draft)))
    if not finish:
        return marked
    page = client.run('reconcile preview', dict(draft=draft, expected_version=marked['draft']['version']),
                      company=COMPANY)
    return call('reconcile finish', dict(
        operation_key=new_id(), draft=draft, expected_version=marked['draft']['version'],
        expected_facts_fingerprint=page['expected_facts_fingerprint'],
        dependency_guard=page['dependency_guard']))


def _marks(client, draft):
    entries, cursor = [], None
    while True:
        page = client.run('reconcile candidates', dict(draft=draft, limit=200, **({'cursor': cursor} if cursor else {})),
                          company=COMPANY)
        entries.extend(dict(movement=row['movement'], group_fingerprint=row['group_fingerprint'], action='mark')
                       for row in page['items'] if not row['claimed'] and row['eligible'])
        cursor = page['next_cursor']
        if not cursor:
            return entries


@pytest.fixture
def june(books):
    """Checking with the three June movements, reconciled to the 910.00 statement."""
    client = books['client']
    equity = client.account.create(name='Owner equity', type='equity', company=COMPANY)['id']
    supplies = client.account.create(name='Plumbing supplies', type='expense', company=COMPANY)['id']
    owner = run(client, 'journal post', dict(date='2026-06-01', memo='Owner money in', lines=[
        dict(account=books['bank'], side='debit', amount='1000.00'),
        dict(account=equity, side='credit', amount='1000.00')]))
    _, payment, _ = receipts_in_undeposited_funds(books)
    sources = run(client, 'deposit sources', dict(date='2026-06-03'))['items']
    deposit = run(client, 'deposit post', dict(operation_key='june-bank', document=dict(
        mode='inline', deposit_to=books['bank'], date='2026-06-03',
        sources=[dict(source_type=row['source_type'], source=row['source'],
                      expected_version=row['expected_version']) for row in sources])))['deposit']
    check = run(client, 'check post', dict(account=books['bank'], date='2026-06-10', amount='250.00',
                                           number='1001', memo='Copper fittings',
                                           expenses=[dict(account=supplies, amount='250.00')]))
    done = reconcile(client, books['bank'], opening_date='2026-05-31', statement_date='2026-06-30',
                     ending_balance='910.00')
    assert done['totals']['difference'] == 0 and done['totals']['cleared_balance'] == 91000
    return dict(books, client=client, owner=owner, deposit=deposit, check=check, supplies=supplies, payment=payment,
                equity=equity, certificate=done['certificate_id'], opening=done['opening_id'])


def discrepancy(client, account):
    return run(client, 'report reconciliation-discrepancy', dict(account=account, as_of='2026-12-31'))


def figures(report):
    """(kind, what, reconciled, current, difference, change) in minor units, row by row."""
    return [(row['kind'], row['reconciliation'] if row['kind'] == 'reconciliation' else row['transaction_id'],
             row['reconciled']['minor_units'], row['current']['minor_units'], row['difference']['minor_units'],
             row['type_of_change']) for row in report['rows']]


def check_amount(client, check, amount, **ctx):
    shown = run(client, 'check show', dict(check=check['id']))
    line = shown['revision']['lines']
    expense = next(row for row in line if row['side'] == 'debit')
    return run(client, 'check update', dict(check=check['id'], expected_version=shown['version'], amount=amount,
               expenses=[dict(line_id=expense['line_id'], account=expense['account_id'], amount=amount)]), **ctx)


def test_correcting_a_reconciled_checks_amount_warns_and_the_report_shows_the_difference(june):
    client = june['client']
    untouched = discrepancy(client, june['bank'])
    assert figures(untouched) == [('reconciliation', 'opening', 0, 0, 0, None),
                                  ('reconciliation', 'statement', 91000, 91000, 0, None)]
    assert untouched['totals'] == dict(reconciliations=2, out_of_balance=0, changes=0)

    preview = check_amount(client, june['check'], '275.00', dry_run=True)
    assert preview['warnings'] == [
        WHERE + ' Saving this change will leave that reconciliation off by 25.00 USD: its cleared balance'
        " becomes 885.00 against the statement's ending balance of 910.00, until the reconciliation is"
        ' re-done. The reconciliation discrepancy report shows the change.']
    # A preview writes nothing: the report still ties.
    assert figures(discrepancy(client, june['bank'])) == figures(untouched)

    saved = check_amount(client, june['check'], '275.00')
    assert saved['warnings'] == [
        WHERE + ' This change left that reconciliation off by 25.00 USD: its cleared balance is now'
        " 885.00 against the statement's ending balance of 910.00, until the reconciliation is"
        ' re-done. The reconciliation discrepancy report shows the change.']

    report = discrepancy(client, june['bank'])
    assert figures(report) == [('reconciliation', 'opening', 0, 0, 0, None),
                               ('reconciliation', 'statement', 91000, 88500, -2500, None),
                               ('change', june['check']['id'], -25000, -27500, -2500, 'amount')]
    assert report['totals'] == dict(reconciliations=2, out_of_balance=1, changes=1)
    change = report['rows'][2]
    assert (change['statement_date'], change['money_out_kind'], change['date']) == ('2026-06-30', 'check', '2026-06-10')

    # Putting it back is a change too, and it says the reconciliation ties again.
    back = check_amount(client, june['check'], '250.00')
    assert back['warnings'] == [
        WHERE + ' This change brings that reconciliation back to tie: its cleared balance is 910.00 USD,'
        " matching the statement's ending balance."]
    assert figures(discrepancy(client, june['bank'])) == figures(untouched)


def test_voiding_a_reconciled_deposit_warns_and_the_report_shows_the_difference(june):
    client = june['client']
    deposit = june['deposit']
    body = dict(deposit=deposit['id'], expected_version=deposit['version'], operation_key='void-june-bank')
    preview = run(client, 'deposit void', body, dry_run=True)
    assert preview['warnings'] == [
        WHERE + ' Saving this change will leave that reconciliation off by 160.00 USD: its cleared balance'
        " becomes 750.00 against the statement's ending balance of 910.00, until the reconciliation is"
        ' re-done. The reconciliation discrepancy report shows the change.']
    saved = run(client, 'deposit void', dict(body, dependency_guard=preview['dependency_guard']))
    assert saved['deposit']['status'] == 'voided'
    assert saved['warnings'] == [
        WHERE + ' This change left that reconciliation off by 160.00 USD: its cleared balance is now'
        " 750.00 against the statement's ending balance of 910.00, until the reconciliation is"
        ' re-done. The reconciliation discrepancy report shows the change.']
    report = discrepancy(client, june['bank'])
    assert figures(report) == [('reconciliation', 'opening', 0, 0, 0, None),
                               ('reconciliation', 'statement', 91000, 75000, -16000, None),
                               ('change', deposit['id'], 16000, 0, -16000, 'voided')]
    assert report['rows'][2]['transaction_type'] == 'deposit'


def test_what_moves_nothing_reconciled_does_not_warn_and_a_date_past_the_statement_does(june):
    client = june['client']
    owner = june['owner']
    memo = run(client, 'journal update', dict(journal=owner['id'], expected_version=owner['version'],
                                              memo='Owner capital contribution'))
    assert memo['warnings'] == []
    moved = run(client, 'journal update', dict(journal=owner['id'], expected_version=memo['version'],
                                               date='2026-06-02'))
    assert moved['warnings'] == []
    assert discrepancy(client, june['bank'])['totals'] == dict(reconciliations=2, out_of_balance=0, changes=0)

    later = dict(journal=owner['id'], expected_version=moved['version'], date='2026-07-02')
    preview = run(client, 'journal update', later, dry_run=True)
    assert preview['warnings'] == [
        WHERE + ' Saving this change will leave that reconciliation off by 1000.00 USD: its cleared'
        " balance becomes -90.00 against the statement's ending balance of 910.00, until the"
        ' reconciliation is re-done. The reconciliation discrepancy report shows the change.']
    run(client, 'journal update', later)
    assert figures(discrepancy(client, june['bank']))[1:] == [
        ('reconciliation', 'statement', 91000, -9000, -100000, None),
        ('change', owner['id'], 100000, 0, -100000, 'date')]


def test_a_transaction_on_an_unfinished_reconciliation_keeps_the_draft_rules(june):
    client = june['client']
    july = run(client, 'journal post', dict(date='2026-07-05', lines=[
        dict(account=june['bank'], side='debit', amount='50.00'),
        dict(account=june['equity'], side='credit', amount='50.00')]))
    from bookflow.core.ids import new_id
    statement = client.run('reconcile start', dict(
        operation_key=new_id(), account=june['bank'], statement_date='2026-07-31', ending_balance='960.00'),
        company=COMPANY, reason='July statement')
    draft = statement['draft']['id']
    client.run('reconcile mark', dict(operation_key=new_id(), draft=draft, expected_version=1,
                                      entries=_marks(client, draft)), company=COMPANY, reason='tick July')
    lines = [dict(account=row['account_id'], side=row['side'], amount='55.00', line_id=row['line_id'])
             for row in july['revision']['lines']]
    change = dict(journal=july['id'], expected_version=july['version'], lines=lines)
    assert run(client, 'journal update', change, dry_run=True)['warnings'] == []
    assert run(client, 'journal update', change)['warnings'] == []
    # The draft's own rules are what they were: its tick was of the version it saw, so it asks
    # for the candidates to be read again before it will say what the statement comes to.
    with pytest.raises(Exception) as stale:
        client.run('reconcile preview', dict(draft=draft, expected_version=2), company=COMPANY)
    assert 'E_RECONCILIATION_SELECTION_STALE' in str(stale.value)
    assert discrepancy(client, june['bank'])['totals'] == dict(reconciliations=2, out_of_balance=0, changes=0)


def test_on_a_card_the_statement_counts_what_is_owed(books):
    client = books['client']
    card = client.account.create(name='Company card', type='credit_card', company=COMPANY)['id']
    fuel = client.account.create(name='Fuel', type='expense', company=COMPANY)['id']
    charge = run(client, 'card-charge post', dict(account=card, date='2026-06-12', amount='40.00',
                                                  expenses=[dict(account=fuel, amount='40.00')]))
    reconcile(client, card, opening_date='2026-05-31', statement_date='2026-06-30', ending_balance='40.00')
    shown = run(client, 'card-charge show', dict(card_charge=charge['id']))
    expense = next(row for row in shown['revision']['lines'] if row['side'] == 'debit')
    saved = run(client, 'card-charge update', dict(
        card_charge=charge['id'], expected_version=shown['version'], amount='45.00',
        expenses=[dict(line_id=expense['line_id'], account=fuel, amount='45.00')]))
    assert saved['warnings'] == [
        'This transaction was reconciled on the Company card statement dated 2026-06-30. This change'
        ' left that reconciliation off by 5.00 USD: its cleared balance is now 45.00 against the'
        " statement's ending balance of 40.00, until the reconciliation is re-done. The reconciliation"
        ' discrepancy report shows the change.']
    assert figures(discrepancy(client, card))[1:] == [
        ('reconciliation', 'statement', 4000, 4500, 500, None),
        ('change', charge['id'], 4000, 4500, 500, 'amount')]


def _said(preview, saved, off):
    """The preview and the save name the same reconciliation and the same figure."""
    ours = [[line for line in result['warnings'] if line.startswith('This transaction')]
            for result in (preview, saved)]
    assert len(ours[0]) == len(ours[1]) == 1, ours
    before, after = ours[0][0], ours[1][0]
    assert f'off by {off} USD' in before and 'Saving this change will leave' in before, before
    assert f'off by {off} USD' in after and 'This change left' in after, after
    assert before.split('.')[0] == after.split('.')[0]


def test_every_kind_of_correction_previews_the_figure_its_save_reports(june):
    """Savings takes a 300.00 transfer, a 60.00 cash sale, a 100.00 payment and a 40.00 register
    deposit in June, 500.00 in all, and is reconciled to that; each is then corrected once."""
    client, books = june['client'], june
    savings = client.account.create(name='Savings', type='bank', company=COMPANY)['id']
    transfer = run(client, 'transfer post', dict(from_account=books['bank'], to_account=savings,
                                                 date='2026-06-05', amount='300.00'))
    sale = run(client, 'sales-receipt post', dict(customer=books['customer'], deposit_to=savings,
        payment_method=books['method'], date='2026-06-06', lines=[dict(item=books['item'], quantity='1', unit_price='60')]))
    invoice = run(client, 'invoice post', dict(customer=books['customer'], date='2026-06-06',
        lines=[dict(item=books['item'], quantity='1', net_amount='100')]))
    payment = run(client, 'payment receive', dict(customer=books['customer'], date='2026-06-07', amount='100',
        payment_method=books['method'], deposit_to=savings, operation_key='savings-cash', applications=dict(
            mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='100')])))
    register = run(client, 'register post', dict(account=savings, date='2026-06-08', direction='increase',
                                                 amount='40.00', category=books['equity']))
    reconcile(client, savings, opening_date='2026-05-31', statement_date='2026-06-30', ending_balance='500.00')

    body = dict(transfer=transfer['id'], expected_version=transfer['version'], amount='250.00')
    _said(run(client, 'transfer update', body, dry_run=True), run(client, 'transfer update', body), '50.00')

    body = dict(sales_receipt=sale['id'], expected_version=sale['version'], deposit_to=books['bank'])
    _said(run(client, 'sales-receipt update', body, dry_run=True), run(client, 'sales-receipt update', body), '110.00')

    shown = run(client, 'payment show', dict(payment=payment['id']))
    body = dict(payment=payment['id'], expected_version=shown['version'], deposit_to=books['bank'],
                operation_key='savings-cash-moved', settlement_guard=shown['settlement_guard'])
    _said(run(client, 'payment update', body, dry_run=True), run(client, 'payment update', body), '210.00')

    shown = run(client, 'journal show', dict(journal=register['id']))
    selected = next(row for row in shown['revision']['lines'] if row['account_id'] == savings)
    body = dict(journal=register['id'], expected_version=shown['version'], selected_line_id=selected['line_id'],
                account=savings, date='2026-06-08', direction='increase', amount='45.00', category=books['equity'])
    preview = run(client, 'register update', body, dry_run=True)
    saved = run(client, 'register update', body)
    # 5.00 back the other way: the statement is off by 205.00 now rather than 210.00.
    _said(preview, saved, '205.00')

    report = discrepancy(client, savings)
    assert figures(report)[1:] == [
        ('reconciliation', 'statement', 50000, 29500, -20500, None),
        *sorted([('change', transfer['id'], 30000, 25000, -5000, 'amount'),
                 ('change', sale['id'], 6000, 0, -6000, 'account'),
                 ('change', payment['id'], 10000, 0, -10000, 'account'),
                 ('change', register['id'], 4000, 4500, 500, 'amount')],
                key=lambda row: [transfer['id'], sale['id'], payment['id'], register['id']].index(row[1]))]


def test_correcting_a_reconciled_deposit_previews_the_figure_its_save_reports(june):
    """Banking only the 100.00 payment instead of both receipts takes 60.00 off June."""
    client, deposit = june['client'], june['deposit']
    payment = run(client, 'payment show', dict(payment=june['payment']['id']))
    body = dict(deposit=deposit['id'], expected_version=deposit['version'], operation_key='june-rebank',
                document=dict(mode='inline', deposit_to=june['bank'], date='2026-06-03', number=deposit['number'],
                              memo=None, cash_back=None, custom_fields={}, expected_custom_field_kinds={},
                              additional=[], sources=[dict(source_type='payment', source=payment['id'],
                                                           expected_version=payment['version'])]))
    preview = run(client, 'deposit update', body, dry_run=True)
    saved = run(client, 'deposit update', dict(body, dependency_guard=preview['dependency_guard']))
    _said(preview, saved, '60.00')
