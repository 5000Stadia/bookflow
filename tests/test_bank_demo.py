"""The demo's attached downloads are usable through the same commands an agent discovers."""
from tests.test_bank_feeds import books, run


def attachments(client):
    company = client.company.show(company='Demo Plumbing Co')['company_id']
    result = run(client, 'attachment list', {'record_type': 'company_info', 'record_id': company})
    return {row['attachment']['original_filename']: row['attachment_id'] for row in result['items']}


def test_demo_statement_attachments_overlap_and_pair_a_card_payment(client):
    files = attachments(client)
    before = books(client)
    assert run(client, 'bank-line query', {})['lines'] == []
    first = run(client, 'bank-feed import', {
        'account': 'Checking', 'attachment': files['september-checking.ofx']})
    assert first['counts']['new'] == 6
    overlap = run(client, 'bank-feed import', {
        'account': 'Checking', 'attachment': files['september-checking-overlap.ofx']})
    assert (overlap['counts']['new'], overlap['counts']['already_in']) == (1, 2)
    card = run(client, 'bank-feed import', {
        'account': 'Business Credit Card', 'attachment': files['september-card.ofx']})
    assert card['counts']['new'] == 2
    assert books(client) == before
    # Opposite bank signs identify exactly one card payment; both ends share one posted transfer.
    card_line = next(row for row in card['lines'] if row['amount'] == 50000)
    checking = run(client, 'bank-line query', {'account': 'Checking', 'amount': '-500.00'})['lines'][0]
    run(client, 'bank-line transfer', {'line': checking['id'], 'pair': card_line['id']})
    paired = run(client, 'bank-line query', {'account': 'Business Credit Card', 'amount': '500.00'})['lines'][0]
    moved = run(client, 'bank-line query', {'account': 'Checking', 'amount': '-500.00'})['lines'][0]
    assert paired['state'] == moved['state'] == 'transfer'
    assert paired['entry_id'] == moved['entry_id']
    assert card_line['amount'] == -checking['amount']


def test_demo_wrong_account_attachment_can_be_moved_without_changing_books(client):
    files = attachments(client)
    before = books(client)
    wrong = run(client, 'bank-feed import', {
        'account': 'Payment Example Bank', 'attachment': files['september-wrong-account.csv']})
    assert wrong['counts']['new'] == 2
    reverse = run(client, 'bank-feed reverse', {'import_id': wrong['import_id']}, dry_run=True)
    assert reverse['lines'] == 2 and not reverse['blockers']
    payload = {'import_id': wrong['import_id'], 'to': 'Checking'}
    preview = run(client, 'bank-feed move', payload, dry_run=True)
    assert preview['next'] in preview['target']['next']
    assert 'bank-feed move' in preview['target']['next']
    moved = run(client, 'bank-feed move', {**payload, 'fingerprint': preview['reversal']['fingerprint']})
    assert moved['reversal']['remaining'] == 0
    assert moved['reversal']['state'] == 'reversed' and moved['target']['counts']['new'] == 2
    assert run(client, 'bank-line query', {'account': 'Payment Example Bank'})['lines'] == []
    assert len(run(client, 'bank-line query', {'account': 'Checking'})['lines']) == 2
    assert books(client) == before
