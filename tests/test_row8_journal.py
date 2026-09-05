"""Independent domestic lifecycle witnesses through the public dispatcher."""
from collections import defaultdict
from pathlib import Path

import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from tests.conftest import make_actor, as_user
from tests.test_row3_host import hosted  # noqa: F401

COMPANY = 'Demo Plumbing Co'


@pytest.fixture
def journal_accounts(client):
    return [client.account.create(name='Journal cash witness', type='bank', company=COMPANY)['id'],
            client.account.create(name='Journal equity witness', type='equity', company=COMPANY)['id']]


def lines(accounts, amount='12.34'):
    return [dict(account=accounts[0], side='debit', amount=amount),
            dict(account=accounts[1], side='credit', amount=amount)]


def post(client, accounts, **kw):
    return client.journal.post(date='2026-01-12', lines=lines(accounts), company=COMPANY, **kw)


def database_path(client):
    return Path(client.company.show(company=COMPANY)['path']) / 'company.db'


def ledger(client, transaction):
    with open_database(database_path(client), writable=False) as db:
        batches = [dict(r) for r in db.conn.execute(sa.select(c.posting_batches).where(c.posting_batches.c.transaction_id == transaction)).mappings()]
        legs = [dict(r) for r in db.conn.execute(sa.select(c.posting_lines).where(c.posting_lines.c.transaction_id == transaction)).mappings()]
        sources = [dict(r) for r in db.conn.execute(sa.select(c.posting_line_sources).where(c.posting_line_sources.c.transaction_id == transaction)).mappings()]
    return batches, legs, sources


def assert_oracle(client, transaction, expected):
    batches, legs, sources = ledger(client, transaction)
    by_date = defaultdict(int)
    for batch in batches:
        own = [l for l in legs if l['batch_id'] == batch['id']]
        assert sum(l['debit_minor_units'] for l in own) == sum(l['credit_minor_units'] for l in own)
        for leg in own:
            by_date[batch['effective_date'], leg['account_id']] += leg['debit_minor_units'] - leg['credit_minor_units']
            assert sum(x['amount_minor_units'] for x in sources if x['posting_line_id'] == leg['id']) == leg['debit_minor_units'] + leg['credit_minor_units']
            if leg['reversed_line_id']:
                old = next(x for x in legs if x['id'] == leg['reversed_line_id'])
                assert (leg['debit_minor_units'], leg['credit_minor_units']) == (old['credit_minor_units'], old['debit_minor_units'])
                assert all(leg[k] == old[k] for k in ('account_id', 'currency', 'account_snapshot', 'name_id', 'class_id', 'original_minor_units', 'rate_used'))
    assert {k: v for k, v in by_date.items() if v} == expected


def test_dated_correction_void_and_sources(client, journal_accounts):
    a, b = journal_accounts
    first = post(client, journal_accounts)
    assert first['id'] and first['version'] == 1 and first['total_minor_units'] == 1234
    assert_oracle(client, first['id'], {('2026-01-12', a): 1234, ('2026-01-12', b): -1234})
    changed = client.journal.update(journal=first['id'], date='2026-02-01', lines=lines(journal_accounts, '15.00'), expected_version=1, company=COMPANY)
    assert changed['id'] == first['id'] and changed['version'] == 2
    assert_oracle(client, first['id'], {('2026-02-01', a): 1500, ('2026-02-01', b): -1500})
    void = client.journal.void(journal=first['id'], expected_version=2, reason='Entered twice', company=COMPANY)
    assert void['status'] == 'voided' and void['number'] == first['number'] and void['total_minor_units'] == 1500
    assert_oracle(client, first['id'], {})
    history = client.journal.history(journal=first['id'], company=COMPANY)
    assert [r['revision_number'] for r in history['items']] == [1, 2]
    assert [len(r['batches']) for r in history['items']] == [2, 2]
    assert client.journal.show(journal=first['id'], revision_number=1, company=COMPANY)['revision']['total_minor_units'] == 1234


def test_stale_always_conflicts_before_noop_or_repeat_void(client, journal_accounts):
    first = post(client, journal_accounts)
    client.journal.update(journal=first['id'], memo='corrected', expected_version=1, company=COMPANY)
    for change in ({'memo': 'corrected'}, {'date': '2026-02-01'}, {}):
        with pytest.raises(BookflowError) as err:
            client.journal.update(journal=first['id'], expected_version=1, company=COMPANY, **change)
        assert err.value.code == 'E_VERSION_CONFLICT'
        assert err.value.details['current_version'] == 2
    client.journal.void(journal=first['id'], expected_version=2, reason='Mistake', company=COMPANY)
    with pytest.raises(BookflowError) as err:
        client.journal.void(journal=first['id'], expected_version=2, reason='Mistake', company=COMPANY)
    assert err.value.code == 'E_VERSION_CONFLICT'
    assert not client.journal.void(journal=first['id'], expected_version=3, reason='Mistake', company=COMPANY)['changed']


def test_numbers_noop_and_retry(client, journal_accounts):
    explicit = post(client, journal_accounts, number='  J-one  ')
    assert explicit['number'] == 'J-one'
    lower = post(client, journal_accounts, number='j-one')
    assert lower['number'] == 'j-one'
    with pytest.raises(BookflowError) as err:
        post(client, journal_accounts, number='J-one')
    assert err.value.code == 'E_DUPLICATE_NUMBER'
    first = post(client, journal_accounts, idempotency_key='journal-once')
    replay = post(client, journal_accounts, idempotency_key='journal-once')
    assert replay['id'] == first['id'] and replay['idempotent_replay']
    before = ledger(client, first['id'])
    noop = client.journal.update(journal=first['id'], expected_version=1, company=COMPANY)
    assert not noop['changed'] and noop['version'] == 1
    assert ledger(client, first['id']) == before
    following = post(client, journal_accounts)
    assert int(following['number']) == int(first['number']) + 1


def test_line_identity_ownership_and_retirement(client, journal_accounts):
    first, other = post(client, journal_accounts), post(client, journal_accounts)
    def retained(output):
        return [dict(value, line_id=old['line_id']) for value, old in zip(lines(journal_accounts), output['revision']['lines'])]
    original = retained(first)
    for bad in (other['revision']['lines'][0]['line_id'], new_id(), original[1]['line_id']):
        submission = [dict(original[0], line_id=bad), original[1]]
        with pytest.raises(BookflowError) as err:
            client.journal.update(journal=first['id'], lines=submission, expected_version=1, company=COMPANY)
        assert err.value.code == 'E_VALIDATION'
    update = client.journal.update(journal=first['id'], lines=lines(journal_accounts), expected_version=1, company=COMPANY)
    assert set(l['line_id'] for l in update['revision']['lines']).isdisjoint(l['line_id'] for l in first['revision']['lines'])
    with pytest.raises(BookflowError) as err:
        client.journal.update(journal=first['id'], lines=original, expected_version=2, company=COMPANY)
    assert err.value.code == 'E_VALIDATION'


def test_historical_snapshot_refresh_and_inactive_reversal(client, journal_accounts):
    first = post(client, journal_accounts)
    snapshot = first['revision']['lines'][0]['account_snapshot']
    client.account.update(account=journal_accounts[0], name='Renamed cash witness', company=COMPANY)
    update = client.journal.update(journal=first['id'], memo='New memo', expected_version=1, company=COMPANY)
    assert update['revision']['lines'][0]['account_snapshot'] == snapshot
    refresh = client.journal.update(journal=first['id'], refresh_defaults=True, expected_version=2, company=COMPANY)
    assert refresh['revision']['lines'][0]['account_snapshot']['name'] == 'Renamed cash witness'
    with pytest.raises(BookflowError) as err:
        client.account.deactivate(account=journal_accounts[0], company=COMPANY)
    assert err.value.code == 'E_RECORD_IN_USE'
    assert client.account.show(account=journal_accounts[0], company=COMPANY)['active']
    # Test-only legacy/import state: normal account commands refuse this transition.
    with open_database(database_path(client), writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        db.conn.execute(c.accounts.update().where(c.accounts.c.id == journal_accounts[0]).values(active=False))
        db.raw.execute('COMMIT')
    historical = client.journal.show(journal=first['id'], revision_number=1, company=COMPANY)
    assert historical['revision']['lines'][0]['account_snapshot'] == snapshot
    client.journal.void(journal=first['id'], expected_version=3, reason='Reverse exact history', company=COMPANY)
    assert_oracle(client, first['id'], {})


def test_query_history_bounded_and_stale(client, journal_accounts):
    marker = 'journal-pagination-witness'
    first = post(client, journal_accounts, memo=marker)
    client.journal.update(journal=first['id'], memo='Revision two', expected_version=1, company=COMPANY)
    page = client.journal.history(journal=first['id'], limit=1, company=COMPANY)
    assert page['has_more'] and page['count'] == 1
    following = client.journal.history(journal=first['id'], limit=1, cursor=page['next_cursor'], company=COMPANY)
    assert following['items'][0]['revision_number'] == 2
    post(client, journal_accounts, memo=marker)
    post(client, journal_accounts, memo=marker)
    query = client.journal.query(query=marker, limit=1, company=COMPANY)
    assert query['count'] == 1 and query['has_more'] and query['next_cursor']
    assert query['items'][0]['memo'] == marker
    post(client, journal_accounts, memo=marker)
    with pytest.raises(BookflowError) as err:
        client.journal.history(journal=first['id'], limit=1, cursor=page['next_cursor'], company=COMPANY)
    assert err.value.code == 'E_QUERY_STALE'
    with pytest.raises(BookflowError) as err:
        client.journal.query(query=marker, limit=1, cursor=query['next_cursor'], company=COMPANY)
    assert err.value.code == 'E_QUERY_STALE'


@pytest.mark.parametrize('amount', [1, 1.25, True, {'minor_units': 125, 'currency': 'USD', 'amount': '1.26'}, '1.00 JPY'])
def test_no_numeric_or_foreign_money(client, journal_accounts, amount):
    with pytest.raises(BookflowError):
        client.journal.post(date='2026-01-12', lines=lines(journal_accounts, amount), company=COMPANY)


def test_unbalanced_overflow_and_void_reason(client, journal_accounts):
    bad = lines(journal_accounts)
    bad[1]['amount'] = '12.35'
    with pytest.raises(BookflowError) as err:
        client.journal.post(date='2026-01-12', lines=bad, company=COMPANY)
    assert err.value.code == 'E_UNBALANCED_ENTRY'
    huge = lines(journal_accounts, {'minor_units': 9223372036854775807, 'currency': 'USD'}) * 2
    with pytest.raises(BookflowError) as err:
        client.journal.post(date='2026-01-12', lines=huge, company=COMPANY)
    assert err.value.code == 'E_VALUE_RANGE'
    first = post(client, journal_accounts)
    with pytest.raises(BookflowError) as err:
        client.journal.void(journal=first['id'], expected_version=1, company=COMPANY)
    assert err.value.code == 'E_REASON_REQUIRED'


def test_readonly_and_cli_show_parity(client, root, cli, journal_accounts):
    first = post(client, journal_accounts)
    shown = client.journal.show(journal=first['id'], company=COMPANY)
    assert cli.json('journal', 'show', first['id'], '--company', COMPANY) == shown
    company = client.company.show(company=COMPANY)
    make_actor(root, 'journal-reader', company_role=(company['id'], 'readonly'))
    reader = as_user(root, 'journal-reader')
    assert reader.journal.show(journal=first['id'], company=COMPANY)['id'] == first['id']
    with pytest.raises(BookflowError):
        post(reader, journal_accounts)


def test_http_python_cli_read_parity(hosted, client, cli):
    cid = hosted.company_id
    accounts = [hosted.ok('account.create', {'name': 'HTTP journal cash', 'type': 'bank'}, company=cid)['id'],
                hosted.ok('account.create', {'name': 'HTTP journal equity', 'type': 'equity'}, company=cid)['id']]
    posted = hosted.ok('journal.post', {'date': '2026-03-01', 'lines': lines(accounts)}, company=cid)
    body = {'journal': posted['id']}
    http = hosted.ok('journal.show', body, company=cid)
    assert http == cli.json('journal', 'show', posted['id'], '--company', cid)
    hosted.handle.stop()
    assert http == client.journal.show(**body, company=cid)


def test_ar_ap_party_and_class_references(client, journal_accounts):
    ar = client.account.create(name='Journal receivable', type='accounts_receivable', company=COMPANY)['id']
    ap = client.account.create(name='Journal payable', type='accounts_payable', company=COMPANY)['id']
    customer = client.customer.create(name='Journal customer', company=COMPANY)
    vendor = client.vendor.create(name='Journal vendor', company=COMPANY)
    klass = client.run('class create', {'name': 'Journal class'}, company=COMPANY)
    entered = lines([ar, ap])
    with pytest.raises(BookflowError) as err:
        client.journal.post(date='2026-01-12', lines=entered, company=COMPANY)
    assert err.value.code == 'E_VALIDATION'
    entered[0].update(name_type='customer', name_id=customer['id'], class_id=klass['id'])
    entered[1].update(name_type='vendor', name_id=vendor['id'])
    first = client.journal.post(date='2026-01-12', lines=entered, company=COMPANY)
    assert first['revision']['lines'][0]['party_name'] == 'Journal customer'
    assert first['revision']['lines'][0]['class_name'] == 'Journal class'
    client.customer.update(customer=customer['id'], name='Renamed journal customer', company=COMPANY)
    client.run('class update', {'class': klass['id'], 'name': 'Renamed journal class'}, company=COMPANY)
    old = client.journal.show(journal=first['id'], company=COMPANY)
    assert old['revision']['lines'][0]['party_name'] == 'Journal customer'
    assert old['revision']['lines'][0]['class_name'] == 'Journal class'


def test_copied_company_keeps_complete_history(client, journal_accounts, tmp_path):
    import shutil
    first = post(client, journal_accounts)
    client.journal.update(journal=first['id'], date='2026-04-01', expected_version=1, company=COMPANY)
    source = database_path(client)
    copied = tmp_path / 'copied-company.db'
    shutil.copy2(source, copied)
    with open_database(source, writable=False) as original, open_database(copied, writable=False) as copy:
        for table in ('transactions', 'transaction_revisions', 'document_lines', 'posting_batches', 'posting_lines', 'posting_line_sources'):
            query = 'SELECT * FROM ' + table + ' ORDER BY id'
            assert list(original.raw.execute(query)) == list(copy.raw.execute(query))


def test_typed_money_on_every_journal_view(client, journal_accounts):
    first = post(client, journal_accounts)
    money = {'amount': '12.34', 'currency': 'USD', 'minor_units': 1234}
    for view in (first, first['revision'], first['revision']['batches'][0]):
        assert view['total'] == view['debit_total'] == view['credit_total'] == money
    assert all(line['amount'] == money for line in first['revision']['lines'])
    query = client.journal.query(query=first['number'], company=COMPANY)
    summary = next(item for item in query['items'] if item['id'] == first['id'])
    assert summary['total'] == money
    history = client.journal.history(journal=first['id'], company=COMPANY)
    assert history['items'][0]['total'] == money
    assert history['items'][0]['batches'][0]['debit_total'] == money


def test_history_contains_only_revision_and_batch_summaries(client, journal_accounts):
    import json
    entered = [dict(line, description='x' * 2000) for line in lines(journal_accounts)] * 100
    first = client.journal.post(date='2026-01-12', lines=entered, company=COMPANY)
    client.journal.update(journal=first['id'], memo='Another revision', expected_version=1, company=COMPANY)
    history = client.journal.history(journal=first['id'], limit=200, company=COMPANY)
    assert len(history['items']) == 2
    for rev in history['items']:
        assert rev['line_count'] == 200 and rev['batches']
        assert not {'lines', 'issuer_snapshot', 'custom_fields_snapshot'} & set(rev)
    assert len(json.dumps(history)) < 20000
    shown = client.journal.show(journal=first['id'], revision_number=1, company=COMPANY)
    assert len(shown['revision']['lines']) == 200
    assert shown['revision']['lines'][0]['description'] == 'x' * 2000
