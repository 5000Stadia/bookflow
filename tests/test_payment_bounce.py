"""R176: a customer's check comes back, recorded in one step as the anchor's Record Bounced Check does.

The worked case is the fake company's July: Tom Hendricks's check 1182 for 486.93 paid invoice 2379
and was banked on 07-02 with another customer's 318.27 as one 805.20 deposit. On 07-09 the bank
returns it, charges 12.00, and Harbor Electric bills Tom a 35.00 returned-check fee as invoice 2393.
Every figure asserted is worked out in the comment beside it from the documents' own amounts and
compared with the report commands, never with the bounce's own output.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'bounce'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Bounce organization')
    company = client.company.new(legal_name='Harbor Electric', home_currency='USD', timezone='UTC',
                                 organization='Bounce organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    by_name = {row['full_name']: row['id'] for row in accounts}
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    service_charges = run('account create', dict(name='Bank Service Charges', type='expense'))['id']
    charges = run('account create', dict(name='Returned Check Charges', type='income'))['id']
    exempt = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    item = run('item create', dict(name='Service call', type='service', sales_enabled=True, description='Service call',
                                   sales_tax_code_id=exempt, income_account_id=income, price='100'))['id']
    fee_item = run('item create', dict(name='Returned Check Charge', type='other_charge', sales_enabled=True,
                                       description='Returned check charge', sales_tax_code_id=exempt,
                                       income_account_id=charges, price='35'))['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    hendricks = run('customer create', dict(name='Hendricks, Tom'))['id']
    kowalski = run('customer create', dict(name='Kowalski, Anna'))['id']
    return dict(client=client, company=company, run=run, bank=bank, income=income, service_charges=service_charges,
                charges=charges, item=item, fee_item=fee_item, methods=methods, exempt=exempt, hendricks=hendricks,
                kowalski=kowalski, by_name=by_name)


def balances(books, date_to='2026-12-31'):
    """Signed minor units per account name from the trial balance, debits positive, zeros dropped."""
    report = books['run']('report trial-balance', dict(date_to=date_to, limit=200))
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['account_name'] if 'account_name' in row else row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows'] if row['debit']['minor_units'] != row['credit']['minor_units']}


def by_id(books, **named):
    """The trial balance keyed by the account ids given, as {label: signed minor units}."""
    raw = balances(books)
    return {label: raw.get(account, 0) for label, account in named.items()}


def invoice(books, customer, amount, number, date='2026-06-20'):
    return books['run']('invoice post', dict(customer=customer, date=date, number=number, lines=[
        dict(item=books['item'], quantity='1', unit_price=amount, tax_code=books['exempt'])]), reason='Bill the customer')


def receive(books, customer, sale, amount, reference, date='2026-07-01'):
    return books['run']('payment receive', dict(
        customer=customer, date=date, amount=amount, payment_method=books['methods']['Check'], reference=reference,
        operation_key='receive-' + reference, applications=dict(mode='inline', items=[
            dict(invoice=sale['id'], expected_version=1, amount=amount)])), reason='Customer paid')


def deposit(books, payments, date='2026-07-02'):
    sources = [dict(source_type='payment', source=row['id'],
                    expected_version=books['run']('payment show', dict(payment=row['id']))['version']) for row in payments]
    return books['run']('deposit post', dict(operation_key='deposit-' + date, document=dict(
        mode='inline', deposit_to=books['bank'], date=date, sources=sources, additional=[])), reason='Bank the checks')


def due(books, sale):
    return books['run']('invoice settlement', dict(invoice=sale['id']))['due_minor_units']


@pytest.fixture
def july(books):
    """Both July receipts banked as one 805.20 deposit; Hendricks's check 1182 is the 486.93 one."""
    sale = invoice(books, books['hendricks'], '486.93', '2379')
    other = invoice(books, books['kowalski'], '318.27', '2380')
    check = receive(books, books['hendricks'], sale, '486.93', '1182')
    second = receive(books, books['kowalski'], other, '318.27', '77')
    made = deposit(books, [check, second])
    return dict(books, sale=sale, check=check, second=second, deposit=made, deposit_id=made['deposit']['id'] if 'deposit' in made else made['id'])


def bounce(books, payment, *, key='bounce-1182', date='2026-07-09', reason='Bank returned check 1182: NSF',
           bank_fee='12.00', customer_fee='35.00', **extra):
    shown = books['run']('payment show', dict(payment=payment['id']))
    raw = dict(payment=payment['id'], expected_version=shown['version'], date=date, operation_key=key, **extra)
    if bank_fee:
        raw['bank_fee'] = dict(amount=bank_fee, account=books['service_charges'])
    if customer_fee:
        raw['customer_fee'] = dict(amount=customer_fee, account=books['charges'], number='2393')
    return books['run']('payment bounce', raw, reason=reason)


def test_the_julys_bounced_check_in_one_step(july):
    run = july['run']
    checking, charges, fees = july['bank'], july['charges'], july['service_charges']
    receivable = july['by_name']['Accounts Receivable']
    # Before: 805.20 banked, both invoices paid, nothing owed.
    assert balances(july)[checking] == 80520
    assert due(july, july['sale']) == 0

    done = bounce(july, july['check'])
    assert done['returned']['minor_units'] == 48693 and done['bounced_on'] == '2026-07-09'
    assert [(row['number'], row['reopened']['minor_units'], row['due']['minor_units'])
            for row in done['reopened_invoices']] == [('2379', 48693, 48693)]
    assert done['bank_fee']['amount']['minor_units'] == 1200 and done['customer_fee']['amount']['minor_units'] == 3500
    assert done['customer_fee']['number'] == '2393'

    # The invoice paid by the check is owed again, with its balance, and the fee is a new open charge.
    assert due(july, july['sale']) == 48693
    fee_invoice = run('invoice show', dict(invoice=done['customer_fee']['id']))
    assert fee_invoice['number'] == '2393' and fee_invoice['total']['minor_units'] == 3500
    assert due(july, fee_invoice) == 3500
    # Checking: 805.20 deposited, 486.93 returned, 12.00 fee = 306.27. Receivable: Hendricks owes 486.93 + 35.00,
    # Kowalski's 318.27 stays paid. The fee is expensed and billed.
    assert by_id(july, checking=checking, receivable=receivable, fees=fees, charges=charges) == dict(
        checking=30627, receivable=52193, fees=1200, charges=-3500)
    assert run('report ar-aging', dict(as_of='2026-07-31'))['totals']['total']['minor_units'] == 52193
    assert run('report customer-balance-summary', dict(as_of='2026-07-31'))['totals']['balance']['minor_units'] == 52193

    # The bank shows the original deposit and, on 07-09, the returned amount and the fee as two lines.
    register = run('register query', dict(account=checking, date_from='2026-07-01', date_to='2026-07-31', limit=50))
    lines = sorted((row['effective_date'], row['transaction_type'], row['increase']['minor_units'], row['decrease']['minor_units'])
                   for row in register['rows'] if row['kind'] == 'posting')
    assert lines == [('2026-07-02', 'deposit', 31827, 0), ('2026-07-02', 'deposit', 48693, 0),
                     ('2026-07-09', 'customer_refund', 0, 48693), ('2026-07-09', 'journal_entry', 0, 1200)]
    # The receipt reads "bounced on ..." and keeps its history.
    receipt = run('payment show', dict(payment=july['check']['id']))
    assert receipt['status'] == 'posted' and receipt['bounce']['note'] == 'bounced on 2026-07-09'
    assert receipt['bounce']['returned']['minor_units'] == 48693
    assert receipt['bounce']['refund_id'] == done['refund']['id']
    listed = run('payment query', {})['items']
    assert {row['id']: row.get('bounced_on') for row in listed}[july['check']['id']] == '2026-07-09'
    # The customer's history carries the returned check.
    refund = run('customer-refund show', dict(refund=done['refund']['id']))
    assert 'Returned check 1182' in refund['reference'] and 'NSF' in refund['memo']


def test_a_dry_run_writes_nothing_and_the_same_key_never_bounces_twice(july):
    run = july['run']
    before = balances(july)
    shown = run('payment show', dict(payment=july['check']['id']))
    raw = dict(payment=july['check']['id'], expected_version=shown['version'], date='2026-07-09', operation_key='bounce-dry',
               bank_fee=dict(amount='12.00', account=july['service_charges']))
    preview = run('payment bounce', raw, reason='NSF', dry_run=True)
    assert preview['dry_run'] and preview['bounce_id'] is None and preview['returned']['minor_units'] == 48693
    assert balances(july) == before and due(july, july['sale']) == 0
    # A stale fingerprint is refused; the one the preview gave is accepted.
    with pytest.raises(BookflowError) as stale:
        run('payment bounce', dict(raw, expected_facts_fingerprint='0' * 64), reason='NSF')
    assert stale.value.code == 'E_PREVIEW_STALE'
    done = run('payment bounce', dict(raw, expected_facts_fingerprint=preview['facts_fingerprint']), reason='NSF')
    again = run('payment bounce', raw, reason='NSF')
    assert again['idempotent_replay'] and not again['changed'] and again['bounce_id'] == done['bounce_id']
    assert [row['number'] for row in again['reopened_invoices']] == ['2379']
    assert by_id(july, checking=july['bank'])['checking'] == 80520 - 48693 - 1200


def test_the_normal_correction_path_reverses_it(july):
    run = july['run']
    done = bounce(july, july['check'], customer_fee=None)
    assert due(july, july['sale']) == 48693
    # Voiding the returned-check refund ends "bounced"; the receipt is an ordinary unapplied receipt again.
    refund = run('customer-refund show', dict(refund=done['refund']['id']))
    run('customer-refund void', dict(refund=refund['id'], expected_version=refund['version']), reason='Bank reversed the return')
    receipt = run('payment show', dict(payment=july['check']['id']))
    assert receipt.get('bounce') is None and receipt['current']['available_minor_units'] == 48693
    assert [row.get('bounced_on') for row in run('payment query', {})['items'] if row['id'] == july['check']['id']] == [None]
    # ...and can be applied to the invoice again, or bounced again.
    run('payment apply', dict(payment=july['check']['id'], expected_version=receipt['version'], date='2026-07-12',
                              operation_key='reapply', applications=dict(mode='inline', items=[
                                  dict(invoice=july['sale']['id'], expected_version=run('invoice show', dict(invoice=july['sale']['id']))['version'],
                                       amount='486.93')])), reason='The bank reversed it')
    assert due(july, july['sale']) == 0
    # The fee entry stands until it is voided like any entry.
    assert by_id(july, fees=july['service_charges'])['fees'] == 1200


def test_refusals_say_what_to_do(july):
    run = july['run']
    shown = run('payment show', dict(payment=july['check']['id']))
    base = dict(payment=july['check']['id'], expected_version=shown['version'], date='2026-07-09', operation_key='refused')

    def refused(raw, *, reason='NSF', code='E_VALIDATION'):
        with pytest.raises(BookflowError) as error:
            run('payment bounce', raw, **({'reason': reason} if reason else {}))
        assert error.value.code == code, (error.value.code, error.value.details)
        return str(error.value.details) + error.value.message

    refused(base, reason=None, code='E_REASON_REQUIRED')
    # The bank's fee is an expense, not the bank or income.
    assert 'expense account' in refused(dict(base, bank_fee=dict(amount='12.00', account=july['bank'])))
    assert 'expense account' in refused(dict(base, bank_fee=dict(amount='12.00', account=july['income'])))
    assert 'greater than zero' in refused(dict(base, bank_fee=dict(amount='0.00', account=july['service_charges'])), code='E_VALUE_RANGE')
    # The customer's fee names an item or an income account, one of the two, and one an item posts to.
    assert 'one of the two' in refused(dict(base, customer_fee=dict(amount='35.00')))
    assert 'one of the two' in refused(dict(base, customer_fee=dict(amount='35.00', item=july['fee_item'], account=july['charges'])))
    lonely = run('account create', dict(name='Other Fees', type='income'))['id']
    assert 'item create' in refused(dict(base, customer_fee=dict(amount='35.00', account=lonely)))
    assert 'income account' in refused(dict(base, customer_fee=dict(amount='35.00', account=july['service_charges'])))
    # Nothing was written by any refusal.
    assert due(july, july['sale']) == 0
    assert by_id(july, checking=july['bank'])['checking'] == 80520
    # A receipt never banked has no returned check to record.
    sale = invoice(july, july['kowalski'], '50.00', '2390')
    unbanked = receive(july, july['kowalski'], sale, '50.00', '78', date='2026-07-03')
    text = refused(dict(payment=unbanked['id'], expected_version=1, date='2026-07-09', operation_key='unbanked'))
    assert 'Undeposited Funds' in text and 'payment void' in text
    # Dated before the receipt.
    refused(dict(base, date='2026-06-30'))
    # Recorded twice.
    bounce(july, july['check'], key='first', bank_fee=None, customer_fee=None)
    again = run('payment show', dict(payment=july['check']['id']))
    assert 'already recorded as bounced' in refused(dict(base, expected_version=again['version'], operation_key='second'))


def test_a_receipt_with_an_early_payment_discount_returns_only_its_cash(books):
    """980.00 cash and a 20.00 discount settled the invoice: the bank takes back 980.00, the discount stays as credit."""
    terms = {row['name']: row['id'] for row in books['run']('term query', dict(limit=50))['items']}
    customer = books['run']('customer create', dict(name='Adams Plumbing', terms_id=terms['2% 10 Net 30']))['id']
    sale = invoice(books, customer, '1000.00', '3001', date='2026-03-01')
    paid = books['run']('payment receive', dict(
        customer=customer, date='2026-03-08', amount='980.00', payment_method=books['methods']['Check'], reference='900',
        deposit_to=books['bank'], operation_key='disc', applications=dict(mode='inline', items=[
            dict(invoice=sale['id'], expected_version=1, amount='980.00')]),
        discounts=[dict(invoice=sale['id'], amount='20.00')]), reason='Paid early')
    assert by_id(books, checking=books['bank'])['checking'] == 98000
    done = bounce(books, paid, date='2026-03-12', bank_fee=None, customer_fee=None)
    assert done['returned']['minor_units'] == 98000
    # The invoice owes its whole 1,000.00 again; the 20.00 discount is the customer's credit, as when any discounted
    # settlement is unapplied.
    assert due(books, sale) == 100000
    assert done['credit_left']['minor_units'] == 2000
    assert by_id(books, checking=books['bank'])['checking'] == 0


def test_a_receipt_applied_in_a_closed_period_cannot_be_reopened(july):
    run = july['run']
    info = run('company show')
    run('company update', dict(expected_version=info['info_version'], closing_date='2026-07-05'))
    shown = run('payment show', dict(payment=july['check']['id']))
    with pytest.raises(BookflowError) as refused:
        run('payment bounce', dict(payment=july['check']['id'], expected_version=shown['version'], date='2026-07-09',
                                   operation_key='closed'), reason='NSF')
    assert refused.value.code == 'E_PERIOD_CLOSED' and 'closing date' in refused.value.details['next']
    assert due(july, july['sale']) == 0


def test_a_step_that_fails_leaves_the_check_exactly_as_it_was(july, monkeypatch):
    """The unapply, the refund and the fee entry were written before the fee invoice failed: all of it rolls back."""
    from bookflow.company import sales
    run = july['run']
    before, version = balances(july), run('payment show', dict(payment=july['check']['id']))['version']

    def fail(plan, ctx, s):
        raise BookflowError('E_INTERNAL', message='the fee invoice could not be written')

    monkeypatch.setattr(sales, 'apply', fail)
    with pytest.raises(BookflowError):
        bounce(july, july['check'])
    monkeypatch.undo()
    assert balances(july) == before and due(july, july['sale']) == 0
    assert run('payment show', dict(payment=july['check']['id']))['version'] == version
    assert run('customer-refund query', {})['items'] == []
    assert run('payment query', {})['items'][0].get('bounced_on') is None
    # And the same bounce goes through afterwards.
    assert bounce(july, july['check'])['bounce_id']


def test_what_the_old_refusals_point_at_and_what_a_bounce_blocks(july):
    run = july['run']
    shown = run('payment show', dict(payment=july['check']['id']))
    # Voiding a deposited receipt points at the bounce when the bank returned the check.
    with pytest.raises(BookflowError) as old:
        run('payment void', dict(payment=july['check']['id'], expected_version=shown['version'], operation_key='old'), reason='Returned')
    assert old.value.code == 'E_HAS_APPLICATIONS' or old.value.code == 'E_DEPOSIT_DEPENDENCY'
    unapplied = run('payment unapply', dict(payment=july['check']['id'], expected_version=shown['version'], operation_key='un',
        applications=[dict(application_id=july['check']['effect']['applications'][0]['application_id'],
                           invoice_expected_version=run('invoice settlement', dict(invoice=july['sale']['id']))['version'])]), reason='Returned')
    with pytest.raises(BookflowError) as deposited:
        run('payment void', dict(payment=july['check']['id'], expected_version=unapplied['version'], operation_key='old2'), reason='Returned')
    assert deposited.value.code == 'E_DEPOSIT_DEPENDENCY'
    assert '`payment bounce`' in deposited.value.details['reason'] and '`deposit delete`' in deposited.value.details['reason']
    # After a bounce: the refund is not corrected in place, the receipt cannot be voided under it, and the deposit
    # cannot be deleted with it still standing.
    done = bounce(july, july['second'], key='second', reason='Kowalski check returned', bank_fee=None, customer_fee=None)
    refund = run('customer-refund show', dict(refund=done['refund']['id']))
    with pytest.raises(BookflowError) as edited:
        run('customer-refund update', dict(refund=refund['id'], expected_version=refund['version'], memo='x'), reason='Fix')
    assert edited.value.code == 'E_VALIDATION' and 'customer-refund void' in str(edited.value.details)
    receipt = run('payment show', dict(payment=july['second']['id']))
    with pytest.raises(BookflowError) as held:
        run('payment void', dict(payment=july['second']['id'], expected_version=receipt['version'], operation_key='v2'), reason='Fix')
    assert held.value.code == 'E_HAS_REFUND' and 'bounced on 2026-07-09' in held.value.details['next']
    client, company = july['client'], july['company']
    state = client.permission.show()
    client.permission.activate(expected_generation=state['generation'], expected_catalog_sha256=state['catalog_sha256'])
    member = next(x for x in client.membership.list(company=company)['items'] if x['scope_type'] == 'company' and x['scope_id'] == company)
    client.membership.grant(user=member['user_id'], company=company, expected_version=member['version'],
                            grants=['transaction.deposit.delete'], denies=[])
    deposit = run('deposit show', dict(deposit=july['deposit_id']))
    with pytest.raises(BookflowError) as blocked:
        run('deposit delete', dict(deposit=july['deposit_id'], expected_version=deposit['current']['version'], operation_key='dd'), reason='Fix')
    assert blocked.value.code == 'E_VALIDATION' and 'customer-refund void' in str(blocked.value.details) + blocked.value.message


def test_a_statement_import_matches_the_deposit_the_returned_item_and_the_fee(july):
    """The bank's July: the 805.20 deposit, then the returned 486.93 and the 12.00 fee as two lines."""
    done = bounce(july, july['check'], customer_fee=None)
    statement = ('Date,Description,Amount\n2026-07-02,DEPOSIT,805.20\n'
                 '2026-07-09,RETURNED DEPOSITED ITEM,-486.93\n2026-07-09,RETURN ITEM FEE,-12.00\n')
    result = july['run']('reconcile import', dict(account=july['bank'], content=statement))
    assert [(row['date'], row['amount'], row['status']) for row in result['lines']] == [
        ('2026-07-02', 80520, 'matched'), ('2026-07-09', -48693, 'matched'), ('2026-07-09', -1200, 'matched')], result['lines']
    assert result['counts']['unmatched'] == 0 and done['bounce_id']


def test_the_cash_basis_follows_the_check_back_and_the_customer_paying_cash(july):
    """Income is recognised when paid: the returned check takes its income back, and the cash that replaces it restores it."""
    run, charges = july['run'], july['charges']

    def income(basis):
        report = run('report profit-and-loss', dict(date_from='2026-07-01', date_to='2026-07-31', basis=basis, limit=200))
        return {row['account_id']: row['amount']['minor_units'] for row in report['rows'] if row['amount']['minor_units']}

    # Both invoices were sold in June and paid in July: 486.93 + 318.27 = 805.20 of cash-basis income.
    assert income('cash')[july['income']] == 80520
    done = bounce(july, july['check'])
    cash, accrual = income('cash'), income('accrual')
    # The returned check's 486.93 is no longer paid, so only Kowalski's 318.27 is cash-basis income; the 12.00 fee is
    # an expense on either basis. The 35.00 fee is billed but unpaid.
    assert cash[july['income']] == 31827 and cash[july['service_charges']] == 1200 and charges not in cash
    assert accrual.get(charges) == 3500
    # Tom pays cash 521.93 on 07-16 and it settles the reopened invoice and the fee invoice.
    fee = run('invoice show', dict(invoice=done['customer_fee']['id']))
    paid = run('payment receive', dict(
        customer=july['hendricks'], date='2026-07-16', amount='521.93', payment_method=july['methods']['Cash'], deposit_to=july['bank'],
        operation_key='cash', applications=dict(mode='inline', items=[
            dict(invoice=july['sale']['id'], expected_version=run('invoice show', dict(invoice=july['sale']['id']))['version'], amount='486.93'),
            dict(invoice=fee['id'], expected_version=fee['version'], amount='35.00')])), reason='Paid cash')
    assert paid['summary']['paid_in_full_count'] == 2
    cash = income('cash')
    assert cash[july['income']] == 80520 and cash[charges] == 3500
    assert run('report ar-aging', dict(as_of='2026-07-31'))['totals']['total']['minor_units'] == 0
    # Checking: 805.20 - 486.93 - 12.00 + 521.93 = 828.20.
    assert by_id(july, checking=july['bank'])['checking'] == 82820


def test_a_parents_check_for_one_jobs_invoice_bills_the_fee_to_that_job(books):
    """The check came from the property manager and paid Job A's invoice: the refund and the fee follow the job."""
    run = books['run']
    parent = run('customer create', dict(name='Property Manager'))['id']
    job = run('customer create', dict(name='Job A', parent_id=parent))['id']
    sale = invoice(books, job, '300.00', '4001')
    paid = run('payment receive', dict(
        customer=parent, date='2026-07-01', amount='300.00', payment_method=books['methods']['Check'], reference='88',
        deposit_to=books['bank'], operation_key='parent', applications=dict(mode='inline', items=[
            dict(invoice=sale['id'], expected_version=1, amount='300.00')])), reason='Paid')
    done = bounce(books, paid, bank_fee=None, customer_fee='20.00')
    assert due(books, sale) == 30000
    fee = run('invoice show', dict(invoice=done['customer_fee']['id']))
    assert fee['customer_name'] == 'Property Manager:Job A' and fee['total']['minor_units'] == 2000
    assert run('customer-refund show', dict(refund=done['refund']['id']))['customer_name'] == 'Property Manager:Job A'


def test_every_document_the_bounce_writes_is_audited_under_the_same_request_and_reason(july):
    """Who, through what, on whose behalf and why: one request, five events, one reason."""
    done = bounce(july, july['check'], reason='Statement 07-09: returned item, check 1182')
    events = july['run']('audit list', dict(limit=200))['items']
    request = next(row['request_id'] for row in events if row['command'] == 'payment bounce')
    mine = [row for row in events if row['request_id'] == request]
    assert sorted(row['command'] for row in mine) == [
        'customer-refund post', 'invoice post', 'payment bounce', 'payment unapply', 'register post']
    assert {row['reason'] for row in mine} == {'Statement 07-09: returned item, check 1182'}
    assert {row['interface'] for row in mine} == {'python'} and done['bounce_id']
