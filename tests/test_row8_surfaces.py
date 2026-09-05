"""Demo reconciliation and generated browser journal editing use posted history."""
import json

from bookflow.core import registry
from tests.test_row3_host import hosted, WB  # noqa: F401
from tests.test_row5_workbench_forms import _browser, _page_originals, _encode_collection

COMPANY = 'Demo Plumbing Co'


def test_demo_account_balances_and_reports_reconcile(client):
    journals = client.journal.query(company=COMPANY)['items']
    assert len(journals) == 9
    assert sum(j['status'] == 'voided' for j in journals) == 1
    service = next(j for j in journals if j['number'] == 'DEMO-SERVICE')
    assert service['version'] == 2
    assert len(client.journal.history(journal=service['id'], company=COMPANY)['items']) == 2
    tb = client.run('report trial-balance', {'date_to': '2026-12-31'}, company=COMPANY)
    assert tb['totals']['debit']['minor_units'] == tb['totals']['credit']['minor_units'] == 663000
    nets = {r['account_id']: r['signed_net']['minor_units'] for r in tb['rows']}
    expected_balances = {'Checking': 610500, 'Professional Fees': 52500,
                         'Service Income': 160000, 'Opening Balance Equity': 500000,
                         'Business Credit Card': 3000}
    for name, amount in expected_balances.items():
        assert client.account.show(account=name, company=COMPANY)['balance']['minor_units'] == amount
    bank_register = client.register.query(account='Checking', date_from='2026-01-01',
                                         date_to='2026-12-31', company=COMPANY)
    assert bank_register['totals']['closing']['minor_units'] == 610500
    split = next(row for row in bank_register['rows'] if row['transaction_number'] == 'REG-SPLIT' and row['batch_kind'] == 'replacement')
    assert split['decrease']['minor_units'] == 10000 and split['category_label'] == 'Splits'
    listed = client.account.list(company=COMPANY)['items']
    queried = client.account.query(company=COMPANY, sort='balance', direction='desc', limit=100)['items']
    assert [a['balance']['minor_units'] for a in queried] == sorted((a['balance']['minor_units'] for a in queried), reverse=True)
    for account in listed:
        expected = nets.get(account['id'], 0) * (-1 if account['normal_balance'] == 'credit' else 1)
        assert account['balance']['minor_units'] == expected
        assert client.account.show(account=account['id'], company=COMPANY)['balance'] == account['balance']
    notes = client.note.list(record_type='transaction', record_id=service['id'], company=COMPANY)['items']
    assert notes
    assert client.attachment.list(record_type='transaction', record_id=service['id'], company=COMPANY)['items']


def test_generated_journal_preview_post_edit_and_historical_detail(hosted):
    browser = _browser(hosted)
    cid = hosted.company_id
    base = f'/c/{cid}/journal'
    inputs = {'originals': '{}', 'f:date': '2026-04-01', 'f:number': 'BROWSER-JOURNAL', 'f:memo': 'Browser receipt',
        **_encode_collection('lines', [{'account': 'Checking', 'side': 'debit', 'amount': '32.10'}, {'account': 'Service Income', 'side': 'credit', 'amount': '32.10'}])}
    preview = browser.post(base + '/post', headers=WB, data={**inputs, 'action': 'preview'})
    assert preview.status_code == 200, preview.text
    assert 'E_VALIDATION' not in preview.text
    assert not any(j['number'] == 'BROWSER-JOURNAL' for j in hosted.ok('journal.query', company=cid)['items'])
    posted = browser.post(base + '/post', headers=WB, data={**inputs, 'action': 'submit'})
    assert posted.status_code == 303, posted.text
    journal = next(j for j in hosted.ok('journal.query', company=cid)['items'] if j['number'] == 'BROWSER-JOURNAL')
    route = base + '/' + journal['id']
    assert posted.headers['location'].split('?')[0] == route
    detail = browser.get(route)
    assert detail.status_code == 200
    assert 'Journal BROWSER-JOURNAL' in detail.text and '32.10' in detail.text
    assert 'data-annotations' in detail.text
    edit = browser.get(route + '/update')
    originals = _page_originals(edit)
    assert originals['lines'][0]['amount'] == '32.10'
    assert originals['lines'][0]['line_id']
    assert originals['expected_version'] == 1
    changed = browser.post(route + '/update', headers=WB, data={
        'originals': json.dumps(originals), 'f:expected_version': '1', 'f:memo': 'Corrected browser explanation', 'action': 'submit'})
    assert changed.status_code == 303, changed.text
    current = browser.get(route)
    historical = browser.get(route + '?revision_number=1')
    assert 'Corrected browser explanation' in current.text
    assert 'Previous revision' in current.text
    assert 'Browser receipt' in historical.text and 'Current revision' in historical.text
    assert 'Corrected browser explanation' not in historical.text.split('All fields and technical details')[0]
    stale = browser.post(route + '/update', headers=WB, data={
        'originals': json.dumps(originals), 'f:expected_version': '1', 'f:memo': 'Stale overwrite', 'action': 'submit'})
    assert 'E_VERSION_CONFLICT' in stale.text
    assert hosted.ok('journal.show', {'journal': journal['id']}, company=cid)['memo'] == 'Corrected browser explanation'
    # A browser collection preserves the stable line identities on a no-op edit.
    from bookflow.adapters.workbench.forms import translate
    command = registry.get('journal update')
    translated, _, _ = translate(command, _encode_collection('lines', originals['lines']), originals)
    assert 'lines' not in translated


def test_cli_posts_and_corrects_structured_lines_with_exact_money(client, cli):
    args = ['--company', COMPANY]
    lines = [{'account': 'Checking', 'side': 'debit', 'amount': '0.07'}, {'account': 'Service Income', 'side': 'credit', 'amount': '0.07'}]
    posted = cli.json('journal', 'post', '--date', '2026-05-01', '--lines', json.dumps(lines), *args)
    assert posted['total']['minor_units'] == 7
    for line in lines:
        line['amount'] = '0.09'
    changed = cli.json('journal', 'update', posted['id'], '--expected-version', '1', '--lines', json.dumps(lines), *args)
    assert changed['total']['minor_units'] == 9 and changed['version'] == 2
    assert client.journal.show(journal=posted['id'], company=COMPANY)['revision'] == changed['revision']
    voided = cli.json('journal', 'void', posted['id'], '--expected-version', '2', '--reason', 'Duplicate entry', *args)
    assert voided['status'] == 'voided'


def test_account_balance_overflow_is_exact_and_sort_cursor_stales(client):
    import pytest
    from bookflow import BookflowError
    bank = client.account.create(name='Large balance witness', type='bank', company=COMPANY)['id']
    equity = client.account.create(name='Large equity witness', type='equity', company=COMPANY)['id']
    def post(amount):
        return client.journal.post(date='2026-06-01', lines=[
            {'account': bank, 'side': 'debit', 'amount': amount},
            {'account': equity, 'side': 'credit', 'amount': amount}], company=COMPANY)
    page = client.account.query(company=COMPANY, sort='balance', limit=1)
    first = post('92233720368547758.07')
    with pytest.raises(BookflowError) as caught:
        client.account.query(company=COMPANY, sort='balance', limit=1, cursor=page['next_cursor'])
    assert caught.value.code == 'E_QUERY_STALE'
    assert client.account.show(account=bank, company=COMPANY)['balance']['minor_units'] == 9223372036854775807
    post('0.01')
    for call in (lambda: client.account.show(account=bank, company=COMPANY),
                 lambda: client.account.list(company=COMPANY),
                 lambda: client.account.query(company=COMPANY, sort='balance', direction='desc')):
        with pytest.raises(BookflowError) as caught:
            call()
        assert caught.value.code == 'E_VALUE_RANGE'
    client.journal.void(journal=first['id'], expected_version=1, reason='Reverse large test entry', company=COMPANY)
    # Intermediate sums exceed i64; exact cancellation must still recover one cent.
    assert client.account.show(account=bank, company=COMPANY)['balance']['minor_units'] == 1


def test_historical_journal_page_uses_revision_number_after_renumber(hosted):
    cid = hosted.company_id
    journal = hosted.ok('journal.query', company=cid)['items'][0]
    old_number = journal['number']
    hosted.ok('journal.update', {'journal': journal['id'], 'expected_version': journal['version'], 'number': 'RENAMED-JOURNAL'}, company=cid)
    browser = _browser(hosted)
    page = browser.get(f"/c/{cid}/journal/{journal['id']}?revision_number=1")
    assert page.status_code == 200
    assert f'<h1>{old_number}</h1>' in page.text
    assert f'<h2>Journal {old_number}</h2>' in page.text
    assert 'Current journal number: RENAMED-JOURNAL' in page.text
