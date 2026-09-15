"""Correcting a vendor credit, and what the correction is allowed to move.

The danger a correction carries here is not the ledger, which is a reversal and a replacement
like every other document's. It is the capacity. A vendor credit's settlement components belong
to one revision, and the bills it answers name those components by id -- so a correction that
minted new components and left the old edges standing would hold bills settled against capacity
the current revision no longer has, while what the credit has free, read off the current
revision, said the whole credit was untouched. The same credit, spendable twice.

So every figure below is read back through the real commands: what the bill owes comes from
`bill show`, what the credit has free from `vendor-credit show`, and the ledger from the
general-ledger report rather than from anything the writer said about itself.
"""
from copy import deepcopy

import pytest

import bookflow
from bookflow.core.errors import BookflowError

BILL = '1000.00'
CREDIT = '462.54'
BILL_UNITS, CREDIT_UNITS = 100000, 46254


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'vc-update'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Correction organization')
    company = client.company.new(legal_name='Corrections', home_currency='USD', timezone='UTC',
                                 organization='Correction organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    repairs = client.account.create(company=company, name='Roof Repairs', type='expense')['id']
    parts = client.account.create(company=company, name='Parts Bought', type='expense')['id']
    alto = client.vendor.create(company=company, name='Alto Roofing')['id']
    other = client.vendor.create(company=company, name='Corner Hardware')['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, payable=payable, repairs=repairs, parts=parts,
                alto=alto, other=other, run=run)


def _bill(books, number='B-1', amount=BILL, **extra):
    return books['run']('bill post', dict(
        vendor=books['alto'], date='2026-05-01', due_date='2026-05-31', number=number,
        expenses=[{'account': books['repairs'], 'amount': amount, 'memo': 'Roof'}],
        **extra), reason='Enter ' + number)


def _credit(books, amount=CREDIT, **extra):
    return books['run']('vendor-credit post', dict(
        vendor=books['alto'], date='2026-05-10', number='VC-1', memo='Material returned',
        expenses=[{'account': books['repairs'], 'amount': amount, 'memo': 'Returned tiles'}],
        **extra), reason='Enter VC-1')


def _ledger(books):
    net = {}
    for line in books['run']('report general-ledger',
                             {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})['rows']:
        if line['kind'] != 'posting':
            continue
        net[line['account_id']] = net.get(line['account_id'], 0) + (
            line['debit']['minor_units'] - line['credit']['minor_units'])
    return {account: units for account, units in net.items() if units}


def _open(books, bill_id):
    shown = books['run']('bill show', {'bill': bill_id})
    return shown['settlement_current']['open_minor_units'], shown['settlement_current']['status']


def _free(books, credit_id):
    return books['run']('vendor-credit show',
                        {'credit': credit_id})['settlement_current']['unapplied_minor_units']


# ---------------------------------------------------------------- the ordinary correction


def test_a_credit_typed_for_the_wrong_amount_is_corrected_not_voided_and_rewritten(books):
    credit = _credit(books)
    corrected = books['run']('vendor-credit update',
                             {'credit': credit['id'], 'expected_version': credit['version'],
                              'expenses': [{'line_id': credit['revision']['expenses'][0]['line_id'],
                                            'account': books['repairs'], 'amount': '500.00',
                                            'memo': 'Returned tiles'}]},
                             reason='The credit note was for five hundred')

    assert corrected['changed'] is True
    assert corrected['revision']['revision_number'] == 2
    assert corrected['total_minor_units'] == 50000
    assert corrected['status'] == 'posted'
    # One document, one number, and the line a reader follows is the same line.
    assert corrected['number'] == credit['number'] == 'VC-1'
    assert corrected['revision']['expenses'][0]['line_id'] == credit['revision']['expenses'][0]['line_id']

    # The books carry the corrected figure once, not the wrong one and the right one.
    assert _ledger(books) == {books['payable']: 50000, books['repairs']: -50000}

    # The revision it replaced is still readable, and says what it always said.
    previous = books['run']('vendor-credit show', {'credit': credit['id'], 'revision_number': 1})
    assert previous['revision']['total']['minor_units'] == CREDIT_UNITS
    assert previous['revision']['id'] == credit['revision']['id']
    assert previous['revision']['supersedes_revision_id'] is None
    assert previous['version'] == corrected['version'], 'the header a superseded read carries is the current one'

    kinds = [batch['kind'] for batch in books['run'](
        'vendor-credit show', {'credit': credit['id'], 'revision_number': 1})['revision']['batches']]
    assert kinds == ['original', 'reversal']
    assert [batch['kind'] for batch in corrected['revision']['batches']] == ['replacement']


def test_only_the_field_supplied_moves_and_an_empty_patch_writes_nothing(books):
    credit = _credit(books)
    same = books['run']('vendor-credit update',
                        {'credit': credit['id'], 'expected_version': credit['version']})
    assert same['changed'] is False
    assert same['revision']['revision_number'] == 1

    corrected = books['run']('vendor-credit update',
                             {'credit': credit['id'], 'memo': 'Material returned to Alto'},
                             reason='The memo named the wrong yard')
    assert corrected['changed_fields'] == ['memo']
    assert corrected['revision']['memo'] == 'Material returned to Alto'
    # Nothing else moved: the grid, the reference, the payable and the total are what they were.
    assert corrected['total_minor_units'] == CREDIT_UNITS
    assert [(line['account_id'], line['amount_minor_units'], line['memo'])
            for line in corrected['revision']['expenses']] == [
        (books['repairs'], CREDIT_UNITS, 'Returned tiles')]
    assert corrected['ap_account_id'] == credit['ap_account_id']

    # And a correction that resolves to exactly what is stored is not a correction.
    unchanged = books['run']('vendor-credit update',
                             {'credit': credit['id'], 'memo': 'Material returned to Alto'})
    assert unchanged['changed'] is False
    assert unchanged['revision']['revision_number'] == 2


def test_a_correction_needs_a_reason_and_refuses_a_stale_version(books):
    credit = _credit(books)
    with pytest.raises(BookflowError) as without:
        books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Anything'})
    assert without.value.code == 'E_REASON_REQUIRED'

    with pytest.raises(BookflowError) as stale:
        books['run']('vendor-credit update',
                     {'credit': credit['id'], 'expected_version': credit['version'] + 5,
                      'memo': 'Anything'}, reason='Wrong memo')
    assert stale.value.code == 'E_VERSION_CONFLICT'


def test_the_same_idempotency_key_replays_one_correction_rather_than_writing_two(books):
    credit = _credit(books)
    first = books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Second try'},
                         reason='Wrong memo', idempotency_key='VC-FIX-1')
    again = books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Second try'},
                         reason='Wrong memo', idempotency_key='VC-FIX-1')
    assert again['revision']['id'] == first['revision']['id']
    assert books['run']('vendor-credit history',
                        {'credit': credit['id']})['count'] == 2


# ---------------------------------------------------------------- capacity: the part that can go wrong


def test_correcting_an_applied_credit_releases_and_retakes_exactly_what_the_bill_owed(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': bill['id']}], 'date': '2026-05-12'},
                 reason='Answer B-1 with VC-1')
    assert _open(books, bill['id']) == (BILL_UNITS - CREDIT_UNITS, 'partial')
    assert _free(books, credit['id']) == 0

    corrected = books['run']('vendor-credit update',
                             {'credit': credit['id'],
                              'expenses': [{'line_id': credit['revision']['expenses'][0]['line_id'],
                                            'account': books['repairs'], 'amount': '600.00',
                                            'memo': 'Returned tiles'},
                                           {'account': books['parts'], 'amount': '50.00',
                                            'memo': 'Two boxes back'}]},
                             reason='Two boxes came back as well')

    # The bill owes exactly what it owed: a correction is not a settlement.
    assert _open(books, bill['id']) == (BILL_UNITS - CREDIT_UNITS, 'partial')
    # The credit is worth 65000 now, of which the bill is still holding 46254.
    assert corrected['total_minor_units'] == 65000
    assert _free(books, credit['id']) == 65000 - CREDIT_UNITS

    # Every standing edge names the corrected revision's own capacity, and nothing else is active.
    shown = books['run']('vendor-credit show', {'credit': credit['id']})
    current = {row['id'] for row in shown['revision']['source']['components']}
    active = [row for row in shown['applications'] if row['active']]
    assert {row['source_component_id'] for row in active} <= current
    assert sum(row['amount_minor_units'] for row in active) == CREDIT_UNITS
    assert [row['effective_date'] for row in active] == ['2026-05-12'] * len(active)
    # Both halves are in the record: what was released and what was taken again.
    assert sorted(row['kind'] for row in shown['applications']) == ['apply', 'apply', 'unapply']

    assert _ledger(books) == {books['payable']: 65000 - BILL_UNITS,
                              books['repairs']: BILL_UNITS - 60000, books['parts']: -5000}


def test_a_correction_worth_less_than_the_bills_it_answers_is_refused_by_name(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': bill['id']}]},
                 reason='Answer B-1 with VC-1')

    with pytest.raises(BookflowError) as refused:
        books['run']('vendor-credit update',
                     {'credit': credit['id'],
                      'expenses': [{'line_id': credit['revision']['expenses'][0]['line_id'],
                                    'account': books['repairs'], 'amount': '100.00',
                                    'memo': 'Returned tiles'}]},
                     reason='Only a hundred came back')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    assert refused.value.details['available_minor_units'] == 10000
    assert refused.value.details['requested_minor_units'] == CREDIT_UNITS
    assert 'Unapply' in refused.value.details['next']

    # And nothing was written: the credit, the bill and the books are untouched.
    assert _free(books, credit['id']) == 0
    assert _open(books, bill['id']) == (BILL_UNITS - CREDIT_UNITS, 'partial')
    assert books['run']('vendor-credit show', {'credit': credit['id']})['revision']['revision_number'] == 1


def test_a_correction_dated_after_a_settlement_it_already_made_is_refused_by_date(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': bill['id']}], 'date': '2026-05-12'},
                 reason='Answer B-1 with VC-1')

    with pytest.raises(BookflowError) as refused:
        books['run']('vendor-credit update', {'credit': credit['id'], 'date': '2026-05-20'},
                     reason='The credit note is dated the twentieth')
    assert refused.value.code == 'E_VALIDATION'
    problem = refused.value.details['fields'][0]
    assert problem['field'] == 'date'
    assert '2026-05-12' in problem['problem']
    assert books['run']('vendor-credit show', {'credit': credit['id']})['revision']['date'] == '2026-05-10'


def test_an_earlier_date_is_fine_and_the_settlement_keeps_its_own_day(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': bill['id']}], 'date': '2026-05-12'},
                 reason='Answer B-1 with VC-1')
    corrected = books['run']('vendor-credit update', {'credit': credit['id'], 'date': '2026-05-08'},
                             reason='The credit note is dated the eighth')
    assert corrected['date'] == '2026-05-08'
    assert [batch['effective_date'] for batch in corrected['revision']['batches']] == ['2026-05-08']
    active = [row for row in books['run']('vendor-credit show', {'credit': credit['id']})['applications']
              if row['active']]
    assert [row['effective_date'] for row in active] == ['2026-05-12']
    assert _open(books, bill['id']) == (BILL_UNITS - CREDIT_UNITS, 'partial')


def test_two_bills_on_two_dates_each_keep_their_own_amount_and_day(books):
    first, second = _bill(books, 'B-1', '300.00'), _bill(books, 'B-2', '200.00')
    credit = _credit(books, '500.00')
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': first['id']}], 'date': '2026-05-12'},
                 reason='Answer B-1')
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': second['id']}], 'date': '2026-05-19'},
                 reason='Answer B-2')

    books['run']('vendor-credit update', {'credit': credit['id'], 'supplier_reference': 'CN-88'},
                 reason='The vendor gave the note a number')
    assert _open(books, first['id']) == (0, 'paid')
    assert _open(books, second['id']) == (0, 'paid')
    active = sorted(((row['bill_number'], row['amount_minor_units'], row['effective_date'])
                     for row in books['run']('vendor-credit show',
                                             {'credit': credit['id']})['applications'] if row['active']))
    assert active == [('B-1', 30000, '2026-05-12'), ('B-2', 20000, '2026-05-19')]


def test_the_credit_can_be_corrected_up_to_what_it_answers_and_not_a_cent_below(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply',
                 {'credit': credit['id'], 'bills': [{'bill': bill['id']}]},
                 reason='Answer B-1 with VC-1')
    exact = books['run']('vendor-credit update',
                         {'credit': credit['id'],
                          'expenses': [{'line_id': credit['revision']['expenses'][0]['line_id'],
                                        'account': books['repairs'], 'amount': CREDIT,
                                        'memo': 'Returned roof tiles'}]},
                         reason='The memo named the wrong material')
    assert exact['total_minor_units'] == CREDIT_UNITS
    assert _free(books, credit['id']) == 0
    assert _open(books, bill['id']) == (BILL_UNITS - CREDIT_UNITS, 'partial')


# ---------------------------------------------------------------- what a correction may never do


def test_a_correction_cannot_move_the_credit_onto_another_vendor_or_payable(books):
    credit = _credit(books)
    with pytest.raises(BookflowError) as vendor:
        books['run']('vendor-credit update', {'credit': credit['id'], 'vendor': books['other']},
                     reason='It was the other yard')
    assert vendor.value.code == 'E_APPLICATION_INCOMPATIBLE'
    assert vendor.value.details['reason'] == 'vendor_credit_ownership'
    assert vendor.value.details['field'] == 'vendor'

    second = books['run']('account create', {'name': 'Second Payable', 'type': 'accounts_payable'},
                          reason='A second payable to aim at')['id']
    with pytest.raises(BookflowError) as payable:
        books['run']('vendor-credit update', {'credit': credit['id'], 'ap_account': second},
                     reason='It belongs on the other payable')
    assert payable.value.code == 'E_APPLICATION_INCOMPATIBLE'
    assert payable.value.details['field'] == 'ap_account'
    # Naming the vendor and payable it already has is a guard that passes, not a change.
    assert books['run']('vendor-credit update',
                        {'credit': credit['id'], 'vendor': books['alto'],
                         'ap_account': books['payable']})['changed'] is False


def test_a_voided_credit_cannot_be_corrected(books):
    credit = _credit(books)
    books['run']('vendor-credit void', {'credit': credit['id']}, reason='Never arrived')
    with pytest.raises(BookflowError) as refused:
        books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Anything'},
                     reason='Wrong memo')
    assert refused.value.code == 'E_APPLICATION_INACTIVE'
    assert refused.value.details['status'] == 'voided'
    assert 'corrected credit instead' in refused.value.details['next']


def test_neither_the_superseded_date_nor_the_corrected_one_may_be_in_a_closed_period(books):
    credit = _credit(books)
    books['run']('company update', {'closing_date': '2026-05-12'}, reason='Close to the twelfth')
    with pytest.raises(BookflowError) as closed:
        books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Anything'},
                     reason='Wrong memo')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert closed.value.details['date'] == '2026-05-10'

    # And the corrected date being open is not enough: the revision being reversed is undone
    # where it happened, so that period has to be open too.
    with pytest.raises(BookflowError) as superseded:
        books['run']('vendor-credit update', {'credit': credit['id'], 'date': '2026-05-15'},
                     reason='Wrong date')
    assert superseded.value.code == 'E_PERIOD_CLOSED'
    assert superseded.value.details['date'] == '2026-05-10'


def test_no_finished_reconciliation_can_hold_a_vendor_credit_so_none_is_fenced(books):
    """Why this correction carries no reconciliation refusal, unlike the customer refund's.

    A statement is written about a bank or credit-card account, and a vendor credit posts to
    neither: Accounts Payable on one side and the expense-family accounts a bill may debit on
    the other. It is not a statement producer at all, so no reconciliation can ever be holding
    one. If that ever changes, this goes red and the fence has to be built before it ships.
    """
    from bookflow.company import reconciliation_models as statements

    assert 'vendor_credit' not in statements.PRODUCER_ROLES
    credit = _credit(books)
    accounts = {row['id']: row for row in books['run']('account query', {'limit': 200})['items']}
    posted = books['run']('report general-ledger',
                          {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200})['rows']
    touched = {row['account_id'] for row in posted if row['kind'] == 'posting'}
    assert touched and not any(accounts[identifier]['type'] in statements.STATEMENT_ACCOUNTS
                               for identifier in touched)
    assert books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Corrected'},
                        reason='Wrong memo')['changed'] is True


def test_a_retired_line_identity_cannot_return_and_none_may_name_two_rows(books):
    credit = _credit(books)
    line = credit['revision']['expenses'][0]['line_id']
    books['run']('vendor-credit update',
                 {'credit': credit['id'],
                  'expenses': [{'account': books['parts'], 'amount': '10.00', 'memo': 'Boxes'}]},
                 reason='It was the boxes, not the tiles')
    with pytest.raises(BookflowError) as retired:
        books['run']('vendor-credit update',
                     {'credit': credit['id'],
                      'expenses': [{'line_id': line, 'account': books['repairs'],
                                    'amount': '10.00', 'memo': 'Tiles'}]},
                     reason='Put the tiles back')
    assert retired.value.code == 'E_VALIDATION'
    assert retired.value.details['fields'][0]['field'] == 'expenses.line_id'


# ---------------------------------------------------------------- history


def test_history_pages_every_revision_oldest_first_with_the_current_header(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply', {'credit': credit['id'], 'bills': [{'bill': bill['id']}]},
                 reason='Answer B-1 with VC-1')
    books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Second'},
                 reason='First correction')
    current = books['run']('vendor-credit update', {'credit': credit['id'], 'memo': 'Third'},
                           reason='Second correction')

    page = books['run']('vendor-credit history', {'credit': credit['id']})
    assert page['count'] == 3 and page['has_more'] is False
    assert [row['revision_number'] for row in page['items']] == [1, 2, 3]
    assert [row['memo'] for row in page['items']] == ['Material returned', 'Second', 'Third']
    assert page['number'] == 'VC-1' and page['status'] == 'posted'
    assert page['version'] == current['version']
    assert page['current_revision_id'] == current['revision']['id']
    assert [row['line_count'] for row in page['items']] == [1, 1, 1]
    # Only the revision the books stand on answers the bill; the superseded ones were released.
    assert [sum(1 for edge in row['applications'] if edge['active']) for row in page['items']] == [0, 0, 1]
    assert [len(row['applications']) for row in page['items']] == [2, 2, 1]
    assert [[batch['kind'] for batch in row['batches']] for row in page['items']] == [
        ['original', 'reversal'], ['replacement', 'reversal'], ['replacement']]

    first = books['run']('vendor-credit history', {'credit': credit['id'], 'limit': 2})
    assert first['has_more'] is True and [row['revision_number'] for row in first['items']] == [1, 2]
    rest = books['run']('vendor-credit history',
                        {'credit': credit['id'], 'limit': 2, 'cursor': first['next_cursor']})
    assert [row['revision_number'] for row in rest['items']] == [3]


def test_history_refuses_an_unknown_credit(books):
    with pytest.raises(BookflowError) as missing:
        books['run']('vendor-credit history', {'credit': 'VC-NOPE'})
    assert missing.value.code == 'E_RECORD_NOT_FOUND'


# ---------------------------------------------------------------- the preview is what the write does


def test_the_preview_of_a_correction_writes_nothing_and_says_what_the_write_would(books):
    bill = _bill(books)
    credit = _credit(books)
    books['run']('vendor-credit apply', {'credit': credit['id'], 'bills': [{'bill': bill['id']}]},
                 reason='Answer B-1 with VC-1')
    before = deepcopy(books['run']('vendor-credit show', {'credit': credit['id']}))
    preview = books['run']('vendor-credit update',
                           {'credit': credit['id'], 'memo': 'Corrected memo'},
                           reason='Wrong memo', dry_run=True)
    assert preview['revision']['revision_number'] == 2
    assert preview['revision']['memo'] == 'Corrected memo'
    assert books['run']('vendor-credit show', {'credit': credit['id']}) == before
