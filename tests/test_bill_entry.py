"""Enter a vendor bill, and check the books by hand.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything: a 284.60 bill split 184.60 / 100.00, a second bill of 75.25, and
what the trial balance, the balance sheet and the profit and loss say before and after the
first one is voided.
"""
from copy import deepcopy

import pytest

import bookflow
from bookflow.core.errors import BookflowError

# The two bills and their split. Everything below is arithmetic on these.
FIRST = '184.60'
SECOND = '100.00'
BILL = '284.60'
OTHER = '75.25'
BOTH = '359.85'            # 284.60 + 75.25
SECOND_ACCOUNT = '175.25'  # 100.00 + 75.25


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'bills'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Bills organization')
    company = client.company.new(legal_name='Bills', home_currency='USD', timezone='UTC',
                                 organization='Bills organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    parts = client.account.create(company=company, name='Parts Bought', type='expense')['id']
    freight = client.account.create(company=company, name='Freight In', type='expense')['id']
    terms = {row['name']: row['id'] for row in client.term.query(company=company, limit=50)['items']}
    vendor = client.vendor.create(company=company, name='Northside Supply',
                                  terms_id=terms['Net 30'])['id']
    plain = client.vendor.create(company=company, name='Corner Hardware')['id']
    customer = client.customer.create(company=company, name='Adams Plumbing')['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, payable=payable, bank=bank, parts=parts,
                freight=freight, vendor=vendor, plain=plain, customer=customer, terms=terms,
                run=run)


def _bill(books, **extra):
    return dict(vendor=books['vendor'], date='2017-03-03', supplier_reference='INV-7742',
                memo='March parts',
                expenses=[{'account': books['parts'], 'amount': FIRST, 'memo': 'Fittings'},
                          {'account': books['freight'], 'amount': SECOND, 'memo': 'Delivery'}],
                **extra)


def _net(ledger_rows):
    """Signed minor units per account, debits positive, from the ledger's posting rows."""
    net = {}
    for line in ledger_rows:
        if line['kind'] != 'posting':
            continue
        net[line['account_id']] = net.get(line['account_id'], 0) + (
            line['debit']['minor_units'] - line['credit']['minor_units'])
    return net


def _row(report, account_id):
    return next(row for row in report['rows'] if row['account_id'] == account_id)


def test_a_bill_debits_each_expense_account_and_credits_the_payable_the_total(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')

    assert posted['status'] == 'posted'
    assert posted['type'] == 'bill'
    assert posted['number'] == '1'
    assert posted['vendor_id'] == books['vendor']
    assert posted['ap_account_id'] == books['payable']
    # 184.60 into Parts Bought, 100.00 into Freight In, and 284.60 owed. 184.60 + 100.00 is
    # 284.60, so the debits and the credits are both 28460 minor units.
    assert posted['total'] == {'amount': BILL, 'currency': 'USD', 'minor_units': 28460}
    assert posted['expense_total']['minor_units'] == 28460
    batch = posted['revision']['batches'][0]
    assert (batch['kind'], batch['effective_date']) == ('original', '2017-03-03')
    assert batch['debit_total']['amount'] == batch['credit_total']['amount'] == BILL

    lines = books['run']('report general-ledger',
                         {'date_from': '2017-01-01', 'date_to': '2017-12-31', 'limit': 200})['rows']
    assert _net(lines) == {books['parts']: 18460, books['freight']: 10000, books['payable']: -28460}

    # The vendor is on every leg, and Accounts Payable is credited once for the whole bill.
    postings = [line for line in lines if line['kind'] == 'posting']
    payable_legs = [line for line in postings if line['account_id'] == books['payable']]
    assert len(payable_legs) == 1 and payable_legs[0]['credit']['minor_units'] == 28460
    assert {line['party_name'] for line in postings} == {'Northside Supply'}
    assert {line['transaction_type'] for line in postings} == {'bill'}
    assert {line['transaction_number'] for line in postings} == {'1'}

    # Nothing is settled yet, so the whole bill is open.
    assert posted['settlement_current']['open_minor_units'] == 28460
    assert posted['settlement_current']['applied_minor_units'] == 0
    assert posted['settlement_current']['status'] == 'unpaid'

    # The audit trail names the document a bookkeeper entered.
    events = books['client'].audit.list(company=books['company'], limit=5)['items']
    assert events[0]['command'] == 'bill post'


def test_the_trial_balance_the_balance_sheet_and_the_profit_and_loss_agree_by_hand(books):
    books['run']('bill post', _bill(books), reason='Enter the March bill')
    books['run']('bill post', dict(
        vendor=books['plain'], date='2017-03-10', supplier_reference='2291',
        expenses=[{'account': books['freight'], 'amount': OTHER}]), reason='Enter the freight bill')

    trial = books['run']('report trial-balance', {'date_to': '2017-12-31', 'limit': 200})
    # Debits: 184.60 + (100.00 + 75.25) = 359.85. Credits: 284.60 + 75.25 = 359.85.
    assert trial['totals']['debit']['minor_units'] == 35985
    assert trial['totals']['credit']['minor_units'] == 35985
    assert trial['totals']['signed_net']['minor_units'] == 0
    assert _row(trial, books['parts'])['debit']['amount'] == FIRST
    assert _row(trial, books['freight'])['debit']['amount'] == SECOND_ACCOUNT
    assert _row(trial, books['payable'])['credit']['amount'] == BOTH

    # Accounts Payable on the balance sheet is exactly the sum of the open bills.
    sheet = books['run']('report balance-sheet', {'date_to': '2017-12-31', 'limit': 200})
    assert _row(sheet, books['payable'])['amount']['amount'] == BOTH
    open_bills = books['run']('bill query', {'status': 'posted', 'limit': 50})
    assert sum(item['settlement_current']['open_minor_units']
               for item in open_bills['items']) == 35985

    # The expense side of the profit and loss moved by exactly the two bills.
    loss = books['run']('report profit-and-loss',
                        {'date_from': '2017-01-01', 'date_to': '2017-12-31', 'limit': 200})
    assert loss['totals']['income']['minor_units'] == 0
    assert loss['totals']['expense']['minor_units'] == 35985
    assert loss['totals']['net_income']['amount'] == '-359.85'
    assert {row['account_id']: row['amount']['amount'] for row in loss['rows']} == {
        books['parts']: FIRST, books['freight']: SECOND_ACCOUNT}


def test_the_terms_the_vendor_carries_fix_the_due_date(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    # Net 30 on 2017-03-03 is 2017-04-02.
    assert posted['due_date'] == '2017-04-02'
    assert posted['revision']['profile']['terms']['label'] == 'Net 30'
    assert posted['revision']['profile']['due_date_basis'] == 'terms'

    fifteen = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-02-25', terms=books['terms']['Net 15'],
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Net 15 bill')
    # Net 15 on 2017-02-25 is 2017-03-12.
    assert fifteen['due_date'] == '2017-03-12'

    # A leap February and a year rollover, both counted in days by the terms owner.
    leap = books['run']('bill post', dict(
        vendor=books['vendor'], date='2024-01-31',
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Leap February')
    assert leap['due_date'] == '2024-03-01'
    common = books['run']('bill post', dict(
        vendor=books['vendor'], date='2023-01-31',
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Ordinary February')
    assert common['due_date'] == '2023-03-02'
    rollover = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-12-15',
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Year rollover')
    assert rollover['due_date'] == '2018-01-14'


def test_a_date_driven_term_clamps_to_the_month_and_honours_its_threshold(books):
    end = books['client'].term.create(company=books['company'], name='Day 31 net 5',
                                      kind='date_driven', due_day_of_month=31,
                                      due_next_month_if_within_days=5)['id']
    leap = books['run']('bill post', dict(
        vendor=books['plain'], date='2024-02-10', terms=end,
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Leap month end')
    assert leap['due_date'] == '2024-02-29'
    ordinary = books['run']('bill post', dict(
        vendor=books['plain'], date='2023-02-10', terms=end,
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Ordinary month end')
    assert ordinary['due_date'] == '2023-02-28'
    # Within the threshold, the due date moves to the next month's clamped day.
    near = books['run']('bill post', dict(
        vendor=books['plain'], date='2024-02-27', terms=end,
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Inside the threshold')
    assert near['due_date'] == '2024-03-31'


def test_an_entered_due_date_overrides_the_terms_and_cannot_precede_the_bill(books):
    posted = books['run']('bill post', _bill(books, due_date='2017-03-20'),
                          reason='Enter the March bill')
    assert posted['due_date'] == '2017-03-20'
    assert posted['revision']['profile']['due_date_basis'] == 'entered'
    assert posted['revision']['profile']['terms']['label'] == 'Net 30'

    # A memo-only correction leaves the entered date alone.
    corrected = books['run']('bill update', {'bill': posted['id'], 'memo': 'March parts and freight',
                                             'expected_version': posted['version']},
                             reason='Clarify the memo')
    assert corrected['due_date'] == '2017-03-20'

    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', _bill(books, due_date='2017-03-02'), reason='Backdated due date')
    assert raised.value.code == 'E_VALIDATION'
    assert raised.value.details['fields'][0]['field'] == 'due_date'


def test_the_payable_account_is_resolved_and_never_guessed(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    assert posted['ap_account_id'] == books['payable']

    second = books['client'].account.create(company=books['company'], name='Payables to factor',
                                            type='accounts_payable')['id']
    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', dict(
            vendor=books['vendor'], date='2017-03-04',
            expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Ambiguous payable')
    assert raised.value.code == 'E_VALIDATION'
    assert raised.value.details['fields'][0]['field'] == 'ap_account'
    assert '2 active ones' in raised.value.details['fields'][0]['problem']

    named = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-04', ap_account=second,
        expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Named payable')
    assert named['ap_account_id'] == second

    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', dict(
            vendor=books['vendor'], date='2017-03-05', ap_account=books['bank'],
            expenses=[{'account': books['parts'], 'amount': '10.00'}]), reason='Bank as payable')
    assert raised.value.code == 'E_VALIDATION'
    assert 'Accounts Payable' in raised.value.details['fields'][0]['problem']


def test_an_expense_line_cannot_name_an_account_no_expense_can_go_to(books):
    # Undeposited Funds is an other-current-asset account, so only its system role keeps it off
    # an expense line: what lands there is the deposit owner's, not a bill's.
    undeposited = next(
        row['id'] for row in books['client'].account.list(company=books['company'])['items']
        if books['client'].account.show(
            account=row['id'], company=books['company'])['system_role'] == 'undeposited_funds')
    for account in (books['bank'], books['payable'], undeposited):
        with pytest.raises(BookflowError) as raised:
            books['run']('bill post', dict(
                vendor=books['vendor'], date='2017-03-04',
                expenses=[{'account': account, 'amount': '10.00'}]), reason='Ineligible line')
        assert raised.value.code == 'E_VALIDATION'
        assert raised.value.details['fields'][0]['field'] == 'expenses.0.account'

    # Nothing was written by any of the refusals.
    assert books['run']('report trial-balance', {'date_to': '2017-12-31', 'limit': 200}
                        )['totals']['debit']['minor_units'] == 0


def test_a_billable_cost_names_the_job_it_will_be_passed_on_to(books):
    posted = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-03',
        expenses=[{'account': books['parts'], 'amount': FIRST, 'customer': books['customer'],
                   'billable': True},
                  {'account': books['freight'], 'amount': SECOND, 'customer': books['customer']}]),
        reason='Job costs')
    lines = posted['revision']['expenses']
    assert [line['billable'] for line in lines] == [True, False]
    assert [line['customer_id'] for line in lines] == [books['customer'], books['customer']]
    assert lines[0]['line_snapshot']['customer']['label'] == 'Adams Plumbing'

    # Naming the job attributes the cost; billable is the separate decision to pass it on, and
    # it cannot be made without a job to pass it to.
    with pytest.raises(BookflowError):
        books['run']('bill post', dict(
            vendor=books['vendor'], date='2017-03-04',
            expenses=[{'account': books['parts'], 'amount': '10.00', 'billable': True}]),
            reason='Billable with no job')


def test_a_repeated_supplier_reference_is_reported_and_never_refused(books):
    first = books['run']('bill post', _bill(books), reason='Enter the March bill')
    assert first['duplicate_references'] == []

    # Same vendor, same reference in a different spelling: stray spaces and a different case
    # fold to one reference, and the spelling each bill arrived in survives on it.
    again = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-09', supplier_reference='  inv-7742 ',
        expenses=[{'account': books['parts'], 'amount': '12.00'}]), reason='The same number twice')
    assert again['status'] == 'posted'
    assert [row['bill_id'] for row in again['duplicate_references']] == [first['id']]
    assert again['duplicate_references'][0]['supplier_reference'] == 'INV-7742'
    assert again['duplicate_references'][0]['total']['amount'] == BILL
    assert again['supplier_reference'] == 'inv-7742'

    # Voided history still counts: it is exactly the case a person needs told.
    books['run']('bill void', {'bill': first['id'], 'expected_version': first['version']},
                 reason='Entered twice')
    third = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-11', supplier_reference='INV-7742',
        expenses=[{'account': books['parts'], 'amount': '13.00'}]), reason='And once more')
    assert {row['bill_id'] for row in third['duplicate_references']} == {first['id'], again['id']}
    assert {row['status'] for row in third['duplicate_references']} == {'voided', 'posted'}

    # A composed accent and a decomposed one are the same reference.
    composed = books['run']('bill post', dict(
        vendor=books['plain'], date='2017-03-12', supplier_reference='RÉF-1',
        expenses=[{'account': books['parts'], 'amount': '16.00'}]), reason='Composed accent')
    decomposed = books['run']('bill post', dict(
        vendor=books['plain'], date='2017-03-13', supplier_reference='RÉf-1',
        expenses=[{'account': books['parts'], 'amount': '17.00'}]), reason='Decomposed accent')
    assert composed['supplier_reference'] != decomposed['supplier_reference']
    assert [row['bill_id'] for row in decomposed['duplicate_references']] == [composed['id']]

    # Another vendor's identical number is not this vendor's repeat.
    other = books['run']('bill post', dict(
        vendor=books['plain'], date='2017-03-14', supplier_reference='INV-7742',
        expenses=[{'account': books['parts'], 'amount': '14.00'}]), reason='Another vendor')
    assert other['duplicate_references'] == []

    # Blank references never collide with each other.
    for date in ('2017-03-15', '2017-03-16'):
        blank = books['run']('bill post', dict(
            vendor=books['vendor'], date=date,
            expenses=[{'account': books['parts'], 'amount': '15.00'}]), reason='No reference')
        assert blank['supplier_reference'] is None
        assert blank['duplicate_references'] == []


def test_correcting_a_bill_reverses_the_old_effect_and_replaces_it_in_full(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    line_ids = [line['line_id'] for line in posted['revision']['expenses']]

    corrected = books['run']('bill update', {
        'bill': posted['id'], 'expected_version': posted['version'],
        'expenses': [{'line_id': line_ids[0], 'account': books['parts'], 'amount': '200.00',
                      'memo': 'Fittings'},
                     {'line_id': line_ids[1], 'account': books['freight'], 'amount': SECOND,
                      'memo': 'Delivery'}]}, reason='The supplier repriced the fittings')

    assert corrected['version'] == posted['version'] + 1
    assert corrected['total']['amount'] == '300.00'
    # The new revision carries the replacement; the reversal stays with the revision it undoes,
    # at that revision's own date, so both are readable where they belong.
    assert [(batch['kind'], batch['effective_date'], batch['debit_total']['amount'])
            for batch in corrected['revision']['batches']] == [
        ('replacement', '2017-03-03', '300.00')]
    original = books['run']('bill show', {'bill': posted['id'], 'revision_number': 1})
    assert [(batch['kind'], batch['effective_date'], batch['debit_total']['amount'])
            for batch in original['revision']['batches']] == [
        ('original', '2017-03-03', BILL), ('reversal', '2017-03-03', BILL)]
    assert any(field.startswith('lines') for field in corrected['changed_fields'])
    # The line identities carried across the correction rather than being retired.
    assert [line['line_id'] for line in corrected['revision']['expenses']] == line_ids

    trial = books['run']('report trial-balance', {'date_to': '2017-12-31', 'limit': 200})
    assert _row(trial, books['payable'])['credit']['amount'] == '300.00'
    assert _row(trial, books['parts'])['debit']['amount'] == '200.00'
    assert trial['totals']['debit']['minor_units'] == 30000
    assert trial['totals']['credit']['minor_units'] == 30000

    # An unchanged correction writes nothing.
    same = books['run']('bill update', {'bill': posted['id'], 'memo': 'March parts',
                                        'expected_version': corrected['version']},
                        reason='No change')
    assert same['changed'] is False
    assert same['version'] == corrected['version']

    history = books['run']('bill history', {'bill': posted['id'], 'limit': 10})
    assert [item['revision_number'] for item in history['items']] == [1, 2]
    assert [item['total']['amount'] for item in history['items']] == [BILL, '300.00']


def test_voiding_a_bill_reverses_it_at_its_own_date_and_closes_what_is_open(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    books['run']('bill post', dict(
        vendor=books['plain'], date='2017-03-10',
        expenses=[{'account': books['freight'], 'amount': OTHER}]), reason='Enter the freight bill')

    voided = books['run']('bill void', {'bill': posted['id'],
                                        'expected_version': posted['version']},
                          reason='Billed to the wrong company')
    assert voided['status'] == 'voided'
    assert voided['void_reason'] == 'Billed to the wrong company'
    assert [(batch['kind'], batch['effective_date'], batch['debit_total']['amount'])
            for batch in voided['revision']['batches']] == [
        ('original', '2017-03-03', BILL), ('reversal', '2017-03-03', BILL)]
    assert voided['settlement_current']['open_minor_units'] == 0
    assert voided['settlement_current']['status'] == 'voided'

    # Only the freight bill is left: 75.25 each side, and Accounts Payable is 75.25.
    trial = books['run']('report trial-balance', {'date_to': '2017-12-31', 'limit': 200})
    assert trial['totals']['debit']['minor_units'] == 7525
    assert trial['totals']['credit']['minor_units'] == 7525
    assert _row(trial, books['payable'])['credit']['amount'] == OTHER
    sheet = books['run']('report balance-sheet', {'date_to': '2017-12-31', 'limit': 200})
    assert _row(sheet, books['payable'])['amount']['amount'] == OTHER

    # The original revision is still readable, with its lines intact.
    history = books['run']('bill history', {'bill': posted['id'], 'limit': 10})
    assert history['items'][0]['line_count'] == 2
    assert books['run']('bill show', {'bill': posted['id'], 'revision_number': 1}
                        )['revision']['expenses'][0]['amount']['amount'] == FIRST

    with pytest.raises(BookflowError) as raised:
        books['run']('bill update', {'bill': posted['id'], 'memo': 'anything',
                                     'expected_version': voided['version']}, reason='After a void')
    assert raised.value.code == 'E_VALIDATION'


def test_the_payable_is_one_stable_record_that_breaks_down_line_by_line(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    obligation = posted['revision']['obligation']
    assert obligation['ordinal'] == 1
    assert obligation['vendor_id'] == books['vendor']
    assert obligation['ap_account_id'] == books['payable']
    assert obligation['currency'] == 'USD'
    # One component per entered line, adding up to the bill, each naming the exact attribution
    # row that credited Accounts Payable for it.
    assert [component['amount']['amount']
            for component in obligation['components']] == [FIRST, SECOND]
    assert sum(component['amount_minor_units'] for component in obligation['components']) == 28460
    assert {component['document_line_id'] for component in obligation['components']} == {
        line['id'] for line in posted['revision']['expenses']}
    assert len({component['posting_source_id'] for component in obligation['components']}) == 2

    corrected = books['run']('bill update', {
        'bill': posted['id'], 'expected_version': posted['version'], 'memo': 'Repriced'},
        reason='Clarify the memo')
    after = corrected['revision']['obligation']
    # The payable itself does not move when the bill is corrected: a settlement attached to it
    # is still attached. Only its per-revision breakdown is rewritten.
    assert after['id'] == obligation['id']
    assert after['created_at'] == obligation['created_at']
    assert {component['id'] for component in after['components']} & {
        component['id'] for component in obligation['components']} == set()
    assert sum(component['amount_minor_units'] for component in after['components']) == 28460
    assert corrected['settlement_current']['obligation_id'] == obligation['id']


def test_query_pages_both_ways_and_a_cursor_belongs_to_the_contract_that_minted_it(books):
    made = [books['run']('bill post', dict(
        vendor=books['vendor'], date=f'2017-03-0{day}', supplier_reference=f'REF-{day}',
        expenses=[{'account': books['parts'], 'amount': f'{day}0.00'}]),
        reason='Enter a bill')['id'] for day in (1, 2, 3)]

    ascending = books['run']('bill query', {'limit': 2})
    assert [item['id'] for item in ascending['items']] == made[:2]
    assert ascending['has_more'] is True
    rest = books['run']('bill query', {'limit': 2, 'cursor': ascending['next_cursor']})
    assert [item['id'] for item in rest['items']] == made[2:]
    assert rest['has_more'] is False

    descending = books['run']('bill query', {'limit': 3, 'direction': 'desc'})
    assert [item['id'] for item in descending['items']] == list(reversed(made))

    # A cursor belongs to the direction that minted it, and to the filters beside it.
    for changed in ({'direction': 'desc'}, {'vendor': books['vendor']}):
        with pytest.raises(BookflowError) as raised:
            books['run']('bill query', dict(limit=2, cursor=ascending['next_cursor'], **changed))
        assert raised.value.code == 'E_VALIDATION'

    # Exact filters: one vendor, one reference, one due-date window.
    assert books['run']('bill query', {'vendor': books['plain'], 'limit': 10})['count'] == 0
    reference = books['run']('bill query', {'supplier_reference': 'ref-2', 'limit': 10})
    assert [item['id'] for item in reference['items']] == [made[1]]
    due = books['run']('bill query', {'due_to': '2017-04-01', 'limit': 10})
    assert [item['id'] for item in due['items']] == made[:2]


def test_a_stale_correction_says_who_changed_the_bill_and_what_they_changed(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    books['run']('bill update', {'bill': posted['id'], 'expected_version': posted['version'],
                                 'memo': 'Someone else got here first'}, reason='First writer')

    with pytest.raises(BookflowError) as raised:
        books['run']('bill update', {'bill': posted['id'], 'expected_version': posted['version'],
                                     'memo': 'My change'}, reason='Second writer')
    assert raised.value.code == 'E_VERSION_CONFLICT'
    assert raised.value.details['current_version'] == posted['version'] + 1
    assert raised.value.details['changed_fields'] == ['memo']
    assert 'memo' in raised.value.message


def test_a_bill_carries_the_company_s_own_custom_fields_and_a_class(books):
    field = books['client'].run('custom-field create', dict(
        name='Purchase order number', kind='text', scopes=['bill']), company=books['company'])['id']
    job = books['client'].run('class create', dict(name='North branch'),
                              company=books['company'])['id']
    posted = books['run']('bill post', _bill(books, class_id=job,
                                             custom_fields={field: 'PO-4471'}),
                          reason='Enter the March bill')
    assert [(row['definition_id'], row['value'])
            for row in posted['revision']['custom_fields']] == [(field, 'PO-4471')]
    assert posted['revision']['profile']['class_id']['label'] == 'North branch'
    # The document's class reaches every line that did not name one of its own.
    assert {line['class_name'] for line in posted['revision']['expenses']} == {'North branch'}

    corrected = books['run']('bill update', {
        'bill': posted['id'], 'expected_version': posted['version'],
        'custom_fields': {field: 'PO-4472'}}, reason='The buyer renumbered the order')
    assert [row['value'] for row in corrected['revision']['custom_fields']] == ['PO-4472']
    # The earlier revision still shows what it was entered with.
    assert [row['value'] for row in books['run'](
        'bill show', {'bill': posted['id'], 'revision_number': 1}
    )['revision']['custom_fields']] == ['PO-4471']


def test_a_bill_cannot_be_entered_into_a_closed_period(books):
    books['client'].company.update(company=books['company'], closing_date='2017-12-31',
                                   expected_version=None, reason='Close the year')
    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', _bill(books), reason='Into a closed year')
    assert raised.value.code == 'E_PERIOD_CLOSED'


# ---------------------------------------------------------------- every surface, same result

COMMANDS = frozenset(('bill post', 'bill show', 'bill update', 'bill void',
                      'bill query', 'bill history'))


@pytest.mark.timeout(300)
def test_the_same_bill_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

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

                expense = (await matrix.call(surface, 'account create',
                                             dict(name='Parity supplies', type='expense')))['id']
                vendor = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Supply Co')))['id']
                entry = dict(vendor=vendor, date='2026-03-04', number='PARITY-1',
                             supplier_reference='INV-9001', memo='Parity parts',
                             expenses=[{'account': expense, 'amount': FIRST, 'memo': 'Fittings'},
                                       {'account': expense, 'amount': SECOND, 'memo': 'Delivery'}])
                assert (await call('bill post', entry, dry_run=True))['dry_run']
                posted = await call('bill post', entry, idempotency_key='bill-1')
                replay = await call('bill post', entry, idempotency_key='bill-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                assert posted['total']['amount'] == BILL

                await call('bill show', {'bill': posted['id']})
                await call('bill history', {'bill': posted['id'], 'limit': 10})
                await call('bill query', {'vendor': vendor, 'limit': 10})
                corrected = await call('bill update', {
                    'bill': posted['id'], 'memo': 'Parity parts and freight',
                    'expected_version': posted['version']})
                await call('bill void', {'bill': posted['id'],
                                         'expected_version': corrected['version']})

                refused = await call('bill post', {
                    **entry, 'number': 'PARITY-2',
                    'expenses': [{'account': posted['ap_account_id'], 'amount': FIRST}]},
                    rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert refused['details']['fields'][0]['field'] == 'expenses.0.account'
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
