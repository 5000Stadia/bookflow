"""R108 increment 6: inbox evidence, unchanged reconciliation, reversible dependency."""
import sqlite3

import anyio
import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.test_bank_feeds import books, csv_file, ofx, run, sample, table
from tests.test_reconcile_import import opening
from tests.test_row8_journal import database_path


def inbox(client, **extra):
    return run(client, 'reconcile import', dict(account='Checking', from_inbox=True,
               start=True, statement_date='2026-09-30', ending_balance='6236.95', **extra))


def cancel(client, draft):
    return run(client, 'reconcile discard', dict(operation_key=new_id(), draft=draft['id'],
                                               expected_version=draft['version']))


def test_same_draft_as_file_dry_run_repeat_and_blocker(client):
    opening(client)
    content = sample('checking-2026-09.csv')
    direct = run(client, 'reconcile import', dict(account='Checking', content=content, start=True,
                                                 statement_date='2026-09-30'), dry_run=True)
    imported = run(client, 'bank-feed import', dict(account='Checking', content=content))
    before = books(client)
    preview = run(client, 'reconcile import', dict(account='Checking', from_inbox=True, start=True,
                  statement_date='2026-09-30', ending_balance='6236.95'), dry_run=True)
    assert table(client, 'SELECT count(*) FROM statement_lines') == [(0,)]
    assert preview['draft']['header'] == direct['draft']['header']
    assert preview['draft']['selections'] == direct['draft']['selections']
    assert [(v['line_id'], v['status'], v['amount']) for v in preview['lines']] == [
        (v['line_id'], v['status'], v['amount']) for v in direct['lines']]
    done = inbox(client)
    assert done['format'] == 'inbox' and done['counts']['newly_marked'] == 8
    assert books(client) == before
    assert table(client, 'SELECT count(*), count(bank_line_id) FROM statement_lines') == [(10, 10)]
    again = inbox(client)
    assert again['draft'] == done['draft'] and again['counts']['newly_marked'] == 0
    assert table(client, 'SELECT count(*) FROM statement_imports') == [(1,)]
    for dry in (True, False):
        with pytest.raises(BookflowError) as raised:
            run(client, 'bank-feed reverse', dict(import_id=imported['import_id']), dry_run=dry)
        assert raised.value.code == 'E_BATCH_BLOCKED'
        assert any(b['code'] == 'statement_copy' and b['draft'] == done['draft']['id']
                   for b in raised.value.details['blockers'])
    cancel(client, done['draft'])
    run(client, 'bank-feed reverse', dict(import_id=imported['import_id']))
    assert table(client, 'SELECT count(*) FROM bank_lines WHERE live=1') == [(0,)]
    assert table(client, 'SELECT count(*) FROM statement_lines') == [(10,)]


def test_period_ignored_reversed_other_account_and_keys(client):
    opening(client)
    content = ofx([('2025-12-31', '-1', 'before', 'Before'),
                   ('2026-01-01', '-2', 'same', 'One'), ('2026-09-30', '-3', 'same', 'Two'),
                   ('2026-10-01', '-4', 'after', 'After')])
    imported = run(client, 'bank-feed import', dict(account='Checking', content=content))
    line = next(v for v in imported['lines'] if v['amount'] == -200)
    run(client, 'bank-line ignore', dict(line=line['id'], ignore_reason='duplicate'))
    other = run(client, 'bank-feed import', dict(account='Payment Example Bank',
                content=csv_file([('2026-05-01', 'Other', '73.12')])))
    discarded = run(client, 'bank-feed import', dict(account='Checking',
                    content=csv_file([('2026-05-02', 'Reversed', '81.12')])))
    run(client, 'bank-feed reverse', dict(import_id=discarded['import_id']))
    done = inbox(client)
    assert [(v['date'], v['amount']) for v in done['lines']] == [('2026-01-01', -200), ('2026-09-30', -300)]
    assert {v['line_id'] for v in done['lines']} == {
        v['line_key'] for v in imported['lines'] if v['amount'] in (-200, -300)}
    assert table(client, 'SELECT state FROM bank_lines WHERE id=?', line['id']) == [('ignored',)]
    assert len({v['line_id'] for v in done['lines']}) == 2


def test_card_sign_and_file_first_deduplication(client):
    run(client, 'reconcile opening start', dict(operation_key=new_id(), account='Business Credit Card',
        opening_date='2025-12-31', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='First statement'), references=[]))
    content = ofx([('2026-09-20', '-20.00', 'charge', 'Shop')], card=True)
    run(client, 'bank-feed import', dict(account='Business Credit Card', content=content))
    direct = run(client, 'reconcile import', dict(account='Business Credit Card', content=content,
        start=True, statement_date='2026-09-30', ending_balance='20.00'))
    copied = run(client, 'reconcile import', dict(account='Business Credit Card', from_inbox=True,
                                                 draft=direct['draft']['id']))
    assert copied['lines'][0]['amount'] == -2000
    assert copied['draft'] == direct['draft']
    assert table(client, 'SELECT amount, bank_line_id FROM statement_lines') == [(2000, None)]
    # Append-only file evidence is never relabeled as an inbox copy.
    assert table(client, 'SELECT count(*) FROM statement_imports') == [(1,)]


@pytest.mark.parametrize('extra', [dict(content='file'), dict(format='csv'), dict(save_mapping='x'),
                                  dict(mapping_name='x'), dict(csv_mapping={'date': 'When'})])
def test_inbox_rejects_file_inputs(client, extra):
    with pytest.raises(BookflowError) as raised:
        run(client, 'reconcile import', dict(account='Checking', from_inbox=True,
                                             statement_date='2026-09-30', **extra))
    assert raised.value.code == 'E_VALIDATION'


def test_empty_inbox_and_invalid_period(client):
    opening(client)
    done = inbox(client)
    assert done['counts']['lines'] == 0 and not done['draft']['selections']
    assert table(client, 'SELECT count(*) FROM statement_lines') == [(0,)]
    with pytest.raises(BookflowError) as raised:
        run(client, 'reconcile import', dict(account='Checking', from_inbox=True, statement_date='2025-12-31'))
    assert raised.value.code == 'E_VALIDATION'


def test_partial_reversal_refuses_copy(client):
    opening(client)
    imported = run(client, 'bank-feed import', dict(account='Checking', content=sample('checking-2026-09.csv')))
    # Model a stopped slice: lines remain live until the final reversal transaction.
    with sqlite3.connect(database_path(client)) as conn:
        conn.execute("UPDATE batches SET state='reversing' WHERE id=?", (imported['batch_id'],))
    with pytest.raises(BookflowError) as raised:
        inbox(client)
    assert raised.value.code == 'E_VALIDATION' and imported['import_id'] in raised.value.message
    assert table(client, "SELECT count(*) FROM reconciliation_drafts WHERE kind='statement'") == [(0,)]


def test_inbox_input_through_four_surfaces(root, tmp_path):
    import bookflow
    from tests.mcp_matrix_support import Matrix
    client = bookflow.connect(data_root=str(root))
    opening(client)
    run(client, 'bank-feed import', dict(account='Checking', content=sample('checking-2026-09.csv')))

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = []
            for surface in ('python', 'cli', 'http', 'mcp'):
                result = await matrix.call(surface, 'reconcile import', dict(account='Checking', from_inbox=True,
                    start=True, statement_date='2026-09-30', ending_balance='6236.95'))
                assert result['format'] == 'inbox'
                results.append((result['counts'], result['draft']['header'], len(result['draft']['selections'])))
            assert all(v == results[0] for v in results)
        finally:
            await matrix.close()
    anyio.run(witness)


def test_card_inbox_copies_in_reconciliation_sign_and_matches_like_file(client):
    account = 'Business Credit Card'
    run(client, 'reconcile opening start', dict(operation_key=new_id(), account=account,
        opening_date='2025-12-31', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='Opening'), references=[]))
    run(client, 'journal post', dict(date='2026-09-20', lines=[
        dict(account='Bank Fees', side='debit', amount='47.19'),
        dict(account=account, side='credit', amount='47.19')]))
    content = ofx([('2026-09-20', '-47.19', 'card-charge', 'Fee')], card=True)
    request = dict(account=account, start=True, statement_date='2026-09-30', ending_balance='47.19')
    direct = run(client, 'reconcile import', dict(request, content=content), dry_run=True)
    run(client, 'bank-feed import', dict(account=account, content=content))
    copied = run(client, 'reconcile import', dict(request, from_inbox=True))
    assert copied['draft']['selections'] == direct['draft']['selections']
    assert copied['counts']['newly_marked'] == direct['counts']['newly_marked'] == 1
    assert copied['lines'][0]['amount'] == -4719
    assert table(client, 'SELECT amount, bank_line_id IS NOT NULL FROM statement_lines') == [(4719, 1)]


def test_inbox_paging_is_stable_and_new_evidence_invalidates_cursor(client):
    opening(client)
    run(client, 'bank-feed import', dict(account='Checking', content=sample('checking-2026-09.csv')))
    request = dict(account='Checking', from_inbox=True, statement_date='2026-09-30', limit=2)
    first = run(client, 'reconcile import', request)
    second = run(client, 'reconcile import', dict(request, cursor=first['next_cursor']))
    assert first['has_more'] and second['has_more']
    assert {v['line_id'] for v in first['lines']}.isdisjoint(v['line_id'] for v in second['lines'])
    assert first['counts'] == second['counts'] and first['counts']['lines'] == 10
    run(client, 'bank-feed import', dict(account='Checking',
        content=csv_file([('2026-09-29', 'New evidence', '-17.98')])))
    with pytest.raises(BookflowError) as raised:
        run(client, 'reconcile import', dict(request, cursor=first['next_cursor']))
    assert raised.value.code == 'E_VALIDATION'


def test_cli_inbox_flag_starts_the_same_reconciliation(client, cli):
    from tests.test_service_sales_lifecycle import COMPANY
    opening(client)
    run(client, 'bank-feed import', dict(account='Checking', content=sample('checking-2026-09.csv')))
    result = cli.json('--company', COMPANY, '--reason', 'Reconcile from bank inbox',
        'reconcile', 'import', '--account', 'Checking', '--from-inbox', '--start',
        '--statement-date', '2026-09-30', '--ending-balance', '6236.95')
    assert result['format'] == 'inbox' and result['counts']['newly_marked'] == 8
    assert len(result['draft']['selections']) == 9
    assert table(client, 'SELECT count(bank_line_id) FROM statement_lines') == [(10,)]
