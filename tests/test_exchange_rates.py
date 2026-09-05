"""Exact conversion and public manual-rate command witnesses in disposable roots."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from bookflow import BookflowError
from bookflow.core.audit import decode_snapshot
from bookflow.company import rates
from bookflow.core.exchange import canonical_rate, convert_money
from bookflow.core.exact import INT64_MAX
from bookflow.core.money import Money
from bookflow.storage.engine import open_database
from tests.conftest import as_user, make_actor

COMPANY = 'Demo Plumbing Co'


@pytest.mark.parametrize('value,expected', [('000.006800', '0.0068'), ('000001.000', '1'),
    ('999999999999.123456789012345678', '999999999999.123456789012345678'), ('0.000000000000000001', '0.000000000000000001')])
def test_canonical(value, expected):
    assert canonical_rate(value) == expected


@pytest.mark.parametrize('value', [None, True, False, 1, 0.5, '', '0', '000.000', ' 1', '1 ',
    '+1', '-1', '1e2', '.1', '1.', '1\n', '１２', '١', '1000000000000', '1.0000000000000000000'])
def test_invalid_rates(value):
    with pytest.raises(BookflowError) as err:
        canonical_rate(value)
    assert err.value.code == 'E_VALIDATION'


@pytest.mark.parametrize('units,original,home,rate,result', [
    (2345, 'JPY', 'USD', '0.0068', 1595), (125, 'JPY', 'USD', '0.0002', 2),
    (175, 'JPY', 'USD', '0.0002', 4), (3, 'USD', 'JPY', '50', 2),
    (1, 'JPY', 'KWD', '0.0015', 2), (1, 'JPY', 'KWD', '0.0025', 2),
    (INT64_MAX, 'USD', 'USD', '1.000000000000000000', INT64_MAX),
    (INT64_MAX, 'KWD', 'JPY', '1000', INT64_MAX),
])
def test_conversion(units, original, home, rate, result):
    assert convert_money(Money(units, original), home, rate) == Money(result, home)


@pytest.mark.parametrize('money,home,rate', [(Money(0, 'JPY'), 'USD', '1'),
    (Money(-1, 'JPY'), 'USD', '1'), (Money(INT64_MAX + 1, 'JPY'), 'USD', '0.01'),
    (Money(1, 'JPY'), 'USD', '0.00001'), (Money(INT64_MAX, 'JPY'), 'USD', '1')])
def test_conversion_range(money, home, rate):
    with pytest.raises(BookflowError) as err:
        convert_money(money, home, rate)
    assert err.value.code == 'E_VALUE_RANGE'


def set_rate(client, **kwargs):
    return client.rate.set(**{'date': '2026-01-12', 'from_currency': 'JPY', 'rate': '000.006800', 'company': COMPANY, **kwargs})


def path(client):
    return Path(client.company.show(company=COMPANY)['path']) / 'company.db'


def snapshot(db_path):
    with open_database(db_path, writable=False) as db:
        return {table: db.raw.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
            for table in ('exchange_rates', 'principals', 'audit_events', 'audit_entries', 'idempotency_keys')}


def test_create_update_noop_preview_retry_audit(client):
    db_path = path(client)
    before = snapshot(db_path)
    preview = set_rate(client, dry_run=True)
    assert preview['dry_run'] and preview['changed'] and preview['version'] == 1
    assert snapshot(db_path) == before
    first = set_rate(client, idempotency_key='fx-create', reason='Manual dated rate')
    assert first['rate'] == '0.0068' and first['to_currency'] == 'USD' and first['source'] == 'manual'
    saved = snapshot(db_path)
    retry = set_rate(client, idempotency_key='fx-create', reason='Manual dated rate')
    assert retry['id'] == first['id'] and retry['entered_at'] == first['entered_at']
    assert retry['idempotent_replay']
    assert snapshot(db_path) == saved
    for version in (0, 2):
        with pytest.raises(BookflowError) as err:
            set_rate(client, expected_version=version)
        assert err.value.code == 'E_VERSION_CONFLICT'
    noop = set_rate(client, rate='0.0068000', expected_version=1)
    assert not noop['changed'] and noop['entered_at'] == first['entered_at']
    assert snapshot(db_path) == saved
    second = set_rate(client, rate='0.007', expected_version=1, reason='Revised quote')
    assert second['id'] == first['id'] and second['version'] == 2
    assert client.rate.show(rate_id=first['id'], company=COMPANY)['rate'] == '0.007'
    assert client.rate.show(date='2026-01-12', from_currency='JPY', company=COMPANY)['id'] == first['id']
    with open_database(db_path, writable=False) as db:
        events = db.raw.execute("SELECT e.actor_id, e.interface, e.reason FROM audit_events e JOIN audit_entries a ON a.event_id=e.id WHERE e.command='rate set' AND a.record_type='exchange_rate' AND a.record_id=? ORDER BY e.seq", (first['id'],)).fetchall()
        assert events == [(first['entered_by'], 'python', 'Manual dated rate'), (second['entered_by'], 'python', 'Revised quote')]
        entries = db.raw.execute("SELECT version_before,version_after,before,after FROM audit_entries WHERE record_type='exchange_rate' AND record_id=? ORDER BY version_after", (first['id'],)).fetchall()
        assert len(entries) == 2 and entries[0][:2] == (None, 1) and entries[1][:2] == (1, 2)
        assert decode_snapshot(entries[1][2])['rate'] == '0.0068'
        assert decode_snapshot(entries[1][3])['rate'] == '0.007'
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('kwargs', [{'expected_version': True}, {'expected_version': '0'}, {'expected_version': -1},
    {'rate': None}, {'rate': 0.5}, {'date': '2026-02-30'}, {'date': '2026-1-12'},
    {'from_currency': 'ZZZ'}, {'from_currency': 'USD'}, {'to_currency': 'EUR'}])
def test_public_validation(client, kwargs):
    with pytest.raises(BookflowError) as err:
        set_rate(client, **kwargs)
    assert err.value.code == 'E_VALIDATION'


@pytest.mark.parametrize('kwargs', [{}, {'date': '2026-01-12'}, {'from_currency': 'JPY'},
    {'rate_id': 'x', 'date': '2026-01-12', 'from_currency': 'JPY'}])
def test_show_selector_validation(client, kwargs):
    with pytest.raises(BookflowError) as err:
        client.rate.show(company=COMPANY, **kwargs)
    assert err.value.code == 'E_VALIDATION'


def test_missing_exact_lookup_and_version(client):
    with pytest.raises(BookflowError) as err:
        set_rate(client, expected_version=1)
    assert err.value.code == 'E_VERSION_CONFLICT'
    first = set_rate(client)
    with open_database(path(client), writable=False) as db:
        s = SimpleNamespace(company=db)
        assert rates.lookup(s, '2026-01-12', 'JPY', 'USD')['id'] == first['id']
        for date, original, home in [('2026-01-13', 'JPY', 'USD'), ('2026-01-12', 'USD', 'JPY'), ('2026-01-12', 'EUR', 'USD')]:
            with pytest.raises(BookflowError) as err:
                rates.lookup(s, date, original, home)
            assert err.value.code == 'E_NO_EXCHANGE_RATE'
            assert err.value.details == {'date': date, 'from_currency': original, 'to_currency': home}
    with pytest.raises(BookflowError) as err:
        client.rate.show(date='2026-01-13', from_currency='JPY', company=COMPANY)
    assert err.value.code == 'E_RECORD_NOT_FOUND'


def test_query_cursor_filters_staleness(client):
    for date, currency in [('2026-01-13', 'JPY'), ('2026-01-12', 'JPY'), ('2026-01-12', 'EUR')]:
        set_rate(client, date=date, from_currency=currency)
    first = client.rate.query(limit=1, company=COMPANY)
    assert first['items'][0]['from_currency'] == 'EUR' and first['has_more']
    second = client.rate.query(limit=1, cursor=first['next_cursor'], company=COMPANY)
    assert second['items'][0]['date'] == '2026-01-12' and second['items'][0]['from_currency'] == 'JPY'
    filtered = client.rate.query(date_from='2026-01-13', date_to='2026-01-13', from_currency='JPY', company=COMPANY)
    assert filtered['count'] == 1
    with pytest.raises(BookflowError) as err:
        client.rate.query(limit=2, cursor=first['next_cursor'], company=COMPANY)
    assert err.value.code == 'E_VALIDATION'
    set_rate(client, rate='0.007', expected_version=1)
    with pytest.raises(BookflowError) as err:
        client.rate.query(limit=1, cursor=first['next_cursor'], company=COMPANY)
    assert err.value.code == 'E_QUERY_STALE'


def test_readonly_current_company_and_other_actor_cursor(client, root, cli):
    first = set_rate(client)
    set_rate(client, date='2026-01-13')
    company = client.company.show(company=COMPANY)
    make_actor(root, 'rate-reader', company_role=(company['id'], 'readonly'))
    reader = as_user(root, 'rate-reader')
    assert reader.rate.show(rate_id=first['id'], company=COMPANY)['id'] == first['id']
    with pytest.raises(BookflowError) as err:
        set_rate(reader)
    assert err.value.code == 'E_PERMISSION'
    page = client.rate.query(limit=1, company=COMPANY)
    with pytest.raises(BookflowError) as err:
        reader.rate.query(limit=1, cursor=page['next_cursor'], company=COMPANY)
    assert err.value.code == 'E_VALIDATION'
    make_actor(root, 'rate-outsider')
    outsider = as_user(root, 'rate-outsider')
    with pytest.raises(BookflowError) as err:
        outsider.rate.show(rate_id=first['id'], company=COMPANY)
    assert err.value.code == 'E_COMPANY_NOT_FOUND'
    shown = cli.json('rate', 'show', first['id'], '--company', COMPANY)
    assert shown['id'] == first['id']
    assert cli.json('rate', 'show', '--date', '2026-01-12', '--from-currency', 'JPY', '--company', COMPANY) == shown


def fail_late(*args, **kwargs):
    raise RuntimeError('injected late failure')


@pytest.mark.parametrize('boundary', ['audit', 'receipt', 'rate'])
@pytest.mark.parametrize('existing', [False, True])
def test_late_failure_rolls_back_rate_principal_audit_receipt(client, root, monkeypatch, boundary, existing):
    from bookflow.core import audit, idempotency
    if existing:
        set_rate(client)
    company = client.company.show(company=COMPANY)
    make_actor(root, 'rate-writer', company_role=(company['id'], 'standard'))
    writer = as_user(root, 'rate-writer')
    db_path = path(client)
    if boundary == 'rate':
        with open_database(db_path, writable=True) as db:
            operation = 'UPDATE' if existing else 'INSERT'
            db.raw.execute(f"CREATE TRIGGER injected_rate_failure AFTER {operation} ON exchange_rates BEGIN SELECT RAISE(ABORT, 'injected late failure'); END")
    before = snapshot(db_path)
    if boundary == 'audit':
        monkeypatch.setattr(audit, 'write_event_to', fail_late)
    elif boundary == 'receipt':
        monkeypatch.setattr(idempotency, 'store', fail_late)
    with pytest.raises(Exception):
        set_rate(writer, rate='0.009', expected_version=int(existing), idempotency_key='late-fx')
    assert snapshot(db_path) == before


def change_version_before_apply(plan, ctx, s):
    s.company.raw.execute('UPDATE exchange_rates SET version=version+1')
    return rates.apply(plan, ctx, s)


def test_writer_rechecks_version(client, monkeypatch):
    from bookflow.commands.rate_cmds import rate_set
    set_rate(client)
    before = snapshot(path(client))
    monkeypatch.setattr(rate_set, 'apply', change_version_before_apply)
    with pytest.raises(BookflowError) as err:
        set_rate(client, rate='0.007', expected_version=1)
    assert err.value.code == 'E_VERSION_CONFLICT'
    assert snapshot(path(client)) == before


def test_cursor_tampering_rejected(client):
    import base64
    import json
    set_rate(client)
    set_rate(client, date='2026-01-13')
    cursor = client.rate.query(limit=1, company=COMPANY)['next_cursor']
    payload, signature = cursor.split('.')
    decoded = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    decoded['offset'] = 0
    forged = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip('=') + '.' + signature
    for value in (forged, payload, cursor + 'x', '非ASCII.signature', payload + '.非ASCII'):
        with pytest.raises(BookflowError) as err:
            client.rate.query(limit=1, cursor=value, company=COMPANY)
        assert err.value.code == 'E_VALIDATION'


def test_current_company_noop_receipt_and_latest_setter(client, root):
    first = set_rate(client)
    company = client.company.show(company=COMPANY)
    client.company.use(company=company['id'])
    assert client.rate.show(rate_id=first['id'])['id'] == first['id']
    noop = set_rate(client, expected_version=1, idempotency_key='fx-noop')
    assert not noop['changed']
    replay = set_rate(client, rate='0.006800', expected_version=1, idempotency_key='fx-noop')
    assert replay['idempotent_replay'] and not replay['changed']
    make_actor(root, 'rate-editor', company_role=(company['id'], 'standard'))
    editor = as_user(root, 'rate-editor')
    latest = set_rate(editor, expected_version=1, rate='0.01')
    assert latest['entered_by'] != first['entered_by']
    assert latest['version'] == 2


def test_non_money_and_unknown_currency_rejected():
    for original, home in [(True, 'USD'), ({'minor_units': 1, 'currency': 'JPY'}, 'USD'), (Money(1, 'JPY'), 'ZZZ')]:
        with pytest.raises(BookflowError) as err:
            convert_money(original, home, '1')
        assert err.value.code == 'E_VALIDATION'
