"""R135: a credit card credit, worked by hand, and the plain "Credit Card" payment method.

The books: one company card, one expense account (Parts), one vendor.

    2026-06-02  card charge, parts bought        120.00   card owes 120.00, Parts 120.00
    2026-06-05  card credit, a valve returned     45.50   card owes  74.50, Parts  74.50
    correction  the credit was really 50.00               card owes  70.00, Parts  70.00
    void        the credit was on another card            card owes 120.00, Parts 120.00

A credit card credit is a card charge in the other direction: the card (credit-normal) is
debited, what is owed on it goes down, and the expense line is credited.
"""
import importlib
import sqlite3

import pytest

import bookflow
from tests.test_row3_host import hosted  # noqa: F401
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'cards'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Card organization')
    company = client.company.new(legal_name='Cards', home_currency='USD', timezone='UTC',
                                 organization='Card organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    card = run('account create', dict(name='Company Visa', type='credit_card'))['id']
    parts = run('account create', dict(name='Parts', type='expense'))['id']
    bank = next(row['id'] for row in run('account query', dict(limit=200))['items'] if row['type'] == 'bank')
    vendor = run('vendor create', dict(name='Northside Supply'))['id']
    return dict(client=client, company=company, run=run, card=card, parts=parts, bank=bank, vendor=vendor)


def balances(books):
    """Signed minor units by account id from the trial balance, debits positive."""
    report = books['run']('report trial-balance', dict(date_to='2026-12-31', limit=200))
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units'] for row in report['rows']}


def charge(books, amount='120.00', date='2026-06-02'):
    return books['run']('card-charge post', dict(account=books['card'], date=date, amount=amount,
                        pay_to=dict(name_id=books['vendor']), expenses=[dict(account=books['parts'], amount=amount)]))


def credit(books, amount='45.50', date='2026-06-05', **extra):
    return books['run']('card-credit post', dict(account=books['card'], date=date, amount=amount,
                        pay_to=dict(name_id=books['vendor']), memo='Returned the wrong valve',
                        expenses=[dict(account=books['parts'], amount=amount, memo='Valve')], **extra))


def test_a_card_credit_lowers_what_is_owed_and_the_expense_and_is_corrected_and_voided(books):
    run = books['run']
    charged = charge(books)
    posted = credit(books)
    assert posted['document']['kind'] == 'card_credit' and posted['document']['funding'] == 'credit_card'
    assert posted['document']['amount']['amount'] == '45.50' and posted['document']['items'] == []
    # The card line is the debit, the expense line the credit, each 45.50.
    assert sorted((line['account_id'], line['side'], line['amount_minor_units']) for line in posted['revision']['lines']) \
        == sorted([(books['card'], 'debit', 4550), (books['parts'], 'credit', 4550)])
    # 120.00 charged less 45.50 credited: the card owes 74.50 and Parts carries 74.50.
    assert balances(books)[books['card']] == -7450 and balances(books)[books['parts']] == 7450

    # Each noun lists and opens only its own documents.
    assert [row['id'] for row in run('card-credit query')['items']] == [posted['id']]
    assert [row['id'] for row in run('card-charge query')['items']] == [charged['id']]
    for noun, selector, other in (('card-charge', 'card_charge', posted['id']), ('card-credit', 'card_credit', charged['id'])):
        with pytest.raises(BookflowError) as missing:
            run(noun + ' show', {selector: other})
        assert missing.value.code == 'E_RECORD_NOT_FOUND'

    # Corrected to 50.00: the card owes 120.00 - 50.00 = 70.00, Parts 70.00.
    shown = run('card-credit show', dict(card_credit=posted['id']))
    line = next(row for row in shown['revision']['lines'] if row['account_id'] == books['parts'])
    corrected = run('card-credit update', dict(card_credit=posted['id'], expected_version=shown['version'], amount='50.00',
                                               expenses=[dict(line_id=line['line_id'], account=books['parts'], amount='50.00')]),
                    reason='The refund was 50.00')
    assert corrected['document']['amount']['amount'] == '50.00'
    assert balances(books)[books['card']] == -7000 and balances(books)[books['parts']] == 7000

    # Voided: only the 120.00 charge is left.
    voided = run('card-credit void', dict(card_credit=posted['id'], expected_version=corrected['version']),
                 reason='It went on the other card')
    assert voided['status'] == 'voided'
    assert balances(books)[books['card']] == -12000 and balances(books)[books['parts']] == 12000
    history = run('card-credit history', dict(card_credit=posted['id']))
    assert [row['revision_number'] for row in history['items']] == [1, 2]
    assert [row['document']['amount']['amount'] for row in history['items']] == ['45.50', '50.00']

    # A report row opens it as the card credit it was entered as.
    rows = run('report transaction-list-by-date', dict(date_from='2026-06-01', date_to='2026-06-30', limit=200))['rows']
    assert {row['money_out_kind'] for row in rows if row['transaction_id'] == posted['id']} == {'card_credit'}


def test_a_card_credit_is_refused_in_plain_words(books):
    run = books['run']
    with pytest.raises(BookflowError) as wrong:
        run('card-credit post', dict(
            account=books['bank'], date='2026-06-05', amount='10.00', expenses=[dict(account=books['parts'], amount='10.00')]))
    assert wrong.value.code == 'E_VALIDATION' and 'credit card credit is drawn on a credit card account' in str(wrong.value.details)
    with pytest.raises(BookflowError) as short:
        run('card-credit post', dict(account=books['card'], date='2026-06-05', amount='10.00',
                                     expenses=[dict(account=books['parts'], amount='9.00')]))
    assert short.value.code == 'E_UNBALANCED_ENTRY'
    assert short.value.details['difference_minor_units'] == -100
    with pytest.raises(BookflowError) as items:
        run('card-credit post', dict(account=books['card'], date='2026-06-05', amount='10.00',
                                     expenses=[dict(account=books['parts'], amount='10.00')], items=[]))
    assert items.value.code == 'E_VALIDATION'
    # The journal editor names the document and the command that owns it.
    posted = credit(books)
    with pytest.raises(BookflowError) as journal:
        run('journal void', dict(journal=posted['id'], expected_version=1), reason='Try the journal editor')
    assert journal.value.code == 'E_VALIDATION' and 'credit card credit' in journal.value.message
    assert journal.value.details['next'] == 'card-credit void'


def test_new_companies_and_the_demo_have_a_plain_credit_card_payment_method(books, client):
    methods = books['run']('payment-method query', dict(limit=50))['items']
    assert [(row['name'], row['kind']) for row in methods if row['name'] == 'Credit Card'] == [('Credit Card', 'credit_card')]
    demo = client.run('payment-method query', dict(limit=50), company='Demo Plumbing Co')['items']
    assert [row['kind'] for row in demo if row['name'] == 'Credit Card'] == ['credit_card']


M = importlib.import_module('bookflow.storage.company_migrations.versions.0063_card_credits')


def test_co0063_widens_the_money_out_kind_and_changes_nothing_else(tmp_path):
    from tests.test_early_discount_migration import _at, _objects
    assert M.down_revision == 'co0062'
    path = tmp_path / 'kinds.db'
    _at(path, M.down_revision)
    marker = "INSERT INTO money_out_documents VALUES ('{}','journal_entry','{}','t','u','cli','E')"
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(marker.format('T1', 'card_credit'))
        before = _objects(conn)
    _at(path, M.revision)
    with sqlite3.connect(path) as conn:
        after = _objects(conn)
        assert set(after) == set(before)
        assert {k: v for k, v in after.items() if k != 'money_out_documents'} == \
            {k: v for k, v in before.items() if k != 'money_out_documents'}
        # The rename SQLite performs quotes the table's own name; nothing else differs.
        assert after['money_out_documents'].replace('"money_out_documents"', 'money_out_documents') == \
            before['money_out_documents'].replace(M.OLD, M.NEW)
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute(marker.format('T2', 'card_credit'))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(marker.format('T3', 'cheque'))
        conn.rollback()


def test_the_browser_enters_a_card_credit_in_the_card_charge_window(hosted):
    from tests.test_row5_workbench_forms import _browser
    browser = _browser(hosted)
    company = hosted.company_id
    page = browser.get(f'/c/{company}/card-credit/post')
    assert page.status_code == 200, page.text[:400]
    assert '<h1' in page.text and 'Credit card credit' in page.text
    assert 'Credit From' in page.text and 'Amount of this credit' in page.text
    # Expenses only: a card credit returns money, not stock.
    assert 'c:expenses:' in page.text and 'c:items:' not in page.text
    card = hosted.ok('account.create', dict(name='Browser card', type='credit_card'), company=company)['id']
    expense = hosted.ok('account.create', dict(name='Browser parts', type='expense'), company=company)['id']
    posted = hosted.ok('card-credit.post', dict(account=card, date='2026-06-05', amount='12.00',
                                                expenses=[dict(account=expense, amount='12.00')]), company=company)
    shown = browser.get(f'/c/{company}/card-credit/{posted["id"]}')
    assert shown.status_code == 200, shown.text[:400]
    listed = browser.get(f'/c/{company}/card-credit')
    assert listed.status_code == 200 and posted['id'] in listed.text
