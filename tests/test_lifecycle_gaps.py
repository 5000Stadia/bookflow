"""Four verbs every comparable document already had, checked against the books by hand.

Every figure asserted here is written out at the top so a reader can add it up without
running anything, and what the books say afterwards is read back through the real report
commands rather than out of the write's own output.

* **A withdrawn quote.** A 300.00 estimate (3 x 100.00) is accepted and then voided. Nothing
  posts -- the trial balance is 0.00 before and after -- but it can no longer become an
  invoice, a sales receipt or a work order, and every revision still reads in full.
* **A corrected deposit.** A 100.00 cheque is banked, the 60.00 counter sale is added, and
  then the whole thing is redated: three revisions, 160.00 in the bank, Undeposited Funds
  empty.
* **A re-pointed cheque.** A 284.60 cheque pays the 284.60 bill, is taken off it and pointed
  at the 75.25 bill instead, leaving 209.35 free; then it is voided and both bills are open
  again at 359.85.
* **A closed period.** With the books closed through 2017-03-31, the 2017-03-10 settlement
  cannot be taken back, and nothing about it moves.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

QUOTED = '300.00'          # 3 x 100.00
CHEQUE = '100.00'
COUNTER = '60.00'
BANKED = '160.00'          # 100.00 + 60.00
FIRST_BILL = '284.60'
SECOND_BILL = '75.25'
OWED = '359.85'            # 284.60 + 75.25
STILL_FREE = '209.35'      # 284.60 - 75.25
CLOSING = '2017-03-31'


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def sales(tmp_path, monkeypatch):
    """A company of its own, so every reported total belongs only to this test."""
    root = tmp_path / 'sales'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.run('organization new', dict(name='Lifecycle Org'), reason='first organization')
    client.run('company new', dict(legal_name='Riverbend Plumbing', home_currency='USD',
                                   organization='Lifecycle Org'), reason='first company')

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company='Riverbend Plumbing', **context)

    accounts = {row['name']: row['id'] for row in run('account list')['items']}
    income = run('account create', dict(name='Drain service income', type='income'))['id']
    customer = run('customer create', dict(name='Ada Waterworks'))['id']
    code = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    item = run('item create', dict(name='Drain service', type='service', sales_enabled=True,
               description='Service labor', income_account_id=income, price=CHEQUE,
               sales_tax_code_id=code))['id']
    method = next(row['id'] for row in run('payment-method list')['items'] if row['kind'] == 'cash')
    return dict(run=run, customer=customer, item=item, method=method,
                bank=accounts['Checking'], uf=accounts['Undeposited Funds'])


@pytest.fixture
def payables(tmp_path, monkeypatch):
    """A second company, for the payables half; its bank starts empty too."""
    root = tmp_path / 'payables'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.run('organization new', dict(name='Payables Org'), reason='first organization')
    company = client.run('company new', dict(legal_name='Payables', home_currency='USD',
                         timezone='UTC', organization='Payables Org', chart='general'),
                         reason='first company')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    parts = run('account create', dict(name='Parts Bought', type='expense'))['id']
    terms = {row['name']: row['id'] for row in run('term query', dict(limit=50))['items']}
    vendor = run('vendor create', dict(name='Northside Supply', terms_id=terms['Net 30']))['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    return dict(run=run, bank=bank, parts=parts, vendor=vendor, check=methods['Check'])


def _balances(run, date_to='2026-12-31'):
    report = run('report trial-balance', dict(date_to=date_to, include_zero=True, limit=200))
    assert not report['next_cursor'], 'this chart fits one page'
    return report['totals'], {row['account_id']: row['signed_net']['minor_units'] for row in report['rows']}


def _accepted_estimate(sales, title, key):
    run = sales['run']
    drafted = run('estimate create', dict(customer=sales['customer'], date='2026-07-01', title=title,
                  lines=[dict(item=sales['item'], quantity='3', unit_price=CHEQUE)]),
                  reason='quote the job')
    return run('estimate update', dict(estimate=drafted['id'], expected_version=drafted['version'],
               status='accepted', decision_note='Customer signed ' + key), reason='they accepted')


def _three_bills(payables):
    run = payables['run']
    first = run('bill post', dict(vendor=payables['vendor'], date='2017-03-03',
                expenses=[{'account': payables['parts'], 'amount': FIRST_BILL}]),
                reason='Enter the first bill')
    second = run('bill post', dict(vendor=payables['vendor'], date='2017-03-05',
                 expenses=[{'account': payables['parts'], 'amount': SECOND_BILL}]),
                 reason='Enter the second bill')
    return first, second


def _open(run, bill_id):
    settlement = run('bill show', dict(bill=bill_id))['settlement_current']
    return settlement['open']['amount'], settlement['status']


# ------------------------------------------------------------------ estimate void


def test_a_voided_estimate_moves_no_money_stops_converting_and_still_reads(sales):
    run = sales['run']
    estimate = _accepted_estimate(sales, 'Re-pipe the kitchen', 'A')
    assert estimate['status'] == 'accepted' and estimate['net']['amount'] == QUOTED

    before_totals, before_rows = _balances(run)
    voided = run('estimate void', dict(estimate=estimate['id'], expected_version=estimate['version']),
                 reason='Customer changed their mind')
    after_totals, after_rows = _balances(run)

    # Non-posting: an estimate never moved money, so withdrawing it cannot move any either.
    assert before_totals['signed_net']['minor_units'] == after_totals['signed_net']['minor_units'] == 0
    assert before_totals['debit']['minor_units'] == after_totals['debit']['minor_units'] == 0
    assert before_rows == after_rows
    assert voided['status'] == 'voided' and voided['active'] is False
    assert sorted(voided['changed_fields']) == ['active', 'decision_note', 'status']

    # It survives as a readable record: the quote, its lines and why it was withdrawn.
    shown = run('estimate show', dict(estimate=estimate['id']))
    assert shown['status'] == 'voided' and shown['net']['amount'] == QUOTED
    assert shown['revision']['decision_note'] == 'Customer changed their mind'
    assert [(line['quantity'], line['net']['amount']) for line in shown['revision']['lines']] == [('3', QUOTED)]
    walk = run('estimate history', dict(estimate=estimate['id'], limit=50))
    assert [(row['revision_number'], row['status'], row['active']) for row in walk['items']] == [
        (1, 'draft', True), (2, 'accepted', True), (3, 'voided', False)]
    assert all(row['net']['amount'] == QUOTED for row in walk['items']), 'the quoted work is unchanged'

    # It is findable as what it is, and gone from the default list of live quotes.
    assert [row['number'] for row in run('estimate query', dict(status='voided', active=None,
                                                                limit=50))['items']] == [shown['number']]
    assert run('estimate query', dict(limit=50))['items'] == []

    # None of the three conversions will take it.
    version = voided['version']
    for command, extra in (
            ('estimate work-order', dict(conversion_key='wo-after-void')),
            ('estimate invoice', dict(conversion_key='inv-after-void')),
            ('estimate sales-receipt', dict(conversion_key='sr-after-void', deposit_to=sales['uf'],
                                            payment_method=sales['method'], amount_received=QUOTED))):
        with pytest.raises(BookflowError) as refusal:
            run(command, dict(estimate=estimate['id'], expected_version=version, date='2026-07-10',
                              **extra), reason='try it anyway')
        assert refusal.value.code == 'E_WORK_DEPENDENCY'
        assert 'voided' in refusal.value.details['problem']

    # Terminal: it cannot be brought back, and voiding it again changes nothing.
    with pytest.raises(BookflowError) as refusal:
        run('estimate update', dict(estimate=estimate['id'], expected_version=version, status='draft'),
            reason='bring it back')
    assert refusal.value.code == 'E_VALIDATION'
    again = run('estimate void', dict(estimate=estimate['id'], expected_version=version),
                reason='void it twice')
    assert again['changed'] is False and again['version'] == version

    # Re-quoting is a copy into a fresh draft, which still works.
    copied = run('estimate copy', dict(estimate=estimate['id'], expected_version=version,
                 date='2026-07-11', copy_mode='independent'), reason='re-quote it')
    assert copied['status'] == 'draft' and copied['net']['amount'] == QUOTED
    assert _balances(run)[0]['signed_net']['minor_units'] == 0


def test_a_void_is_refused_while_a_work_order_or_a_sale_consumes_the_quote(sales):
    run = sales['run']
    scheduled = _accepted_estimate(sales, 'Re-pipe the bathroom', 'B')
    run('estimate work-order', dict(estimate=scheduled['id'], expected_version=scheduled['version'],
        date='2026-07-05', conversion_key='wo-b'), reason='schedule it')
    with pytest.raises(BookflowError) as refusal:
        current = run('estimate show', dict(estimate=scheduled['id']))
        run('estimate void', dict(estimate=scheduled['id'], expected_version=current['version']),
            reason='too late')
    assert refusal.value.code == 'E_WORK_DEPENDENCY'
    assert refusal.value.details['problem'] == 'agreed estimate already has a work order'
    assert run('estimate show', dict(estimate=scheduled['id']))['status'] == 'accepted'

    billed = _accepted_estimate(sales, 'Re-pipe the garage', 'C')
    invoice = run('estimate invoice', dict(estimate=billed['id'], expected_version=billed['version'],
                  date='2026-07-06', conversion_key='inv-c'), reason='bill it')
    assert invoice['total']['amount'] == QUOTED
    with pytest.raises(BookflowError) as refusal:
        current = run('estimate show', dict(estimate=billed['id']))
        run('estimate void', dict(estimate=billed['id'], expected_version=current['version']),
            reason='too late')
    assert refusal.value.code == 'E_WORK_DEPENDENCY'
    assert refusal.value.details['problem'] == 'billed estimate acceptance cannot be revoked'

    # The invoice it produced is untouched, and the books still balance.
    totals, _ = _balances(run)
    assert totals['debit'] == totals['credit'] and totals['signed_net']['minor_units'] == 0
    assert totals['debit']['amount'] == QUOTED


# ---------------------------------------------------------------- deposit history


def test_deposit_history_walks_every_revision_and_membership(sales):
    run = sales['run']
    invoice = run('invoice post', dict(customer=sales['customer'], date='2026-06-01',
                  lines=[dict(item=sales['item'], quantity='1', net_amount=CHEQUE)]),
                  reason='bill the June service call')
    run('payment receive', dict(customer=sales['customer'], date='2026-06-02', amount=CHEQUE,
        payment_method=sales['method'], operation_key='june-cash',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1,
                                                     amount=CHEQUE)])), reason='customer paid')
    run('sales-receipt post', dict(customer=sales['customer'], deposit_to=sales['uf'],
        payment_method=sales['method'], date='2026-06-02',
        lines=[dict(item=sales['item'], quantity='1', unit_price=COUNTER)]), reason='counter sale')

    available = {row['source_type']: row for row in run('deposit sources', dict(date='2026-06-03'))['items']}

    def selected(*kinds):
        return [dict(source_type=kind, source=row['source'], expected_version=row['expected_version'])
                for kind in kinds for row in [available[kind]]]

    document = dict(mode='inline', deposit_to=sales['bank'], date='2026-06-03',
                    memo='Saturday receipts', sources=selected('payment'))
    banked = run('deposit post', dict(operation_key='june-deposit', document=document),
                 reason='bank the cheque')
    deposit = banked['deposit']['id']
    assert banked['deposit']['bank_total'] == {'minor_units': 10000, 'currency': 'USD'}

    walk = run('deposit history', dict(deposit=deposit, page=dict(limit=50)))
    assert walk['deposit_id'] == deposit and walk['total_count'] == 2
    assert sorted(row['kind'] for row in walk['items']) == ['membership_claimed', 'revision_created']
    assert all(row['reason'] == 'bank the cheque' and row['interface'] == 'python'
               for row in walk['items'])
    # The caller's own permanent-retry secret is not a fact about the books.
    assert 'june-deposit' not in str(walk)

    def corrected(key, date, memo, kinds, reason):
        current = run('deposit show', dict(deposit=deposit))
        members = {row['source']: row for row in run('deposit sources',
                   dict(date=date, limit=200, for_deposit=deposit))['items']}
        members.update({row['source']: row for row in
                        run('deposit sources', dict(date=date, limit=200))['items']})
        rows = [members[available[kind]['source']] for kind in kinds]
        replacement = dict(mode='inline', deposit_to=sales['bank'], date=date, memo=memo,
                           number=current['selected']['number'], custom_fields={},
                           expected_custom_field_kinds={}, cash_back=None, additional=[],
                           sources=[dict(source_type=row['source_type'], source=row['source'],
                                         expected_version=row['expected_version']) for row in rows])
        request = dict(deposit=deposit, expected_version=current['current']['version'],
                       operation_key=key, document=replacement)
        preview = run('deposit update', request, dry_run=True, reason=reason)
        return run('deposit update', dict(request, dependency_guard=preview['dependency_guard']),
                   reason=reason)

    added = corrected('add-the-counter-sale', '2026-06-03', 'Saturday receipts',
                      ('payment', 'sales_receipt'), 'The counter sale went in too')
    assert added['deposit']['bank_total'] == {'minor_units': 16000, 'currency': 'USD'}
    second = run('deposit history', dict(deposit=deposit, page=dict(limit=50)))
    assert second['total_count'] == 6
    replaced = next(row for row in second['items'] if row['kind'] == 'replaced')
    created = next(row for row in second['items'] if row['kind'] == 'revision_created')
    assert replaced['previous_revision_id'] == created['revision_id']
    assert [row['kind'] for row in second['items']].count('membership_claimed') == 3
    assert [row['kind'] for row in second['items']].count('membership_released') == 1

    redated = corrected('redate-it', '2026-06-04', 'Cleared on the Thursday',
                        ('payment', 'sales_receipt'), 'Wrong day')
    assert redated['deposit']['date'] == '2026-06-04'
    assert redated['deposit']['bank_total'] == {'minor_units': 16000, 'currency': 'USD'}
    third = run('deposit history', dict(deposit=deposit, page=dict(limit=50)))
    assert third['total_count'] == 11
    assert [row['kind'] for row in third['items']].count('replaced') == 2
    assert [row['reason'] for row in third['items']].count('Wrong day') == 5

    # Both earlier revisions are still readable, at the figures they were saved with.
    detail = run('deposit show', dict(deposit=deposit))
    assert [(row['revision_number'], row['current']) for row in detail['revisions']] == [
        (1, False), (2, False), (3, True)]
    saved = [run('deposit show', dict(deposit=deposit, revision_number=number)) for number in (1, 2, 3)]
    assert [(row['selected']['date'], row['totals']['bank_total']['minor_units'],
             row['counts']['sources']) for row in saved] == [
        ('2026-06-03', 10000, 1), ('2026-06-03', 16000, 2), ('2026-06-04', 16000, 2)]

    # The books: Undeposited Funds emptied into the bank, and the trial balance still balances.
    totals, rows = _balances(run)
    assert rows[sales['uf']] == 0 and rows[sales['bank']] == 16000
    assert totals['debit'] == totals['credit'] and totals['signed_net']['minor_units'] == 0

    # Cursor discipline: its own page domain, which an items cursor cannot cross into.
    first_page = run('deposit history', dict(deposit=deposit, page=dict(limit=3)))
    assert len(first_page['items']) == 3 and first_page['next_cursor']
    next_page = run('deposit history', dict(deposit=deposit,
                    page=dict(limit=3, cursor=first_page['next_cursor'])))
    assert len(next_page['items']) == 3
    assert next_page['items'] != first_page['items']
    with pytest.raises(BookflowError) as refusal:
        run('deposit items', dict(deposit=deposit, kind='sources',
            page=dict(limit=3, cursor=first_page['next_cursor'])))
    assert refusal.value.code == 'E_VALIDATION'


# ----------------------------------------------------------- bill payment history


def test_bill_payment_history_reads_the_repoint_and_the_void(payables):
    run = payables['run']
    first, second = _three_bills(payables)
    paid = run('bill pay', dict(date='2017-03-10', funding_account=payables['bank'],
               method=payables['check'], bills=[{'bill': first['id']}]), reason='Pay the first bill')
    payment = paid['payments'][0]['id']
    assert paid['payments'][0]['total']['amount'] == FIRST_BILL

    walk = run('bill payment history', dict(payment=payment))
    assert (walk['id'], walk['status'], walk['count'], walk['has_more']) == (payment, 'posted', 1, False)
    only = walk['items'][0]
    assert only['revision_number'] == 1 and only['total']['amount'] == FIRST_BILL and only['line_count'] == 1
    assert [batch['kind'] for batch in only['batches']] == ['original']
    assert only['batches'][0]['effective_date'] == '2017-03-10'
    assert [(edge['kind'], edge['amount']['amount'], edge['effective_date'], edge['active'])
            for edge in only['applications']] == [('apply', FIRST_BILL, '2017-03-10', True)]

    # The correction a payment actually has: take it off one bill and point it at another.
    run('bill payment unapply', dict(payment=payment), reason='Wrong bill')
    run('bill payment apply', dict(payment=payment, bills=[{'bill': second['id']}], date='2017-03-12'),
        reason='Point it at the second bill')
    repointed = run('bill payment history', dict(payment=payment))['items'][0]
    assert [(edge['kind'], edge['bill_number'], edge['amount']['amount'], edge['effective_date'],
             edge['active']) for edge in repointed['applications']] == [
        ('apply', first['number'], FIRST_BILL, '2017-03-10', False),
        ('unapply', first['number'], FIRST_BILL, '2017-03-10', False),
        ('apply', second['number'], SECOND_BILL, '2017-03-12', True)]
    assert _open(run, first['id']) == (FIRST_BILL, 'unpaid')
    assert _open(run, second['id']) == ('0.00', 'paid')
    assert run('bill payment show', dict(payment=payment))['settlement_current']['unapplied']['amount'] == STILL_FREE

    # The void: an exact reversal at the payment's own date, readable beside the original.
    run('bill payment unapply', dict(payment=payment), reason='Take it all back')
    run('bill payment void', dict(payment=payment), reason='Cheque was never sent')
    voided = run('bill payment history', dict(payment=payment))
    assert voided['status'] == 'voided' and voided['count'] == 1
    batches = voided['items'][0]['batches']
    assert [batch['kind'] for batch in batches] == ['original', 'reversal']
    assert batches[0]['effective_date'] == batches[1]['effective_date'] == '2017-03-10'
    assert not any(edge['active'] for edge in voided['items'][0]['applications'])

    # Both bills are open again, and the books still balance.
    aging = run('report ap-aging', dict(as_of='2017-03-31'))
    assert aging['totals']['total']['amount'] == OWED
    totals, _ = _balances(run, '2017-03-31')
    assert totals['debit'] == totals['credit'] and totals['debit']['amount'] == OWED

    # The revision walk pages on the bill's own cursor discipline.
    page = run('bill payment history', dict(payment=payment, limit=1))
    assert page['count'] == 1 and page['has_more'] is False and page['next_cursor'] is None
    assert page['audit_watermark'] == voided['audit_watermark']
    with pytest.raises(BookflowError) as missing:
        run('bill payment history', dict(payment='no-such-payment'))
    assert missing.value.code == 'E_RECORD_NOT_FOUND'


# ------------------------------------------------------- the closing-date line on AP


def test_unapply_is_refused_into_a_closed_period_and_leaves_the_state_alone(payables):
    run = payables['run']
    first, _second = _three_bills(payables)
    paid = run('bill pay', dict(date='2017-03-10', funding_account=payables['bank'],
               method=payables['check'], bills=[{'bill': first['id']}]), reason='Pay the first bill')
    payment = paid['payments'][0]['id']
    assert _open(run, first['id']) == ('0.00', 'paid')

    run('company update', dict(closing_date=CLOSING), reason='Close March')

    before = run('bill payment show', dict(payment=payment))
    with pytest.raises(BookflowError) as refusal:
        run('bill payment unapply', dict(payment=payment), reason='Take it back')
    assert refusal.value.code == 'E_PERIOD_CLOSED'
    assert refusal.value.details == {'date': '2017-03-10', 'closing_date': CLOSING}

    # Nothing moved: not the payment, not the bill, not the books.
    after = run('bill payment show', dict(payment=payment))
    assert after == before
    assert _open(run, first['id']) == ('0.00', 'paid')
    assert run('report ap-aging', dict(as_of='2017-03-31'))['totals']['total']['amount'] == SECOND_BILL


def test_the_same_closing_date_already_held_for_apply_and_void(payables):
    """Apply and void were built with the gate; this is the check, not the assumption."""
    run = payables['run']
    march, _second = _three_bills(payables)
    april = run('bill post', dict(vendor=payables['vendor'], date='2017-04-03',
                expenses=[{'account': payables['parts'], 'amount': SECOND_BILL}]),
                reason='Enter an April bill')
    paid = run('bill pay', dict(date='2017-03-10', funding_account=payables['bank'],
               method=payables['check'], bills=[{'bill': march['id']}]), reason='Pay the March bill')
    payment = paid['payments'][0]['id']
    # Free the capacity while March is still open, so the void has nothing left attached and
    # cannot be refused for that instead.
    run('bill payment unapply', dict(payment=payment), reason='Free it before the close')
    assert run('bill payment show', dict(payment=payment))['settlement_current']['unapplied']['amount'] == FIRST_BILL
    run('company update', dict(closing_date=CLOSING), reason='Close March')
    before = run('bill payment show', dict(payment=payment))

    with pytest.raises(BookflowError) as void_refusal:
        run('bill payment void', dict(payment=payment), reason='Never sent')
    assert void_refusal.value.code == 'E_PERIOD_CLOSED'
    assert void_refusal.value.details == {'date': '2017-03-10', 'closing_date': CLOSING}

    with pytest.raises(BookflowError) as apply_refusal:
        run('bill payment apply', dict(payment=payment, bills=[{'bill': march['id']}],
            date='2017-03-20'), reason='Put it back on the March bill')
    assert apply_refusal.value.code == 'E_PERIOD_CLOSED'
    assert apply_refusal.value.details == {'date': '2017-03-20', 'closing_date': CLOSING}
    assert run('bill payment show', dict(payment=payment)) == before

    # An application dated after the closing date, on a payment dated before it, is allowed:
    # it changes the open balance from its own date, not the closed-period postings.
    attached = run('bill payment apply', dict(payment=payment, bills=[{'bill': april['id']}],
                   date='2017-04-05'), reason='Point it at the April bill')
    assert _open(run, april['id']) == ('0.00', 'paid')
    assert attached['settlement_current']['unapplied']['amount'] == STILL_FREE


def test_an_open_period_settlement_still_comes_back_off_its_bill(payables):
    run = payables['run']
    _first, second = _three_bills(payables)
    paid = run('bill pay', dict(date='2017-04-10', funding_account=payables['bank'],
               method=payables['check'], bills=[{'bill': second['id']}]), reason='Pay the second bill')
    payment = paid['payments'][0]['id']
    run('company update', dict(closing_date=CLOSING), reason='Close March')

    assert _open(run, second['id']) == ('0.00', 'paid')
    taken_back = run('bill payment unapply', dict(payment=payment), reason='Re-point it')
    assert taken_back['changed'] is True
    assert _open(run, second['id']) == (SECOND_BILL, 'unpaid')
    assert run('bill payment show', dict(payment=payment))['settlement_current']['unapplied']['amount'] == SECOND_BILL
