"""Independent review witnesses for pinned domestic Row8 artifact."""
import base64
import json
import pytest
from bookflow import BookflowError
from tests.test_row8_journal import COMPANY, journal_accounts, lines, database_path
from tests.test_row3_host import hosted
from tests.test_row5_workbench_forms import _browser
from bookflow.storage.engine import open_database


def decode(cursor):
    payload, signature = cursor.split('.')
    value = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    value['_retained_signature'] = signature
    return value


def encode(cursor):
    value = dict(cursor)
    signature = value.pop('_retained_signature')
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=') + '.' + signature


def test_historical_number_render(hosted):
    cid = hosted.company_id
    result = hosted.ok('journal.post', {'date': '2026-04-01', 'number': 'ORIGINAL-CRITIC', 'lines': lines(['Checking', 'Service Income'])}, company=cid)
    hosted.ok('journal.update', {'journal': result['id'], 'number': 'CURRENT-CRITIC', 'expected_version': 1}, company=cid)
    response = _browser(hosted).get(f"/c/{cid}/journal/{result['id']}?revision_number=1")
    assert response.status_code == 200
    assert '<h2>Journal ORIGINAL-CRITIC</h2>' in response.text


def test_continuation_metadata_cannot_be_forged(client):
    args = {'date_to': '2026-12-31', 'limit': 1}
    page = client.run('report trial-balance', args, company=COMPANY)
    cursor = decode(page['next_cursor'])
    cursor['metadata']['generation_time'] = '1900-01-01T00:00:00Z'
    cursor['metadata']['audit_watermark'] = 0
    with pytest.raises(BookflowError):
        client.run('report trial-balance', {**args, 'cursor': encode(cursor)}, company=COMPANY)


def test_continuation_cannot_substitute_account(client):
    args = {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 1}
    a = client.run('report general-ledger', {**args, 'account': 'Checking'}, company=COMPANY)
    b = client.run('report general-ledger', {**args, 'account': 'Service Income'}, company=COMPANY)
    forged = decode(b['next_cursor'])
    forged['query'] = decode(a['next_cursor'])['query']
    with pytest.raises(BookflowError):
        client.run('report general-ledger', {**args, 'account': 'Checking', 'cursor': encode(forged)}, company=COMPANY)


def test_independent_correction_pages_and_account_balances(client, journal_accounts):
    a, b = journal_accounts
    old = client.journal.post(date='2026-04-01', lines=lines([a,b], '0.17'), company=COMPANY)
    new = client.journal.update(journal=old['id'], date='2026-05-01', lines=lines([a,b], '0.29'), expected_version=1, company=COMPANY)
    args = {'date_from':'2026-04-01','date_to':'2026-05-31','account':a,'limit':1}
    rows=[]; cursor=None; metadata=None
    while True:
        page=client.run('report general-ledger', {**args,'cursor':cursor}, company=COMPANY)
        if metadata is None: metadata=page['metadata']
        assert page['metadata']==metadata
        rows.extend(page['rows']); cursor=page['next_cursor']
        if cursor is None: break
    detail=[r for r in rows if r['kind']=='posting']
    assert [r['batch_kind'] for r in detail]==['original','reversal','replacement']
    assert [r['signed_balance']['minor_units'] for r in detail]==[17,0,29]
    assert [r['effective_date'] for r in detail]==['2026-04-01','2026-04-01','2026-05-01']
    assert client.account.show(account=a, company=COMPANY)['balance']['minor_units']==29
    assert client.account.show(account=b, company=COMPANY)['balance']['minor_units']==29
    with open_database(database_path(client), writable=False) as db:
        sources=db.raw.execute('SELECT l.debit_minor_units+l.credit_minor_units,s.amount_minor_units FROM posting_lines l JOIN posting_line_sources s ON s.posting_line_id=l.id WHERE l.transaction_id=?',(old['id'],)).fetchall()
        assert len(sources)==6 and all(x==y for x,y in sources)
    client.journal.void(journal=old['id'], expected_version=2, reason='Independent inverse', company=COMPANY)
    assert client.account.show(account=a, company=COMPANY)['balance']['minor_units']==0
    assert client.account.show(account=b, company=COMPANY)['balance']['minor_units']==0


def test_authenticated_live_cursor_survives_company_copy(ledger, tmp_path):
    from types import SimpleNamespace
    from tests.test_row8_reports import gl
    import sqlite3
    s, batch, _ = ledger
    batch('2026-01-10', 101)
    first = gl(s, account='a', limit=1)
    expected = gl(s, account='a', limit=1, cursor=first.next_cursor)
    copied = tmp_path / 'portable.db'
    with sqlite3.connect(copied) as target:
        s.company.raw.backup(target)
    with open_database(copied, writable=False) as db:
        relocated = SimpleNamespace(**{**vars(s), 'company': db})
        actual = gl(relocated, account='a', limit=1, cursor=first.next_cursor)
        assert actual == expected

from tests.test_row8_reports import ledger  # fixture for portable cursor witness
