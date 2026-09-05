"""Customer balance witnesses use existing public journals, not sale totals."""
from pathlib import Path
import shutil

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import customer_balances as balances, schema
from bookflow.core.exact import INT64_MAX
from bookflow.storage.engine import open_database

COMPANY = 'Demo Plumbing Co'


@pytest.fixture
def ar(client):
    parent = client.customer.create(name='Balance Parent', company=COMPANY)['id']
    job = client.customer.create(name='Job', parent_id=parent, company=COMPANY)['id']
    leaf = client.customer.create(name='Leaf', parent_id=job, company=COMPANY)['id']
    other = client.customer.create(name='Balance Other', company=COMPANY)['id']
    account = client.account.create(name='Balance AR', type='accounts_receivable', company=COMPANY)['id']
    equity = client.account.create(name='Balance Equity', type='equity', company=COMPANY)['id']
    return parent, job, leaf, other, account, equity


def legs(ar, customer, amount, credit=False):
    return [dict(account=ar[4], side='credit' if credit else 'debit', amount=amount,
                 name_type='customer', name_id=customer),
            dict(account=ar[5], side='debit' if credit else 'credit', amount=amount)]


def post(client, ar, customer, amount, credit=False):
    return client.journal.post(date='2026-01-12', lines=legs(ar, customer, amount, credit), company=COMPANY)


def path(client):
    return Path(client.company.show(company=COMPANY)['path']) / 'company.db'


def check(client, customer, own, family):
    shown = client.customer.show(customer=customer, company=COMPANY)
    assert shown['balances_available'] is True
    assert shown['current_balance'] == shown['open_balance']
    assert shown['current_balance']['minor_units'] == own
    assert shown['family_balance']['minor_units'] == family
    with open_database(path(client), writable=False) as db:
        assert balances.own_balance(db, customer) == own
        assert balances.family_balance(db, customer) == family


def test_exact_tree_correction_void_and_copy(client, ar, tmp_path):
    parent, job, leaf, other, *_ = ar
    original = post(client, ar, parent, '10.00')
    credit = post(client, ar, job, '3.00', True)
    post(client, ar, leaf, '2.00')
    post(client, ar, other, '80.00')
    # A customer-tagged non-AR leg must not contribute.
    client.journal.post(date='2026-01-12', company=COMPANY, lines=[
        dict(account=ar[5], side='debit', amount='7.00', name_type='customer', name_id=parent),
        dict(account=ar[5], side='credit', amount='7.00')])
    check(client, parent, 1000, 900)
    check(client, job, -300, -100)
    check(client, leaf, 200, 200)
    client.journal.update(journal=original['id'], expected_version=1,
                          lines=legs(ar, other, '12.00'), company=COMPANY)
    check(client, parent, 0, -100)
    check(client, other, 9200, 9200)
    client.journal.void(journal=credit['id'], expected_version=1, reason='Mistake', company=COMPANY)
    check(client, job, 0, 200)
    client.customer.deactivate(customer=leaf, expected_version=1, company=COMPANY)
    check(client, parent, 0, 200)
    copied = tmp_path / 'copy.db'
    shutil.copy2(path(client), copied)
    with open_database(copied, writable=False) as db:
        assert balances.family_balance(db, parent) == 200
        assert balances.own_balance(db, other) == 9200


def test_public_query_and_list_numeric_sort_without_projection_loop(client, ar, monkeypatch):
    parent, job, leaf, other, *_ = ar
    for customer, amount, credit in [(parent, '10.00', False), (job, '2.00', False),
                                     (leaf, '0.09', False), (other, '1.00', True)]:
        post(client, ar, customer, amount, credit)
    listed = client.customer.list(query='Balance', sort='current_balance', company=COMPANY)
    assert [r['id'] for r in listed['items']] == [other, leaf, job, parent]
    assert [r['current_balance']['minor_units'] for r in listed['items']] == [-100, 9, 200, 1000]
    def forbidden(*args, **kw):
        raise AssertionError('query used a per-record balance projection')
    monkeypatch.setattr(balances, 'own_balance', forbidden)
    monkeypatch.setattr(balances, 'family_balance', forbidden)
    result = client.run('customer query', {'query': 'Balance', 'sort': 'current_balance'}, company=COMPANY)
    assert [r['id'] for r in result['items']] == [other, leaf, job, parent]
    assert [r['current_balance']['minor_units'] for r in result['items']] == [-100, 9, 200, 1000]
    filtered = client.run('customer query', {'filter': ['open_balance=200']}, company=COMPANY)
    assert [row['id'] for row in filtered['items']] == [job]


def test_overflow_is_named_and_cancellation_is_exact(client, ar):
    parent, job, *_ = ar
    maximum = '92233720368547758.07'
    first = post(client, ar, parent, maximum)
    second = post(client, ar, parent, maximum)
    for read in [lambda: client.customer.show(customer=parent, company=COMPANY),
                 lambda: client.run('customer query', {'query': 'Balance Parent'}, company=COMPANY),
                 lambda: client.customer.list(query='Balance Parent', company=COMPANY)]:
        with pytest.raises(BookflowError) as err:
            read()
        assert err.value.code == 'E_VALUE_RANGE'
    # Intermediate sum exceeds i64, final net is representable.
    post(client, ar, parent, maximum, True)
    check(client, parent, INT64_MAX, INT64_MAX)
    post(client, ar, job, '0.01')
    with open_database(path(client), writable=False) as db:
        assert balances.own_balance(db, parent) == INT64_MAX
        with pytest.raises(BookflowError) as err:
            balances.family_balance(db, parent)
        assert err.value.code == 'E_VALUE_RANGE'
    client.journal.void(journal=first['id'], expected_version=1, reason='Mistake', company=COMPANY)
    client.journal.void(journal=second['id'], expected_version=1, reason='Mistake', company=COMPANY)
    check(client, parent, -INT64_MAX, -INT64_MAX + 1)


def test_projection_precision_and_exact_party_type_without_fixture():
    """Exercise SQL independently of concurrent sales migration/seed work."""
    from types import SimpleNamespace
    engine = sa.create_engine('sqlite://')
    with engine.connect() as conn:
        db = SimpleNamespace(conn=conn, raw=conn.connection.driver_connection)
        db.raw.executescript('''
            CREATE TABLE customers (id TEXT PRIMARY KEY, parent_id TEXT);
            CREATE TABLE accounts (id TEXT PRIMARY KEY, type TEXT);
            CREATE TABLE posting_lines (account_id TEXT, name_type TEXT, name_id TEXT,
                                        debit_minor_units INTEGER, credit_minor_units INTEGER);
            INSERT INTO customers VALUES ('parent', NULL), ('job', 'parent'), ('leaf', 'job'), ('other', NULL);
            INSERT INTO accounts VALUES ('ar', 'accounts_receivable'), ('bank', 'bank');
        ''')
        effects = [('ar', 'customer', 'parent', INT64_MAX, 0),
                   ('ar', 'customer', 'parent', INT64_MAX, 0),
                   ('ar', 'customer', 'parent', 0, INT64_MAX),
                   ('ar', 'customer', 'job', 0, 20),
                   ('ar', 'customer', 'leaf', 10, 0),
                   ('ar', 'customer', 'other', 700, 0),
                   ('ar', 'vendor', 'parent', 300, 0),
                   ('bank', 'customer', 'parent', 500, 0)]
        db.raw.executemany('INSERT INTO posting_lines VALUES (?,?,?,?,?)', effects)
        assert balances.own_balance(db, 'parent') == INT64_MAX
        assert balances.family_balance(db, 'parent') == INT64_MAX - 10
        assert balances.own_balance(db, 'job') == -20
        assert balances.family_balance(db, 'job') == -10
        expr = balances.balance_expression(db)
        rows = conn.execute(sa.select(schema.customers.c.id, expr).order_by(expr)).all()
        assert rows == [('job', '-20'), ('leaf', '10'), ('other', '700'), ('parent', str(INT64_MAX))]
        assert conn.execute(sa.select(schema.customers.c.id).where(expr == -20)).scalars().all() == ['job']
        assert conn.execute(sa.select(schema.customers.c.id).where(expr == INT64_MAX)).scalars().all() == ['parent']
        db.raw.execute("INSERT INTO posting_lines VALUES ('ar','customer','parent',1,0)")
        with pytest.raises(BookflowError) as err:
            balances.own_balance(db, 'parent')
        assert err.value.code == 'E_VALUE_RANGE'
        # A family net can fit even if one own balance does not; sum before range checking.
        assert balances.family_balance(db, 'parent') == INT64_MAX - 9
        db.raw.execute("INSERT INTO posting_lines VALUES ('ar','customer','job',20,0)")
        with pytest.raises(BookflowError) as err:
            balances.family_balance(db, 'parent')
        assert err.value.code == 'E_VALUE_RANGE'
    engine.dispose()


def test_readonly_customer_balance_and_company_isolation(client, root, ar):
    from tests.conftest import make_actor, as_user
    parent = ar[0]
    post(client, ar, parent, '12.34')
    company = client.company.show(company=COMPANY)
    make_actor(root, 'balance-reader', company_role=(company['id'], 'readonly'))
    reader = as_user(root, 'balance-reader')
    assert reader.customer.show(customer=parent, company=COMPANY)['current_balance']['minor_units'] == 1234
    rows = reader.run('customer query', {'query': 'Balance Parent'}, company=COMPANY)['items']
    assert rows[0]['current_balance']['minor_units'] == 1234
    with pytest.raises(BookflowError):
        reader.customer.show(customer=parent, company='Reference Plumbing Co')
    with pytest.raises(BookflowError):
        reader.run('customer query', {}, company='Reference Plumbing Co')
    with pytest.raises(BookflowError):
        post(reader, ar, parent, '1.00')


def test_public_balance_filters_and_open_sort(client, ar):
    parent, job, leaf, other, *_ = ar
    post(client, ar, parent, '10.00')
    post(client, ar, job, '2.00')
    post(client, ar, other, '1.00', True)
    for command in ('customer list', 'customer query'):
        for field in ('current_balance', 'open_balance'):
            for units, wanted in [(-100, other), (0, leaf), (200, job), (1000, parent)]:
                result = client.run(command, {'query': 'Balance', 'filter': [f'{field}={units}']}, company=COMPANY)
                assert [row['id'] for row in result['items']] == [wanted]
                assert result['items'][0]['current_balance']['minor_units'] == units
                assert result['items'][0]['open_balance'] == result['items'][0]['current_balance']
            with pytest.raises(BookflowError) as err:
                client.run(command, {'filter': [f'{field}=2.00']}, company=COMPANY)
            assert err.value.code == 'E_LIST_FILTER'
        result = client.run(command, {'query': 'Balance', 'sort': 'open_balance', 'direction': 'desc'}, company=COMPANY)
        assert [row['id'] for row in result['items']] == [parent, job, leaf, other]
    from bookflow.company.lists import get_list_definition
    definition = get_list_definition('customer')
    assert 'open_balance' in definition.summary_columns


def warning(client, customer, amount, **kw):
    with open_database(path(client), writable=False) as db:
        before = db.raw.total_changes
        result = balances.credit_warning(db, customer, amount, **kw)
        assert db.raw.total_changes == before
        return result


def test_credit_warning_inherited_limit_credits_and_corrections(client, ar):
    parent, job, leaf, other, *_ = ar
    client.customer.update(customer=parent, credit_limit='100.00', company=COMPANY)
    post(client, ar, parent, '60.00')
    post(client, ar, leaf, '20.00')
    credit = post(client, ar, job, '10.00', True)
    post(client, ar, other, '500.00')
    # All descendants contribute once; existing credits reduce exposure to 70.
    assert warning(client, leaf, 3000) is None
    assert warning(client, leaf, 3001) == (
        'Credit limit for Balance Parent: proposed net AR exposure 100.01 USD '
        'exceeds the limit of 100.00 USD.')
    # Correction on the same job and movement between sibling/ancestor parties
    # both remove the old effect, wherever it lies in the limit owner's tree.
    assert warning(client, leaf, 4000, old_customer_id=leaf, old_total=2000) is None
    assert warning(client, job, 4000, old_customer_id=leaf, old_total=2000) is None
    assert warning(client, leaf, 8000, old_customer_id=parent, old_total=6000) is None
    assert '110.00 USD' in warning(client, job, 4000, old_customer_id=other, old_total=50000)
    assert warning(client, other, 100000, old_customer_id=leaf, old_total=2000) is None
    client.journal.void(journal=credit['id'], expected_version=1, reason='Mistake', company=COMPANY)
    assert '110.00 USD' in warning(client, leaf, 3000)


def test_credit_warning_nearest_limit_zero_and_no_limit(client, ar):
    parent, job, leaf, other, *_ = ar
    assert warning(client, leaf, INT64_MAX) is None
    client.customer.update(customer=parent, credit_limit='100.00', company=COMPANY)
    client.customer.update(customer=job, credit_limit='0.00', company=COMPANY)
    post(client, ar, parent, '80.00')
    post(client, ar, leaf, '5.00', True)
    assert warning(client, leaf, 500) is None
    assert warning(client, leaf, 501) == (
        'Credit limit for Balance Parent:Job: proposed net AR exposure 0.01 USD '
        'exceeds the limit of 0.00 USD.')
    # Parent is outside the nearer job's family, so its old invoice cannot
    # reduce this job's proposed exposure even though it is an ancestor.
    assert '1.00 USD' in warning(client, leaf, 600, old_customer_id=parent, old_total=8000)
    client.customer.update(customer=leaf, credit_limit='20.00', company=COMPANY)
    assert warning(client, leaf, 2500) is None
    assert 'Balance Parent:Job:Leaf' in warning(client, leaf, 2501)
    assert warning(client, other, 999999) is None


def test_credit_warning_unbounded_exposure_never_blocks(client, ar):
    parent = ar[0]
    client.customer.update(customer=parent, credit_limit='0.00', company=COMPANY)
    post(client, ar, parent, '92233720368547758.07')
    post(client, ar, parent, '92233720368547758.07')
    assert warning(client, parent, 1) == (
        'Credit limit for Balance Parent: proposed net AR exposure 184467440737095516.15 USD '
        'exceeds the limit of 0.00 USD.')
    # Subtract the replaced invoice before comparing, with no intermediate clamp.
    assert '92233720368547758.07 USD' in warning(
        client, parent, 0, old_customer_id=parent, old_total=INT64_MAX)
