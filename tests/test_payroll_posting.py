"""Posting one paycheck and settling its net pay by check (R116 increment 2).

Every expected amount is worked by hand from the design's fixtures and written here as a literal; none is taken
from the production calculator. Balances are read straight from posting_lines (debit minus credit, cents).
"""
import json
import sqlite3
import subprocess
from pathlib import Path

import anyio
import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_row3_host import hosted  # noqa: F401 - fixture

ACCOUNTS = [('Wages', 'expense'), ('Employer Tax Expense', 'expense'), ('Net Pay Payable', 'other_current_liability'),
            ('Employee Tax Payable', 'other_current_liability'), ('Employer Tax Payable', 'other_current_liability'),
            ('Deduction Payable', 'other_current_liability'), ('Payroll Bank', 'bank')]


@pytest.fixture
def books(tmp_path, monkeypatch):
    root = tmp_path / 'payroll-posting-books'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Payroll posting organization')
    cid = client.company.new(legal_name='Payroll posting fixture', home_currency='USD', timezone='UTC',
        organization='Payroll posting organization', chart='general')['company_id']

    def run(command, raw=None, **context):
        if registry.get(command).is_write:
            context.setdefault('reason', 'Fictional payroll posting fixture')
        return client.run(command, raw or {}, company=cid, **context)
    for name, kind in ACCOUNTS:
        run('account create', {'name': name, 'type': kind})
    employee = run('employee create', {'name': 'Fictional paycheck employee'})['id']
    run('payroll setup', {'enabled': True, 'expected_version': 0, 'operation_key': 'enable', 'wage_account': 'Wages',
        'employer_tax_account': 'Employer Tax Expense', 'net_pay_account': 'Net Pay Payable'})
    folder = Path(run('company show')['path'])
    return type('Books', (), {'root': root, 'client': client, 'company': cid, 'employee': employee,
                              'path': folder / 'company.db', 'run': staticmethod(run)})


def percent(code, kind, millionths, account, base=('regular',)):
    return {'code': code, 'kind': kind, 'method': 'percent', 'rate_millionths': millionths, 'base': list(base),
            'liability_account': account, 'payee': 'Fictional agency', 'source_note': 'Fictional rate entered by the owner'}


def fixed(code, kind, cents, account):
    return {'code': code, 'kind': kind, 'amount_minor_units': cents, 'liability_account': account,
            'payee': 'Fictional agency', 'source_note': 'Fictional amount entered by the owner'}


def p1(co, key='p1', pay_date='2026-10-09'):
    # P1: 40 h x $25 = $1,000.00 gross; employee tax 10% = $100.00; employer tax 5% = $50.00; net $900.00.
    return {'operation_key': key, 'employee': co.employee, 'period_start': '2026-10-01', 'period_end': '2026-10-07',
            'pay_date': pay_date, 'taxes_confirmed': True, 'earnings': [{'code': 'regular', 'quantity': '40', 'rate': '25'}],
            'items': [percent('income-tax', 'employee_tax', 10_000_000, 'Employee Tax Payable'),
                      percent('employer-tax', 'employer_tax', 5_000_000, 'Employer Tax Payable')]}


def prepared(co, raw):
    draft = co.run('payroll draft', raw)
    return co.run('payroll prepare', {'draft': draft['id'], 'expected_version': draft['version'],
                                      'operation_key': raw['operation_key'] + '-calc'})


def post(co, draft, key, **check):
    return co.run('payroll post', {'draft': draft['id'], 'expected_version': draft['version'], 'operation_key': key,
                                   **{f'check_{k}': v for k, v in check.items()}})


def balances(co):
    with sqlite3.connect(co.path) as db:
        return {name: net for name, net in db.execute(
            'SELECT a.name, sum(p.debit_minor_units) - sum(p.credit_minor_units) FROM posting_lines p '
            'JOIN accounts a ON a.id = p.account_id GROUP BY a.name') if net}


def legs(co, transaction):
    with sqlite3.connect(co.path) as db:
        return sorted(db.execute('SELECT a.name, p.debit_minor_units, p.credit_minor_units FROM posting_lines p '
                                 'JOIN accounts a ON a.id = p.account_id WHERE p.transaction_id = ?', (transaction,)))


def count(co, table):
    with sqlite3.connect(co.path) as db:
        return db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def refused(co, command, body, code, **context):
    with pytest.raises(BookflowError) as caught:
        co.run(command, body, **context)
    assert caught.value.code == code, caught.value.to_dict()
    return caught.value


def test_p1_posts_one_paycheck_and_one_check_and_reversal_restores_the_books(books):
    co = books
    draft = prepared(co, p1(co))
    assert draft['calculation']['net_minor_units'] == 90000
    before = balances(co)
    preview = post(co, draft, 'post-p1', account='Payroll Bank', number='1001')
    pc = co.run('payroll paycheck', {'paycheck': preview['id']})
    assert pc == preview
    assert (pc['gross_minor_units'], pc['employee_tax_minor_units'], pc['employer_cost_minor_units'],
            pc['net_minor_units'], pc['debit_minor_units'], pc['credit_minor_units']) == (100000, 10000, 5000, 90000, 105000, 105000)
    assert [(x['code'], x['amount_minor_units']) for x in pc['components']] == [
        ('regular', 100000), ('income-tax', 10000), ('employer-tax', 5000), ('net', 90000)]
    # Dr Wages 1,000 + Employer Tax Expense 50 = Cr Employee Tax 100 + Employer Tax 50 + Net Pay 900 = 1,050.
    assert legs(co, pc['journal']) == [('Employee Tax Payable', 0, 10000), ('Employer Tax Expense', 5000, 0),
        ('Employer Tax Payable', 0, 5000), ('Net Pay Payable', 0, 90000), ('Wages', 100000, 0)]
    # One check: Dr Net Pay Payable 900 / Cr Bank 900.
    payment = pc['net_payment']
    assert (payment['check_number'], payment['amount_minor_units'], payment['status']) == ('1001', 90000, 'recorded')
    assert legs(co, payment['check']) == [('Net Pay Payable', 90000, 0), ('Payroll Bank', 0, 90000)]
    assert pc['net_status'] == 'payment_recorded' and pc['version'] == 2
    assert balances(co) == {'Wages': 100000, 'Employer Tax Expense': 5000, 'Employee Tax Payable': -10000,
                            'Employer Tax Payable': -5000, 'Payroll Bank': -90000}
    assert pc['year_to_date'] == {'tax_year': 2026, 'gross_minor_units': 100000, 'employee_tax_minor_units': 10000,
        'employee_deduction_minor_units': 0, 'employer_cost_minor_units': 5000, 'net_minor_units': 90000}
    shown = co.run('journal show', {'journal': pc['journal']})
    assert 'Fictional' not in json.dumps(shown) and 'income-tax' not in json.dumps(shown)
    assert co.run('payroll show', {'draft': draft['id']})['paycheck'] == pc['id']
    # The same unit reverses newest first: the check, then the paycheck; the books return exactly.
    plan = co.run('batch reverse', {'batch': pc['batch']}, dry_run=True)
    assert [s['record_type'] for s in plan['steps']] == ['payroll_net_payment', 'payroll_paycheck']
    done = co.run('batch reverse', {'batch': pc['batch'], 'fingerprint': plan['fingerprint']}, reason='Wrong week')
    assert done['state'] == 'reversed'
    assert balances(co) == before == {}
    after = co.run('payroll paycheck', {'paycheck': pc['id']})
    assert (after['status'], after['net_status'], after['net_payment']['status']) == ('voided', 'voided', 'voided')
    assert after['year_to_date'] == {'tax_year': 2026, 'gross_minor_units': 0, 'employee_tax_minor_units': 0,
        'employee_deduction_minor_units': 0, 'employer_cost_minor_units': 0, 'net_minor_units': 0}
    with sqlite3.connect(co.path) as db:
        assert db.execute("SELECT kind, gross_minor_units, net_minor_units FROM payroll_ytd_events ORDER BY kind DESC").fetchall() == [
            ('void', -100000, -90000), ('paycheck', 100000, 90000)]
        assert db.execute('SELECT reason FROM payroll_paycheck_events ORDER BY version').fetchall()[-1] == ('Wrong week',)


def test_p6_cents_and_zero_net(books):
    co = books
    # 0.3333 h x $30 = $9.999 -> $10.00 (half up per line); 5% of $10.00 = $0.50 exactly; net $9.50.
    raw = p1(co, 'cents')
    raw['earnings'] = [{'code': 'regular', 'quantity': '0.3333', 'rate': '30'}]
    raw['items'] = [percent('income-tax', 'employee_tax', 5_000_000, 'Employee Tax Payable')]
    pc = post(co, prepared(co, raw), 'post-cents', account='Payroll Bank')
    assert legs(co, pc['journal']) == [('Employee Tax Payable', 0, 50), ('Net Pay Payable', 0, 950), ('Wages', 1000, 0)]
    assert legs(co, pc['net_payment']['check']) == [('Net Pay Payable', 950, 0), ('Payroll Bank', 0, 950)]
    # 0.001 h x $5 = $0.005 -> $0.01; no taxes: Dr Wages 0.01 / Cr Net Pay 0.01, then a one-cent check.
    raw = p1(co, 'cent', pay_date='2026-10-16')
    raw['earnings'], raw['items'] = [{'code': 'regular', 'quantity': '0.001', 'rate': '5'}], []
    pc = post(co, prepared(co, raw), 'post-cent', account='Payroll Bank')
    assert legs(co, pc['journal']) == [('Net Pay Payable', 0, 1), ('Wages', 1, 0)]
    assert pc['net_payment']['amount_minor_units'] == 1
    # A percentage rounds half up once: 1.5% of $3.33 = 4.995 cents -> 5 cents.
    raw = p1(co, 'half', pay_date='2026-10-23')
    raw['earnings'] = [{'code': 'regular', 'amount_minor_units': 333}]
    raw['items'] = [percent('income-tax', 'employee_tax', 1_500_000, 'Employee Tax Payable')]
    pc = post(co, prepared(co, raw), 'post-half')
    assert [(x['code'], x['amount_minor_units'], x['base_minor_units']) for x in pc['components']] == [
        ('regular', 333, None), ('income-tax', 5, 333), ('net', 328, None)]
    assert pc['net_status'] == 'payment_not_recorded'
    # Cash $10 less a $10 deduction: Dr Wages 10 / Cr Deduction Payable 10; net zero, no net leg, no check.
    raw = p1(co, 'zero', pay_date='2026-10-30')
    raw['earnings'] = [{'code': 'regular', 'amount_minor_units': 1000}]
    raw['items'] = [fixed('union', 'employee_deduction', 1000, 'Deduction Payable')]
    zero = prepared(co, raw)
    transactions = count(co, 'transactions')
    error = refused(co, 'payroll post', {'draft': zero['id'], 'expected_version': zero['version'],
                                         'operation_key': 'post-zero-check', 'check_account': 'Payroll Bank'}, 'E_VALIDATION')
    assert 'zero net' in error.message and count(co, 'transactions') == transactions
    pc = post(co, zero, 'post-zero')
    assert legs(co, pc['journal']) == [('Deduction Payable', 0, 1000), ('Wages', 1000, 0)]
    assert pc['net_status'] == 'no_payment_needed' and pc['net_payment'] is None
    refused(co, 'payroll pay-net', {'paycheck': pc['id'], 'expected_version': pc['version'], 'operation_key': 'pay-zero',
                                    'account': 'Payroll Bank'}, 'E_VALIDATION')
    # A $11 deduction gives net -$1: refused before anything is saved, so nothing can post.
    raw = p1(co, 'negative', pay_date='2026-10-30')
    raw['earnings'] = [{'code': 'regular', 'amount_minor_units': 1000}]
    raw['items'] = [fixed('union', 'employee_deduction', 1100, 'Deduction Payable')]
    with pytest.raises(BookflowError):
        prepared(co, raw)
    assert count(co, 'transactions') == transactions + 1
    # Year to date for 2026: 1000 + 1 + 333 + 1000 gross; 50 + 5 tax; 1000 deduction; 950 + 1 + 328 + 0 net.
    assert pc['year_to_date'] == {'tax_year': 2026, 'gross_minor_units': 2334, 'employee_tax_minor_units': 55,
        'employee_deduction_minor_units': 1000, 'employer_cost_minor_units': 0, 'net_minor_units': 1279}
    assert balances(co) == {'Wages': 2334, 'Employee Tax Payable': -55, 'Deduction Payable': -1000,
                            'Net Pay Payable': -328, 'Payroll Bank': -951}


def test_duplicate_post_retry_stale_settings_and_switch(books):
    co = books
    draft = prepared(co, p1(co))
    pc = post(co, draft, 'post-once')
    transactions = count(co, 'transactions')
    assert post(co, draft, 'post-once') == pc  # a lost-response retry replays, posting nothing
    assert count(co, 'transactions') == transactions
    refused(co, 'payroll post', {'draft': draft['id'], 'expected_version': draft['version'], 'operation_key': 'post-once',
                                 'check_account': 'Payroll Bank'}, 'E_IDEMPOTENCY_MISMATCH')
    error = refused(co, 'payroll post', {'draft': draft['id'], 'expected_version': draft['version'],
                                         'operation_key': 'post-twice'}, 'E_VALIDATION')
    assert 'already posted' in error.message
    refused(co, 'payroll prepare', {'draft': draft['id'], 'expected_version': draft['version'],
                                    'operation_key': 'recalc'}, 'E_VALIDATION')
    assert count(co, 'transactions') == transactions and count(co, 'payroll_paychecks') == 1
    # Settings changed after a calculation: post refuses until it is prepared again.
    other = prepared(co, p1(co, 'stale', pay_date='2026-10-16'))
    settings = co.run('payroll settings')
    off = co.run('payroll setup', {'enabled': False, 'expected_version': settings['version'], 'operation_key': 'off'})
    refused(co, 'payroll post', {'draft': other['id'], 'expected_version': other['version'], 'operation_key': 'off-post'},
            'E_FEATURE_DISABLED')
    # With the layer off a posted paycheck can still be read and voided.
    voided = co.run('payroll void', {'paycheck': pc['id'], 'expected_version': pc['version'], 'operation_key': 'void-off'})
    assert voided['status'] == 'voided' and balances(co) == {}
    co.run('payroll setup', {'enabled': True, 'expected_version': off['version'], 'operation_key': 'on',
        'wage_account': 'Wages', 'employer_tax_account': 'Employer Tax Expense', 'net_pay_account': 'Net Pay Payable'})
    refused(co, 'payroll post', {'draft': other['id'], 'expected_version': other['version'], 'operation_key': 'stale-post'},
            'E_PREVIEW_STALE')


def test_closed_date_refuses_post_void_payment_and_reversal(books):
    co = books
    pc = post(co, prepared(co, p1(co)), 'post-open')
    late = prepared(co, p1(co, 'late', pay_date='2026-10-20'))
    co.run('company update', {'closing_date': '2026-10-31'}, reason='October is closed')
    transactions = count(co, 'transactions')
    refused(co, 'payroll post', {'draft': late['id'], 'expected_version': late['version'], 'operation_key': 'post-closed'},
            'E_PERIOD_CLOSED')
    refused(co, 'payroll void', {'paycheck': pc['id'], 'expected_version': pc['version'], 'operation_key': 'void-closed'},
            'E_PERIOD_CLOSED')
    refused(co, 'payroll pay-net', {'paycheck': pc['id'], 'expected_version': pc['version'], 'operation_key': 'pay-closed',
                                    'account': 'Payroll Bank', 'date': '2026-10-15'}, 'E_PERIOD_CLOSED')
    blocked = refused(co, 'batch reverse', {'batch': pc['batch']}, 'E_BATCH_BLOCKED', dry_run=True)
    assert [b['code'] for b in blocked.details['blockers']] == ['closed_period']
    assert count(co, 'transactions') == transactions and count(co, 'payroll_paychecks') == 1
    # Net pay may be settled after the close, on an open date.
    paid = co.run('payroll pay-net', {'paycheck': pc['id'], 'expected_version': pc['version'], 'operation_key': 'pay-open',
                                      'account': 'Payroll Bank', 'date': '2026-11-02', 'number': '501'})
    assert paid['net_payment']['date'] == '2026-11-02' and balances(co)['Payroll Bank'] == -90000


def test_print_reconciliation_and_later_paycheck_blockers(books):
    co = books
    first = post(co, prepared(co, p1(co)), 'post-print', account='Payroll Bank', to_be_printed=True)
    assert first['net_payment']['check_number'] is None
    run = co.run('check print', {'account': 'Payroll Bank', 'first_number': '2001', 'style': 'voucher'}, reason='Print')
    assert [i['transaction_id'] for i in run['items']] == [first['net_payment']['check']]
    body = {'paycheck': first['id'], 'expected_version': first['version']}
    assert 'print run' in refused(co, 'payroll void-payment', dict(body, operation_key='vp-1'), 'E_VALIDATION').message
    blocked = refused(co, 'batch reverse', {'batch': first['batch']}, 'E_BATCH_BLOCKED', dry_run=True)
    assert [b['code'] for b in blocked.details['blockers']] == ['in_print_run']
    co.run('check print-confirm', {'run': run['id']}, reason='Printed')
    blocked = refused(co, 'batch reverse', {'batch': first['batch']}, 'E_BATCH_BLOCKED', dry_run=True)
    assert [b['code'] for b in blocked.details['blockers']] == ['printed']
    refused(co, 'payroll void', dict(body, operation_key='v-1'), 'E_VALIDATION')  # its net payment comes first
    # A printed check is voided by void-payment, which keeps the paper record; the paycheck stays posted.
    unpaid = co.run('payroll void-payment', dict(body, operation_key='vp-2'))
    assert (unpaid['status'], unpaid['net_status'], unpaid['version']) == ('posted', 'payment_not_recorded', 3)
    assert balances(co)['Net Pay Payable'] == -90000 and 'Payroll Bank' not in balances(co)
    # A later paycheck for the same employee and year depends on this one's year to date.
    second = post(co, prepared(co, p1(co, 'second', pay_date='2026-10-16')), 'post-second', account='Payroll Bank', number='3001')
    error = refused(co, 'payroll void', {'paycheck': first['id'], 'expected_version': 3, 'operation_key': 'v-2'}, 'E_VALIDATION')
    assert error.details['blockers'] == ['later_paycheck']
    backdated = prepared(co, p1(co, 'backdated', pay_date='2026-10-12'))
    refused(co, 'payroll post', {'draft': backdated['id'], 'expected_version': backdated['version'],
                                 'operation_key': 'post-back'}, 'E_VALIDATION')
    # A cleared check blocks both its void and the reversal.
    co.run('reconcile opening start', dict(operation_key=new_id(), account='Payroll Bank', opening_date='2026-09-30',
        entered_balance='0.00', evidence=dict(format=1, statement_reference=None, entered_text='Opening'), references=[]))
    draft = co.run('reconcile start', dict(operation_key=new_id(), account='Payroll Bank', statement_date='2026-10-31',
                                           ending_balance='-900.00'))['draft']
    rows = co.run('reconcile candidates', dict(draft=draft['id'], limit=200, filters=dict(hide_after_date=False)))['items']
    mine = [r for r in rows if r['movement']['transaction_id'] == second['net_payment']['check']]
    draft = co.run('reconcile mark', dict(draft=draft['id'], operation_key=new_id(), expected_version=draft['version'],
        entries=[dict(movement=r['movement'], group_fingerprint=r['group_fingerprint'], action='mark') for r in mine]))['draft']
    guards = co.run('reconcile preview', dict(draft=draft['id'], expected_version=draft['version']))
    co.run('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
        expected_facts_fingerprint=guards['expected_facts_fingerprint'], dependency_guard=guards['dependency_guard']))
    refused(co, 'payroll void-payment', {'paycheck': second['id'], 'expected_version': second['version'],
                                         'operation_key': 'vp-3'}, 'E_RECONCILIATION_DEPENDENCY')
    blocked = refused(co, 'batch reverse', {'batch': second['batch']}, 'E_BATCH_BLOCKED', dry_run=True)
    assert [b['code'] for b in blocked.details['blockers']] == ['reconciled']


def test_paycheck_and_its_check_land_together_or_not_at_all(books, monkeypatch):
    from bookflow.company import payroll_paychecks
    co = books
    draft = prepared(co, p1(co))
    tables = ('transactions', 'posting_lines', 'payroll_paychecks', 'payroll_net_payments', 'payroll_ytd_events',
              'payroll_ledger_links', 'batches', 'audit_events')
    before = {t: count(co, t) for t in tables}
    real = payroll_paychecks._record_payment

    def recorded_then_lost(*args, **kwargs):
        real(*args, **kwargs)  # the check is written inside the same transaction...
        raise RuntimeError('power lost after the check was written')  # ...and the transaction never commits
    monkeypatch.setattr(payroll_paychecks, '_record_payment', recorded_then_lost)
    with pytest.raises(Exception):
        post(co, draft, 'post-lost', account='Payroll Bank', number='1001')
    assert {t: count(co, t) for t in tables} == before and balances(co) == {}
    assert co.run('payroll show', {'draft': draft['id']})['paycheck'] is None
    monkeypatch.setattr(payroll_paychecks, '_record_payment', real)
    pc = post(co, draft, 'post-again', account='Payroll Bank', number='1001')
    assert pc['net_payment']['check_number'] == '1001'  # the lost attempt held no number
    assert count(co, 'payroll_paychecks') == 1 and count(co, 'payroll_net_payments') == 1


def test_direct_journal_and_every_other_bypass_route_refuse(books):
    co = books
    pc = post(co, prepared(co, p1(co)), 'post-p1', account='Payroll Bank', number='1001')
    before = balances(co)
    for account in ('Wages', 'Net Pay Payable', 'Employer Tax Expense'):
        error = refused(co, 'journal post', {'date': '2026-10-20', 'lines': [
            {'account': account, 'side': 'debit', 'amount': '1.00'},
            {'account': 'Payroll Bank', 'side': 'credit', 'amount': '1.00'}]}, 'E_SYSTEM_RECORD')
        assert f'"{account}" is a payroll account' in error.message and '`payroll post`' in error.message
    journal = co.run('journal show', {'journal': pc['journal']})
    error = refused(co, 'journal void', {'journal': pc['journal'], 'expected_version': journal['version']}, 'E_SYSTEM_RECORD')
    assert 'owned by payroll' in error.message and '`payroll void`' in error.message
    error = refused(co, 'check void', {'check': pc['net_payment']['check'], 'expected_version': 1}, 'E_SYSTEM_RECORD')
    assert '`payroll void-payment`' in error.message
    refused(co, 'check update', {'check': pc['net_payment']['check'], 'memo': 'changed'}, 'E_SYSTEM_RECORD')
    # Import: the row is refused by name in the preview, so the run never starts.
    csv = 'Num,Date,Account,Debit,Credit\nJ-9,2026-10-20,Wages,5.00,\nJ-9,2026-10-20,Payroll Bank,,5.00\n'
    preview = co.run('import preview', {'kind': 'journal', 'content': csv})
    assert preview['counts'] == {'refused': 1}
    assert preview['refused'][0]['code'] == 'E_SYSTEM_RECORD' and 'payroll account' in preview['refused'][0]['message']
    # Memorized entries: the template saves, but entering it is refused and nothing posts.
    memorized = co.run('memorized create', {'name': 'Wages by hand', 'command': 'journal post', 'payload': {'lines': [
        {'account': 'Wages', 'side': 'debit', 'amount': '1.00'}, {'account': 'Payroll Bank', 'side': 'credit', 'amount': '1.00'}]}})
    entered = co.run('memorized enter', {'memorized': memorized['id'], 'date': '2026-10-20'})
    assert entered['entered'] == [] and len(entered['blocked']) == 1
    assert 'payroll account' in json.dumps(entered['blocked'])
    # Writers without the journal line check (a bill) meet the posting-line backstop, still refused by name.
    co.run('vendor create', {'name': 'Fictional vendor'})
    error = refused(co, 'bill post', {'vendor': 'Fictional vendor', 'date': '2026-10-20',
                                      'expenses': [{'account': 'Wages', 'amount': '3.00'}]}, 'E_SYSTEM_RECORD')
    assert 'payroll account' in error.message
    refused(co, 'check post', {'account': 'Payroll Bank', 'pay_to': {'name_type': 'vendor', 'name_id': 'Fictional vendor'},
        'date': '2026-10-20', 'amount': '2.00', 'expenses': [{'account': 'Net Pay Payable', 'amount': '2.00'}]}, 'E_SYSTEM_RECORD')
    assert balances(co) == before


def test_readers_without_payroll_read_see_no_paycheck_details(books):
    from tests.conftest import as_user, make_actor
    co = books
    raw = p1(co)
    raw['items'][0]['source_note'] = 'PRIVATE-NOTE-48213'
    draft = prepared(co, raw)
    pc = post(co, draft, 'post-private', account='Payroll Bank', number='1001')
    uid = make_actor(co.root, 'ledger-only')
    co.client.run('membership grant', {'user': uid, 'company': co.company, 'role': 'standard', 'expected_version': 0})
    ledger = as_user(co.root, 'ledger-only')
    for command, body in [('payroll paycheck', {'paycheck': pc['id']}), ('payroll paychecks', {}),
                          ('batch show', {'batch': pc['batch']})]:
        with pytest.raises(BookflowError) as caught:
            ledger.run(command, body, company=co.company)
        assert caught.value.code == 'E_PERMISSION', command
        assert '48213' not in json.dumps(caught.value.to_dict())
    # The ledger shows legitimate totals only: neutral memo and descriptions, no codes, notes or reasons.
    journal = json.dumps(ledger.run('journal show', {'journal': pc['journal']}, company=co.company))
    assert 'Payroll paycheck' in journal and '48213' not in journal and 'income-tax' not in journal
    assert 'Fictional payroll posting fixture' not in journal
    # Audit events that post or void a paycheck carry a payroll link record, so they need payroll.read.
    seen = {e['id'] for e in ledger.run('audit tail', {'after': 0, 'limit': 1000}, company=co.company)['items']}
    with sqlite3.connect(co.path) as db:
        payroll_events = {r[0] for r in db.execute("SELECT DISTINCT event_id FROM audit_entries WHERE record_type LIKE 'payroll_%'")}
        general = db.execute("SELECT DISTINCT reason FROM audit_events WHERE command LIKE 'payroll %'").fetchall()
        private = db.execute('SELECT reason FROM payroll_paycheck_events').fetchall()
    assert payroll_events and not payroll_events & seen
    assert general == [('Private payroll change; reason retained in payroll history.',),
                       ('Private payroll write; reason retained in payroll history.',)] or all('reason retained' in g[0] for g in general)
    assert private == [('Fictional payroll posting fixture',), ('Fictional payroll posting fixture',)]
    reader = make_actor(co.root, 'payroll-reader')
    co.client.run('membership grant', {'user': reader, 'company': co.company, 'role': 'readonly', 'expected_version': 0,
                                       'grants': ['payroll.read']})
    assert as_user(co.root, 'payroll-reader').run('payroll paycheck', {'paycheck': pc['id']}, company=co.company) == \
        co.run('payroll paycheck', {'paycheck': pc['id']})


def test_agent_rights_are_its_grants_intersected_with_its_principals_and_the_directive_link(hosted):
    from tests.conftest import hosted_call, make_agent
    from tests.test_people_only import REASON, _bearer, _grant
    cid = hosted.company_id
    settings = hosted.ok('payroll.settings', {}, company=cid)
    hosted.ok('payroll.setup', {'enabled': True, 'expected_version': settings['version'], 'operation_key': 'agent-on',
        'wage_account': 'Demo Payroll Wages', 'employer_tax_account': 'Demo Payroll Employer Expense',
        'net_pay_account': 'Demo Payroll Net Payable'}, company=cid, headers=REASON)
    demo = hosted.ok('payroll.list', {}, company=cid)['rows'][0]
    draft = hosted.ok('payroll.prepare', {'draft': demo['id'], 'expected_version': demo['version'],
                                          'operation_key': 'agent-recalc'}, company=cid, headers=REASON)
    hosted.ok('user.add', {'username': 'payroll-p', 'company': cid, 'role': 'owner', 'password': 'pw-payroll-p-12345'},
              headers=REASON)
    q = hosted.ok('user.add', {'username': 'payroll-q', 'company': cid, 'role': 'admin', 'password': 'pw-payroll-q-12345'},
                  headers=REASON)['user_id']
    _grant(hosted, q, 'admin', denies=['payroll.post'])
    agent = make_agent(hosted_call(hosted), 'payroll-agent', principals='payroll-p', company=cid, role='admin')
    other = make_agent(hosted_call(hosted), 'payroll-agent-q', principals='payroll-q', company=cid, role='admin')
    as_agent, as_other = _bearer(hosted, agent, 'payroll-p'), _bearer(hosted, other, 'payroll-q')
    body = {'draft': draft['id'], 'expected_version': draft['version'], 'operation_key': 'agent-post'}

    def call(headers, extra=None):
        return hosted.api.post(f'/companies/{cid}/commands/payroll.post?dry_run=true', json={**body, **(extra or {})},
                               headers={**REASON, **headers})
    r = call(as_agent)
    assert r.status_code == 403 and r.json()['details']['reason'] == 'capability_not_granted', r.text
    _grant(hosted, agent, 'admin', grants=['payroll.read', 'payroll.post'])
    _grant(hosted, other, 'admin', grants=['payroll.read', 'payroll.post'])
    r = call(as_other)
    # The catalog's actor-and-principal intersection refuses here, before the agent gate, naming the capability.
    assert r.status_code == 403 and r.json()['details'] == {'capability': 'payroll.post', 'required_role': 'standard',
        'reason': 'unavailable_target', 'field': 'scope'}, r.text
    r = call(as_agent)
    assert r.status_code == 200 and r.json()['net_minor_units'] == 90000, r.text
    # Recording the check in the same post needs payroll.pay as well, by the agent's own grant.
    r = call(as_agent, {'check_account': 'Checking'})
    assert r.status_code == 403 and r.json()['details']['capability'] == 'payroll.pay', r.text
    assert hosted.ok('payroll.paychecks', {}, company=cid)['rows'] == []  # dry runs only
    # A paycheck written under a directive links to it only for a reader who may open the directive.
    directive = hosted.ok('directive.add', {'text': 'Pay the fictional demo employee on payday'}, company=cid,
                          headers=REASON)['directive']['id']
    pc = hosted.ok('payroll.post', body, company=cid, headers={**REASON, 'X-Bookflow-Directive': directive})
    assert pc['directive'] == directive
    with sqlite3.connect(Path(hosted.ok('company.show', {}, company=cid)['path']) / 'company.db') as db:
        assert not db.execute('SELECT 1 FROM audit_events WHERE directive_id = ?', (directive,)).fetchall()
    owner = hosted.api.get(f'/c/{cid}/payroll?paycheck={pc["id"]}', headers=_bearer(hosted, 'payroll-p'))
    assert owner.status_code == 200 and f'href="/c/{cid}/directive/{directive}"' in owner.text
    reader = hosted.ok('user.add', {'username': 'payroll-r', 'company': cid, 'role': 'readonly',
                                    'password': 'pw-payroll-r-12345'}, headers=REASON)['user_id']
    _grant(hosted, reader, 'readonly', grants=['payroll.read'], denies=['directive'])
    page = hosted.api.get(f'/c/{cid}/payroll?paycheck={pc["id"]}', headers=_bearer(hosted, reader))
    assert page.status_code == 200 and 'data-payroll-paycheck' in page.text
    assert f'/directive/{directive}' not in page.text and '(you cannot open it)' in page.text


def test_cli_and_mcp(books, tmp_path):
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from tests import provenance
    from tests.test_row3_host import Hosted, live
    co = books
    draft = prepared(co, p1(co))
    args = ['--company', co.company, '--reason', 'Fictional payroll posting fixture', 'payroll', 'post',
            '--draft', draft['id'], '--expected-version', str(draft['version']), '--operation-key', 'cli-post', '--json']
    result = subprocess.run(['/home/k/Projects/Bookflow/.venv/bin/python', '-c',
        'from bookflow.bootstrap import main; main()', *args], capture_output=True, text=True, timeout=60,
        env=provenance.child_env(BOOKFLOW_DATA_ROOT=str(co.root)))
    assert result.returncode == 0, result.stderr
    pc = json.loads(result.stdout)
    assert pc['net_status'] == 'payment_not_recorded' and pc['net_minor_units'] == 90000
    token = co.client.token.issue(label='Payroll posting continuation')
    handle = start_serving(co.root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    hosted = Hosted(handle, co.root, '', co.company, token, '', {})
    server = live.__wrapped__(hosted)
    url = next(server)

    async def witness():
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        params = StdioServerParameters(command=provenance.launcher(), args=['mcp', '--url', url],
            env=provenance.child_env(BOOKFLOW_TOKEN=token['secret'], BOOKFLOW_COMPANY=co.company,
                                     BOOKFLOW_DATA_ROOT=str(tmp_path / 'absent')))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                reply = await session.call_tool('bookflow_run', {'command': 'payroll pay-net', 'company': co.company,
                    'reason': 'Record the net-pay check', 'input': {'paycheck': pc['id'], 'expected_version': pc['version'],
                    'operation_key': 'mcp-pay', 'account': 'Payroll Bank', 'number': '1001'}})
                assert not reply.is_error, reply.structured_content
                assert reply.structured_content['net_payment']['check_number'] == '1001'
    try:
        anyio.run(witness)
    finally:
        server.close()
        handle.stop()
    shown = co.run('payroll paycheck', {'paycheck': pc['id']})
    assert shown['net_status'] == 'payment_recorded' and co.run('payroll paychecks')['rows'] == [shown]
    assert balances(co)['Payroll Bank'] == -90000
